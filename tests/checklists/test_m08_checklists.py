"""
================================================================================
Test Suite: FON-M08 Inspection & Checklist Engine
Location: tests/checklists/test_m08_checklists.py
================================================================================

WHY THIS TEST FILE EXISTS:
--------------------------
This test suite verifies the business logic, REST APIs, and multi-tenant security
controls of FON-M08 (Inspection & Checklist Engine) as mandated by the HPE PRD
(HPE-PRD-2026-FOPS02/VER-10-DRAFT, Sections 4, 5, 6, 7 & 10).

WHAT THIS TEST FILE TESTS:
--------------------------
1. Checklist Template Lifecycle & Versioning:
   - Draft -> Active -> Inactive state machine transitions.
   - Immutability of active/historical templates (frozen items; changes create new draft versions).
2. Dynamic Checklist Question / Item Types:
   - TEXT: Technician open notes and qualitative input.
   - NUMERIC: Sensor/gauge readings with min/max threshold validation.
   - BOOLEAN: Pass / Fail criteria.
   - SELECTION: Multi-choice values and exception flags that automatically require findings.
3. Inspection Execution Lifecycle:
   - Execution against Assets and Work Orders (IN_PROGRESS -> COMPLETED).
   - Validation that duplicate inspections cannot be started for the same work order.
4. Exception Findings & Remediation:
   - Automated & manual Finding creation for failed items or out-of-range readings.
   - Finding resolution workflows and RBAC permission checks (Supervisors vs Technicians).
5. Work Order Closure Blocker (Critical ERP Integration):
   - Strict verification that Work Orders CANNOT transition to COMPLETED / CLOSED
     if mandatory checklists are incomplete (returns 409 Conflict with 'checklist_incomplete').
6. Evidence & File Uploads:
   - Mandatory photo / evidence attachment validation for safety items.
   - File type validation and scoped access permissions.
7. Enterprise Multi-Tenant & RBAC Hardening:
   - Strict tenant data isolation: Alpha tenant templates, inspections, and findings
     are 100% invisible and inaccessible to Beta tenant (returns 404 / 403).
   - Site scoping: Supervisors and Technicians can only access inspections within
     their assigned site scope.
   - Suspended / inactive membership rejection.
================================================================================
"""
"""M08 Inspection & Checklist Engine: templates (draft / freeze / version), item validation, inspection execution,
findings, evidence, completion rules, the M06 closure integration matrix, RBAC, tenant isolation, site scope and
malicious direct API calls."""
import threading

import pytest
from django.db import close_old_connections
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.checklists import services as cl
from apps.checklists.models import ChecklistItem, ChecklistTemplate, Finding, Inspection, InspectionResponse
from apps.core.exceptions import Conflict, InvalidTransition, PermissionDenied, ValidationFailed
from apps.workorders import services as wos
from tests.phase3_support import (
    finish_work,
    good_answers,
    item_ids,
    make_template,
    new_wo,
    step,
    upload,
    wo_in_progress,
)

pytestmark = pytest.mark.django_db


def audited(org, action):
    return AuditLog.objects.filter(organization=org, action=action).exists()


def start(p3, t, wo, who="tech"):
    return cl.start_inspection(p3["org"], template=t, membership=p3[who], actor=p3[who].user, work_order=wo)


def answer(p3, insp, answers, who="tech"):
    return cl.save_responses(insp, answers, membership=p3[who], actor=p3[who].user)


def complete(p3, insp, who="tech", **kw):
    return cl.complete_inspection(insp, membership=p3[who], actor=p3[who].user, **kw)


# --- templates -----------------------------------------------------------------------------------------------------


def test_template_lifecycle_items_reorder_and_audit(p3):
    t = make_template(p3, activate=False)
    assert t.status == "DRAFT" and t.version == 1 and t.items.count() == 4
    ids = [str(i.pk) for i in t.items.order_by("position")]
    cl.reorder_items(t, list(reversed(ids)), actor=p3["sup"].user)
    assert [str(i.pk) for i in t.items.order_by("position")] == list(reversed(ids))
    assert [i.position for i in t.items.order_by("position")] == [1, 2, 3, 4]
    with pytest.raises(ValidationFailed):
        cl.reorder_items(t, ids[:3], actor=p3["sup"].user)  # must list every item once
    with pytest.raises(ValidationFailed):
        cl.reorder_items(t, ids[:3] + [ids[0]], actor=p3["sup"].user)
    cl.remove_item(t.items.get(prompt="Technician remarks"), actor=p3["sup"].user)
    assert [i.position for i in t.items.order_by("position")] == [1, 2, 3]
    cl.update_item(t.items.get(prompt="Seal condition"), actor=p3["sup"].user, required=False)
    t = cl.activate_template(t, actor=p3["sup"].user)
    assert t.status == "ACTIVE" and t.activated_at
    for action in ("checklist.created", "checklist.item_added", "checklist.items_reordered",
                   "checklist.item_removed", "checklist.item_updated", "checklist.activated"):
        assert audited(p3["org"], action), action


