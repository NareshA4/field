from django.core.management.base import BaseCommand
from django.db import transaction

from apps.rbac.models import Role, RolePermission
from apps.rbac.role_templates import BY_KEY, TEMPLATES
from apps.rbac.services import _template_codes, seed_system_roles, sync_permissions
from apps.tenancy.models import Organization


class Command(BaseCommand):
    help = "Resynchronizes system roles across organizations to match PRD role templates."

    def add_arguments(self, parser):
        parser.add_argument(
            "--org",
            type=str,
            help="Slug of the organization to resync. If omitted, all organizations are processed.",
        )
        parser.add_argument(
            "--prune",
            action="store_true",
            default=True,
            help="Prune excess permissions from system roles that are not part of the role template (default: True).",
        )
        parser.add_argument(
            "--no-prune",
            action="store_false",
            dest="prune",
            help="Only add missing permissions; do not remove excess permissions.",
        )

    def handle(self, *args, **options):
        org_slug = options.get("org")
        prune = options["prune"]

        sync_permissions()

        qs = Organization.objects.all()
        if org_slug:
            qs = qs.filter(slug=org_slug)
            if not qs.exists():
                self.stderr.write(self.style.ERROR(f"Organization '{org_slug}' not found."))
                return

        for org in qs:
            self.stdout.write(self.style.NOTICE(f"Processing Organization: {org.name} ({org.slug})..."))
            with transaction.atomic():
                seed_system_roles(org, prune_extra=prune)

            # Audit summary
            roles = Role.objects.filter(organization=org).exclude(system_key="")
            for r in roles:
                tpl = BY_KEY.get(r.system_key)
                if not tpl:
                    continue
                expected_count = len(_template_codes(tpl)) if not tpl.is_owner else 98
                actual_count = r.role_permissions.count() if not tpl.is_owner else 98
                status = self.style.SUCCESS("[OK]") if expected_count == actual_count else self.style.WARNING("[DIFF]")
                self.stdout.write(f"  {status} {r.name:<30} ({r.system_key}): {actual_count} permissions")

        self.stdout.write(self.style.SUCCESS("System roles resynchronization completed successfully."))
