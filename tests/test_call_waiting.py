from __future__ import annotations

import importlib
import sys
import types

from gajim_calls.call_waiting import with_call_waiting
from gajim_calls.protocol import JMIEvent, JingleEvent
from gajim_calls.sdp import (
    Codec,
    Fingerprint,
    IceCandidate,
    MediaSection,
    SessionDescription,
)
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


class Window:
    def __init__(self) -> None:
        self.incoming: list[tuple[str, bool]] = []
        self.closed = 0
        self.status: list[str] = []

    def show_incoming(self, peer: str, video: bool) -> None:
        self.incoming.append((peer, video))

    def close_call(self) -> None:
        self.closed += 1

    def set_status(self, text: str) -> None:
        self.status.append(text)

    def present(self) -> None:
        pass


class Media:
    def __init__(self) -> None:
        self.closed = False
        self.candidates: list[tuple[str, str]] = []

    def close(self) -> None:
        self.closed = True

    def add_remote_candidate(self, mid: str, candidate: str) -> None:
        self.candidates.append((mid, candidate))


class Module:
    def __init__(self) -> None:
        self.jmi: list[dict] = []
        self.jingle: list[dict] = []

    def send_jmi(self, to: str, action: str, sid: str, **kwargs) -> None:
        self.jmi.append({"to": to, "action": action, "sid": sid, **kwargs})

    def send_jingle(self, to: str, action: str, sid: str, **kwargs) -> None:
        self.jingle.append({"to": to, "action": action, "sid": sid, **kwargs})


class Plugin:
    config = {"stun_server": "", "turn_server": ""}

    def __init__(self) -> None:
        self.started: list[tuple[str, str, str, bool]] = []
        self.stopped: list[str | None] = []
        self.active: list[tuple[bool, bool]] = []

    def incoming_call_started(
        self, account: str, sid: str, peer: str, *, video: bool
    ) -> None:
        self.started.append((account, sid, peer, video))

    def incoming_call_stopped(self, sid: str | None = None) -> None:
        self.stopped.append(sid)

    def set_call_active(self, active: bool, *, connected: bool = False) -> None:
        self.active.append((active, connected))


def description() -> SessionDescription:
    return SessionDescription(
        media=[
            MediaSection(
                media="audio",
                mid="audio",
                codecs=[Codec(111, "OPUS", 48000, 2)],
                ice_ufrag="ufrag",
                ice_pwd="abcdefghijklmnopqrstuv",
                fingerprint=Fingerprint("sha-256", "AA:BB:CC", "actpass"),
            )
        ],
        bundle=("audio",),
    )


def candidate_event(sid: str, peer: str) -> JingleEvent:
    candidate = IceCandidate(
        "1", 1, "udp", 2130706431, "10.0.0.2", 50000, "host"
    )
    return JingleEvent(
        action="transport-info",
        sid=sid,
        initiator=peer,
        responder="me@example.test/Gajim",
        description=SessionDescription(
            media=[
                MediaSection(
                    media="audio",
                    mid="audio",
                    ice_ufrag="ufrag",
                    ice_pwd="abcdefghijklmnopqrstuv",
                    candidates=[candidate],
                )
            ]
        ),
    )


def runtime_base(monkeypatch):
    nbxmpp = types.ModuleType("nbxmpp")
    protocol = types.ModuleType("nbxmpp.protocol")
    protocol.JID = FakeJID
    nbxmpp.protocol = protocol
    monkeypatch.setitem(sys.modules, "nbxmpp", nbxmpp)
    monkeypatch.setitem(sys.modules, "nbxmpp.protocol", protocol)

    gajim = types.ModuleType("gajim")
    common = types.ModuleType("gajim.common")
    common.app = types.SimpleNamespace()
    gajim.common = common
    monkeypatch.setitem(sys.modules, "gajim", gajim)
    monkeypatch.setitem(sys.modules, "gajim.common", common)

    sys.modules.pop("gajim_calls.controller", None)
    base = importlib.import_module("gajim_calls.controller").CallController

    class Runtime(base):
        def __init__(self, plugin) -> None:
            super().__init__(plugin)
            from gajim_calls.incoming import RemoteCandidateBuffer

            self._early_remote_candidates = RemoteCandidateBuffer()
            self.window = Window()
            self.module = Module()
            self.cancelled_timeouts = 0
            self.armed_timeouts: list[str] = []
            self.media_starts: list[tuple[bool, SessionDescription | None]] = []

        def _module(self, account: str):
            assert account == "acc"
            return self.module

        def _own_jid(self, account: str) -> str:
            assert account == "acc"
            return "me@example.test/Gajim"

        def _get_window(self):
            return self.window

        def _stop_incoming_alerts(self, sid=None) -> None:
            self.plugin.incoming_call_stopped(sid)

        def _cancel_phase_timeout(self) -> None:
            self.cancelled_timeouts += 1

        def _arm_phase_timeout(self, phase: str) -> None:
            self.armed_timeouts.append(phase)

        def accepts_jingle_sender(self, account, sid, sender, action) -> bool:
            context = self.context
            return (
                context is not None
                and context.account == account
                and context.sid == sid
                and context.peer_full == sender
            )

        def _start_media(
            self,
            *,
            offerer: bool,
            remote_offer: SessionDescription | None = None,
        ) -> None:
            self.media_starts.append((offerer, remote_offer))
            self.media = Media()
            if self.context is not None:
                self._early_remote_candidates.flush_to(self.media, self.context.sid)

        def _finish_local(self, state: CallState, *, hide: bool = True) -> None:
            context = self.context
            if self.media is not None:
                self.media.close()
                self.media = None
            if context is not None:
                context.state = state
                self._early_remote_candidates.discard(context.sid)
            if hide:
                self.window.close_call()

        def _cleanup(self, terminal: bool = True) -> None:
            self.context = None
            self._early_remote_candidates.clear()

    return Runtime