def test_empty_template_cannot_be_activated(p3):
    t = cl.create_template(p3["org"], actor=p3["sup"].user, name="Empty")
    with pytest.raises(Conflict) as e:
        cl.activate_template(t, actor=p3["sup"].user)
    assert e.value.code == "template_empty"


@pytest.mark.parametrize("spec,code", [
    ({"prompt": "ab", "item_type": "TEXT"}, "invalid_text"),
    ({"prompt": "Valid prompt", "item_type": "COLOUR"}, "invalid_item_type"),
    ({"prompt": "Pick one", "item_type": "SELECTION", "options": ["Only"]}, "invalid_options"),
    ({"prompt": "Pick one", "item_type": "SELECTION", "options": ["A", "B"], "exception_options": ["C"]},
     "invalid_options"),
    ({"prompt": "Reading", "item_type": "NUMERIC", "min_value": "9", "max_value": "1"}, "invalid_range"),
    ({"prompt": "Reading", "item_type": "NUMERIC", "min_value": "abc"}, "invalid_number"),
    ({"prompt": "Reading", "item_type": "NUMERIC", "max_value": "NaN"}, "invalid_number"),
])
def test_item_configuration_is_validated(p3, spec, code):
    t = cl.create_template(p3["org"], actor=p3["sup"].user, name="Cfg")
    with pytest.raises(ValidationFailed) as e:
        cl.add_item(t, actor=p3["sup"].user, **spec)
    assert e.value.code == code and not t.items.exists()


def test_active_template_is_frozen_and_new_version_keeps_history(p3):
    t = make_template(p3)
    for fn in (lambda: cl.add_item(t, actor=p3["sup"].user, prompt="Extra question", item_type="TEXT"),
               lambda: cl.update_template(t, actor=p3["sup"].user, name="Renamed"),
               lambda: cl.update_item(t.items.first(), actor=p3["sup"].user, prompt="Changed prompt"),
               lambda: cl.remove_item(t.items.first(), actor=p3["sup"].user),
               lambda: cl.reorder_items(t, [str(i.pk) for i in t.items.all()], actor=p3["sup"].user)):
        with pytest.raises(Conflict) as e:
            fn()
        assert e.value.code == "template_frozen"
    wo = wo_in_progress(p3)
    insp = start(p3, t, wo)
    answer(p3, insp, good_answers(t))
    v2 = cl.new_version(t, actor=p3["sup"].user)
    assert v2.version == 2 and v2.key == t.key and v2.status == "DRAFT" and v2.items.count() == 4
    with pytest.raises(Conflict):
        cl.new_version(t, actor=p3["sup"].user)  # a draft already exists
    cl.update_item(v2.items.get(prompt="Casing free of leaks"), actor=p3["sup"].user, prompt="Casing dry and sealed")
    v2 = cl.activate_template(v2, actor=p3["sup"].user)
    t.refresh_from_db()
    assert t.status == "INACTIVE" and v2.status == "ACTIVE"  # one active version per checklist
    # the historical inspection still shows the question it was answered against
    insp.refresh_from_db()
    assert insp.template_id == t.pk and t.items.filter(prompt="Casing free of leaks").exists()
    assert insp.responses.count() == 3


def test_deactivated_template_cannot_start_new_inspections(p3):
    t = make_template(p3)
    cl.deactivate_template(t, actor=p3["sup"].user)
    with pytest.raises(Conflict) as e:
        start(p3, t, wo_in_progress(p3))
    assert e.value.code == "template_not_active"
    with pytest.raises(InvalidTransition):
        cl.deactivate_template(t, actor=p3["sup"].user)


def test_template_work_type_applicability(p3):
    t = make_template(p3, work_type="CORRECTIVE")
    with pytest.raises(ValidationFailed) as e:
        start(p3, t, wo_in_progress(p3, work_type="PREVENTIVE"))
    assert e.value.code == "template_not_applicable"
    with pytest.raises(ValidationFailed):
        cl.create_template(p3["org"], actor=p3["sup"].user, name="Bad type", work_type="NOPE")


# --- execution: responses ------------------------------------------------------------------------------------------


