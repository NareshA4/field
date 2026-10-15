"""
Setup script for Job Completion Screen QA Playwright tests.

Ensures a work order exists in IN_PROGRESS state with:
- Technician assigned (lead.technician@apexindustrial.com)
- Labor entry logged
- Evidence file attached (for corrective type)
- Checklist bypassed
- No existing sign-off (cleaned before each run)

Outputs the work order PK for test consumption.
"""
import os
import sys
import json
from pathlib import Path

root = Path(__file__).resolve().parent.parent
env_path = root / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()

sys.path.insert(0, str(root / "src"))
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings.dev"

import django
django.setup()

from decimal import Decimal
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.workorders.models import WorkOrder, WorkOrderLabor, WorkOrderSignoff
from apps.tenancy.models import Membership


def reset_completion_work_order():
    """Reset a work order to IN_PROGRESS with labor and evidence for completion tests."""
    from axes.models import AccessAttempt
    AccessAttempt.objects.all().delete()

    tech_member = Membership.objects.get(user__email="lead.technician@apexindustrial.com")
    org = tech_member.organization

    # Pick an existing IN_PROGRESS or create-ready WO assigned to the technician
    wo = WorkOrder.objects.filter(
        organization=org,
        assigned_to=tech_member,
    ).order_by("created_at").first()

    if wo is None:
        raise RuntimeError(
            "No work order found assigned to lead.technician@apexindustrial.com. "
            "Run seed_demo_data.py first."
        )

    # Force to IN_PROGRESS and bypass checklist gating
    WorkOrder.objects.filter(pk=wo.pk).update(
        status=WorkOrder.Status.IN_PROGRESS,
        checklist_required=False,
    )
    wo.refresh_from_db()

    # Ensure at least one labor entry exists
    labor_exists = WorkOrderLabor.objects.filter(work_order=wo).exists()
    if not labor_exists:
        WorkOrderLabor.objects.create(
            organization=org,
            work_order=wo,
            technician=tech_member,
            entered_by=tech_member.user,
            work_date=timezone.localdate(),
            hours=Decimal("1.50"),
            description="QA setup: diagnostic and inspection labor",
        )

    # Upload a text evidence file via service if corrective and no evidence exists
    from apps.workspace import services as ws_services
    from apps.workspace import selectors as ws_selectors
    try:
        ctx = ws_selectors.get_job_completion_context(tech_member, str(wo.pk))
        has_evidence = ctx.get("has_evidence", False)
    except Exception:
        has_evidence = False

    if wo.work_type == "CORRECTIVE" and not has_evidence:
        dummy = SimpleUploadedFile(
            "qa_evidence.txt",
            b"QA test evidence document for completion screen verification.",
            content_type="text/plain",
        )
        ws_services.upload_job_evidence(
            wo, file=dummy, description="QA evidence doc",
            membership=tech_member, actor=tech_member.user,
        )

    # Clean any existing sign-off so the form is available
    WorkOrderSignoff.objects.filter(work_order=wo).delete()

    # Output for test consumption
    result = {
        "work_order_pk": str(wo.pk),
        "order_number": wo.order_number,
        "tech_email": "lead.technician@apexindustrial.com",
        "tech_password": "Password123!",
        "status": wo.status,
        "work_type": wo.work_type,
    }
    print(json.dumps(result))
    return result


if __name__ == "__main__":
    reset_completion_work_order()
