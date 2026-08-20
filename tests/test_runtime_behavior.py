from __future__ import annotations

import importlib
import sys
import types

from gajim_calls.sdp import Codec, MediaSection, SessionDescription
from gajim_calls.state import CallContext, CallState


class FakeJID:
    def __init__(self, value: str) -> None:
        self.value = value

    @classmethod
    def from_string(cls, value: str):
        return cls(value)

    def new_as_bare(self):
        return type(self)(self.value.split("/", 1)[0])

    def __str__(self) -> str:
        return self.value


class FakeGLib:
    SOURCE_REMOVE = False
    next_source = 1

    @classmethod
    def timeout_add_seconds(cls, _seconds, _callback, *_args):
        source = cls.next_source
        cls.next_source += 1
        return source

    @staticmethod
    def source_remove(_source) -> None:
        return None


class FakeWindow:
    def __init__(self) -> None:
        self.status: list[str] = []
        self.incoming: list[tuple[str, bool]] = []
        self.connected_calls: list[bool] = []
        self.closed = 0

    def set_status(self, text: str) -> None:
        self.status.append(text)

    def show_incoming(self, peer: str, video: bool) -> None:
        self.incoming.append((peer, video))

    def show_outgoing(self, _peer: str, _video: bool) -> None:
        pass

    def present(self) -> None:
        pass

    def close_call(self) -> None:
        self.closed += 1

    def connected(self, video: bool) -> None:
        self.connected_calls.append(video)


class FakeMedia:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeModule:
    def __init__(self) -> None:
        self.jmi: list[dict] = []
        self.jingle: list[dict] = []

    def send_jmi(self, to: str, action: str, sid: str, **kwargs) -> None:
        self.jmi.append({"to": to, "action": action, "sid": sid, **kwargs})

    def send_jingle(self, to: str, action: str, sid: str, **kwargs) -> None:
        self.jingle.append({"to": to, "action": action, "sid": sid, **kwargs})


class FakePlugin:
    config = {"stun_server": "", "turn_server": ""}

    def __init__(self) -> None:
        self.alert_started: list[tuple[str, str, str, bool]] = []
        self.alert_stopped: list[str | None] = []
        self.active: list[tuple[bool, bool]] = []

    def incoming_call_started(
        self, account: str, sid: str, peer: str, *, video: bool
    ) -> None:
        self.alert_started.append((account, sid, peer, video))

    def incoming_call_stopped(self, sid: str | None = None) -> None:
        self.alert_stopped.append(sid)

    def set_call_active(self, active: bool, *, connected: bool = False) -> None:
        self.active.append((active, connected))


def description() -> SessionDescription:
    return SessionDescription(
        media=[
            MediaSection(
                media="audio",
                mid="audio",
                codecs=[Codec(111, "OPUS", 48000, 2)],
            )
        ],
        bundle=("audio",),
    )


def load_runtime(monkeypatch):
    gi = types.ModuleType("gi")
    repository = types.ModuleType("gi.repository")
    repository.GLib = FakeGLib
    gi.repository = repository
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repository)

    nbxmpp = types.ModuleType("nbxmpp")
    nbxmpp_protocol = types.ModuleType("nbxmpp.protocol")
    nbxmpp_protocol.JID = FakeJID
    nbxmpp.protocol = nbxmpp_protocol
    monkeypatch.setitem(sys.modules, "nbxmpp", nbxmpp)
    monkeypatch.setitem(sys.modules, "nbxmpp.protocol", nbxmpp_protocol)

    gajim = types.ModuleType("gajim")
    common = types.ModuleType("gajim.common")
    common.app = types.SimpleNamespace()
    gajim.common = common
    monkeypatch.setitem(sys.modules, "gajim", gajim)
    monkeypatch.setitem(sys.modules, "gajim.common", common)

    for module_name in ("gajim_calls.controller_runtime", "gajim_calls.controller"):
        sys.modules.pop(module_name, None)
    runtime_module = importlib.import_module("gajim_calls.controller_runtime")

    class Harness(runtime_module.RuntimeCallController):
        def __init__(self) -> None:
            self.test_plugin = FakePlugin()
            super().__init__(self.test_plugin)
            self.window = FakeWindow()
            self.signaling = FakeModule()

        def _module(self, account: str):
            assert account == "acc"
            return self.signaling

        def _own_jid(self, account: str) -> str:
            assert account == "acc"
            return "me@example.test/Gajim"

        def _get_window(self):
            return self.window

    return Harness()


def test_connecting_cancel_sends_terminal_signals_and_blocks_late_connect(monkeypatch):
    controller = load_runtime(monkeypatch)
    context = CallContext(
        account="acc",
        sid="connecting",
        peer_bare="phone@example.test",
        peer_full="phone@example.test/Conversations",
        media=("audio",),
        incoming=False,
        state=CallState.NEGOTIATING,
        initiator="me@example.test/Gajim",
        responder="phone@example.test/Conversations",
    )
    context.metadata["signaling"] = "jmi"
    controller.context = context
    controller._local_description = description()
    media = FakeMedia()
    controller.media = media

    controller.hangup()

    assert context.state is CallState.ENDED
    assert media.closed
    assert controller.media is None
    assert controller.test_plugin.active[-1] == (False, False)
    assert controller.signaling.jingle[-1]["action"] == "session-terminate"
    assert controller.signaling.jingle[-1]["sid"] == "connecting"
    assert controller.signaling.jingle[-1]["reason"] == "cancel"
    assert controller.signaling.jmi[-1]["action"] == "finish"
    assert controller.signaling.jmi[-1]["reason"] == "cancel"

    controller._on_media_connected()
    assert context.state is CallState.ENDED
    assert controller.window.connected_calls == []

    # Repeated cancellation after cleanup is intentionally harmless.
    controller.hangup()
    assert context.state is CallState.ENDED


def test_incoming_alert_lifecycle_executes_on_ring_and_decline(monkeypatch):
    controller = load_runtime(monkeypatch)
    from gajim_calls.protocol import JMIEvent

    assert controller.handle_jmi(
        "acc",
        "phone@example.test/Conversations",
        JMIEvent("propose", "incoming", media=("audio",)),
    )
    assert controller.context is not None
    assert controller.context.state is CallState.RINGING
    assert controller.test_plugin.alert_started == [
        ("acc", "incoming", "phone@example.test", False)
    ]

    controller.decline()

    assert controller.context.state is CallState.ENDED
    assert "incoming" in controller.test_plugin.alert_stopped
    assert controller.signaling.jmi[-1]["action"] == "reject"
    assert controller.signaling.jmi[-1]["sid"] == "incoming"


def test_accept_stops_alert_before_entering_negotiation(monkeypatch):
    controller = load_runtime(monkeypatch)
    from gajim_calls.protocol import JMIEvent

    controller.handle_jmi(
        "acc",
        "phone@example.test/Conversations",
        JMIEvent("propose", "accept-me", media=("audio",)),
    )
    controller.accept()

    assert controller.context is not None
    assert controller.context.state is CallState.NEGOTIATING
    assert "accept-me" in controller.test_plugin.alert_stopped
    assert controller.signaling.jmi[-1]["action"] == "proceed"
    assert controller.signaling.jmi[-1]["sid"] == "accept-me"
