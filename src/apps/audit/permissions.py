"""M15 permissions. ``audit.view`` is registered with the foundation permissions (rbac); exporting evidence is its
own right so read-only reviewers can be kept from taking copies."""
from apps.rbac.catalog import register

register("audit", "audit.export", "Export audit evidence (CSV / XLSX / PDF) within your site scope",
         site_scoped=True)
