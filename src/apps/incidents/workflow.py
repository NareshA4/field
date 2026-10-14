"""M05 service request / incident lifecycle (HPE: NEW -> TRIAGED -> APPROVED/REJECTED -> WORK ORDER CREATED ->
IN SERVICE -> RESOLVED -> CONFIRMED -> CLOSED).

    NEW                --triage-->               TRIAGED
    TRIAGED            --approve-->              APPROVED
    TRIAGED            --reject-->               REJECTED            (terminal; reason required)
    APPROVED           --link_work_order-->      WORK_ORDER_CREATED  (only by M05 creating an M06 work order)
    WORK_ORDER_CREATED --work_order_cancelled--> APPROVED            (M06 cancelled the work order)
    WORK_ORDER_CREATED --start_service-->        IN_SERVICE          (M06 work started)
    IN_SERVICE         --resolve-->              RESOLVED            (M06 work completed)
    RESOLVED           --resume_service-->       IN_SERVICE          (M06 returned the work for rework)
    RESOLVED           --confirm-->              CONFIRMED
    RESOLVED           --reopen-->               APPROVED            (not fixed: a new work order is needed; reason)
    CONFIRMED          --close-->                CLOSED              (terminal)

``link_work_order``, ``work_order_cancelled``, ``start_service``, ``resolve`` and ``resume_service`` are SYSTEM actions: they are
driven by the work-order lifecycle (M06) and are not offered as buttons / not accepted by the transition API.
``reopen`` and the APPROVED return path are OUR IMPLEMENTATION DECISIONS (docs/DECISIONS.md D-035).
"""
from apps.core.workflow import StateMachine, Transition

NEW = "NEW"
TRIAGED = "TRIAGED"
APPROVED = "APPROVED"
REJECTED = "REJECTED"
WORK_ORDER_CREATED = "WORK_ORDER_CREATED"
IN_SERVICE = "IN_SERVICE"
RESOLVED = "RESOLVED"
CONFIRMED = "CONFIRMED"
CLOSED = "CLOSED"
IN_PROGRESS = "IN_PROGRESS"
ON_HOLD = "ON_HOLD"
CANCELLED = "CANCELLED"

STATES = (NEW, TRIAGED, APPROVED, REJECTED, WORK_ORDER_CREATED, IN_SERVICE, RESOLVED, CONFIRMED, CLOSED, IN_PROGRESS, ON_HOLD)
TERMINAL_STATES = (REJECTED, CLOSED)
OPEN_STATES = (NEW, TRIAGED, APPROVED, WORK_ORDER_CREATED, IN_SERVICE, RESOLVED, CONFIRMED)

# Duplicate Incident Detection status boundaries
ACTIVE_INCIDENT_STATUSES = (
    NEW,
    TRIAGED,
    APPROVED,
    WORK_ORDER_CREATED,
    IN_SERVICE,
    IN_PROGRESS,
    ON_HOLD,
)
NON_BLOCKING_STATUSES = (
    CLOSED,
    REJECTED,
    CANCELLED,
    CONFIRMED,
)

SYSTEM_ACTIONS = ("link_work_order", "work_order_cancelled", "start_service", "resolve", "resume_service")

REQUEST_STATUS = StateMachine(
    "service_request",
    STATES,
    [
        Transition("triage", (NEW,), TRIAGED, label="Triage Request"),
        Transition("approve", (TRIAGED,), APPROVED, label="Approve Request"),
        Transition("reject", (TRIAGED,), REJECTED, label="Reject Request"),
        Transition("link_work_order", (APPROVED,), WORK_ORDER_CREATED, label="Convert to Work Order"),
        Transition("work_order_cancelled", (WORK_ORDER_CREATED,), APPROVED, label="Work order cancelled"),
        Transition("start_service", (WORK_ORDER_CREATED,), IN_SERVICE, label="Start service"),
        Transition("resolve", (IN_SERVICE,), RESOLVED, label="Resolve"),
        Transition("resume_service", (RESOLVED,), IN_SERVICE, label="Back in service (rework)"),
        Transition("confirm", (RESOLVED,), CONFIRMED, label="Confirm resolved"),
        Transition("reopen", (RESOLVED,), APPROVED, label="Reopen (not fixed)"),
        Transition("close", (CONFIRMED,), CLOSED, label="Close"),
    ],
)

# permission needed for each user-facing action (site-scoped)
ACTION_PERMISSIONS = {
    "triage": "incident.triage", "approve": "incident.approve", "reject": "incident.approve",
    "confirm": "incident.confirm", "reopen": "incident.confirm", "close": "incident.close",
}
REASON_REQUIRED = ("reject", "reopen")
BUTTON_STYLES = {"triage": "info", "approve": "success", "reject": "danger", "reopen": "warning", "close": "secondary"}