def test_responses_persist_validate_and_flag_exceptions(p3):
    t = make_template(p3)
    ids = item_ids(t)
    insp = start(p3, t, wo_in_progress(p3))
    answer(p3, insp, {ids["Casing free of leaks"]: "fail", ids["Discharge pressure"]: "9.5",
                      ids["Seal condition"]: "Failed", ids["Technician remarks"]: "  loud  "})
    rows = {r.item.prompt: r for r in InspectionResponse.objects.filter(inspection=insp).select_related("item")}
    assert rows["Casing free of leaks"].value_bool is False and rows["Casing free of leaks"].is_exception
    assert str(rows["Discharge pressure"].value_number) == "9.5000" and rows["Discharge pressure"].is_exception
    assert rows["Seal condition"].is_exception and rows["Technician remarks"].value_text == "loud"
    assert not rows["Technician remarks"].is_exception
    # overwrite (no duplicate row), blank leaves the stored answer alone
    answer(p3, insp, {ids["Casing free of leaks"]: True, ids["Seal condition"]: ""})
    assert InspectionResponse.objects.filter(inspection=insp).count() == 4
    rows = {r.item.prompt: r for r in InspectionResponse.objects.filter(inspection=insp).select_related("item")}
    assert rows["Casing free of leaks"].value_bool is True and not rows["Casing free of leaks"].is_exception
    assert rows["Seal condition"].value_text == "Failed"
    assert audited(p3["org"], "inspection.responses_saved")


@pytest.mark.parametrize("prompt,value", [
    ("Discharge pressure", "abc"), ("Discharge pressure", "NaN"), ("Discharge pressure", "Infinity"),
    ("Discharge pressure", True), ("Discharge pressure", "1.23456"), ("Discharge pressure", "99999999999"),
    ("Casing free of leaks", "maybe"), ("Seal condition", "Excellent"), ("Seal condition", "good"),
    ("Technician remarks", "x" * 2001),
])
def test_invalid_responses_are_rejected_all_or_nothing(p3, prompt, value):
    t = make_template(p3)
    ids = item_ids(t)
    insp = start(p3, t, wo_in_progress(p3))
    with pytest.raises(ValidationFailed) as e:
        answer(p3, insp, {ids["Casing free of leaks"]: True, ids[prompt]: value})
    assert e.value.code == "invalid_responses" and ids[prompt] in e.value.details["errors"]
    assert not InspectionResponse.objects.filter(inspection=insp).exists()  # the valid answer was not saved either


def test_answer_for_foreign_item_is_rejected(p3):
    t, other = make_template(p3), make_template(p3, name="Other list")
    insp = start(p3, t, wo_in_progress(p3))
    with pytest.raises(ValidationFailed):
        answer(p3, insp, {item_ids(other)["Casing free of leaks"]: True})
    with pytest.raises(ValidationFailed):
        answer(p3, insp, {"not-a-uuid": True})


# --- completion rules -----------------------------------------------------------------------------------------------


def test_required_items_exceptions_and_evidence_gate_completion(p3):
    items = [{"prompt": "Casing free of leaks", "item_type": "BOOLEAN"},
             {"prompt": "Gauge photo", "item_type": "NUMERIC", "min_value": "0", "max_value": "10",
              "evidence_required": True},
             {"prompt": "Remarks", "item_type": "TEXT", "required": False}]
    t = make_template(p3, items=items)
    ids = item_ids(t)
    insp = start(p3, t, wo_in_progress(p3))
    with pytest.raises(Conflict) as e:  # nothing answered
        complete(p3, insp)
    assert e.value.code == "inspection_incomplete" and len(e.value.details["issues"]) == 2
    answer(p3, insp, {ids["Casing free of leaks"]: False, ids["Gauge photo"]: "5"})
    with pytest.raises(Conflict) as e:  # exception without finding + missing evidence
        complete(p3, insp)
    assert any("finding" in i for i in e.value.details["issues"]) and any("evidence" in i for i in e.value.details["issues"])
    f = cl.add_finding(insp, description="Water on the casing", severity="HIGH",
                       item=t.items.get(prompt="Casing free of leaks"), membership=p3["tech"], actor=p3["tech"].user)
    cl.add_response_evidence(insp, t.items.get(prompt="Gauge photo"), upload(), membership=p3["tech"],
                             actor=p3["tech"].user)
    done = complete(p3, insp, summary="Leak found, rest fine")
    assert done.status == "COMPLETED" and done.completed_at and done.completed_by_id == p3["tech"].pk
    assert f.status == "OPEN" and f.work_order_id == insp.work_order_id and f.asset_id == insp.asset_id
    assert audited(p3["org"], "inspection.completed") and audited(p3["org"], "inspection.finding_added")


