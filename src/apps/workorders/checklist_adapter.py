"""M06 boundary for the future M08 Checklist Engine integration.

References are opaque M08 identifiers (UUIDs or stable external codes); M06 does
not own checklist templates, questions, answers, versions, or scoring.
"""
from dataclasses import dataclass

from django.utils import timezone

NOT_REQUIRED = "NOT_REQUIRED"
PENDING = "PENDING"
IN_PROGRESS = "IN_PROGRESS"
COMPLETED = "COMPLETED"
FAILED = "FAILED"

# Work-type policy is M06 configuration until it is supplied by M08.
WORK_TYPE_REQUIREMENTS = {
    "CORRECTIVE": (True, "M08-WO-CORRECTIVE"),
    "PREVENTIVE": (True, "M08-WO-PREVENTIVE"),
    "EMERGENCY": (True, "M08-WO-EMERGENCY"),
    "INSPECTION": (True, "M08-WO-INSPECTION"),
    "INSTALLATION": (True, "M08-WO-INSTALLATION"),
}
REQUIRED_EVIDENCE_PROFILES = {
    "CORRECTIVE": "M06-EVIDENCE-CORRECTIVE",
    "INSTALLATION": "M06-EVIDENCE-INSTALLATION",
}


def configure_requirement(work_order, work_type=None):
    """Set reference metadata only; never create M08 records or answers."""
    kind = work_type or work_order.work_type
    required, template_ref = WORK_TYPE_REQUIREMENTS.get(kind, (False, ""))
    if work_order.checklist_template_ref and work_order.checklist_template_ref != template_ref:
        work_order.checklist_instance_ref = ""
        work_order.checklist_completed_at = None
        work_order.checklist_validated_at = None
        work_order.checklist_status = PENDING if required else NOT_REQUIRED
        work_order.checklist_validation_state = PENDING if required else NOT_REQUIRED
    work_order.checklist_required = required
    work_order.checklist_template_ref = template_ref
    if required:
        if work_order.checklist_status in ("", NOT_REQUIRED):
            work_order.checklist_status = PENDING
        if work_order.checklist_validation_state in ("", NOT_REQUIRED):
            work_order.checklist_validation_state = PENDING
    else:
        work_order.checklist_status = NOT_REQUIRED
        work_order.checklist_validation_state = NOT_REQUIRED
        work_order.checklist_instance_ref = ""
        work_order.checklist_completed_at = None
        work_order.checklist_validated_at = None


@dataclass(frozen=True)
class ChecklistResult:
    required: bool
    status: str
    is_valid: bool
    missing_items: tuple = ()
    unresolved_exceptions: tuple = ()
    message: str = ""

    def as_dict(self):
        return {
            "required": self.required,
            "status": self.status,
            "is_valid": self.is_valid,
            "missing_items": list(self.missing_items),
            "unresolved_exceptions": list(self.unresolved_exceptions),
            "message": self.message,
        }


def get_checklist_requirement(work_order):
    return {"required": work_order.checklist_required,
            "requires_checklist": work_order.checklist_required,
            "template_ref": work_order.checklist_template_ref,
            "required_evidence_profile": REQUIRED_EVIDENCE_PROFILES.get(work_order.work_type, "")}


def create_checklist_reference(work_order):
    """Ensure M06 metadata is set; instance refs are assigned only by M08 later."""
    configure_requirement(work_order)
    if work_order.checklist_required and not work_order.checklist_instance_ref:
        work_order.checklist_status = PENDING
        work_order.checklist_validation_state = PENDING
    return get_checklist_status(work_order)


def get_checklist_status(work_order):
    if work_order.checklist_instance_ref:
        if (work_order.checklist_status == COMPLETED
                and work_order.checklist_validation_state == "VALID"):
            return ChecklistResult(True, COMPLETED, True, message="Checklist validated.")
        status = work_order.checklist_status if work_order.checklist_status in {
            PENDING, IN_PROGRESS, FAILED, COMPLETED} else PENDING
        return ChecklistResult(True, status, False,
            message="Checklist completion is required before completing this work order.")

    try:
        from apps.checklists import services as checklist_services
        blockers = checklist_services.checklist_blockers(work_order)
        if not blockers:
            return ChecklistResult(bool(work_order.checklist_required), COMPLETED, True, message="Checklist validated.")
        return ChecklistResult(True, PENDING, False,
                               message=blockers[0] if blockers else "Checklist completion is required before completing this work order.")
    except Exception:
        pass

    if not work_order.checklist_required:
        return ChecklistResult(False, NOT_REQUIRED, True, message="No checklist is required.")
    status = work_order.checklist_status if work_order.checklist_status in {
        PENDING, IN_PROGRESS, FAILED, COMPLETED} else PENDING
    return ChecklistResult(True, status, False,
        message="Checklist completion is required before completing this work order.")


def validate_for_completion(work_order):
    result = get_checklist_status(work_order)
    if not result.is_valid:
        from apps.core.exceptions import Conflict
        raise Conflict(result.message, code="checklist_incomplete")
    return result


def validate_for_closure(work_order):
    return validate_for_completion(work_order)


def get_checklist_summary(work_order):
    result = get_checklist_status(work_order)
    return result.as_dict() | {
        "template_ref": work_order.checklist_template_ref,
        "instance_ref": work_order.checklist_instance_ref,
        "validation_state": work_order.checklist_validation_state,
        "integration_status": "Checklist Engine integration pending.",
        "provisional_only": bool(work_order.provisional_checklist),
        "provisional_message": ("Provisional checklist only — not valid as completion evidence."
                                if work_order.provisional_checklist else ""),
    }


def record_external_validation(work_order, *, status, validation_state, completed_at=None):
    """Narrow callback used by a future M08 provider; does not persist itself."""
    if status not in {PENDING, IN_PROGRESS, COMPLETED, FAILED}:
        raise ValueError("Unsupported checklist status from provider.")
    if validation_state not in {"PENDING", "VALID", "INVALID"}:
        raise ValueError("Unsupported checklist validation state from provider.")
    if status == COMPLETED and (validation_state != "VALID" or not work_order.checklist_instance_ref):
        raise ValueError("A completed checklist must include a valid provider reference.")
    work_order.checklist_status = status
    work_order.checklist_validation_state = validation_state
    work_order.checklist_validated_at = timezone.now()
    work_order.checklist_completed_at = completed_at
