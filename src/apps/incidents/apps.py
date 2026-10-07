from django.apps import AppConfig


class IncidentsConfig(AppConfig):
    name = "apps.incidents"
    label = "incidents"

    def ready(self):
        from apps.files import access

        def can_read_request_file(membership, request_obj) -> bool:
            # evidence follows the request's site scope, like asset documents
            from . import selectors

            return selectors.requests_for(membership, membership.organization, "incident.view_assigned").filter(
                pk=request_obj.pk).exists()

        access.register("incidents.servicerequest", can_read_request_file)