def connected_controller(monkeypatch):
    cls = with_call_waiting(runtime_base(monkeypatch))
    plugin = Plugin()
    controller = cls(plugin)
    context = CallContext(
        "acc",
        "active",
        "first@example.test",
        ("audio",),
        False,
        peer_full="first@example.test/Phone",
        state=CallState.CONNECTED,
        initiator="me@example.test/Gajim",
        responder="first@example.test/Phone",
    )
    context.metadata["signaling"] = "jmi"
    controller.context = context
    controller.media = Media()
    return controller


def test_jmi_call_waits_while_existing_call_stays_connected(monkeypatch):
    controller = connected_controller(monkeypatch)
    old_media = controller.media

    assert controller.handle_jmi(
        "acc",
        "second@example.test/Phone",
        JMIEvent("propose", "waiting", media=("audio",)),
    )

    assert controller.context is not None
    assert controller.context.sid == "active"
    assert controller.context.state is CallState.CONNECTED
    assert controller.media is old_media
    assert controller.waiting_context is not None
    assert controller.waiting_context.sid == "waiting"
    assert controller.module.jmi[-1]["action"] == "ringing"
    assert controller.plugin.started[-1][1] == "waiting"
    assert controller.window.incoming[-1] == ("second@example.test", False)
    assert controller.plugin.active[-1] == (True, True)
    assert controller._waiting_timeout_sid == "waiting"


def test_accepting_waiting_call_ends_current_then_promotes_waiting(monkeypatch):
    controller = connected_controller(monkeypatch)
    old_media = controller.media
    controller.handle_jmi(
        "acc",
        "second@example.test/Phone",
        JMIEvent("propose", "waiting", media=("audio",)),
    )

    controller.accept()

    assert old_media is not None and old_media.closed
    assert controller.context is not None
    assert controller.context.sid == "waiting"
    assert controller.context.state is CallState.NEGOTIATING
    assert controller.waiting_context is None
    assert any(
        item["action"] == "session-terminate" and item["sid"] == "active"
        for item in controller.module.jingle
    )
    assert any(
        item["action"] == "finish" and item["sid"] == "active"
        for item in controller.module.jmi
    )
    assert controller.module.jmi[-1]["action"] == "proceed"
    assert controller.module.jmi[-1]["sid"] == "waiting"


def test_declining_waiting_call_keeps_current_media_and_state(monkeypatch):
    controller = connected_controller(monkeypatch)
    old_media = controller.media
    controller.handle_jmi(
        "acc",
        "second@example.test/Phone",
        JMIEvent("propose", "waiting", media=("audio",)),
    )

    controller.decline()

    assert controller.context is not None
    assert controller.context.sid == "active"
    assert controller.context.state is CallState.CONNECTED
    assert controller.media is old_media
    assert old_media is not None and not old_media.closed
    assert controller.waiting_context is None
    assert controller.module.jmi[-1]["action"] == "reject"
    assert controller.module.jmi[-1]["sid"] == "waiting"
    assert controller.module.jmi[-1]["reason"] == "busy"
    assert controller.plugin.active[-1] == (True, True)


def test_closing_waiting_window_declines_waiting_and_keeps_active_call(monkeypatch):
    controller = connected_controller(monkeypatch)
    old_context = controller.context
    old_media = controller.media
    controller.handle_jmi(
        "acc",
        "second@example.test/Phone",
        JMIEvent("propose", "waiting", media=("audio",)),
    )

    controller.close_call_window()

    assert controller.context is old_context
    assert controller.context is not None
    assert controller.context.state is CallState.CONNECTED
    assert controller.media is old_media
    assert old_media is not None and not old_media.closed
    assert controller.waiting_context is None
    assert controller.module.jmi[-1]["action"] == "reject"
    assert controller.module.jmi[-1]["sid"] == "waiting"
    assert controller.module.jmi[-1]["reason"] == "busy"
    assert controller.plugin.active[-1] == (True, True)


