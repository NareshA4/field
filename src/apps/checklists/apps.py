from django.apps import AppConfig


class ChecklistsConfig(AppConfig):
    name = "apps.checklists"
    label = "checklists"

    def ready(self):
        from apps.files import access

        def can_read_evidence(membership, target) -> bool:
            # evidence on an answer / finding follows the inspection's visibility (site scope or assigned work)
            from . import selectors

            return selectors.can_see_inspection(membership, target.inspection)

        access.register("checklists.inspectionresponse", can_read_evidence)
        access.register("checklists.finding", can_read_evidence)
