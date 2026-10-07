from apps.rbac.catalog import register

for action, description in (
    ("view", "View work orders"), ("view_assigned", "View assigned work orders"),
    ("create", "Create work orders"), ("update", "Edit work order details and provisional notes before dispatch"),
    ("plan", "Plan work order schedules"), ("assign", "Assign technicians"),
    ("dispatch", "Dispatch work orders"), ("start", "Start and resume assigned work"),
    ("hold", "Place assigned work on hold"), ("complete", "Submit assigned work for review"),
    ("review", "Review completed work orders"), ("close", "Approve and close work orders"),
    ("cancel", "Cancel work before execution"), ("record", "Record labor and material usage"),
    ("attach", "Link evidence to work orders"),
):
    register("work_order", f"work_order.{action}", description, site_scoped=True,
             implies=("work_order.view_assigned",) if action == "view" else ())
