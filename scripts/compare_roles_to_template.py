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
from apps.rbac.role_templates import TEMPLATES, BY_KEY
from apps.rbac.services import _template_codes

org = Organization.objects.filter(slug="apex-industrial-technologies").first()
print(f"Organization: {org.name} ({org.slug})\n")

tpl_by_key = BY_KEY

roles = Role.objects.filter(organization=org).prefetch_related("role_permissions__permission")
for r in roles:
    current = set(rp.permission.code for rp in r.role_permissions.all())
    tpl = tpl_by_key.get(r.system_key)
    if not tpl:
        print(f"Role: {r.name} (Key: {r.system_key}) -> No template match (custom or legacy)")
        continue
    expected = _template_codes(tpl)
    extra = current - expected
    missing = expected - current
    print(f"Role: {r.name:<30} Key: {r.system_key:<20} Current: {len(current):<3} Expected: {len(expected):<3} Extra: {len(extra):<3} Missing: {len(missing):<3}")
    if extra:
        print(f"   -> Extra permissions count: {len(extra)}")
        for x in sorted(extra):
            print(f"      + {x}")
    if missing:
        print(f"   -> Missing: {sorted(missing)}")
