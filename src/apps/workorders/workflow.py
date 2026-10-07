"""M06 work-order lifecycle (HPE: DRAFT -> PLANNED -> ASSIGNED -> DISPATCHED -> IN PROGRESS -> ON HOLD (back to IN
PROGRESS) -> COMPLETED -> SUPERVISOR REVIEW -> CLOSED; never DRAFT -> CLOSED).

    DRAFT             --plan-->          PLANNED
    PLANNED           --assign-->        ASSIGNED
    ASSIGNED          --dispatch-->      DISPATCHED
    DISPATCHED        --start-->         IN_PROGRESS
    IN_PROGRESS       --hold-->          ON_HOLD            (reason)
    ON_HOLD           --resume-->        IN_PROGRESS
    IN_PROGRESS       --complete-->      COMPLETED          (resolution notes + evidence rule)
    COMPLETED         --start_review-->  SUPERVISOR_REVIEW
    SUPERVISOR_REVIEW --close-->         CLOSED             (terminal)
    SUPERVISOR_REVIEW --reject_review--> IN_PROGRESS        (rework; reason)
    DRAFT/PLANNED/ASSIGNED/DISPATCHED --cancel--> CANCELLED (terminal; reason)

``reject_review`` and ``cancel`` are OUR IMPLEMENTATION DECISIONS (HPE lists neither); see D-036.
Re-assignment (``reassign``) keeps the state and is a service operation on ASSIGNED / DISPATCHED orders.
"""
from apps.core.workflow import StateMachine, Transition

DRAFT = "DRAFT"
PLANNED = "PLANNED"
ASSIGNED = "ASSIGNED"
DISPATCHED = "DISPATCHED"
IN_PROGRESS = "IN_PROGRESS"
ON_HOLD = "ON_HOLD"
COMPLETED = "COMPLETED"
SUPERVISOR_REVIEW = "SUPERVISOR_REVIEW"
CLOSED = "CLOSED"
CANCELLED = "CANCELLED"

STATES = (DRAFT, PLANNED, ASSIGNED, DISPATCHED, IN_PROGRESS, ON_HOLD, COMPLETED, SUPERVISOR_REVIEW, CLOSED,
          CANCELLED)
TERMINAL_STATES = (CLOSED, CANCELLED)
ACTIVE_ASSIGNMENT_STATES = (ASSIGNED, DISPATCHED, IN_PROGRESS, ON_HOLD)  # a technician is committed
EXECUTION_STATES = (IN_PROGRESS, ON_HOLD)

WORK_ORDER_STATUS = StateMachine(
    "work_order",
    STATES,
    [
        Transition("plan", (DRAFT,), PLANNED, label="Plan"),
        Transition("assign", (PLANNED,), ASSIGNED, label="Assign"),
        Transition("dispatch", (ASSIGNED,), DISPATCHED, label="Dispatch"),
        Transition("start", (DISPATCHED,), IN_PROGRESS, label="Start work"),
        Transition("hold", (IN_PROGRESS,), ON_HOLD, label="Put on hold"),
        Transition("resume", (ON_HOLD,), IN_PROGRESS, label="Resume"),
        Transition("complete", (IN_PROGRESS,), COMPLETED, label="Complete"),
        Transition("start_review", (COMPLETED,), SUPERVISOR_REVIEW, label="Start supervisor review"),
        Transition("close", (SUPERVISOR_REVIEW,), CLOSED, label="Close"),
        Transition("reject_review", (SUPERVISOR_REVIEW,), IN_PROGRESS, label="Return for rework"),
        Transition("cancel", (DRAFT, PLANNED, ASSIGNED, DISPATCHED), CANCELLED, label="Cancel"),
    ],
)

# permission per user-facing action (site-scoped); the assignee-only rule for execution is in services.
ACTION_PERMISSIONS = {
    "plan": "work_order.plan", "assign": "work_order.assign", "dispatch": "work_order.dispatch",
    "start": "work_order.start", "hold": "work_order.hold", "resume": "work_order.hold",
    "complete": "work_order.complete", "start_review": "work_order.review", "close": "work_order.close",
    "reject_review": "work_order.review", "cancel": "work_order.cancel",
}
REASON_REQUIRED = ("hold", "reject_review", "cancel")
EXECUTION_ACTIONS = ("start", "hold", "resume", "complete")
BUTTON_STYLES = {"cancel": "danger", "hold": "warning", "reject_review": "warning", "close": "success"}
