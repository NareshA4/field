"""
Tests for Job Completion Screen & Customer Digital Sign-off (Phase 1).
Validates WorkOrderSignoff schema and get_job_completion_context selector.
"""
from decimal import Decimal
import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.workorders.models import WorkOrder, WorkOrderLabor, WorkOrderSignoff
from apps.workspace import selectors as ws_selectors
from tests.phase3_support import wo_in_progress

pytestmark = pytest.mark.django_db


def test_work_order_signoff_model_creation_and_constraints(p3):
    """Verify WorkOrderSignoff model persists properly with tenant isolation and relations."""
    wo = wo_in_progress(p3)
    tech = p3["tech"]

    signoff = WorkOrderSignoff.objects.create(
        organization=wo.organization,
        work_order=wo,
        signer_name="Jane Doe",
        signer_title="Site Manager",
        signed_at=timezone.now(),
        customer_notes="All work completed satisfactorily and verified operational.",
        technician_notes="Customer approved and signed on tablet.",
    )

    assert signoff.pk is not None
    assert signoff.signer_name == "Jane Doe"
    assert signoff.signer_title == "Site Manager"
    assert wo.customer_signoff == signoff
    assert wo.signoff == signoff
    assert "Jane Doe" in str(signoff)

    # Cross-tenant violation in clean()
    from apps.tenancy.models import Organization

    other_org = Organization.objects.create(name="Other Org", slug="other-org-signoff")
    signoff.organization = other_org
    with pytest.raises(ValidationError):
        signoff.clean()


def test_get_job_completion_context_evaluation(p3):
    """Verify get_job_completion_context computes labor, evidence, checklist, and completion readiness."""
    wo = wo_in_progress(p3, work_type="CORRECTIVE")
    tech = p3["tech"]

    # 1. Initially without labor or evidence
    ctx = ws_selectors.get_job_completion_context(tech, str(wo.pk))
    assert ctx["work_order"] == wo
    assert ctx["total_labor_hours"] == Decimal("0.00")
    assert ctx["evidence"] == []
    assert ctx["signoff"] is None
    assert ctx["is_completed"] is False

    # Since wo is corrective, evidence is required
    assert ctx["evidence_required"] is True
    assert ctx["has_evidence"] is False
    assert ctx["evidence_is_ready"] is False
    assert ctx["can_complete"] is False
    assert any("evidence" in b.lower() for b in ctx["blockers"])

    # 2. Add labor entry
    WorkOrderLabor.objects.create(
        organization=wo.organization,
        work_order=wo,
        technician=tech,
        entered_by=tech.user,
        work_date=timezone.localdate(),
        hours=Decimal("2.50"),
        description="Initial diagnostic and inspection",
    )

    # 3. Add evidence
    from django.core.files.uploadedfile import SimpleUploadedFile
    from apps.workspace import services as ws_services

    dummy_file = SimpleUploadedFile("evidence_doc.txt", b"Valid text report content for completion verification.", content_type="text/plain")
    ws_services.upload_job_evidence(wo, file=dummy_file, description="Completed repair doc", membership=tech, actor=tech.user)

    # 4. Re-evaluate context
    ctx = ws_selectors.get_job_completion_context(tech, str(wo.pk))
    assert ctx["total_labor_hours"] == Decimal("2.50")
    assert len(ctx["labor_entries"]) == 1
    assert ctx["has_evidence"] is True
    assert ctx["evidence_is_ready"] is True

    # Checklist is bypassed/passed if no template or status completed
    if ctx["checklist_is_ready"]:
        assert ctx["can_complete"] is True
        assert ctx["blockers"] == []


