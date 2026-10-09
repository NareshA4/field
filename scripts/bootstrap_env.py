#!/usr/bin/env python
"""Creates .env from .env.example with freshly generated random secrets. Never overwrites an existing .env."""
import secrets
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
target, example = root / ".env", root / ".env.example"
if target.exists():
    sys.exit(".env already exists; refusing to overwrite.")

db_password = secrets.token_urlsafe(24)
text = example.read_text(encoding="utf-8")
text = text.replace("DJANGO_SECRET_KEY=change-me-use-scripts-bootstrap_env", f"DJANGO_SECRET_KEY={secrets.token_urlsafe(60)}")
text = text.replace("POSTGRES_PASSWORD=change-me", f"POSTGRES_PASSWORD={db_password}")
text = text.replace("postgres://fieldops:change-me@", f"postgres://fieldops:{db_password}@")
target.write_text(text, encoding="utf-8")
print(f"Wrote {target} with generated secrets (git-ignored).")
