from django.apps import AppConfig


class IncidentsConfig(AppConfig):
    name = "apps.incidents"
    label = "incidents"

    def ready(self):
        from apps.files import access
        from apps.rbac import services as rbac

        def can_read_request_file(membership, request_obj) -> bool:
            # evidence follows the request's site scope, like asset documents
            return rbac.has_permission(membership, "incident.view", request_obj.site_id)

        access.register("incidents.servicerequest", can_read_request_file)