def test_completed_inspection_is_immutable_and_cannot_be_completed_twice(p3):
    t = make_template(p3)
    insp = start(p3, t, wo_in_progress(p3))
    answer(p3, insp, good_answers(t))
    complete(p3, insp)
    for fn in (lambda: answer(p3, insp, {item_ids(t)["Discharge pressure"]: "3"}),
               lambda: cl.add_finding(insp, description="Late finding", membership=p3["tech"], actor=p3["tech"].user),
               lambda: cl.add_response_evidence(insp, t.items.first(), upload(), membership=p3["tech"],
                                                actor=p3["tech"].user)):
        with pytest.raises(Conflict) as e:
            fn()
        assert e.value.code == "inspection_locked"
    with pytest.raises(InvalidTransition):
        complete(p3, insp)  # duplicate submission
    assert Inspection.objects.get(pk=insp.pk).status == "COMPLETED"


def test_duplicate_execution_on_same_work_order_is_refused(p3):
    t = make_template(p3)
    wo = wo_in_progress(p3)
    start(p3, t, wo)
    with pytest.raises(Conflict) as e:
        start(p3, t, wo)
    assert e.value.code == "inspection_exists"
    v2 = cl.new_version(t, actor=p3["sup"].user)  # another version of the same checklist counts as the same checklist
    cl.activate_template(v2, actor=p3["sup"].user)
    with pytest.raises(Conflict):
        start(p3, ChecklistTemplate.objects.get(pk=v2.pk), wo)
    assert Inspection.objects.filter(work_order=wo).count() == 1


def test_concurrent_completion_is_serialised(p3, transactional_db):
    t = make_template(p3)
    insp = start(p3, t, wo_in_progress(p3))
    answer(p3, insp, good_answers(t))
    results = []

    def run():
        close_old_connections()
        try:
            complete(p3, Inspection.objects.get(pk=insp.pk))
            results.append("ok")
        except InvalidTransition:
            results.append("refused")
        finally:
            close_old_connections()

    threads = [threading.Thread(target=run) for _ in range(2)]
    [th.start() for th in threads]
    [th.join() for th in threads]
    assert sorted(results) == ["ok", "refused"]


# --- who may execute / when -----------------------------------------------------------------------------------------


def test_only_assignee_or_dispatcher_executes_and_work_order_must_run(p3):
    t = make_template(p3)
    wo = wo_in_progress(p3, tech="tech")
    with pytest.raises(PermissionDenied):  # a different technician
        start(p3, t, wo, who="tech2")
    with pytest.raises(PermissionDenied):  # supervisor holds inspection.* but is not the assignee/dispatcher
        start(p3, t, wo, who="sup")
    with pytest.raises(PermissionDenied):  # read-only role
        start(p3, t, wo, who="reader")
    insp = start(p3, t, wo)
    wo = step(wo, "hold", p3, "tech", reason="Waiting for access")
    with pytest.raises(Conflict) as e:
        answer(p3, insp, good_answers(t))
    assert e.value.code == "work_order_not_in_progress"
    step(wo, "resume", p3, "tech")
    answer(p3, insp, good_answers(t))
    # not started work cannot be inspected
    other = new_wo(p3, title="Not started")
    with pytest.raises(Conflict):
        cl.start_inspection(p3["org"], template=make_template(p3, name="Second"), membership=p3["tech"],
                            actor=p3["tech"].user, work_order=other)


def test_standalone_inspection_on_an_asset(p3):
    t = make_template(p3)
    insp = cl.start_inspection(p3["org"], template=t, membership=p3["tech"], actor=p3["tech"].user,
                               asset=p3["asset"])
    assert insp.work_order_id is None and insp.site_id == p3["site"].pk
    answer(p3, insp, good_answers(t))
    assert complete(p3, insp).status == "COMPLETED"


def test_findings_resolve_once_and_keep_context(p3):
    t = make_template(p3)
    wo = wo_in_progress(p3)
    insp = start(p3, t, wo)
    f = cl.add_finding(insp, description="Bearing noise", severity="LOW", membership=p3["tech"],
                       actor=p3["tech"].user)
    assert (f.inspection_id, f.work_order_id, f.asset_id, f.site_id, f.status) == (
        insp.pk, wo.pk, p3["asset"].pk, p3["site"].pk, "OPEN")
    assert f.created_by_id == p3["tech"].user.pk and f.created_at
    with pytest.raises(ValidationFailed):
        cl.add_finding(insp, description="x", membership=p3["tech"], actor=p3["tech"].user)
    with pytest.raises(ValidationFailed):
        cl.add_finding(insp, description="Valid text", severity="SEVERE", membership=p3["tech"],
                       actor=p3["tech"].user)
    r = cl.resolve_finding(f, notes="Bearing greased", actor=p3["sup"].user)
    assert r.status == "RESOLVED" and r.resolved_by_id == p3["sup"].user.pk
    with pytest.raises(Conflict):
        cl.resolve_finding(f, notes="again again", actor=p3["sup"].user)


