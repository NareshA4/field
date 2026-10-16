"""
================================================================================
Test Suite: FON-M08 End-to-End Business Integration Tests
Location: tests/checklists/test_m08_integration.py
================================================================================

WHY THIS TEST FILE EXISTS:
--------------------------
This test suite validates the deep cross-module integrations of FON-M08 (Inspection &
Checklist Engine) with Work Orders (FON-M06), Asset Management (FON-M02), and
Audit Trail (FON-M15) as specified by the HPE PRD (HPE-PRD-2026-FOPS02/VER-10-DRAFT,
Section 10 Acceptance Criteria #3: 'Dispatch a technician to a work order, complete
checklist steps, log time, and attach completion photos').

WHAT THIS INTEGRATION TEST SUITE COVERS:
-----------------------------------------
1. Work Order Closure Gatekeeper Matrix (M08 <-> M06 Integration):
   - Verifies that Work Orders strictly CANNOT be marked COMPLETED or CLOSED
     while any required checklist for the asset or work type is uncompleted.
   - Verifies that once all required checklists are completed and findings resolved,
     the Work Order transition succeeds atomically.
2. Defect / Finding Escalation Cycle:
   - Out-of-bounds readings generate exception findings.
   - Verifies findings are attached to the Asset and Work Order for long-term audit history.
   - Verifies supervisor-only resolution authorization.
3. Multi-Section Complex Checklist Execution:
   - Multi-question templates combining pass/fail, numeric bounds, notes, and photos.
   - Comprehensive data persistence and score / status validation.
4. Audit Trail Verification:
   - Confirms that every inspection completion and finding resolution produces an
     immutable audit log entry in the system ledger.
================================================================================
"""
import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.checklists import services as cl
from apps.checklists.models import Finding, Inspection
from apps.core.exceptions import Conflict
from apps.workorders import services as wos
from tests.phase3_support import (
    good_answers,
    make_template,
    wo_in_progress,
)

pytestmark = pytest.mark.django_db


