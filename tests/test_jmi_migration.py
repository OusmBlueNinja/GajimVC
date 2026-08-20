from __future__ import annotations

from xml.etree import ElementTree as ET

from gajim_calls.constants import NS_JMI
from gajim_calls.jmi_migration import add_migrated_element, with_jmi_migration
from gajim_calls.protocol import JMIEvent, build_jmi, xml_text
from gajim_calls.state import CallContext, CallState


class Media:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class Candidates:
    def __init__(self) -> None:
        self.discarded: list[str] = []

    def discard(self, sid: str) -> None:
        self.discarded.append(sid)


class Module:
    def __init__(self) -> None:
        self.jmi: list[dict] = []
        self.jingle: list[dict] = []

    def send_jmi(self, to: str, action: str, sid: str, **kwargs) -> None:
        self.jmi.append({"to": to, "action": action, "sid": sid, **kwargs})

    def send_jingle(self, to: str, action: str, sid: str, **kwargs) -> None:
        self.jingle.append({"to": to, "action": action, "sid": sid, **kwargs})


class Window:
    def __init__(self) -> None:
        self.statuses: list[str] = []

    def set_status(self, text: str) -> None:
        self.statuses.append(text)


class Plugin:
    def __init__(self) -> None:
        self.active: list[tuple[bool, bool]] = []

    def set_call_active(self, active: bool, *, connected: bool = False) -> None:
        self.active.append((active, connected))


class Base:
    def __init__(self) -> None:
        self.plugin = Plugin()
        self.module = Module()
        self.window = Window()
        self.media = Media()
        self._early_remote_candidates = Candidates()
        self._pending_offer = object()
        self._local_description = object()
        self._pending_local_candidates = [("audio", "candidate")]
        self.cancelled_timeouts = 0
        self.armed_timeouts: list[str] = []
        self.stopped_alerts: list[str] = []
        self.delegated: list[tuple[str, str, str]] = []
        self.context = CallContext(
            account="acc",
            sid="old-call",
            peer_bare="phone@example.test",
            peer_full="phone@example.test/Phone",
            media=("audio",),
            incoming=False,
            state=CallState.CONNECTED,
            initiator="me@example.test/Gajim",
            responder="phone@example.test/Phone",
        )
        self.context.metadata["signaling"] = "jmi"

    @staticmethod
    def _bare(jid: str) -> str:
        return jid.split("/", 1)[0]

    def _module(self, account: str):
        assert account == "acc"
        return self.module

    def _own_jid(self, account: str) -> str:
        assert account == "acc"
        return "me@example.test/Gajim"

    def _get_window(self):
        return self.window

    def _jingle_started(self, context: CallContext) -> bool:
        return context is self.context and self._local_description is not None

    def _cancel_phase_timeout(self) -> None:
        self.cancelled_timeouts += 1

    def _arm_phase_timeout(self, phase: str) -> None:
        self.armed_timeouts.append(phase)

    def _stop_incoming_alerts(self, sid: str) -> None:
        self.stopped_alerts.append(sid)

    def handle_jmi(self, account: str, from_jid: str, event: JMIEvent) -> bool:
        self.delegated.append((account, from_jid, event.id))
        return False


def test_migrated_finish_xml_contains_new_session_id():
    node = build_jmi("finish", "old-call", reason="expired")
    add_migrated_element(node, "new-call")

    parsed = ET.fromstring(xml_text(node))
    migrated = parsed.find(f"{{{NS_JMI}}}migrated")
    assert migrated is not None
    assert migrated.attrib == {"to": "new-call"}


def test_same_peer_proposal_transparently_migrates_active_jmi_session():
    controller = with_jmi_migration(Base)()
    old_media = controller.media

    assert controller.handle_jmi(
        "acc",
        "phone@example.test/Tablet",
        JMIEvent("propose", "new-call", media=("audio",)),
    )

    assert old_media.closed is True
    assert controller.context.sid == "new-call"
    assert controller.context.peer_full == "phone@example.test/Tablet"
    assert controller.context.state is CallState.NEGOTIATING
    assert controller.context.incoming is True
    assert controller.delegated == []
    assert controller._early_remote_candidates.discarded == ["old-call"]
    assert controller._pending_offer is None
    assert controller._local_description is None
    assert controller._pending_local_candidates == []

    assert controller.module.jingle == [
        {
            "to": "phone@example.test/Phone",
            "action": "session-terminate",
            "sid": "old-call",
            "initiator": "me@example.test/Gajim",
            "responder": "phone@example.test/Phone",
            "reason": "expired",
        }
    ]
    assert controller.module.jmi[0] == {
        "to": "phone@example.test/Phone",
        "action": "finish",
        "sid": "old-call",
        "reason": "expired",
        "migrated_to": "new-call",
    }
    assert controller.module.jmi[-1] == {
        "to": "phone@example.test/Tablet",
        "action": "proceed",
        "sid": "new-call",
    }
    assert controller.armed_timeouts == ["negotiating"]
    assert controller.window.statuses == ["Connecting…"]
    assert controller.plugin.active == [(True, False)]


def test_different_peer_proposal_remains_delegated_to_call_waiting_layer():
    controller = with_jmi_migration(Base)()

    handled = controller.handle_jmi(
        "acc",
        "other@example.test/Phone",
        JMIEvent("propose", "other-call", media=("audio",)),
    )

    assert handled is False
    assert controller.context.sid == "old-call"
    assert controller.media.closed is False
    assert controller.module.jmi == []
    assert controller.module.jingle == []
    assert controller.delegated == [
        ("acc", "other@example.test/Phone", "other-call")
    ]
