"""M09 part-request lifecycle (HPE CONFIRMED chain: REQUESTED -> RESERVED -> ISSUED -> CONSUMED / RETURNED ->
RECONCILED; CANCELLED added by us for requirements that never left the shelf, D-042).

The working states (REQUESTED..RETURNED) are derived from the persisted quantities by
``inventory.services._refresh_status`` (one place), because partial reserve / issue / consume / return make the
target of an action depend on the quantities. The two terminal actions are real state-machine transitions."""
from apps.core.workflow import StateMachine, Transition

REQUESTED, RESERVED, ISSUED = "REQUESTED", "RESERVED", "ISSUED"
CONSUMED, RETURNED, RECONCILED, CANCELLED = "CONSUMED", "RETURNED", "RECONCILED", "CANCELLED"
STATES = (REQUESTED, RESERVED, ISSUED, CONSUMED, RETURNED, RECONCILED, CANCELLED)
TERMINAL = (RECONCILED, CANCELLED)

PART_LINE = StateMachine("part_line", STATES, [
    Transition("reconcile", (CONSUMED, RETURNED), RECONCILED, label="Reconcile"),
    Transition("cancel", (REQUESTED, RESERVED, RETURNED), CANCELLED, label="Cancel"),
])

# Work-order states in which each kind of operation is meaningful (M06 owns the lifecycle; M09 only reads it).
REQUEST_STATES = ("DRAFT", "PLANNED", "ASSIGNED", "DISPATCHED", "IN_PROGRESS", "ON_HOLD")
STOCK_OUT_STATES = ("PLANNED", "ASSIGNED", "DISPATCHED", "IN_PROGRESS", "ON_HOLD")  # reserve / issue
CONSUME_STATES = ("IN_PROGRESS", "ON_HOLD", "COMPLETED", "SUPERVISOR_REVIEW")
RETURN_STATES = ("PLANNED", "ASSIGNED", "DISPATCHED", "IN_PROGRESS", "ON_HOLD", "COMPLETED", "SUPERVISOR_REVIEW")
