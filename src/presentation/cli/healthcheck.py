import os
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen


def main() -> None:
    role = sys.argv[1]
    if role == "api":
        request = Request(
            "http://127.0.0.1:8000/health",
            headers={"X-API-Key": os.environ["API_KEY"]},
        )
        with urlopen(request, timeout=5) as response:
            if response.status != 200:
                raise SystemExit(1)
    else:
        names = (
            ("outbox", "webhooks") if role == "publisher" else ("consumer",)
        )
        max_age = max(
            15, float(os.environ.get("WORKER_POLL_SECONDS", "1")) * 3
        )
        for name in names:
            path = Path(f"/tmp/payments-{name}.heartbeat")
            if (
                not path.exists()
                or time.time() - path.stat().st_mtime > max_age
            ):
                raise SystemExit(1)


if __name__ == "__main__":
    main()