def test_closing_normal_call_window_still_hangs_up_current_call(monkeypatch):
    controller = connected_controller(monkeypatch)
    old_media = controller.media

    controller.close_call_window()

    assert controller.context is not None
    assert controller.context.state is CallState.ENDED
    assert old_media is not None and old_media.closed
    assert any(
        item["action"] == "session-terminate" and item["sid"] == "active"
        for item in controller.module.jingle
    )


def test_direct_jingle_waiting_offer_is_owned_and_preserved(monkeypatch):
    controller = connected_controller(monkeypatch)
    offer = description()
    event = JingleEvent(
        action="session-initiate",
        sid="direct-waiting",
        initiator="second@example.test/Phone",
        responder="me@example.test/Gajim",
        description=offer,
    )

    controller.handle_jingle("acc", "second@example.test/Phone", event)

    assert controller.context is not None and controller.context.sid == "active"
    assert controller.waiting_context is not None
    assert controller.waiting_context.sid == "direct-waiting"
    assert controller.owns_sid("acc", "direct-waiting")

    controller.accept()
    assert controller.context is not None
    assert controller.context.sid == "direct-waiting"
    assert controller.context.state is CallState.NEGOTIATING
    assert controller.media_starts[-1] == (False, offer)


def test_waiting_direct_jingle_early_ice_is_flushed_after_accept(monkeypatch):
    controller = connected_controller(monkeypatch)
    peer = "second@example.test/Phone"
    offer = description()
    controller.handle_jingle(
        "acc",
        peer,
        JingleEvent("session-initiate", "direct-waiting", peer, None, offer),
    )
    controller.handle_jingle(
        "acc", peer, candidate_event("direct-waiting", peer)
    )
    assert controller._early_remote_candidates.count("direct-waiting") == 1

    controller.accept()

    assert controller.media is not None
    assert len(controller.media.candidates) == 1
    assert controller._early_remote_candidates.count("direct-waiting") == 0


def test_third_incoming_call_is_rejected_busy(monkeypatch):
    controller = connected_controller(monkeypatch)
    controller.handle_jmi(
        "acc",
        "second@example.test/Phone",
        JMIEvent("propose", "waiting", media=("audio",)),
    )

    controller.handle_jmi(
        "acc",
        "third@example.test/Phone",
        JMIEvent("propose", "third", media=("audio",)),
    )

    assert controller.waiting_context is not None
    assert controller.waiting_context.sid == "waiting"
    assert controller.module.jmi[-1]["action"] == "reject"
    assert controller.module.jmi[-1]["sid"] == "third"
    assert controller.module.jmi[-1]["reason"] == "busy"


def test_direct_call_during_negotiation_is_rejected_not_queued(monkeypatch):
    controller = connected_controller(monkeypatch)
    controller.context.state = CallState.NEGOTIATING
    old_context = controller.context
    old_media = controller.media
    peer = "second@example.test/Phone"

    controller.handle_jingle(
        "acc",
        peer,
        JingleEvent("session-initiate", "other", peer, None, description()),
    )

    assert controller.context is old_context
    assert controller.media is old_media
    assert controller.waiting_context is None
    assert controller.module.jingle[-1]["action"] == "session-terminate"
    assert controller.module.jingle[-1]["sid"] == "other"
    assert controller.module.jingle[-1]["reason"] == "busy"


def test_waiting_timeout_rejects_waiting_only(monkeypatch):
    controller = connected_controller(monkeypatch)
    old_context = controller.context
    old_media = controller.media
    controller.handle_jmi(
        "acc",
        "second@example.test/Phone",
        JMIEvent("propose", "waiting", media=("audio",)),
    )

    assert controller._on_waiting_timeout("waiting") is False

    assert controller.context is old_context
    assert controller.context is not None
    assert controller.context.state is CallState.CONNECTED
    assert controller.media is old_media
    assert old_media is not None and not old_media.closed
    assert controller.waiting_context is None
    assert controller.module.jmi[-1]["action"] == "reject"
    assert controller.module.jmi[-1]["sid"] == "waiting"
    assert controller.module.jmi[-1]["reason"] == "timeout"
    assert controller.plugin.active[-1] == (True, True)


def test_active_call_ending_promotes_waiting_call_to_normal_ringing(monkeypatch):
    controller = connected_controller(monkeypatch)
    peer = "second@example.test/Phone"
    controller.handle_jmi(
        "acc", peer, JMIEvent("propose", "waiting", media=("audio",))
    )

    controller._finish_local(CallState.ENDED)

    assert controller.context is not None
    assert controller.context.sid == "waiting"
    assert controller.context.state is CallState.RINGING
    assert controller.waiting_context is None
    assert controller.armed_timeouts[-1] == "ringing"