# --- M06 integration: closure / completion matrix -------------------------------------------------------------------


def _to_review(wo, p3):
    wo = step(wo, "start_review", p3, "sup")
    return wo


def test_closure_matrix_no_required_checklist_keeps_m06_behaviour(p3):
    make_template(p3, required=False, name="Optional")
    wo = wo_in_progress(p3)
    assert wos.checklist_blockers(wo) == []
    wo = finish_work(wo, p3)
    wo = _to_review(wo, p3)
    assert wos.closure_blockers(wo) == []
    assert step(wo, "close", p3, "sup").status == "CLOSED"


def test_closure_matrix_required_checklist_not_started_blocks_completion_and_closure(p3):
    t = make_template(p3)
    wo = wo_in_progress(p3)
    assert wos.closure_blockers(wo) and any("has not been started" in b for b in wos.checklist_blockers(wo))
    wos.record_labor(wo, technician=p3["tech"], work_date=timezone.localdate(),
                     hours="1", actor=p3["tech"].user, membership=p3["tech"])
    with pytest.raises(Conflict) as e:  # completion guard (backend)
        step(wo, "complete", p3, "tech", resolution_notes="Everything was done properly.")
    assert e.value.code == "checklist_incomplete"
    assert type(wo).objects.get(pk=wo.pk).status == "IN_PROGRESS"
    assert t.status == "ACTIVE"


def test_closure_matrix_incomplete_then_complete_inspection(p3):
    t = make_template(p3)
    wo = wo_in_progress(p3)
    insp = start(p3, t, wo)
    assert any("is not completed" in b for b in wos.checklist_blockers(wo))
    answer(p3, insp, good_answers(t))
    with pytest.raises(Conflict):
        step(wo, "complete", p3, "tech", resolution_notes="Everything was done properly.")  # still not completed
    complete(p3, insp)
    assert wos.checklist_blockers(wo) == []
    wo = finish_work(wo, p3)
    assert wo.status == "COMPLETED"
    wo = _to_review(wo, p3)
    assert wos.closure_blockers(wo) == []
    assert step(wo, "close", p3, "sup").status == "CLOSED"


def test_closure_blocker_reads_db_state_not_a_flag(p3):
    """Rework after review: an inspection that is deleted from the picture (new required template activated
    later) re-blocks closure; the blocker follows persisted state, never a cached/fake value."""
    t = make_template(p3)
    wo = wo_in_progress(p3)
    insp = start(p3, t, wo)
    answer(p3, insp, good_answers(t))
    complete(p3, insp)
    wo = _to_review(finish_work(wo, p3), p3)
    assert wos.closure_blockers(wo) == []
    make_template(p3, name="Safety lock-out", items=[{"prompt": "Isolated?", "item_type": "BOOLEAN"}])
    assert any("Safety lock-out" in b for b in wos.closure_blockers(wo))
    with pytest.raises(Conflict) as e:
        step(wo, "close", p3, "sup")
    assert e.value.code == "closure_blocked"


def test_unfinished_inspection_of_deactivated_required_template_still_blocks(p3):
    t = make_template(p3)
    wo = wo_in_progress(p3)
    start(p3, t, wo)
    cl.deactivate_template(t, actor=p3["sup"].user)
    assert wos.checklist_blockers(wo)


def test_required_template_applies_only_to_matching_work_type(p3):
    make_template(p3, work_type="CORRECTIVE")
    assert wos.checklist_blockers(wo_in_progress(p3, work_type="PREVENTIVE")) == []
    assert wos.checklist_blockers(wo_in_progress(p3, work_type="CORRECTIVE", tech="tech2")) != []


# --- API: RBAC, validation, malicious calls --------------------------------------------------------------------------


def hdr(as_user, member):
    return as_user(member.user, member.organization)


