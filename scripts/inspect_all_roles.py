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

from apps.tenancy.models import Organization
from apps.rbac.models import Role

org = Organization.objects.filter(slug="apex-industrial-technologies").first()
print(f"Organization: {org.name} ({org.slug})")

roles = Role.objects.filter(organization=org).prefetch_related("role_permissions__permission")
for r in roles:
    perms = [rp.permission.code for rp in r.role_permissions.all()]
    print(f"Role: {r.name:<30} Key: {r.system_key:<20} is_owner={r.is_owner:<5} Perm Count: {len(perms)}")
