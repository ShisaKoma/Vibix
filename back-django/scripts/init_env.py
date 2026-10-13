"""Generate local secrets once, without printing or overwriting them."""

import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parent.parent
target = root / ".env"
try:
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    print(".env existe déjà ; aucune modification.")
else:
    content = (root / ".env.example").read_text()
    content = content.replace(
        "DJANGO_SECRET_KEY=", f"DJANGO_SECRET_KEY={secrets.token_urlsafe(64)}"
    )
    content = content.replace(
        "POSTGRES_PASSWORD=", f"POSTGRES_PASSWORD={secrets.token_urlsafe(32)}"
    )
    with os.fdopen(fd, "w") as stream:
        stream.write(content)
    print(".env créé avec des secrets locaux et des permissions 0600.")