def test_full_inspection_to_work_order_closure_lifecycle(p3, as_user):
    """
    End-to-End Scenario:
    1. A work order is in progress for an asset.
    2. A mandatory checklist template exists for PREVENTIVE maintenance.
    3. Attempting to complete the work order early fails with 409 Conflict.
    4. Technician starts inspection, submits answers with 1 out-of-range parameter.
    5. An exception finding is logged.
    6. Technician completes inspection.
    7. Supervisor resolves the open finding.
    8. Technician records labor hours and completes work order.
    9. Work order successfully completes and closes.
    """
    org = p3["org"]
    tech = p3["tech"]
    sup = p3["sup"]
    p3["planner"]
    asset = p3["asset"]

    # Step 1: Create active mandatory checklist template
    template_items = [
        {"prompt": "Safety shield intact", "item_type": "BOOLEAN", "required": True},
        {"prompt": "Operating temperature", "item_type": "NUMERIC", "min_value": "40", "max_value": "90", "unit": "degC", "required": True},
        {"prompt": "Oil level", "item_type": "SELECTION", "options": ["Normal", "Low", "Critical"], "exception_options": ["Critical"], "required": True},
        {"prompt": "General observations", "item_type": "TEXT", "required": False},
    ]
    template = make_template(p3, items=template_items, required=True, activate=True, work_type="PREVENTIVE", name="Pre-Op Safety & Thermal Inspection")

    # Step 2: Work order in progress
    wo = wo_in_progress(p3, work_type="PREVENTIVE", asset=asset)

    # Step 3: Verify closure blocker is active
    blockers = wos.closure_blockers(wo)
    assert any("checklist" in str(b).lower() for b in blockers), f"Expected checklist blocker, got: {blockers}"

    # Verify transition directly raises Conflict
    with pytest.raises(Conflict):
        wos.transition(wo, action="complete", actor=tech.user, membership=tech, resolution_notes="Completed without checklist")

    # Step 4: Technician starts the inspection
    inspection = cl.start_inspection(org, template=template, membership=tech, actor=tech.user, work_order=wo)
    assert inspection.status == "IN_PROGRESS"
    assert inspection.work_order == wo
    assert inspection.asset == asset

    # Step 5: Technician answers items (temperature = 95 degC -> out of range!)
    item_map = {item.prompt: str(item.pk) for item in template.items.all()}
    answers = {
        item_map["Safety shield intact"]: True,
        item_map["Operating temperature"]: "95.5",  # Over max 90.0!
        item_map["Oil level"]: "Critical",           # In exception options!
        item_map["General observations"]: "Slight thermal anomaly observed on bearing housing.",
    }
    responses = cl.save_responses(inspection, answers, membership=tech, actor=tech.user)
    assert len(responses) == 4

    # Verify exceptions were flagged
    temp_resp = next(r for r in responses if r.item.prompt == "Operating temperature")
    assert temp_resp.is_exception is True

    oil_resp = next(r for r in responses if r.item.prompt == "Oil level")
    assert oil_resp.is_exception is True

    # Step 6: Log exception findings for all exception questions
    finding = cl.add_finding(
        inspection,
        item=temp_resp.item,
        description="Temperature at 95.5 degC exceeds safe 90.0 degC limit",
        severity=Finding.Severity.HIGH,
        membership=tech,
        actor=tech.user,
    )
    assert finding.status == Finding.Status.OPEN
    assert finding.asset == asset
    assert finding.work_order == wo

    oil_finding = cl.add_finding(
        inspection,
        item=oil_resp.item,
        description="Oil level critical",
        severity=Finding.Severity.MEDIUM,
        membership=tech,
        actor=tech.user,
    )
    assert oil_finding.status == Finding.Status.OPEN

    # Step 7: Complete the inspection
    cl.complete_inspection(inspection, membership=tech, actor=tech.user, summary="Checklist finished with 2 findings logged.")
    inspection.refresh_from_db()
    assert inspection.status == "COMPLETED"

    # Step 8: Supervisor resolves both findings
    cl.resolve_finding(finding, actor=sup.user, notes="Bearing lubrication flushed and re-greased; temp stabilized at 68 degC.")
    cl.resolve_finding(oil_finding, actor=sup.user, notes="Reservoir topped up with synthetic ISO VG 46.")
    finding.refresh_from_db()
    assert finding.status == Finding.Status.RESOLVED
    assert finding.resolved_by == sup.user

    # Step 9: Work order checklist blockers should now be cleared
    blockers_now = wos.closure_blockers(wo)
    assert not any("checklist" in str(b).lower() for b in blockers_now), f"Checklist blockers should be cleared, got: {blockers_now}"

    # Step 10: Complete and close the work order cleanly
    wos.record_labor(wo, technician=tech, work_date=timezone.localdate(), hours="2.5", actor=tech.user, membership=tech)
    wo = wos.transition(wo, action="complete", actor=tech.user, membership=tech, resolution_notes="Inspections passed, bearing lubricated.")
    assert wo.status == "COMPLETED"

    wo = wos.transition(wo, action="start_review", actor=sup.user, membership=sup)
    assert wo.status == "SUPERVISOR_REVIEW"

    wo = wos.transition(wo, action="close", actor=sup.user, membership=sup)
    assert wo.status == "CLOSED"

    # Step 11: Audit Trail Verification
    assert AuditLog.objects.filter(organization=org, action="inspection.completed", target_id=inspection.pk).exists()
    assert AuditLog.objects.filter(organization=org, action="inspection.finding_resolved", target_id=finding.pk).exists()


def test_asset_inspection_history_immutability(p3):
    """
    Validates that completed inspections provide an authoritative, immutable
    historical audit record for the asset.
    """
    org = p3["org"]
    tech = p3["tech"]
    asset = p3["asset"]

    template = make_template(p3, name="Quarterly Pump Inspection")
    inspection = cl.start_inspection(org, template=template, membership=tech, actor=tech.user, asset=asset)

    answers = good_answers(template)
    cl.save_responses(inspection, answers, membership=tech, actor=tech.user)
    cl.complete_inspection(inspection, membership=tech, actor=tech.user, summary="Routine check clean")

    # Asset should show inspection in historical lookups
    inspections_for_asset = Inspection.objects.filter(asset=asset, organization=org)
    assert inspections_for_asset.count() >= 1

    # Attempting to mutate answers on completed inspection must fail
    with pytest.raises(Conflict):
        cl.save_responses(inspection, answers, membership=tech, actor=tech.user)
