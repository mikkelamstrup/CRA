#!/usr/bin/env python3
"""Read-only M0-01 evidence; stdout only, no downloads, services or privileges.

Uses fixed commands against local metadata/package caches. No environment,
usernames, process arguments, other projects, credentials or raw stderr are
included. Exit zero means inventory completed, not infrastructure acceptance.
"""
from __future__ import annotations

import datetime
import json
import re
import subprocess

import doctor

INSTALL_PACKAGES = (
    "qemu-system-x86", "qemu-utils", "libvirt-daemon-system",
    "libvirt-clients", "cloud-image-utils",
)
INSPECT_PACKAGES = INSTALL_PACKAGES + (
    "openssh-client", "iproute2", "nftables", "apparmor",
)


def run_local(argv: list[str]) -> tuple[int | None, str]:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None, ""
    return result.returncode, result.stdout


def parse_simulation(code: int | None, output: str) -> dict:
    actions = []
    unparsed_action = False
    summary = None
    for line in output.splitlines():
        install = re.match(r"^Inst (\S+)(?: \[[^\]]+\])? \((\S+)\s", line)
        remove = re.match(r"^Remv (\S+)", line)
        if install:
            actions.append({"action": "install", "package": install[1], "version": install[2]})
        elif remove:
            actions.append({"action": "remove", "package": remove[1]})
        elif line.startswith(("Inst ", "Remv ")):
            unparsed_action = True
        counts = re.fullmatch(
            r"(\d+) upgraded, (\d+) newly installed, (\d+) to remove and (\d+) not upgraded\.",
            line.strip(),
        )
        if counts:
            summary = dict(zip(("upgraded", "new", "removed", "not_upgraded"), map(int, counts.groups())))
    complete = (code == 0 and summary is not None and not unparsed_action
                and sum(a["action"] == "install" for a in actions) == summary["upgraded"] + summary["new"]
                and sum(a["action"] == "remove" for a in actions) == summary["removed"])
    return {"exit_code": code, "parse_complete": complete,
            "counts": summary, "actions": actions,
            "scope": "Local-cache simulation only; not an approved install or downloaded package lock."}


def collect() -> dict:
    report = doctor.collect_report()
    report["collected_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if report["os"] != "Linux":
        report["extended_inventory"] = {"status": "NOT RUN", "reason": "Requires Linux local package metadata"}
        return report
    code, value = run_local(["systemd-detect-virt"])
    allowed = {"none", "kvm", "qemu", "vmware", "microsoft", "oracle", "xen",
               "docker", "podman", "lxc", "lxc-libvirt", "systemd-nspawn", "wsl"}
    kind = value.strip() if value.strip() in allowed and code in (0, 1) else "UNKNOWN"
    report["virtualization_detection"] = {
        "kind": kind, "exit_code": code,
        "scope": "No detected virtualization is an indication, not proof of server identity.",
    }
    report["packages"] = []
    for name in INSPECT_PACKAGES:
        code, value = run_local(["dpkg-query", "-W", "-f=${db:Status-Status} ${Version}", name])
        installed = re.fullmatch(r"installed ([A-Za-z0-9.+:~_-]+)", value.strip())
        installed_version = installed[1] if code == 0 and installed else None
        code, value = run_local(["apt-cache", "policy", name])
        candidate = re.search(r"^\s*Candidate: ([A-Za-z0-9.+:~_-]+)\s*$", value, re.MULTILINE)
        report["packages"].append({
            "name": name, "installed_version": installed_version,
            "cached_candidate": candidate[1] if code == 0 and candidate else None,
            "null_means": "not installed, unavailable or unreadable; no readiness claim",
        })
    cmd = ["apt-get", "--simulate", "--no-install-recommends", "install", *INSTALL_PACKAGES]
    code, value = run_local(cmd)
    report["apt_simulation"] = {"command": cmd, **parse_simulation(code, value)}
    report["dedication"] = "UNKNOWN: owner confirmation is separate from this inventory"
    return report


if __name__ == "__main__":
    print(json.dumps(collect(), ensure_ascii=False, indent=2))
