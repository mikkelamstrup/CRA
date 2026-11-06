"""Separate unprivileged SSH/PTY relay. No database, signing key or lifecycle rights."""

import asyncio
import json
import logging
import re
import ssl
import urllib.request
from contextlib import AsyncExitStack
from typing import Any
from uuid import UUID

import asyncssh
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import WebSocketException
from websockets.typing import Origin

from .auth import origins
from .capacity import RESERVATION_SLOTS
from .compute_nodes import node
from .relay_transport import claims_for, remote_transport, verify_allocation

WINDOW = 65536
ACTIVE = 0
LOGGER = logging.getLogger("cra.relay")
LOGGER.addHandler(logging.NullHandler())
LOGGER.propagate = False


def request(
    url: str, body: dict[str, Any], headers: dict[str, str], context: ssl.SSLContext | None = None
) -> dict[str, Any]:
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context)
    )
    message = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={**headers, "Content-Type": "application/json"}
    )
    with opener.open(message, timeout=5) as response:
        data = response.read(16385)
        if len(data) > 16384:
            raise ValueError("Oversized control response")
        result = json.loads(data)
        if not isinstance(result, dict):
            raise TypeError("Invalid control response")
        return result


def node_allocation(body: dict[str, Any]) -> dict[str, Any]:
    target = node(claims_for(body).node_id)
    if target.id != 1:
        raise ValueError("Remote node requires stream transport")
    folder = target.certificates(relay=True)
    context = ssl.create_default_context(cafile=str(folder / "ca.crt"))
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(folder / "relay.crt", folder / "relay.key")
    return request(target.endpoint + "/v1/terminal-allocation", body, {}, context)


def dimensions(message: dict[str, Any]) -> tuple[int, int]:
    cols, rows = message.get("cols"), message.get("rows")
    if (
        type(cols) is not int
        or type(rows) is not int
        or not 10 <= cols <= 240
        or not 2 <= rows <= 100
    ):
        raise ValueError("Invalid terminal dimensions")
    return cols, rows


def operator_port(value: object) -> bool:
    return type(value) is int and value in {
        24000 + slot * 10 + ordinal for slot in range(RESERVATION_SLOTS) for ordinal in range(1, 5)
    }