def test_template_api_permissions(p3, as_user):
    sup, tech, reader = (hdr(as_user, p3[k]) for k in ("sup", "tech", "reader"))
    body = {"name": "API checklist", "work_type": "PREVENTIVE", "is_required": True}
    assert tech.post("/api/v1/checklist-templates/", body, format="json").status_code == 403
    assert reader.post("/api/v1/checklist-templates/", body, format="json").status_code == 403
    r = sup.post("/api/v1/checklist-templates/", body, format="json")
    assert r.status_code == 201 and r.json()["status"] == "DRAFT"
    tid = r.json()["id"]
    assert tech.get("/api/v1/checklist-templates/").status_code == 403  # technicians do not browse templates
    assert reader.get("/api/v1/checklist-templates/").status_code == 200
    assert reader.post(f"/api/v1/checklist-templates/{tid}/items/",
                       {"prompt": "Anything", "item_type": "TEXT"}, format="json").status_code == 403
    r = sup.post(f"/api/v1/checklist-templates/{tid}/items/", {"prompt": "Gauge reading", "item_type": "NUMERIC",
                                                               "min_value": "1", "max_value": "5"}, format="json")
    assert r.status_code == 201
    iid = r.json()["id"]
    assert sup.post(f"/api/v1/checklist-templates/{tid}/items/", {"prompt": "x", "item_type": "TEXT"},
                    format="json").status_code == 400
    assert sup.patch(f"/api/v1/checklist-templates/{tid}/", {"status": "ACTIVE"}, format="json").status_code == 400
    assert sup.patch(f"/api/v1/checklist-templates/{tid}/items/{iid}/", {"required": False},
                     format="json").json()["required"] is False
    assert sup.post(f"/api/v1/checklist-templates/{tid}/activate/").status_code == 200
    r = sup.patch(f"/api/v1/checklist-templates/{tid}/", {"name": "Renamed"}, format="json")
    assert r.status_code == 409 and r.json()["error"]["code"] == "template_frozen"
    assert sup.delete(f"/api/v1/checklist-templates/{tid}/items/{iid}/").status_code == 409
    assert sup.post(f"/api/v1/checklist-templates/{tid}/new-version/").status_code == 201
    assert ChecklistItem.objects.filter(pk=iid).exists()


def test_inspection_api_flow_and_malicious_calls(p3, as_user):
    t = make_template(p3)
    wo = wo_in_progress(p3)
    tech, tech2, sup, reader = (hdr(as_user, p3[k]) for k in ("tech", "tech2", "sup", "reader"))
    ids = item_ids(t)
    assert tech2.post("/api/v1/inspections/", {"template": str(t.pk), "work_order": str(wo.pk)},
                      format="json").status_code == 404  # not their work order: invisible
    assert reader.post("/api/v1/inspections/", {"template": str(t.pk), "work_order": str(wo.pk)},
                       format="json").status_code == 403
    assert tech.post("/api/v1/inspections/", {"template": str(t.pk)}, format="json").status_code == 400
    r = tech.post("/api/v1/inspections/", {"template": str(t.pk), "work_order": str(wo.pk)}, format="json")
    assert r.status_code == 201 and r.json()["status"] == "IN_PROGRESS" and len(r.json()["items"]) == 4
    iid = r.json()["id"]
    assert tech.post("/api/v1/inspections/", {"template": str(t.pk), "work_order": str(wo.pk)},
                     format="json").status_code == 409  # duplicate execution
    # other technician cannot see / write / complete it (404, not 403: existence is not revealed)
    for method, url, body in (("get", f"/api/v1/inspections/{iid}/", None),
                              ("post", f"/api/v1/inspections/{iid}/responses/", {"answers": {}}),
                              ("post", f"/api/v1/inspections/{iid}/complete/", {}),
                              ("get", f"/api/v1/inspections/{iid}/findings/", None)):
        assert getattr(tech2, method)(url, *( [body] if body is not None else []), **({"format": "json"} if body is not None else {})).status_code == 404
    # supervisor sees it (inspection.view) but may not write
    assert sup.get(f"/api/v1/inspections/{iid}/").status_code == 200
    assert sup.post(f"/api/v1/inspections/{iid}/responses/", {"answers": {}}, format="json").status_code == 403
    assert sup.post(f"/api/v1/inspections/{iid}/complete/", {}, format="json").status_code == 403
    # invalid payloads
    r = tech.post(f"/api/v1/inspections/{iid}/responses/",
                  {"answers": {ids["Discharge pressure"]: "lots"}}, format="json")
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_responses"
    r = tech.post(f"/api/v1/inspections/{iid}/complete/", {}, format="json")
    assert r.status_code == 409 and r.json()["error"]["code"] == "inspection_incomplete"
    # valid
    r = tech.post(f"/api/v1/inspections/{iid}/responses/", {"answers": good_answers(t)}, format="json")
    assert r.status_code == 200 and r.json()["issues"] == [] and len(r.json()["responses"]) == 3
    # refresh preserves state
    assert len(tech.get(f"/api/v1/inspections/{iid}/").json()["responses"]) == 3
    r = tech.post(f"/api/v1/inspections/{iid}/complete/", {"summary": "ok"}, format="json")
    assert r.status_code == 200 and r.json()["status"] == "COMPLETED"
    assert tech.post(f"/api/v1/inspections/{iid}/complete/", {}, format="json").status_code == 409
    assert tech.post(f"/api/v1/inspections/{iid}/responses/", {"answers": good_answers(t)},
                     format="json").status_code == 409
    # requirements contract
    r = tech.get(f"/api/v1/inspections/requirements/?work_order={wo.pk}")
    assert r.status_code == 200 and r.json()["blockers"] == [] and r.json()["checklists"][0]["state"] == "COMPLETED"
    assert tech2.get(f"/api/v1/inspections/requirements/?work_order={wo.pk}").status_code == 404
    # nothing else changed: the work order is still IN_PROGRESS
    assert type(wo).objects.get(pk=wo.pk).status == "IN_PROGRESS"


