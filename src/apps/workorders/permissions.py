"""M06 permissions (names match the role templates). All are site-scoped through the work order's site."""
from apps.rbac.catalog import register

register("work_order", "work_order.view", "View all work orders in scope", site_scoped=True,
         implies=("work_order.view_assigned",))
register("work_order", "work_order.view_assigned", "View work orders assigned to oneself", site_scoped=True)
register("work_order", "work_order.create", "Create work orders", site_scoped=True)
register("work_order", "work_order.update", "Edit draft / planned work orders", site_scoped=True)
register("work_order", "work_order.plan", "Plan and prioritise work orders", site_scoped=True)
register("work_order", "work_order.assign", "Assign / re-assign technicians", site_scoped=True)
register("work_order", "work_order.dispatch", "Dispatch work orders (and act for the assignee)", site_scoped=True)
register("work_order", "work_order.start", "Start assigned work", site_scoped=True)
register("work_order", "work_order.hold", "Put work on hold / resume", site_scoped=True)
register("work_order", "work_order.complete", "Complete work", site_scoped=True)
register("work_order", "work_order.record", "Record labor, time and material on a work order", site_scoped=True)
register("work_order", "work_order.attach", "Attach evidence to a work order", site_scoped=True)
register("work_order", "work_order.review", "Review completed work", site_scoped=True)
register("work_order", "work_order.close", "Close reviewed work orders", site_scoped=True)
register("work_order", "work_order.cancel", "Cancel work orders that have not started", site_scoped=True)
