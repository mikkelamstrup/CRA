"""Trusted two-node registry. Browser inputs never select hosts or key paths."""

import ipaddress
import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ComputeNode:
    id: int
    host: str
    accept_new: bool = True

    @property
    def endpoint(self) -> str:
        return f"https://{self.host}:22290"

    def certificates(self, *, relay: bool = False) -> Path:
        if relay:
            root = Path("/var/lib/cra-relay/node-tls")
        else:
            root = Path(os.environ.get("CRA_NODE_CERTS", "/var/lib/cra/node-tls"))
        return root if self.id == 1 else root / str(self.id)

    def signing_key(self) -> Path:
        root = Path(os.environ.get("CRA_TERMINAL_SIGNING_KEY", "/var/lib/cra/terminal-signing.key"))
        return root if self.id == 1 else root.parent / "node-signing" / f"{self.id}.key"


def nodes() -> dict[int, ComputeNode]:
    path = Path(os.environ.get("CRA_NODES_FILE", "/etc/cra/nodes.json"))
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {1: ComputeNode(1, "10.0.2.2")}
    if len(raw) > 4096:
        raise ValueError("Invalid compute registry")
    values = json.loads(raw)
    if not isinstance(values, list) or not 1 <= len(values) <= 2:
        raise ValueError("Invalid compute registry")
    result = {}
    for value in values:
        if not isinstance(value, dict) or set(value) != {"id", "host", "accept_new"}:
            raise ValueError("Invalid compute entry")
        identity, host, accept = value["id"], value["host"], value["accept_new"]
        if type(identity) is not int or identity not in (1, 2) or identity in result:
            raise ValueError("Invalid compute identity")
        if type(accept) is not bool or not isinstance(host, str):
            raise ValueError("Invalid compute entry")
        address = ipaddress.IPv4Address(host)
        if (identity == 1 and host != "10.0.2.2") or (
            identity == 2 and address not in ipaddress.IPv4Network("100.64.0.0/10")
        ):
            raise ValueError("Invalid compute address")
        result[identity] = ComputeNode(identity, host, accept)
    return result


def node(identity: int) -> ComputeNode:
    if type(identity) is not int or identity not in (1, 2):
        raise ValueError("Invalid compute identity")
    try:
        return nodes()[identity]
    except KeyError:
        raise ValueError("Compute node is not configured") from None
