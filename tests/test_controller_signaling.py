from __future__ import annotations

import importlib
import sys
import types

from gajim_calls.protocol import JMIEvent, JingleEvent, build_jingle, parse_jingle, xml_text
from gajim_calls.sdp import Codec, Fingerprint, MediaSection, SessionDescription
from gajim_calls.state import CallState


class _FakeJID:
    def __init__(self, value: str) -> None:
        self.value = value

    @classmethod
    def from_string(cls, value: str):
        return cls(value)

    def new_as_bare(self):
        return type(self)(self.value.split("/", 1)[0])

    def __str__(self) -> str:
        return self.value


class _Window:
    def __init__(self) -> None:
        self.statuses: list[str] = []
        self.connected_calls: list[bool] = []
        self.outgoing: list[tuple[str, bool]] = []
        self.incoming: list[tuple[str, bool]] = []
        self.closed = 0

    def set_status(self, value: str) -> None:
        self.statuses.append(value)

    def present(self) -> None:
        pass

    def show_outgoing(self, peer: str, video: bool) -> None:
        self.outgoing.append((peer, video))

    def show_incoming(self, peer: str, video: bool) -> None:
        self.incoming.append((peer, video))

    def connected(self, video: bool) -> None:
        self.connected_calls.append(video)

    def close_call(self) -> None:
        self.closed += 1


class _Media:
    def __init__(self) -> None:
        self.remote_answers: list[SessionDescription] = []
        self.candidates: list[tuple[str, str]] = []
        self.closed = False

    def set_remote_answer(self, description: SessionDescription) -> None:
        self.remote_answers.append(description)

    def add_remote_candidate(self, mid: str, candidate: str) -> None:
        self.candidates.append((mid, candidate))

    def close(self) -> None:
        self.closed = True


class _SignalModule:
    def __init__(self) -> None:
        self.jmi: list[dict] = []
        self.jingle: list[dict] = []

    def send_jmi(self, to: str, action: str, sid: str, **kwargs) -> None:
        self.jmi.append({"to": to, "action": action, "sid": sid, **kwargs})

    def send_jingle(
        self,
        to: str,
        action: str,
        sid: str,
        *,
        initiator: str,
        responder: str | None,
        description: SessionDescription | None = None,
        creator: str = "initiator",
        reason: str | None = None,
    ) -> None:
        payload = build_jingle(
            action,
            sid,
            initiator=initiator,
            responder=responder,
            description=description,
            creator=creator,
            reason=reason,
        )
        event = parse_jingle(xml_text(payload))
        assert event is not None
        self.jingle.append({"to": to, "event": event})


class _Plugin:
    config = {"stun_server": "", "turn_server": ""}


def _audio_description(*, setup: str = "actpass") -> SessionDescription:
    return SessionDescription(
        media=[
            MediaSection(
                media="audio",
                mid="audio",
                codecs=[Codec(111, "OPUS", 48000, 2)],
                ice_ufrag="ufrag",
                ice_pwd="abcdefghijklmnopqrstuv",
                fingerprint=Fingerprint("sha-256", "AA:BB:CC", setup),
            )
        ],
        bundle=("audio",),
    )


def _controller_class(monkeypatch):
    nbxmpp = types.ModuleType("nbxmpp")
    nbxmpp_protocol = types.ModuleType("nbxmpp.protocol")
    nbxmpp_protocol.JID = _FakeJID
    nbxmpp.protocol = nbxmpp_protocol
    monkeypatch.setitem(sys.modules, "nbxmpp", nbxmpp)
    monkeypatch.setitem(sys.modules, "nbxmpp.protocol", nbxmpp_protocol)

    gajim = types.ModuleType("gajim")
    common = types.ModuleType("gajim.common")
    common.app = types.SimpleNamespace()
    gajim.common = common
    monkeypatch.setitem(sys.modules, "gajim", gajim)
    monkeypatch.setitem(sys.modules, "gajim.common", common)

    sys.modules.pop("gajim_calls.controller", None)
    return importlib.import_module("gajim_calls.controller").CallController


def _harness(monkeypatch):
    base = _controller_class(monkeypatch)

    class Harness(base):
        def __init__(self) -> None:
            super().__init__(_Plugin())
            self.window = _Window()
            self.signaling = _SignalModule()
            self.media_starts: list[tuple[bool, SessionDescription | None]] = []

        def _module(self, account: str):
            assert account == "acc"
            return self.signaling

        def _own_jid(self, account: str) -> str:
            assert account == "acc"
            return "desktop@example.test/Gajim"

        def _get_window(self):
            return self.window

        def _start_media(
            self,
            *,
            offerer: bool,
            remote_offer: SessionDescription | None = None,
        ) -> None:
            self.media_starts.append((offerer, remote_offer))
            self.media = _Media()

    return Harness()


def test_outgoing_conversations_audio_handshake_reaches_connected(monkeypatch):
    controller = _harness(monkeypatch)
    peer_full = "phone@example.test/Conversations"

    controller.start_outgoing("acc", "phone@example.test", video=False)
    context = controller.context
    assert context is not None
    sid = context.sid
    assert context.state is CallState.PROPOSING
    assert controller.signaling.jmi[-1]["action"] == "propose"
    assert controller.signaling.jmi[-1]["media"] == ("audio",)

    assert controller.handle_jmi("acc", peer_full, JMIEvent("ringing", sid))
    assert controller.handle_jmi("acc", peer_full, JMIEvent("proceed", sid))
    assert context.state is CallState.NEGOTIATING
    assert controller.media_starts == [(True, None)]

    offer = _audio_description()
    controller._on_local_description(offer)
    initiated = controller.signaling.jingle[-1]["event"]
    assert initiated.action == "session-initiate"
    assert initiated.description.bundle == ("audio",)

    answer = _audio_description(setup="active")
    controller.handle_jingle(
        "acc",
        peer_full,
        JingleEvent(
            action="session-accept",
            sid=sid,
            initiator="desktop@example.test/Gajim",
            responder=peer_full,
            description=answer,
        ),
    )
    assert controller.media is not None
    assert controller.media.remote_answers == [answer]

    controller._on_media_connected()
    assert context.state is CallState.CONNECTED
    assert controller.window.connected_calls == [False]


def test_incoming_conversations_audio_handshake_reaches_connected(monkeypatch):
    controller = _harness(monkeypatch)
    peer_full = "phone@example.test/Conversations"
    sid = "incoming-audio"

    assert controller.handle_jmi(
        "acc",
        peer_full,
        JMIEvent("propose", sid, media=("audio",)),
    )
    context = controller.context
    assert context is not None
    assert context.state is CallState.RINGING
    assert controller.signaling.jmi[-1]["action"] == "ringing"

    controller.accept()
    assert context.state is CallState.NEGOTIATING
    assert controller.signaling.jmi[-1]["action"] == "proceed"
    assert controller.media_starts == []

    offer = _audio_description()
    controller.handle_jingle(
        "acc",
        peer_full,
        JingleEvent(
            action="session-initiate",
            sid=sid,
            initiator=peer_full,
            responder="desktop@example.test/Gajim",
            description=offer,
        ),
    )
    assert controller.media_starts == [(False, offer)]

    answer = _audio_description(setup="active")
    controller._on_local_description(answer)
    accepted = controller.signaling.jingle[-1]["event"]
    assert accepted.action == "session-accept"
    assert accepted.description.bundle == ("audio",)

    controller._on_media_connected()
    assert context.state is CallState.CONNECTED
    assert controller.window.connected_calls == [False]
