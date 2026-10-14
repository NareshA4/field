from apps.core.exceptions import NotFound
from apps.files.models import Attachment
from apps.rbac import services as rbac

from .models import WorkOrder


def get_work_order(membership, organization, pk):
    wo = WorkOrder.objects.for_organization(organization).select_related(
        "site", "assigned_to__user", "created_by").filter(pk=pk).first()
    if wo is None:
        raise NotFound("Work order not found.")
    if not (rbac.has_permission(membership, "work_order.view", wo.site) or
            (wo.assigned_to_id == membership.pk and
             rbac.has_permission(membership, "work_order.view_assigned", wo.site))):
        raise NotFound("Work order not found.")
    return wo


def work_order_list(membership, organization, params=None):
    qs = WorkOrder.objects.for_organization(organization).select_related("site", "assigned_to__user")
    if not rbac.has_permission_anywhere(membership, "work_order.view"):
        qs = qs.filter(assigned_to=membership)
    site_ids = [sid for sid in qs.values_list("site_id", flat=True).distinct()
                if rbac.has_permission(membership, "work_order.view", sid) or
                rbac.has_permission(membership, "work_order.view_assigned", sid)]
    qs = qs.filter(site_id__in=site_ids)
    params = params or {}
    if params.get("status"):
        qs = qs.filter(status=params["status"])
    if params.get("q"):
        from django.db.models import Q
        qs = qs.filter(Q(order_number__icontains=params["q"]) | Q(title__icontains=params["q"]))
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

