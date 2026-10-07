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


def new_wo(p3, *, work_type="PREVENTIVE", asset=None, title="Service pump"):
    return wos.create_work_order(p3["org"], asset=asset or p3["asset"], actor=p3["planner"].user, title=title,
                                 work_type=work_type)


def step(wo, action, p3, who, **data):
    return wos.transition(wo, action=action, actor=p3[who].user, membership=p3[who], **data)


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
