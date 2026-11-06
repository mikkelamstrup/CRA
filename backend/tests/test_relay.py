"""Relay port boundary is independent of authenticated node response contents."""

from cra.relay import operator_port


def test_only_assigned_operator_port_ranges_are_reachable():
    for allowed in [
        24001,
        24002,
        24003,
        24004,
        24011,
        24012,
        24013,
        24014,
        24021,
        24034,
        24041,
        24054,
        24061,
        24151,
        24154,
        24161,
        24174,
    ]:
        assert operator_port(allowed)
    for denied in [
        24000,
        24010,
        24020,
        24050,
        24005,
        24015,
        24055,
        24155,
        24160,
        24175,
        24180,
        24181,
        22280,
        22290,
        28000,
        22,
        "24001",
        24001.0,
        True,
        None,
    ]:
        assert not operator_port(denied)


def test_proxy_input_burst_drains_without_closing_and_stalled_peer_is_bounded(monkeypatch):
    """Synthetic transport regression; real VM/public-tunnel proof is separate."""
    import asyncio
    import json
    import time
    from types import SimpleNamespace

    from cra import relay

    async def exercise(blocked):
        delivered = bytearray()
        complete = asyncio.Event()
        closed = []
        frames = [bytes([97 + i]) for i in range(20)]

        class Input:
            def write(self, frame):
                delivered.extend(frame)

            async def drain(self):
                if blocked:
                    await asyncio.Event().wait()
                await asyncio.sleep(0.005)
                if len(delivered) == len(frames):
                    complete.set()

        class Output:
            async def read(self, size):
                await complete.wait()
                return b""

        process = SimpleNamespace(stdin=Input(), stdout=Output(), close=lambda: None)

        class SSH:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def create_process(self, *args, **kwargs):
                return process

        class WS:
            request = SimpleNamespace(
                path="/terminal/33333333-3333-4333-8333-333333333333",
                headers={"Origin": "http://localhost"},
            )

            async def recv(self):
                return json.dumps(
                    {"type": "auth", "grant": "x" * 40, "csrf": "0" * 64, "cols": 80, "rows": 24}
                )

            async def send(self, value):
                pass

            async def close(self, *reason):
                closed.append(reason)

            async def __aiter__(self):
                for frame in frames:
                    yield frame  # Already buffered by the proxy; no scheduling gap.
                await asyncio.Event().wait()

        monkeypatch.setattr(relay, "request", lambda *a: {})
        monkeypatch.setattr(relay, "claims_for", lambda signed: SimpleNamespace(node_id=1))
        monkeypatch.setattr(relay, "verify_allocation", lambda *a: None)
        monkeypatch.setattr(
            relay,
            "node_allocation",
            lambda signed: {
                "purpose": "terminal", "host": "10.0.2.2", "port": 24001,
                "username": "cra", "session": "cra-" + "0" * 32 + "-1",
                "host_key": "fixture", "private_key": "fixture",
            },
        )
        monkeypatch.setattr(relay.asyncssh, "import_private_key", lambda value: None)
        monkeypatch.setattr(relay.asyncssh, "connect", lambda *a, **kw: SSH())
        began = time.monotonic()
        await asyncio.wait_for(relay.handler(WS()), 3)
        assert relay.ACTIVE == 0
        if blocked:
            assert closed == [(4403, "terminal_unavailable")]
            assert time.monotonic() - began < 2.5
            assert len(delivered) <= 5
        else:
            assert delivered == b"".join(frames)
            assert closed == []

    asyncio.run(exercise(False))
    asyncio.run(exercise(True))
