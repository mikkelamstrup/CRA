"""Offensive solo mission: real HTTP/SQLite on the isolated target only."""

import json
from pathlib import Path

import course_recipes
import lab_tools

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "content/labs/request-forge"


def pages():
    return {
        "index.html": '<!doctype html><meta charset="utf-8"><h1>Nordtele · Ordrestatus</h1>'
        "<p>Ordreportalen findes på port 8081. Læs dit mandat i academy/request-forge.</p>"
    }


def setup(command, target, operators, links, flag, guest):
    if len(operators) != 1 or len(links) != 1:
        raise ValueError("Request Forge requires an individual operator")
    put = lambda path, text, mode="0644": course_recipes.root_file(
        target, path, text, guest, mode
    )
    guest(target, "sudo install -d -m 0755 /opt/request-forge /etc/request-forge")
    for name in ("portal.py", "index.html", "client.js"):
        put("/opt/request-forge/" + name, (CONTENT / name).read_text())
    address = "192.168." + str(links[0]["subnet"]) + ".10"
    put(
        "/etc/request-forge/portal.json",
        json.dumps({"address": address, "flag": flag}),
        "0640",
    )
    guest(target, "sudo chown root:www-data /etc/request-forge/portal.json")
    put(
        "/etc/systemd/system/request-forge.service",
        """[Unit]
Description=CRA Request Forge isolated SQL injection exercise
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0
[Service]
User=www-data
Group=www-data
RestartSec=1
ExecStart=/usr/bin/python3 -I /opt/request-forge/portal.py --config /etc/request-forge/portal.json
Restart=on-failure
NoNewPrivileges=true
ProtectHome=true
ProtectSystem=strict
PrivateTmp=true
PrivateDevices=true
RestrictSUIDSGID=true
MemoryMax=128M
TasksMax=32
[Install]
WantedBy=multi-user.target
""",
    )
    guest(
        target,
        "sudo systemctl daemon-reload && sudo systemctl enable --now request-forge",
    )
    # Only the brief and empty report are delivered to the operator, never source or secrets.
    for name in ("README.txt", "mandat.txt", "rapport.md"):
        lab_tools.upload(
            operators[0],
            "/home/cra/academy/request-forge/" + name,
            (CONTENT / name).read_bytes(),
        )


def check(command, target, operators, links, guest):
    guest(target, "sudo systemctl is-active --quiet request-forge")
    for name, link in zip(operators, links):
        guest(
            name,
            "curl --fail --max-time 5 http://192.168."
            + str(link["subnet"])
            + ".10:8081/ >/dev/null",
        )
