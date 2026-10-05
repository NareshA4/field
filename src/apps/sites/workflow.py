"""Activation lifecycle shared by sites and zones (HPE: site status ACTIVE/INACTIVE)."""
from apps.core.workflow import StateMachine, Transition

ACTIVE = "ACTIVE"
INACTIVE = "INACTIVE"

ACTIVATION = StateMachine(
    "site_activation",
    (ACTIVE, INACTIVE),
    [
        Transition("deactivate", (ACTIVE,), INACTIVE, label="Deactivate"),
        Transition("reactivate", (INACTIVE,), ACTIVE, label="Reactivate"),
    ],
)
