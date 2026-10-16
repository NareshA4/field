"""M08 lifecycles (OUR IMPLEMENTATION DECISIONS, D-039; HPE gives no inspection / template states).

Inspection:  IN_PROGRESS --complete--> COMPLETED   (terminal: a completed inspection is authoritative, immutable)
Template:    DRAFT --activate--> ACTIVE --deactivate--> INACTIVE
An ACTIVE or INACTIVE template is frozen; changes are made on a new DRAFT version (``new_version``).
"""
from apps.core.workflow import StateMachine, Transition

IN_PROGRESS = "IN_PROGRESS"
COMPLETED = "COMPLETED"
INSPECTION_STATES = (IN_PROGRESS, COMPLETED)
INSPECTION_STATUS = StateMachine("inspection", INSPECTION_STATES,
                                 [Transition("complete", (IN_PROGRESS,), COMPLETED, label="Complete inspection")])

DRAFT = "DRAFT"
ACTIVE = "ACTIVE"
INACTIVE = "INACTIVE"
TEMPLATE_STATES = (DRAFT, ACTIVE, INACTIVE)
TEMPLATE_STATUS = StateMachine("checklist_template", TEMPLATE_STATES, [
    Transition("activate", (DRAFT,), ACTIVE, label="Activate"),
    Transition("deactivate", (ACTIVE,), INACTIVE, label="Deactivate"),
])
