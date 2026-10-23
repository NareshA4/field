import os, sys

with open(".env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

sys.path.insert(0, "src")
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings.prod"

import django
django.setup()

from django.db import transaction
from apps.tenancy.models import Organization
from apps.rbac.models import Role, RolePermission
from apps.rbac.role_templates import BY_KEY
from apps.rbac.services import _template_codes

org = Organization.objects.filter(slug="apex-industrial-technologies").first()
print(f"Target Organization: {org.name} ({org.slug})\n")

with transaction.atomic():
    roles = Role.objects.filter(organization=org).exclude(system_key="")
    for r in roles:
        tpl = BY_KEY.get(r.system_key)
        if not tpl:
            print(f"[SKIP] No template for system_key={r.system_key}")
            continue
        if tpl.is_owner:
            print(f"[KEEP] Owner role {r.name} has full wildcard access.")
            continue
        
        expected = _template_codes(tpl)
        current = set(r.role_permissions.values_list("permission__code", flat=True))
        extra = current - expected
        if extra:
            deleted_count, _ = RolePermission.objects.filter(role=r, permission__code__in=extra).delete()
            print(f"[PRUNED] Role: {r.name:<25} Key: {r.system_key:<20} Removed {deleted_count} extra permissions.")
        else:
            print(f"[OK]     Role: {r.name:<25} Key: {r.system_key:<20} Exactly matches template ({len(current)} perms).")

print("\nPruning complete.")
