"""Local readiness probe; keep the configured Host and bypass proxy redirects."""

import os
from urllib.request import Request, urlopen

host = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost").split(",")[0].strip()
headers = {"Host": host}
if os.environ.get("DJANGO_TRUST_PROXY", "false").lower() == "true":
    headers["X-Forwarded-Proto"] = "https"
request = Request("http://127.0.0.1:8000/health/ready/", headers=headers)
with urlopen(request, timeout=3) as response:
    if response.status != 200:
        raise SystemExit(1)
