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
    a = asset or p3["asset"]
    return wos.create_work_order(p3["org"], site_id=a.site_id, asset=a, actor=p3["planner"].user,
                                 membership=p3["planner"], title=title, work_type=work_type, priority=priority)


def step(wo, action, p3, who, **data):
    actor = p3[who].user
    membership = p3[who]
    if action == "plan":
        planned_start = data.get("planned_start") or (timezone.now() + timedelta(days=1))
        planned_end = data.get("planned_end") or (planned_start + timedelta(hours=2))
        due_at = data.get("due_at") or (planned_end + timedelta(days=1))
        estimated_hours = data.get("estimated_hours") or 2
        priority = data.get("priority") or wo.priority or "MEDIUM"
        return wos.plan_work_order(wo, membership=membership, actor=actor,
                                   planned_start=planned_start, planned_end=planned_end,
                                   due_at=due_at, estimated_hours=estimated_hours,
                                   work_type=wo.work_type, priority=priority)
    elif action == "assign":
        technician = data.get("technician")
        return wos.assign_work_order(wo, membership=membership, actor=actor,
                                    assignee_id=technician.pk)
    elif action == "dispatch":
        return wos.dispatch_work_order(wo, membership=membership, actor=actor)
    elif action == "start":
        return wos.start_work_order(wo, membership=membership, actor=actor)
    raise NotImplementedError(f"Action {action} not mapped")


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
    if wo.work_type in wos.EVIDENCE_REQUIRED_TYPES:
        wos.add_evidence(wo, upload("done.txt"), actor=p3[tech].user, membership=p3[tech])
    wos.record_labor(wo, technician=p3[tech], work_date=timezone.localdate(), hours="1.5", actor=p3[tech].user,
                     membership=p3[tech])
    return step(wo, "complete", p3, tech, resolution_notes=notes)
