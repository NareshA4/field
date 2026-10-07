"""M12 QR & Barcode identification permissions."""
from apps.rbac.catalog import register

register("qr", "qr.view", "View asset labels and QR codes", site_scoped=True)
register("qr", "qr.generate", "Generate, replace and revoke asset labels and QR codes", site_scoped=True)
register("qr", "qr.scan", "Scan asset labels and resolve assets", site_scoped=True)
