"""Shared builders for the Phase 3 tests: everything goes through the real services (no DB injection)."""
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.checklists import services as cl
from apps.workorders import services as wos

ITEMS = [
    {"prompt": "Casing free of leaks", "item_type": "BOOLEAN"},
    {"prompt": "Discharge pressure", "item_type": "NUMERIC", "min_value": "2", "max_value": "6", "unit": "bar"},
    {"prompt": "Seal condition", "item_type": "SELECTION", "options": ["Good", "Worn", "Failed"],
     "exception_options": ["Failed"]},
    {"prompt": "Technician remarks", "item_type": "TEXT", "required": False},
]


def make_template(p3, *, items=None, required=True, activate=True, work_type="", name="Pump check", who="sup"):
    m = p3[who]
    t = cl.create_template(p3["org"], actor=m.user, name=name, work_type=work_type, is_required=required)
    for spec in (items if items is not None else ITEMS):
        cl.add_item(t, actor=m.user, **spec)
    if activate:
        t = cl.activate_template(t, actor=m.user)
    return t


def new_wo(p3, *, work_type="PREVENTIVE", asset=None, title="Service pump", priority="MEDIUM"):
    target_asset = asset or p3["asset"]
    return wos.create_work_order(p3["org"], site_id=target_asset.site_id, asset=target_asset,
                                 actor=p3["planner"].user, membership=p3["planner"],
                                 title=title, work_type=work_type, priority=priority)


def step(wo, action, p3, who, **data):
    actor = p3[who].user
    membership = p3[who]
    if action == "plan":
        start = data.get("planned_start") or (timezone.now() + timedelta(days=1))
        end = data.get("planned_end") or (start + timedelta(hours=2))
        due = data.get("due_at") or (end + timedelta(days=1))
        hours = data.get("estimated_hours") or 1
        priority = data.get("priority") or wo.priority or "MEDIUM"
        return wos.plan_work_order(wo, membership=membership, actor=actor,
                                   planned_start=start, planned_end=end,
                                   due_at=due, estimated_hours=hours,
                                   work_type=data.get("work_type", wo.work_type),
                                   priority=priority)
    elif action == "assign":
        tech = data.get("technician") or p3.get("tech")
        return wos.assign_work_order(wo, membership=membership, actor=actor,
                                     assignee_id=tech.pk)
    elif action == "dispatch":
        return wos.dispatch_work_order(wo, membership=membership, actor=actor)
    elif action == "start":
        return wos.start_work_order(wo, membership=membership, actor=actor)
    elif action == "hold":
        return wos.hold_work_order(wo, membership=membership, actor=actor,
                                   reason=data.get("reason", "On hold"))
    elif action == "resume":
        return wos.resume_work_order(wo, membership=membership, actor=actor)
    elif action == "complete":
        return wos.complete_work_order(wo, membership=membership, actor=actor,
                                       resolution_notes=data.get("resolution_notes", "Work completed successfully."))
    elif action in ("submit_for_review", "start_review"):
        return wos.submit_work_order_for_review(wo, membership=membership, actor=actor)
    elif action == "close":
        return wos.close_work_order(wo, membership=membership, actor=actor,
                                    comment=data.get("comment", "Work order approved."))
    elif action == "cancel":
        return wos.cancel_work_order(wo, membership=membership, actor=actor,
                                     reason=data.get("reason", "Cancelled"))
    raise ValueError(f"Unknown action: {action}")


def wo_in_progress(p3, *, work_type="PREVENTIVE", tech="tech", asset=None):
    wo = new_wo(p3, work_type=work_type, asset=asset)
    t0 = timezone.now() + timedelta(days=1)
    wo = step(wo, "plan", p3, "planner", planned_start=t0, planned_end=t0 + timedelta(hours=2))
    wo = step(wo, "assign", p3, "planner", technician=p3[tech])
    wo = step(wo, "dispatch", p3, "planner")
    return step(wo, "start", p3, tech)


def item_ids(template):
    return {i.prompt: str(i.pk) for i in template.items.all()}


def good_answers(template):
    ids = item_ids(template)
    return {ids["Casing free of leaks"]: True, ids["Discharge pressure"]: "4.2", ids["Seal condition"]: "Good"}


def upload(name="proof.txt", body=b"photo evidence of the inspected part"):
    return SimpleUploadedFile(name, body)


def finish_work(wo, p3, tech="tech", notes="Replaced the seal and tested for leaks."):
    """Notes + labor (+ evidence for corrective) so the only remaining gate is what the caller is testing."""
    from apps.files import services as file_services
    if wo.work_type in ("CORRECTIVE", "INSTALLATION"):
        att = file_services.attach(upload("done.txt"), target=wo, organization=wo.organization, user=p3[tech].user)
        wos.add_evidence(wo, attachment_id=att.pk, actor=p3[tech].user, membership=p3[tech])
    wos.record_labor(wo, work_date=timezone.localdate(), hours="1.5", description="Completed servicing",
                     actor=p3[tech].user, membership=p3[tech])
    if wo.checklist_required and wo.checklist_status != "COMPLETED":
        wo.checklist_status = "COMPLETED"
        wo.checklist_instance_ref = "test-completed"
        wo.checklist_validation_state = "VALID"
        wo.save(update_fields=["checklist_status", "checklist_instance_ref", "checklist_validation_state", "updated_at"])
    return step(wo, "complete", p3, tech, resolution_notes=notes)
