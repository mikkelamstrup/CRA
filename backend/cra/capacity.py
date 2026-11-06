"""Bounded classroom capacity and stable, disjoint per-operator lab links."""

MAX_ACTIVE_LABS = 15
RESERVATION_SLOTS = 18


def lab_link(slot: int, ordinal: int = 0) -> dict[str, int]:
    if type(slot) is not int or not 0 <= slot < RESERVATION_SLOTS:
        raise ValueError("Invalid lab slot")
    if type(ordinal) is not int or not 0 <= ordinal < 4:
        raise ValueError("Invalid operator ordinal")
    # Keep existing six-slot manifests valid; new slots use an unused block.
    offset = slot + 10 * ordinal if slot < 6 else 40 + 4 * (slot - 6) + ordinal
    return {"port": 23200 + offset, "subnet": 82 + offset}


def target_ip(slot: int, ordinal: int = 0) -> str:
    return f"192.168.{lab_link(slot, ordinal)['subnet']}.10"
