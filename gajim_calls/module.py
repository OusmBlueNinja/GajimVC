"""Gajim network module for Jingle Message Initiation and Jingle IQs."""

from __future__ import annotations

from typing import Any
import logging

import nbxmpp
from nbxmpp.protocol import Iq, Message, Presence
from nbxmpp.simplexml import Node
from nbxmpp.structs import StanzaHandler

from gajim.common.modules.base import BaseModule

from .constants import NS_JINGLE
from .incoming import should_claim_jingle
from .jmi_migration import add_migrated_element
from .protocol import (
    build_jingle,
    build_jmi,
    parse_jingle,
    parse_jmi,
    store_hint,
    xml_text,
)
from .sdp import SessionDescription

log = logging.getLogger("gajim.p.gajim_calls.module")

name = "DeauthCalls"
_controller: Any = None


def set_controller(controller: Any) -> None:
    global _controller
    _controller = controller


def _log_candidates(prefix: str, description: SessionDescription) -> None:
    total = sum(len(section.candidates) for section in description.media)
    log.info("%s ICE candidates=%d", prefix, total)
    for section in description.media:
        log.info(
            "%s ICE mid=%s ufrag=%s pwd=%s candidates=%d",
            prefix,
            section.mid,
            section.ice_ufrag,
            "set" if section.ice_pwd else "missing",
            len(section.candidates),
        )
        for candidate in section.candidates:
            log.info(
                "%s ICE candidate mid=%s type=%s protocol=%s ip=%s port=%s component=%s",
                prefix,
                section.mid,
                candidate.type,
                candidate.protocol,
                candidate.ip,
                candidate.port,
                candidate.component,
            )


class CallsModule(BaseModule):
    def __init__(self, client) -> None:
        BaseModule.__init__(self, client, plugin=True)
        self.handlers = [
            StanzaHandler(
                name="message",
                callback=self._on_jmi,
                priority=5,
            ),
            StanzaHandler(
                name="iq",
                typ="set",
                ns=NS_JINGLE,
                callback=self._on_jingle_iq,
                priority=5,
            ),
        ]

    def _send(self, stanza) -> None:
        self._client.connection.send(stanza)

    def _on_jmi(self, _con, stanza, _properties) -> None:
        event = parse_jmi(str(stanza))
        if event is None or _controller is None:
            return
        from_jid = stanza.getFrom()
        if from_jid is None:
            return
        log.info(
            "RX JMI %s sid=%s from=%s",
            event.action,
            event.id,
            from_jid,
        )
        if _controller.handle_jmi(self._account, str(from_jid), event):
            raise nbxmpp.NodeProcessed

    def _on_jingle_iq(self, _con, stanza, _properties) -> None:
        event = parse_jingle(str(stanza))
        if event is None or _controller is None:
            return

        from_jid = stanza.getFrom()
        if from_jid is None:
            return
        sender = str(from_jid)

        owned = _controller.owns_sid(self._account, event.sid)
        if not should_claim_jingle(event, owns_sid=owned):
            return

        # Validate the peer before ACKing an owned session. Returning here lets
        # other Jingle handlers treat the stanza as unknown/unrelated instead of
        # allowing a guessed/stale SID to inject media state into this call.
        if owned:
            accepts_sender = getattr(_controller, "accepts_jingle_sender", None)
            if callable(accepts_sender) and not accepts_sender(
                self._account, event.sid, sender, event.action
            ):
                log.warning(
                    "Ignoring Jingle %s sid=%s from unexpected sender=%s",
                    event.action,
                    event.sid,
                    sender,
                )
                return

        log.info(
            "RX Jingle %s sid=%s from=%s media=%s",
            event.action,
            event.sid,
            from_jid,
            ",".join(section.media for section in event.description.media) or "none",
        )
        _log_candidates("RX", event.description)

        response = stanza.buildReply("result")
        query = response.getQuery()
        if query is not None:
            response.delChild(query)
        self._send(response)

        _controller.handle_jingle(self._account, sender, event)
        raise nbxmpp.NodeProcessed

    def send_jmi(
        self,
        to_jid: str,
        action: str,
        sid: str,
        *,
        media: tuple[str, ...] = (),
        reason: str | None = None,
        tie_break: bool = False,
        migrated_to: str | None = None,
    ) -> None:
        message = Message(to=to_jid, typ="chat")
        payload = build_jmi(
            action,
            sid,
            media=media,
            reason=reason,
            tie_break=tie_break,
        )
        if migrated_to is not None:
            add_migrated_element(payload, migrated_to)
        message.addChild(node=Node(node=xml_text(payload)))
        message.addChild(node=Node(node=xml_text(store_hint())))
        log.info("TX JMI %s sid=%s to=%s", action, sid, to_jid)
        self._send(message)

        if action == "proceed":
            log.info("TX directed presence to=%s", to_jid)
            self._send(Presence(to=to_jid))

    def send_jingle(
        self,
        to_jid: str,
        action: str,
        sid: str,
        *,
        initiator: str,
        responder: str | None,
        description: SessionDescription | None = None,
        creator: str = "initiator",
        reason: str | None = None,
    ) -> None:
        iq = Iq(to=to_jid, typ="set")
        payload = build_jingle(
            action,
            sid,
            initiator=initiator,
            responder=responder,
            description=description,
            creator=creator,
            reason=reason,
        )
        iq.addChild(node=Node(node=xml_text(payload)))
        log.info("TX Jingle %s sid=%s to=%s", action, sid, to_jid)
        if description is not None:
            _log_candidates("TX", description)
        self._send(iq)


def get_instance(*args: Any, **kwargs: Any) -> tuple[CallsModule, str]:
    return CallsModule(*args, **kwargs), name
