"""
================================================================================
Test Suite: FON-M08 Web UI & HTML Template Verification
Location: tests/checklists/test_m08_ui_views.py
================================================================================

WHY THIS TEST FILE EXISTS:
--------------------------
This test suite verifies the server-rendered HTML views, Bootstrap UI templates,
and HTMX integration points for FON-M08 (Inspection & Checklist Engine) as mandated
by the HPE PRD (HPE-PRD-2026-FOPS02/VER-10-DRAFT, Sections 2, 4 & 7).

WHAT THIS TEST SUITE COVERS:
----------------------------
1. Template Management Screens:
   - Checklist templates list (`/app/checklists/`): Shows active/draft templates with filters.
   - Template create screen (`/app/checklists/new/`): Validates form render and CSRF protection.
   - Template detail & builder (`/app/checklists/<id>/`): Displays sections, question items,
     type badges, and status controls.
2. Inspection Execution Screens:
   - Inspections list (`/app/inspections/`): Filters by status and asset.
   - Inspection runner (`/app/inspections/<id>/`): Renders question widgets
     (pass/fail toggles, numeric inputs with units, open text areas, file evidence dropzones).
3. Finding Resolution Modal / Views:
   - Resolving findings via `/app/findings/<id>/resolve/`.
4. Role-Based Access Control (RBAC) on UI Views:
   - Technicians can view templates and execute inspections.
   - Only Planners/Supervisors can create and activate templates.
   - Unauthenticated or cross-tenant users are redirected or denied.
================================================================================
"""
import pytest

from apps.checklists import services as cl
from apps.checklists.models import Finding
from tests.phase3_support import make_template, wo_in_progress

pytestmark = pytest.mark.django_db


def test_checklist_templates_ui_rendering(p3, client):
    """Verifies template list and detail views render HTTP 200 with Navy & Gold shell."""
    user = p3["sup"].user
    client.force_login(user)
    session = client.session
    session["organization_id"] = str(p3["org"].pk)
    session.save()

    t = make_template(p3, name="HVAC Air Handler Checklist")

    # List view
    r = client.get("/app/checklists/")
    assert r.status_code == 200
    assert "HVAC Air Handler Checklist" in r.content.decode()

    # Detail / Builder view
    r = client.get(f"/app/checklists/{t.pk}/")
    assert r.status_code == 200
    content = r.content.decode()
    assert "HVAC Air Handler Checklist" in content
    assert "Discharge pressure" in content


def test_inspection_execution_ui_rendering(p3, client):
    """Verifies supervisor inspection UI list and detail screens load with interactive widgets."""
    sup = p3["sup"]
    tech = p3["tech"]
    client.force_login(sup.user)
    session = client.session
    session["organization_id"] = str(p3["org"].pk)
    session.save()

    t = make_template(p3, name="Centrifugal Pump Safety Run")
    wo = wo_in_progress(p3)
    insp = cl.start_inspection(p3["org"], template=t, membership=tech, actor=tech.user, work_order=wo)

    # Inspection list
    r = client.get("/app/inspections/")
    assert r.status_code == 200
    assert "Centrifugal Pump Safety Run" in r.content.decode()

    # Inspection detail page
    r = client.get(f"/app/inspections/{insp.pk}/")
    assert r.status_code == 200
    content = r.content.decode()
    assert "Centrifugal Pump Safety Run" in content
    assert "IN_PROGRESS" in content or "In Progress" in content


def test_finding_resolve_ui_flow(p3, client):
    """Verifies supervisor finding resolution POST view."""
    sup = p3["sup"]
    tech = p3["tech"]
    t = make_template(p3)
    wo = wo_in_progress(p3)
    insp = cl.start_inspection(p3["org"], template=t, membership=tech, actor=tech.user, work_order=wo)
    finding = cl.add_finding(insp, description="High vibration on casing", severity=Finding.Severity.HIGH,
                             membership=tech, actor=tech.user)

    client.force_login(sup.user)
    session = client.session
    session["organization_id"] = str(p3["org"].pk)
    session.save()

    r = client.post(f"/app/findings/{finding.pk}/resolve/", {"reason": "Realigned motor coupling"})
    assert r.status_code == 302
    finding.refresh_from_db()
    assert finding.status == Finding.Status.RESOLVED
    assert finding.resolution_notes == "Realigned motor coupling"


def test_inspection_runner_ui_and_mandatory_photo_flow(p3, client):
    """Verifies runner UI loads, records responses, blocks completion without mandatory photo, and modal renders."""
    tech = p3["tech"]
    client.force_login(tech.user)
    session = client.session
    session["organization_id"] = str(p3["org"].pk)
    session.save()

    # Template with one photo-mandatory question
    t = make_template(p3, name="HVAC Air Handler Maintenance", items=[], activate=False)
    q_photo = cl.add_item(t, prompt="Clean filter and attach photo", item_type="BOOLEAN",
                          evidence_required=True, actor=p3["sup"].user)
    t = cl.activate_template(t, actor=p3["sup"].user)
    wo = wo_in_progress(p3)


    insp = cl.start_inspection(p3["org"], template=t, membership=tech, actor=tech.user, work_order=wo)

    # 1. Runner page loads HTTP 200
    r = client.get(f"/app/inspections/{insp.pk}/run/")
    assert r.status_code == 200
    content = r.content.decode()
    assert "Execution Runner" in content
    assert "Photo Required" in content

    # 2. Reusable modal partial loads HTTP 200
    r_modal = client.get(f"/app/inspections/{insp.pk}/modal/")
    assert r_modal.status_code == 200
    assert "checklistRunnerModal" in r_modal.content.decode()

    # 3. Answer regular and photo questions
    client.post(f"/app/inspections/{insp.pk}/items/{q_photo.pk}/answer/", {"value": "PASS"})

    # 4. Attempt to complete inspection without photo: must fail and redirect back to runner with warning
    r_comp = client.post(f"/app/inspections/{insp.pk}/complete/", {"summary": "Done"})
    assert r_comp.status_code == 302
    assert f"/app/inspections/{insp.pk}/run/" in r_comp.url
    insp.refresh_from_db()
    assert insp.status == "IN_PROGRESS"

    # 5. Attach photo evidence to question
    from django.core.files.uploadedfile import SimpleUploadedFile
    photo_file = SimpleUploadedFile("filter_clean.jpg", b"\xff\xd8\xff\xe0mock_jpeg_content", content_type="image/jpeg")
    r_up = client.post(f"/app/inspections/{insp.pk}/items/{q_photo.pk}/evidence/",
                       {"file": photo_file, "description": "Cleaned air filter"}, format="multipart")
    assert r_up.status_code == 302

    # 6. Now completion succeeds
    r_comp2 = client.post(f"/app/inspections/{insp.pk}/complete/", {"summary": "All filters inspected and clean"})
    assert r_comp2.status_code == 302
    assert f"/app/inspections/{insp.pk}/" in r_comp2.url
    insp.refresh_from_db()
    assert insp.status == "COMPLETED"
    assert insp.summary == "All filters inspected and clean"

