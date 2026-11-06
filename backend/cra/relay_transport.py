"""mTLS stream adapter retaining SSH host verification and bounded backpressure."""

import asyncio
import json
import socket
import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from .compute_nodes import node
from .node_contracts import TerminalClaims


def claims_for(signed: dict[str, Any]) -> TerminalClaims:
    return TerminalClaims.model_validate_json(signed["payload"])


def verify_allocation(allocation: dict[str, Any], claims: TerminalClaims) -> None:
    expected = {
        "node_id": claims.node_id,
        "lab_id": str(claims.lab_id),
        "organization_id": str(claims.organization_id),
        "user_id": str(claims.user_id),
        "operator_user_id": str(claims.operator_user_id or claims.user_id),
        "generation": claims.generation,
        "purpose": claims.purpose,
        "session": "cra-"
        + (claims.operator_user_id or claims.user_id).hex
        + "-"
        + str(claims.generation),
    }
    if any(allocation.get(key) != value for key, value in expected.items()):
        raise ValueError("Allocation identity mismatch")


@asynccontextmanager
async def remote_transport(
    signed: dict[str, Any],
) -> AsyncIterator[tuple[dict[str, Any], socket.socket]]:
    claims = claims_for(signed)
    target = node(claims.node_id)
    if target.id != 2:
        raise ValueError("Remote node required")
    folder = target.certificates(relay=True)
    context = ssl.create_default_context(cafile=folder / "ca.crt")
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(folder / "relay.crt", folder / "relay.key")
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(
            target.host,
            22291,
            ssl=context,
            server_hostname=target.host,
            ssl_handshake_timeout=5,
            ssl_shutdown_timeout=3,
            limit=16384,
        ),
        6,
    )
    pair: tuple[socket.socket, socket.socket] | None = None
    tasks: list[asyncio.Task[None]] = []
    try:
        header = json.dumps(signed, separators=(",", ":")).encode() + b"\n"
        if len(header) > 8192:
            raise ValueError("Invalid stream request")
        writer.write(header)
        await asyncio.wait_for(writer.drain(), 5)
        line = await asyncio.wait_for(reader.readline(), 15)
        if not line.endswith(b"\n") or len(line) > 16384:
            raise ValueError("Invalid allocation response")
        allocation = json.loads(line)
        if not isinstance(allocation, dict):
            raise TypeError("Invalid allocation response")
        verify_allocation(allocation, claims)
        pair = socket.socketpair()
        for item in pair:
            item.setblocking(False)
        client, bridge = pair
        loop = asyncio.get_running_loop()

        async def upstream() -> None:
            while data := await loop.sock_recv(bridge, 16384):
                writer.write(data)
                await asyncio.wait_for(writer.drain(), 15)

        async def downstream() -> None:
            while data := await reader.read(16384):
                await asyncio.wait_for(loop.sock_sendall(bridge, data), 15)

        async def run() -> None:
            pumps = [asyncio.create_task(upstream()), asyncio.create_task(downstream())]
            try:
                done, _ = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            except (OSError, TimeoutError):
                pass
            finally:
                for task in pumps:
                    task.cancel()
                await asyncio.gather(*pumps, return_exceptions=True)
                bridge.close()
                writer.close()

        tasks.append(asyncio.create_task(run()))
        yield allocation, client
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if pair:
            for item in pair:
                item.close()
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 3)
        except (OSError, TimeoutError):
            pass
