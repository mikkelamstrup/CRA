#!/usr/bin/env python3
"""Read-only inventory for the CRA Ubuntu/AMD64 host. Requires Python 3.10+.

Reads fixed OS metadata only. No installation, VM start, network queries,
credentials, environment dump or file writes. Tools are located, not invoked.
An inventory is NOT proof of KVM boot, host dedication or lab isolation.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
from pathlib import Path
from typing import Any

GIB = 1024 ** 3
TOOLS = ("git", "python3", "node", "pnpm", "uv", "docker", "virsh",
         "qemu-system-x86_64", "qemu-img", "codex", "claude")


def read_system_file(path: str) -> str | None:
    """Called only for the three fixed, public system metadata paths below."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def parse_memory_gib(value: str | None) -> float | None:
    try:
        raw = int(value) if value is not None else 0
    except (TypeError, ValueError):
        return None
    return round(raw / GIB, 2) if raw > 0 else None


def parse_meminfo(text: str | None) -> dict[str, float | None]:
    result: dict[str, float | None] = {"physical": None, "available": None}
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) != 3 or parts[2] != "kB":
            continue
        key = {"MemTotal:": "physical", "MemAvailable:": "available"}.get(parts[0])
        if key is not None:
            try:
                amount = int(parts[1])
            except ValueError:
                continue
            if amount >= 0:
                result[key] = (parse_memory_gib(str(amount * 1024)) if key == "physical"
                               else round(amount * 1024 / GIB, 2))
    return result


def parse_os_release(text: str | None) -> dict[str, str | None]:
    result: dict[str, str | None] = {"ID": None, "VERSION_ID": None}
    for line in (text or "").splitlines():
        key, sep, value = line.partition("=")
        if sep and key in result:
            result[key] = value.strip().strip('\"\'') or None
    return result


def parse_cpuinfo(text: str | None) -> dict[str, Any]:
    model = None
    flags: list[set[str]] = []
    for line in (text or "").splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        if key.strip() == "model name" and model is None:
            model = value.strip() or None
        if key.strip() == "flags":
            flags.append(set(value.split()))
    return {"model": model,
            "svm_flag_on_all_reported_cpus": all("svm" in f for f in flags) if flags else None}


def kvm_inventory() -> dict[str, bool | None]:
    try:
        Path("/dev/kvm").stat()
    except FileNotFoundError:
        return {"present": False, "read_write_access_for_current_user": False}
    except OSError:
        return {"present": None, "read_write_access_for_current_user": None}
    return {"present": True,
            "read_write_access_for_current_user": os.access("/dev/kvm", os.R_OK | os.W_OK)}


def collect_report() -> dict[str, Any]:
    system, architecture = platform.system(), platform.machine()
    is_linux = system == "Linux"
    distro = parse_os_release(read_system_file("/etc/os-release") if is_linux else None)
    memory = parse_meminfo(read_system_file("/proc/meminfo") if is_linux else None)
    cpu = parse_cpuinfo(read_system_file("/proc/cpuinfo") if is_linux else None)
    kvm = kvm_inventory() if is_linux else {
        "present": None, "read_write_access_for_current_user": None}
    target = is_linux and architecture == "x86_64" and distro["ID"] == "ubuntu"
    try:
        free_disk = round(shutil.disk_usage(Path.cwd()).free / GIB, 2)
    except OSError:
        free_disk = None
    warnings: list[str] = []
    if not target:
        warnings.append("Ikke identificeret som Ubuntu/x86_64: dette er ikke accept af den planlagte serverprofil.")
    if memory["physical"] is None:
        warnings.append("RAM kunne ikke aflæses; ingen kapacitetskonklusion er mulig.")
    if free_disk is None:
        warnings.append("Ledig disk kunne ikke aflæses.")
    elif free_disk < 60:
        warnings.append("Under det indledende diskbudget på 60 GiB; vurder plads før installation.")
    if is_linux and cpu["svm_flag_on_all_reported_cpus"] is not True:
        warnings.append("AMD SVM er ikke bekræftet på alle rapporterede CPU'er; undersøg vært/firmware.")
    if is_linux and kvm["read_write_access_for_current_user"] is not True:
        warnings.append("/dev/kvm-adgang er ikke bekræftet; giv ikke agenten brede rettigheder som genvej.")
    return {
        "report_kind": "read_only_inventory_not_acceptance_test",
        "host_is_target_platform": target,
        "target_platform_scope": "OS/architecture only; not server identity, bare metal or acceptance",
        "os": system,
        "distribution": distro["ID"],
        "os_version": distro["VERSION_ID"],
        "kernel": platform.release(),
        "architecture": architecture,
        "cpu": cpu,
        "physical_memory_gib": memory["physical"],
        "available_memory_gib": memory["available"],
        "free_disk_gib_on_current_volume": free_disk,
        "kvm": kvm,
        "tools_on_path": {name: shutil.which(name) is not None for name in TOOLS},
        "not_tested": [
            "Server identity, bare metal and host dedication",
            "Installed tool versions and compatibility",
            "Actual KVM guest boot",
            "Control VM and lab network isolation",
            "Existing project backups and approved installation scope",
            "Classroom capacity and performance",
        ],
        "warnings": warnings,
        "system_changes_performed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="JSON til stdout, ingen filskrivning.")
    args = parser.parse_args()
    report = collect_report()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print("Cyber Range Academy — læsende Ubuntu-server-preflight")
    print("Inventar, ikke bevis for KVM, dedikering eller sikker isolation.\n")
    for key in ("os", "distribution", "os_version", "kernel", "architecture", "cpu",
                "physical_memory_gib", "available_memory_gib",
                "free_disk_gib_on_current_volume", "kvm"):
        print(f"{key}: {report[key]}")
    print("\nVærktøjer på PATH (ikke kørt eller versionskontrolleret):")
    for name, present in report["tools_on_path"].items():
        print(f"  {name}: {'fundet' if present else 'ikke fundet'}")
    for warning in report["warnings"]:
        print(f"\nBEMÆRK: {warning}")
    print("\nNOT RUN:")
    for item in report["not_tested"]:
        print(f"  {item}")
    print("\nIngen installationer, VM-starter eller systemændringer udført.")
    print("Næste trin: dokumentér mangler; M0-02 kræver dedikering og konkret godkendelse.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
