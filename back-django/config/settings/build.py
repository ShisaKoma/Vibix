"""Static collection only. Never use as runtime settings."""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "build-only-not-a-runtime-secret")
for name in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"):
    os.environ.setdefault(name, "unused-for-static-build")

from .base import *  # noqa: E402,F403
