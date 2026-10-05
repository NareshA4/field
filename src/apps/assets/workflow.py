"""Asset status lifecycle (HPE: ACTIVE -> UNDER MAINTENANCE -> OUT OF SERVICE -> ACTIVE / RETIRED / DISPOSED).

Allowed transitions (anything else is rejected with HTTP 409 ``invalid_transition``):

    ACTIVE            --start_maintenance-->    UNDER_MAINTENANCE
    UNDER_MAINTENANCE --complete_maintenance--> ACTIVE
    UNDER_MAINTENANCE --mark_out_of_service-->  OUT_OF_SERVICE
    OUT_OF_SERVICE    --return_to_service-->    ACTIVE
    OUT_OF_SERVICE    --retire-->               RETIRED     (terminal)
    OUT_OF_SERVICE    --dispose-->              DISPOSED    (terminal)

``complete_maintenance`` (the maintenance outcome that keeps the asset) is the natural reading of the HPE
chain; direct ACTIVE -> OUT_OF_SERVICE is intentionally NOT allowed (see docs/DECISIONS.md D-027).
"""
from apps.core.workflow import StateMachine, Transition

ACTIVE = "ACTIVE"
UNDER_MAINTENANCE = "UNDER_MAINTENANCE"
OUT_OF_SERVICE = "OUT_OF_SERVICE"
RETIRED = "RETIRED"
DISPOSED = "DISPOSED"

STATES = (ACTIVE, UNDER_MAINTENANCE, OUT_OF_SERVICE, RETIRED, DISPOSED)
TERMINAL_STATES = (RETIRED, DISPOSED)

ASSET_STATUS = StateMachine(
    "asset_status",
    STATES,
    [
        Transition("start_maintenance", (ACTIVE,), UNDER_MAINTENANCE, label="Start maintenance"),
        Transition("complete_maintenance", (UNDER_MAINTENANCE,), ACTIVE, label="Complete maintenance"),
        Transition("mark_out_of_service", (UNDER_MAINTENANCE,), OUT_OF_SERVICE, label="Mark out of service"),
        Transition("return_to_service", (OUT_OF_SERVICE,), ACTIVE, label="Return to service"),
        Transition("retire", (OUT_OF_SERVICE,), RETIRED, label="Retire"),
        Transition("dispose", (OUT_OF_SERVICE,), DISPOSED, label="Dispose"),
    ],
)

# UI hint only (the server never trusts it): button style per action.
BUTTON_STYLES = {"retire": "danger", "dispose": "danger", "mark_out_of_service": "warning"}
