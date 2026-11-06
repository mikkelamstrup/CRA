"""Only the trusted worker can invoke the fixed node protocol over mTLS."""

import json
import ssl
import urllib.error
import urllib.request
from typing import Any

from .compute_nodes import node
from .node_contracts import NodeCommand


class NodeUnavailable(RuntimeError):
    pass


class NodeRejected(RuntimeError):
    pass


def request(
    path: str, command: NodeCommand | None = None, *, node_id: int | None = None
) -> dict[str, Any]:
    try:
        identity = node_id if node_id is not None else (command.node_id if command else 1)
        if command is not None and command.node_id != identity:
            raise NodeRejected("Command node mismatch")
        if (command is None and path != "/v1/inventory") or (
            command is not None and path != "/v1/operations"
        ):
            raise NodeRejected("Invalid node route")
        target = node(identity)
        certificates = target.certificates()
        context = ssl.create_default_context(cafile=certificates / "ca.crt")
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.load_cert_chain(certificates / "client.crt", certificates / "client.key")
        # Fixed endpoint; no browser host/path and no ambient proxy configuration.
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context)
        )
        message = urllib.request.Request(
            target.endpoint + path,
            data=command.model_dump_json().encode() if command else None,
            headers={"Content-Type": "application/json"},
        )
        with opener.open(message, timeout=240 if command else 5) as response:
            data = response.read(1048577)
            if len(data) > 1048576:
                raise NodeUnavailable("Node response exceeds limit")
            value = json.loads(data)
            if not isinstance(value, dict):
                raise NodeUnavailable("Invalid node response")
            response_node = value.get("node_id", 1)
            if type(response_node) is not int or response_node != identity:
                raise NodeRejected("Response node mismatch")
            return value
    except urllib.error.HTTPError as error:
        if error.code == 409:
            raise NodeRejected("Node rejected operation") from None
        raise NodeUnavailable("Node unavailable") from None
    except (OSError, ValueError):
        raise NodeUnavailable("Node unavailable") from None
