path = "src/apps/ui/views.py"
with open(path, "r", encoding="utf-8") as f:
    text = f.read()

target = """    stats = {
        "active_members": Membership.objects.for_organization(org).filter(status=Membership.Status.ACTIVE).count(),
        "pending_invites": Membership.objects.for_organization(org).filter(status=Membership.Status.INVITED).count(),
        "roles": Role.objects.for_organization(org).count(),
    }
    recent = []
    if rbac.has_permission(request.membership, "audit.view"):
        recent = list(AuditLog.objects.for_organization(org)[:8])
    return render(request, "ui/home.html", {"stats": stats, "recent": recent})"""

replacement = """    can_view_users = rbac.has_permission(request.membership, "user.view")
    can_view_roles = rbac.has_permission(request.membership, "role.view")
    can_view_assets = rbac.has_permission(request.membership, "asset.view")
    can_view_sites = rbac.has_permission(request.membership, "site.view")
    can_view_work_orders = (
        rbac.has_permission(request.membership, "work_order.view")
        or rbac.has_permission(request.membership, "work_order.view_assigned")
    )

    stats = {
        "active_members": Membership.objects.for_organization(org).filter(status=Membership.Status.ACTIVE).count() if can_view_users else 0,
        "pending_invites": Membership.objects.for_organization(org).filter(status=Membership.Status.INVITED).count() if can_view_users else 0,
        "roles": Role.objects.for_organization(org).count() if can_view_roles else 0,
        "total_assets": 0,
        "total_sites": 0,
        "open_work_orders": 0,
    }
    if can_view_assets:
        from apps.assets.models import Asset
        stats["total_assets"] = Asset.objects.for_organization(org).exclude(status__in=["RETIRED", "DISPOSED"]).count()
    if can_view_sites:
        from apps.sites.models import Site
        stats["total_sites"] = Site.objects.for_organization(org).filter(is_active=True).count()
    if can_view_work_orders:
        from apps.workorders.models import WorkOrder
        stats["open_work_orders"] = WorkOrder.objects.for_organization(org).exclude(status__in=[WorkOrder.Status.COMPLETED, WorkOrder.Status.CANCELLED]).count()

    recent = []
    if rbac.has_permission(request.membership, "audit.view"):
        recent = list(AuditLog.objects.for_organization(org)[:8])
    return render(request, "ui/home.html", {
        "stats": stats,
        "recent": recent,
        "can_view_users": can_view_users,
        "can_view_assets": can_view_assets,
    })"""

assert target in text, "Target not found in views.py"
text = text.replace(target, replacement, 1)

with open(path, "w", encoding="utf-8") as f:
    f.write(text)

print("Updated ui/views.py home dashboard stats")