def test_malicious_completion_of_work_through_api_is_blocked(p3, as_user):
    make_template(p3)
    wo = wo_in_progress(p3)
    tech = hdr(as_user, p3["tech"])
    wos.record_labor(wo, technician=p3["tech"], work_date=timezone.localdate(),
                     hours="1", actor=p3["tech"].user, membership=p3["tech"])
    r = tech.post(f"/api/v1/work-orders/{wo.pk}/transition/",
                  {"action": "complete", "resolution_notes": "Done without any checklist at all."}, format="json")
    assert r.status_code == 409 and r.json()["error"]["code"] == "checklist_incomplete"
    assert type(wo).objects.get(pk=wo.pk).status == "IN_PROGRESS"
    assert tech.get(f"/api/v1/work-orders/{wo.pk}/closure/").json()["blockers"]


def test_findings_api_resolve_permissions_and_filters(p3, as_user):
    t = make_template(p3)
    wo = wo_in_progress(p3)
    insp = start(p3, t, wo)
    f = cl.add_finding(insp, description="Coupling worn", severity="HIGH", membership=p3["tech"],
                       actor=p3["tech"].user)
    tech, tech2, sup = (hdr(as_user, p3[k]) for k in ("tech", "tech2", "sup"))
    assert tech.get("/api/v1/findings/").json()["results"][0]["id"] == str(f.pk)
    assert tech2.get(f"/api/v1/findings/{f.pk}/").status_code == 404
    assert tech.post(f"/api/v1/findings/{f.pk}/resolve/", {"notes": "fixed it"}, format="json").status_code == 403
    assert sup.get("/api/v1/findings/?status=OPEN&severity=HIGH").status_code == 200
    r = sup.post(f"/api/v1/findings/{f.pk}/resolve/", {"notes": "Coupling replaced"}, format="json")
    assert r.status_code == 200 and r.json()["status"] == "RESOLVED"
    assert Finding.objects.get(pk=f.pk).resolved_at


def test_evidence_api_validation_and_download_authorisation(p3, as_user):
    from django.core.files.uploadedfile import SimpleUploadedFile

    items = [{"prompt": "Photo of nameplate", "item_type": "TEXT", "evidence_required": True}]
    t = make_template(p3, items=items)
    wo = wo_in_progress(p3)
    insp = start(p3, t, wo)
    tech, tech2, sup = (hdr(as_user, p3[k]) for k in ("tech", "tech2", "sup"))
    item = t.items.get()
    url = f"/api/v1/inspections/{insp.pk}/evidence/"
    ok = SimpleUploadedFile("plate.txt", b"nameplate reading captured")
    assert tech.post(url, {"file": ok, "item": str(item.pk)}, format="multipart").status_code == 409  # answer first
    answer(p3, insp, {str(item.pk): "Model X-100"})
    bad = SimpleUploadedFile("run.exe", b"MZ\x90\x00malicious")
    assert tech.post(url, {"file": bad, "item": str(item.pk)}, format="multipart").status_code == 400
    assert tech.post(url, {"file": SimpleUploadedFile("a.txt", b"some proof of work"), "description": "x"},
                     format="multipart").status_code == 400  # neither item nor finding
    r = tech.post(url, {"file": SimpleUploadedFile("plate.txt", b"nameplate reading captured"),
                        "item": str(item.pk)}, format="multipart")
    assert r.status_code == 201 and r.json()["target"] == "inspectionresponse"
    assert len(tech.get(url).json()["results"]) == 1
    assert tech2.post(url, {"file": upload("t2.txt"), "item": str(item.pk)}, format="multipart").status_code == 404
    assert sup.post(url, {"file": upload("s.txt"), "item": str(item.pk)}, format="multipart").status_code == 403
    assert complete(p3, insp).status == "COMPLETED"  # evidence requirement satisfied


