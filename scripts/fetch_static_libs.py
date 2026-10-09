#!/usr/bin/env python
"""Fetches the pinned third-party front-end libraries into src/static/lib (committed to Git for offline builds).
Source-map comments are stripped so WhiteNoise's manifest does not look for missing .map files."""
import re
import urllib.request
from pathlib import Path

LIBS = {
    "bootstrap.min.css": "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css",
    "bootstrap.bundle.min.js": "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js",
    "htmx.min.js": "https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/dist/htmx.min.js",
    "zxing.min.js": "https://cdn.jsdelivr.net/npm/@zxing/library@0.21.3/umd/index.min.js",  # M12 camera scanner
}
out = Path(__file__).resolve().parent.parent / "src" / "static" / "lib"
out.mkdir(parents=True, exist_ok=True)
for name, url in LIBS.items():
    text = urllib.request.urlopen(url, timeout=60).read().decode("utf-8")
    text = re.sub(r"/\*#\s*sourceMappingURL=[^*]*\*/|//#\s*sourceMappingURL=\S+", "", text)
    (out / name).write_text(text, encoding="utf-8")
    print(f"{name}: {len(text):,} bytes")
(out / "VERSIONS.txt").write_text("bootstrap 5.3.3 (MIT), htmx 2.0.4 (BSD-2), @zxing/library 0.21.3 (Apache-2.0). Re-create with scripts/fetch_static_libs.py\n")
