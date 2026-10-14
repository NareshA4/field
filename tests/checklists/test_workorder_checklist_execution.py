"""Tests for Work Order Checklist Execution Workflow (M07 <-> M08 integration)."""
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.checklists import services as cl
from apps.checklists.models import ACTIVE, ChecklistItem, ChecklistTemplate, Inspection
from apps.core.exceptions import Conflict, PermissionDenied
from apps.workorders import services as wos
from apps.workorders.models import WorkOrder
from tests.phase3_support import make_template, wo_in_progress

pytestmark = pytest.mark.django_db


def test_work_order_checklist_tab_and_execution_lifecycle(p3, client):
    """Verifies complete technician workflow from Checklist tab to execution runner to completion."""
    org = p3["org"]
    tech = p3["tech"]
    client.force_login(tech.user)
    session = client.session
    session["organization_id"] = str(org.pk)
    session.save()

    # 1. Create M08-WO-CORRECTIVE checklist template
    items = [
        {"prompt": "Lockout/Tagout applied", "item_type": "BOOLEAN", "required": True},
        {"prompt": "Operating temperature", "item_type": "NUMERIC", "min_value": "20", "max_value": "80", "unit": "C", "required": True},
        {"prompt": "Post-repair photo", "item_type": "TEXT", "evidence_required": True, "required": True},
    ]
    t = make_template(p3, name="Corrective Maintenance Checklist (M08-WO-CORRECTIVE)",
                      items=items, work_type="CORRECTIVE", required=True)

    # 2. Work order in progress
    wo = wo_in_progress(p3, work_type="CORRECTIVE", tech="tech")
    wo.checklist_template_ref = "M08-WO-CORRECTIVE"
    wo.checklist_required = True
    wo.save(update_fields=["checklist_template_ref", "checklist_required", "updated_at"])

    # 3. View Checklist tab: Start Checklist button and steps table should be visible
    tab_url = f"{reverse('workorders:detail', args=[wo.pk])}?tab=checklist"
    resp = client.get(tab_url)
    assert resp.status_code == 200
    content = resp.content.decode()
    assert "Start Checklist" in content
    assert "M08-WO-CORRECTIVE" in content
    assert "Lockout/Tagout applied" in content
    assert "Post-repair photo" in content

    # 4. Attempting to complete work order now must be blocked
    with pytest.raises(Conflict):
        wos.complete_work_order(wo, membership=tech, actor=tech.user, resolution_notes="Completed without checklist.")

    # 5. Technician clicks Start Checklist
    execute_url = reverse("workorders:checklist_execute", args=[wo.pk])
    exec_resp = client.get(execute_url)
    assert exec_resp.status_code == 302
    wo.refresh_from_db()
    assert wo.checklist_instance_ref != ""
    assert wo.checklist_status == "IN_PROGRESS"

    insp = Inspection.objects.get(pk=wo.checklist_instance_ref)
    assert insp.status == "IN_PROGRESS"
    assert insp.work_order == wo
    assert exec_resp.url == reverse("checklists:inspection_run", args=[insp.pk])

    # 6. Tab now shows "Execute Checklist"
    tab_resp = client.get(tab_url)
    assert "Execute Checklist" in tab_resp.content.decode()

    # 7. Execute checklist: answer items
    loto_item = t.items.get(prompt="Lockout/Tagout applied")
    temp_item = t.items.get(prompt="Operating temperature")
    photo_item = t.items.get(prompt="Post-repair photo")

    client.post(reverse("checklists:inspection_save_answer", args=[insp.pk, loto_item.pk]), {"value": "PASS"})
    client.post(reverse("checklists:inspection_save_answer", args=[insp.pk, temp_item.pk]), {"value": "45.5"})
    client.post(reverse("checklists:inspection_save_answer", args=[insp.pk, photo_item.pk]), {"value": "Verified and intact"})

    # Attach required photo evidence (text upload format used in test runner)
    photo_file = SimpleUploadedFile("pump_tag.txt", b"photo evidence content")
    upload_resp = client.post(reverse("checklists:inspection_upload_evidence", args=[insp.pk, photo_item.pk]), {
        "file": photo_file,
        "description": "Post-repair photo evidence",
    })
    assert upload_resp.status_code == 302

    # 8. Complete the inspection
    comp_resp = client.post(reverse("checklists:inspection_complete", args=[insp.pk]), {
        "summary": "All corrective verification checks passed.",
    })
    assert comp_resp.status_code == 302

    insp.refresh_from_db()
    assert insp.status == "COMPLETED"

    # 9. Verify work order checklist status is now COMPLETED and VALID
    status_result = wos.checklist_blockers(wo)
    assert status_result == []

    # 10. Complete the work order
    evidence_file = SimpleUploadedFile("wo_completion.txt", b"wo-completion-evidence")
    from apps.files import services as file_services
    att = file_services.attach(evidence_file, target=wo, organization=org, user=tech.user, read_permission="work_order.view")
    wos.add_evidence(wo, membership=tech, actor=tech.user, attachment_id=att.pk)
    wos.record_labor(wo, technician=tech, work_date=timezone.localdate(), hours="2.0", actor=tech.user, membership=tech)

    completed_wo = wos.complete_work_order(wo, membership=tech, actor=tech.user,
                                          resolution_notes="Repaired pump seal, verified with M08 checklist.")
    assert completed_wo.status == WorkOrder.Status.COMPLETED
    assert completed_wo.checklist_status == "COMPLETED"
    assert completed_wo.checklist_validation_state == "VALID"


def test_checklist_execute_permission_denied_for_unassigned_technician(p3, client):
    """Only the assigned technician or dispatcher can start/execute the checklist."""
    tech2 = p3["tech2"]
    client.force_login(tech2.user)
    session = client.session
    session["organization_id"] = str(p3["org"].pk)
    session.save()

    wo = wo_in_progress(p3, work_type="CORRECTIVE", tech="tech")
    wo.checklist_template_ref = "M08-WO-CORRECTIVE"
    wo.save(update_fields=["checklist_template_ref"])

    execute_url = reverse("workorders:checklist_execute", args=[wo.pk])
    resp = client.get(execute_url)
    assert resp.status_code in (403, 404)


def test_checklist_execute_blocked_when_work_order_not_in_progress(p3, client):
    """Checklist execution cannot be started when work order is not in progress."""
    tech = p3["tech"]
    client.force_login(tech.user)
    session = client.session
    session["organization_id"] = str(p3["org"].pk)
    session.save()

    wo = wo_in_progress(p3, work_type="CORRECTIVE", tech="tech")
    wo = wos.hold_work_order(wo, membership=tech, actor=tech.user, reason="Waiting for parts")
    assert wo.status == WorkOrder.Status.ON_HOLD

    execute_url = reverse("workorders:checklist_execute", args=[wo.pk])
    resp = client.get(execute_url)
    assert resp.status_code == 302
    assert "tab=overview" in resp.url or reverse("workorders:detail", args=[wo.pk]) in resp.url