VALID_PNG_B64 = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def test_complete_job_with_signoff_service_happy_path(p3):
    """Technician completes work order with customer digital signature and optional notes."""
    from apps.workspace import services as ws_services

    wo = wo_in_progress(p3, work_type="PREVENTIVE")
    wo.checklist_required = False
    wo.save(update_fields=["checklist_required"])
    tech = p3["tech"]

    completed_order, signoff = ws_services.complete_job_with_signoff(
        wo,
        resolution_notes="Cleaned fan blades, tested rotation, verified amperage.",
        signer_name="Sarah Connor",
        signer_title="Facility Director",
        signature_data_base64=VALID_PNG_B64,
        customer_notes="Fast and clean maintenance service.",
        technician_notes="All clear.",
        membership=tech,
        actor=tech.user,
    )

    assert completed_order.status == WorkOrder.Status.COMPLETED
    assert signoff.signer_name == "Sarah Connor"
    assert signoff.signer_title == "Facility Director"
    assert signoff.customer_notes == "Fast and clean maintenance service."
    assert signoff.signature_attachment is not None
    assert signoff.signature_attachment.original_name.startswith("signature_")
    assert completed_order.signoff == signoff


def test_complete_job_with_signoff_service_negative_paths(p3):
    """Validation errors for missing signer, missing signature, and short resolution notes."""
    from apps.core.exceptions import ValidationFailed
    from apps.workspace import services as ws_services

    wo = wo_in_progress(p3, work_type="PREVENTIVE")
    wo.checklist_required = False
    wo.save(update_fields=["checklist_required"])
    tech = p3["tech"]

    # 1. Missing signer name
    with pytest.raises(ValidationFailed) as exc:
        ws_services.complete_job_with_signoff(
            wo,
            resolution_notes="Serviced all components properly.",
            signer_name="",
            signature_data_base64=VALID_PNG_B64,
            membership=tech,
            actor=tech.user,
        )
    assert "name is required" in str(exc.value.message)

    # 2. Missing digital signature
    with pytest.raises(ValidationFailed) as exc:
        ws_services.complete_job_with_signoff(
            wo,
            resolution_notes="Serviced all components properly.",
            signer_name="Jane Doe",
            signature_data_base64=None,
            membership=tech,
            actor=tech.user,
        )
    assert "digital signature is required" in str(exc.value.message).lower()

    # 3. Resolution notes too short (< 10 chars)
    with pytest.raises(ValidationFailed) as exc:
        ws_services.complete_job_with_signoff(
            wo,
            resolution_notes="Done",
            signer_name="Jane Doe",
            signature_data_base64=VALID_PNG_B64,
            membership=tech,
            actor=tech.user,
        )
    assert "10 characters" in str(exc.value.message)


def test_job_complete_html_view(p3, client):
    """GET renders completion summary screen; POST completes with sign-off and redirects."""
    wo = wo_in_progress(p3, work_type="PREVENTIVE")
    wo.checklist_required = False
    wo.save(update_fields=["checklist_required"])
    tech = p3["tech"]

    client.force_login(tech.user)
    session = client.session
    session["active_org_id"] = str(p3["org"].pk)
    session.save()

    # 1. GET completion screen
    url = f"/app/workspace/jobs/{wo.pk}/complete/"
    resp = client.get(url)
    assert resp.status_code == 200
    assert "Job Completion & Customer Sign-off" in resp.content.decode()

    # 2. POST completion with sign-off
    post_data = {
        "is_completion_screen": "1",
        "resolution_notes": "Completed inspection and verified all equipment.",
        "signer_name": "Marcus Wright",
        "signer_title": "Site Lead",
        "signature_data": VALID_PNG_B64,
        "customer_notes": "All satisfactory.",
    }
    post_resp = client.post(url, data=post_data)
    assert post_resp.status_code == 302
    assert post_resp["Location"] == f"/app/workspace/jobs/{wo.pk}/"

    wo.refresh_from_db()
    assert wo.status == WorkOrder.Status.COMPLETED
    assert wo.signoff is not None
    assert wo.signoff.signer_name == "Marcus Wright"


