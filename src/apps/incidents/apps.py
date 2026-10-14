from django.apps import AppConfig


class IncidentsConfig(AppConfig):
    name = "apps.incidents"
    label = "incidents"

    def ready(self):
        from apps.files import access
        from apps.rbac import services as rbac

        def can_read_request_file(membership, request_obj, attachment=None) -> bool:
            # Evidence follows the request's site scope for internal staff
            if rbac.has_permission(membership, "incident.view", request_obj.site_id):
                return True
            # Portal clients may download files they uploaded to their own requests (D-051)
            if rbac.has_permission(membership, "portal.request.view"):
                if request_obj.reported_by_id == membership.pk:
                    if attachment is None or attachment.uploaded_by_id == membership.user_id:
                        return True
            elif request_obj.reported_by_id == membership.pk:
                return True
            try:
                from apps.workorders.models import WorkOrder

                wo = WorkOrder.objects.for_organization(request_obj.organization).filter(
                    source_request=request_obj,
                ).first()
                if wo is None:
                    return False
                return (rbac.has_permission(membership, "work_order.view", wo.site_id) or
                        (wo.assigned_to_id == membership.pk and
                         rbac.has_permission(membership, "work_order.view_assigned", wo.site_id)))
            except (ImportError, LookupError):
                return False

        access.register("incidents.servicerequest", can_read_request_file)
