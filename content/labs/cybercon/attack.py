"""Bounded real HTTP attempts against the fictional local portal, without egress."""
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
import logging


def attempt(base="http://127.0.0.1:8081"):
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    results = []
    for path, cookie in [("/support/diagnostics.json", ""),
                         ("/api/reports?id=8202", "nt_session=nt-support-legacy")]:
        request = urllib.request.Request(base + path, headers={"Cookie": cookie})
        try:
            with client.open(request, timeout=3) as response:
                body = response.read(8192)
                status = response.status
        except urllib.error.HTTPError as response:
            status, body = response.code, response.read(8192)
        except OSError:
            status, body = 0, b""
        results.append({"time": datetime.now(timezone.utc).isoformat(), "path": path,
                        "status": status, "received_bytes": len(body)})
    return results


if __name__ == "__main__":
    logger = logging.getLogger("exercise-attempts")
    logger.setLevel(logging.INFO)
    logger.addHandler(RotatingFileHandler("/var/log/cybercon/attack.jsonl", maxBytes=2_000_000, backupCount=2))
    while True:
        for result in attempt():
            logger.info(json.dumps(result))
        time.sleep(5)