def test_job_complete_api_view(p3):
    """REST API GET returns completion context; POST executes completion and returns signoff JSON."""
    from rest_framework.test import APIClient

    wo = wo_in_progress(p3, work_type="PREVENTIVE")
    wo.checklist_required = False
    wo.save(update_fields=["checklist_required"])
    tech = p3["tech"]

    api = APIClient()
    api.force_authenticate(user=tech.user)
    session = api.session
    session["active_org_id"] = str(p3["org"].pk)
    session.save()

    api_url = f"/api/v1/workspace/jobs/{wo.pk}/complete/"

    # 1. GET completion summary
    resp = api.get(api_url)
    assert resp.status_code == 200
    data = resp.json()
    assert data["order_number"] == wo.order_number
    assert data["status"] == "IN_PROGRESS"
    assert "total_labor_hours" in data
    assert "checklist_is_ready" in data

    # 2. POST sign-off payload
    payload = {
        "resolution_notes": "Preventive inspection completed without defects.",
        "signer_name": "Kyle Reese",
        "signer_title": "Operations Manager",
        "signature_data": VALID_PNG_B64,
        "customer_notes": "Verified good.",
    }
    post_resp = api.post(api_url, data=payload, format="json")
    assert post_resp.status_code == 200
    res_data = post_resp.json()
    assert res_data["status"] == "COMPLETED"
    assert res_data["signoff"]["signer_name"] == "Kyle Reese"
    assert res_data["signoff"]["signer_title"] == "Operations Manager"

    wo.refresh_from_db()
    assert wo.status == WorkOrder.Status.COMPLETED


def test_job_complete_html_view_negative_paths(p3, client):
    """HTML view rejects submission when signature is missing (422) or work order already completed."""
    wo = wo_in_progress(p3, work_type="PREVENTIVE")
    wo.checklist_required = False
    wo.save(update_fields=["checklist_required"])
    tech = p3["tech"]

    client.force_login(tech.user)
    session = client.session
    session["active_org_id"] = str(p3["org"].pk)
    session.save()

    url = f"/app/workspace/jobs/{wo.pk}/complete/"

    # 1. Missing signature in POST data returns 422 Unprocessable Entity
    invalid_post_data = {
        "is_completion_screen": "1",
        "resolution_notes": "Diagnostic performed successfully.",
        "signer_name": "Marcus Wright",
        "signature_data": "",  # Empty signature
    }
    resp = client.post(url, data=invalid_post_data)
    assert resp.status_code == 422
    assert "digital signature is required" in resp.content.decode().lower()

    # 2. Cannot complete a non-IN_PROGRESS work order
    WorkOrder.objects.filter(pk=wo.pk).update(status=WorkOrder.Status.PLANNED)
    wo.refresh_from_db()
    resp2 = client.post(url, data={
        "is_completion_screen": "1",
        "resolution_notes": "Diagnostic performed successfully.",
        "signer_name": "Marcus Wright",
        "signature_data": VALID_PNG_B64,
    })
    assert resp2.status_code in (400, 409, 422)


def test_job_complete_api_view_negative_paths(p3):
    """REST API rejects incomplete payload with 400 Bad Request."""
    from rest_framework.test import APIClient

    wo = wo_in_progress(p3, work_type="PREVENTIVE")
    wo.checklist_required = False
    wo.save(update_fields=["checklist_required"])
    tech = p3["tech"]

    api = APIClient()
    api.force_authenticate(user=tech.user)
    session = api.session
    session["active_org_id"] = str(p3["org"].pk)
    session.save()

    api_url = f"/api/v1/workspace/jobs/{wo.pk}/complete/"

    # 1. Missing required field (resolution_notes)
    resp = api.post(api_url, data={"signer_name": "Test"}, format="json")
    assert resp.status_code == 400
    res_json = resp.json()
    fields = res_json.get("error", {}).get("details", {}).get("fields", res_json)
    assert "resolution_notes" in fields

    # 2. Missing signature
    resp2 = api.post(api_url, data={"signer_name": "Test", "resolution_notes": "Diagnostic notes minimum length."}, format="json")
    assert resp2.status_code == 400
    res_json2 = resp2.json()
    fields2 = res_json2.get("error", {}).get("details", {}).get("fields", res_json2)
    assert "signature_data" in fields2
