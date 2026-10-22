from apps.core.exceptions import NotFound
from apps.files.models import Attachment
from apps.rbac import services as rbac

from .models import WorkOrder


def get_work_order(membership, organization, pk):
    wo = WorkOrder.objects.for_organization(organization).select_related(
        "site", "assigned_to__user", "created_by").filter(pk=pk).first()
    if wo is None:
        raise NotFound("Work order not found.")
    can_view = (
        rbac.has_permission(membership, "work_order.view", wo.site) or
        (rbac.has_permission(membership, "work_order.view_assigned", wo.site) and (
            wo.assigned_to_id == membership.pk or
            wo.assigned_to_id is None or
            (wo.source_request_id and rbac.has_permission(membership, "incident.view", wo.site))
        ))
    )
    if not can_view:
        raise NotFound("Work order not found.")
    return wo


def work_orders_for(membership, organization):
    return work_order_list(membership, organization)


def work_order_list(membership, organization, params=None):
    qs = WorkOrder.objects.for_organization(organization).select_related(
        "site", "assigned_to__user", "asset", "source_request"
    )
    if not rbac.has_permission_anywhere(membership, "work_order.view"):
        from django.db.models import Q
        qs = qs.filter(Q(assigned_to=membership) | Q(assigned_to__isnull=True))
    scope = rbac.site_scope(membership, "work_order.view")
    if not scope.all_sites:
        scope_assigned = rbac.site_scope(membership, "work_order.view_assigned")
        if not scope_assigned.all_sites:
            allowed = set(scope.site_ids) | set(scope_assigned.site_ids)
            qs = qs.filter(site_id__in=allowed)
    params = params or {}
    if params.get("status"):
        qs = qs.filter(status=params["status"])
    if params.get("q"):
        from django.db.models import Q
        qs = qs.filter(Q(order_number__icontains=params["q"]) | Q(title__icontains=params["q"]))
    return qs


def filter_work_orders(queryset, params, membership):
    from django.db.models import Q
    params = params or {}
    qs = queryset
    if params.get("status"):
        qs = qs.filter(status=params["status"])
    if params.get("work_type"):
        qs = qs.filter(work_type=params["work_type"])
    if params.get("priority"):
        qs = qs.filter(priority=params["priority"])
    if params.get("site"):
        qs = qs.filter(site_id=params["site"])
    if params.get("asset"):
        qs = qs.filter(asset_id=params["asset"])
    if params.get("assigned_to"):
        qs = qs.filter(assigned_to_id=params["assigned_to"])
    if params.get("mine") in ("1", "true", "True", True):
        qs = qs.filter(assigned_to=membership)
    if params.get("q"):
        query = str(params["q"]).strip()
        qs = qs.filter(Q(order_number__icontains=query) | Q(title__icontains=query) | Q(description__icontains=query))
    ordering = params.get("ordering")
    if ordering:
        allowed_ordering = {
            "created_at", "-created_at", "priority", "-priority",
            "order_number", "-order_number", "planned_start", "-planned_start",
            "due_date", "-due_date", "updated_at", "-updated_at"
        }
        if ordering in allowed_ordering:
            qs = qs.order_by(ordering)
    return qs


work_orders_for = work_order_list


def evidence_for(wo):
    return Attachment.objects.for_organization(wo.organization).filter(work_order_links__work_order=wo)


def total_hours(organization, wo):
    from django.db.models import Sum
    from .models import WorkOrderLabor
    res = WorkOrderLabor.objects.filter(organization=organization, work_order=wo).aggregate(total=Sum("hours"))["total"]
    return res or 0


def labor_for(organization, wo):
    from .models import WorkOrderLabor
    return WorkOrderLabor.objects.filter(organization=organization, work_order=wo).select_related("technician__user")


def materials_for(organization, wo):
    from .models import WorkOrderMaterial
    return WorkOrderMaterial.objects.filter(organization=organization, work_order=wo)