async def session(ws: ServerConnection) -> None:
    assert ws.request
    match = re.fullmatch(r"/(terminal|desktop)/([0-9a-f-]{36})", ws.request.path)
    if not match:
        raise ValueError("Invalid terminal route")
    purpose = match[1]
    lab_id = str(UUID(match[2]))
    first = await asyncio.wait_for(ws.recv(), 5)
    if not isinstance(first, str) or len(first) > 2048:
        raise ValueError("Expected grant frame")
    hello = json.loads(first)
    if (
        not isinstance(hello, dict)
        or set(hello) != {"type", "grant", "csrf", "cols", "rows"}
        or hello["type"] != "auth"
    ):
        raise ValueError("Invalid grant frame")
    cols, rows = dimensions(hello)
    if not isinstance(hello["grant"], str) or not 40 <= len(hello["grant"]) <= 128:
        raise ValueError("Invalid grant")
    if not isinstance(hello["csrf"], str) or not re.fullmatch("[0-9a-f]{64}", hello["csrf"]):
        raise ValueError("Invalid CSRF")
    headers = {
        "Cookie": ws.request.headers.get("Cookie", ""),
        "Origin": ws.request.headers["Origin"],
        "X-CRA-CSRF": hello["csrf"],
    }
    body = {"lab_id": lab_id, "grant": hello["grant"], "purpose": purpose}

    async def control(action: str) -> dict[str, Any]:
        return await asyncio.to_thread(
            request, "http://127.0.0.1:8002/internal/terminal/" + action, body, headers
        )

    signed = await control("consume")
    async with AsyncExitStack() as stack:
        claims = claims_for(signed)
        sock = None
        if claims.node_id == 2:
            allocation, sock = await stack.enter_async_context(remote_transport(signed))
        else:
            allocation = await asyncio.to_thread(node_allocation, signed)
            verify_allocation(allocation, claims)
        if (
            allocation.get("purpose", "terminal") != purpose
            or allocation["host"] != "10.0.2.2"
            or not operator_port(allocation["port"])
            or allocation["username"] != "cra"
        ):
            raise ValueError("Invalid operator allocation")
        if not re.fullmatch(r"cra-[0-9a-f]{32}-[0-9]{1,5}", allocation["session"]):
            raise ValueError("Invalid session")
        known = f"[10.0.2.2]:{allocation['port']} {allocation['host_key']}\n".encode()
        key = asyncssh.import_private_key(allocation.pop("private_key"))
        async with asyncssh.connect(
            "10.0.2.2",
            port=allocation["port"],
            sock=sock,
            username="cra",
            client_keys=[key],
            known_hosts=known,
            config=None,
            agent_path=None,
            agent_forwarding=False,
            login_timeout=10,
            keepalive_interval=10,
            keepalive_count_max=3,
        ) as connection:
            await control("check")
            # Both commands are fixed; only the signed actor's own operator is reachable.
            if purpose == "desktop":
                process = await connection.create_process(
                    "socat STDIO TCP:127.0.0.1:5900",
                    encoding=None,
                    window=WINDOW,
                    max_pktsize=16384,
                )
            else:
                process = await connection.create_process(
                    "tmux new-session -A -s " + allocation["session"],
                    term_type="xterm-256color",
                    term_size=(cols, rows),
                    encoding=None,
                    window=WINDOW,
                    max_pktsize=16384,
                )
            await ws.send(json.dumps({"type": "ready", "window": WINDOW}))
            sent = 0
            acked = 0
            condition = asyncio.Condition()
            inputs: asyncio.Queue[bytes] = asyncio.Queue(maxsize=4)

            async def output() -> None:
                nonlocal sent
                while True:
                    async with condition:
                        while sent - acked >= WINDOW:
                            await condition.wait()
                        credit = WINDOW - (sent - acked)
                    data = await process.stdout.read(min(16384, credit))
                    if not data:
                        return
                    sent += len(data)
                    await ws.send(data)

            async def incoming() -> None:
                nonlocal acked
                async for frame in ws:
                    if isinstance(frame, bytes):
                        if not frame or len(frame) > 16384:
                            raise ValueError("Invalid input frame")
                        # A proxy may deliver ordinary keystrokes in one burst.
                        # Keep the four-frame bound, but let the SSH writer drain
                        # it before rejecting input. A blocked peer stays bounded.
                        await asyncio.wait_for(inputs.put(frame), 1)
                    else:
                        if len(frame) > 256:
                            raise ValueError("Oversized control frame")
                        message = json.loads(frame)
                        if not isinstance(message, dict):
                            raise TypeError("Invalid control frame")
                        if set(message) == {"type", "bytes"} and message["type"] == "ack":
                            value = message["bytes"]
                            if type(value) is not int or not acked <= value <= sent:
                                raise ValueError("Invalid acknowledgement")
                            async with condition:
                                acked = value
                                condition.notify_all()
                        elif (
                            purpose == "terminal"
                            and set(message) == {"type", "cols", "rows"}
                            and message["type"] == "resize"
                        ):
                            width, height = dimensions(message)
                            process.change_terminal_size(width, height)
                        else:
                            raise ValueError("Unknown control frame")

            async def write_input() -> None:
                while True:
                    frame = await inputs.get()
                    process.stdin.write(frame)
                    await process.stdin.drain()

            async def reauthorize() -> None:
                while True:
                    await asyncio.sleep(2)
                    await control("check")

            tasks = [
                asyncio.create_task(fn()) for fn in (output, incoming, write_input, reauthorize)
            ]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                process.close()


async def handler(ws: ServerConnection) -> None:
    global ACTIVE
    if ACTIVE >= 20:
        await ws.close(1013, "terminal_busy")
        return
    ACTIVE += 1
    try:
        await session(ws)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        asyncio.QueueFull,
        asyncssh.Error,
        WebSocketException,
    ) as error:
        print("terminal_closed:" + type(error).__name__, flush=True)
        # Neither terminal bytes, grant, cookie nor SSH exception details are logged.
        await ws.close(4403, "terminal_unavailable")
    finally:
        ACTIVE -= 1


async def main() -> None:
    async with serve(
        handler,
        "127.0.0.1",
        8001,
        origins=[Origin(value) for value in origins()],
        max_size=16384,
        max_queue=4,
        write_limit=32768,
        compression=None,
        ping_interval=15,
        ping_timeout=15,
        close_timeout=3,
        logger=LOGGER,
    ) as server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