# --- tenant isolation / site scope -----------------------------------------------------------------------------------


def test_cross_tenant_everything_is_invisible_and_unusable(p3, org_b, make_member, make_site, make_asset, as_user):
    t = make_template(p3)
    wo = wo_in_progress(p3)
    insp = start(p3, t, wo)
    f = cl.add_finding(insp, description="Alpha-only finding", membership=p3["tech"], actor=p3["tech"].user)
    sup_b = make_member(org_b, "sup@beta.test", "supervisor")
    tech_b = make_member(org_b, "tech@beta.test", "technician")
    site_b = make_site(org_b, "B9")
    asset_b = make_asset(org_b, site_b, "BETA-1")
    sb, tb = hdr(as_user, sup_b), hdr(as_user, tech_b)
    for url in (f"/api/v1/checklist-templates/{t.pk}/", f"/api/v1/inspections/{insp.pk}/",
                f"/api/v1/findings/{f.pk}/", f"/api/v1/inspections/{insp.pk}/findings/"):
        assert sb.get(url).status_code == 404, url
    for url, body in ((f"/api/v1/checklist-templates/{t.pk}/activate/", None),
                      (f"/api/v1/checklist-templates/{t.pk}/new-version/", None),
                      (f"/api/v1/findings/{f.pk}/resolve/", {"notes": "hijack it"}),
                      (f"/api/v1/inspections/{insp.pk}/complete/", {})):
        assert sb.post(url, body, format="json").status_code == 404, url
    assert sb.get("/api/v1/checklist-templates/").json()["results"] == []
    assert sb.get("/api/v1/inspections/").json()["results"] == []
    # Beta cannot execute an Alpha template against its own asset, nor Alpha's template against Alpha's work order
    assert tb.post("/api/v1/inspections/", {"template": str(t.pk), "asset": str(asset_b.pk)},
                   format="json").status_code == 404
    with pytest.raises(ValidationFailed):
        cl.start_inspection(org_b, template=t, membership=tech_b, actor=tech_b.user, asset=asset_b)
    tb_t = make_template({**p3, "org": org_b, "sup": sup_b}, name="Beta list")
    with pytest.raises(ValidationFailed):
        cl.start_inspection(org_b, template=tb_t, membership=tech_b, actor=tech_b.user, work_order=wo)
    with pytest.raises(ValidationFailed):
        cl.start_inspection(p3["org"], template=tb_t, membership=p3["tech"], actor=p3["tech"].user, work_order=wo)
    # an Alpha token cannot be pointed at Beta by header
    assert as_user(p3["tech"].user, org_b).get("/api/v1/inspections/").status_code in (403, 404)


def test_site_scope_limits_inspection_visibility(p3, make_scoped_member, as_user):
    t = make_template(p3)
    wo = wo_in_progress(p3)
    insp = start(p3, t, wo)
    sup_site2 = make_scoped_member(p3["org"], "sup2@alpha.test", "supervisor", [p3["site2"]])
    sup_site1 = make_scoped_member(p3["org"], "sup1@alpha.test", "supervisor", [p3["site"]])
    c2, c1 = hdr(as_user, sup_site2), hdr(as_user, sup_site1)
    assert c2.get(f"/api/v1/inspections/{insp.pk}/").status_code == 404
    assert c2.get("/api/v1/inspections/").json()["results"] == []
    assert c1.get(f"/api/v1/inspections/{insp.pk}/").status_code == 200
    f = cl.add_finding(insp, description="Seen only at site 1", membership=p3["tech"], actor=p3["tech"].user)
    assert c2.get(f"/api/v1/findings/{f.pk}/").status_code == 404
    assert c2.post(f"/api/v1/findings/{f.pk}/resolve/", {"notes": "not my site"}, format="json").status_code == 404
    assert c1.post(f"/api/v1/findings/{f.pk}/resolve/", {"notes": "my site fixed"}, format="json").status_code == 200


def test_inactive_member_is_refused(p3, as_user, make_member):
    from apps.tenancy.models import Membership

    t = make_template(p3)
    wo = wo_in_progress(p3)
    insp = start(p3, t, wo)
    m = p3["tech"]
    m.status = Membership.Status.SUSPENDED
    m.save(update_fields=["status"])
    r = hdr(as_user, m).get(f"/api/v1/inspections/{insp.pk}/")
    assert r.status_code in (401, 403)

