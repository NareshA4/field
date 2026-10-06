from django.apps import AppConfig


class IncidentsConfig(AppConfig):
    name = "apps.incidents"
    label = "incidents"

    def ready(self):
        from apps.files import access
<<<<<<< HEAD

        def can_read_request_file(membership, request_obj) -> bool:
            # evidence follows the request's site scope, like asset documents
            from . import selectors

            return selectors.requests_for(membership, membership.organization, "incident.view_assigned").filter(
                pk=request_obj.pk).exists()
=======
        from apps.rbac import services as rbac

        def can_read_request_file(membership, request_obj) -> bool:
            # evidence follows the request's site scope, like asset documents
            return rbac.has_permission(membership, "incident.view", request_obj.site_id)
>>>>>>> ba3f639 (feat(m08): implement Inspection & Checklist Engine with Work Order integration)

        access.register("incidents.servicerequest", can_read_request_file)
