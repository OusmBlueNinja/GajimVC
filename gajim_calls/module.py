"""Gajim network module for Jingle Message Initiation and Jingle IQs."""

from __future__ import annotations

from typing import Any
import logging

import nbxmpp
from nbxmpp.protocol import Iq, Message
from nbxmpp.simplexml import Node
from nbxmpp.structs import StanzaHandler

from gajim.common.modules.base import BaseModule

from .constants import NS_JINGLE, NS_JMI
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


class CallsModule(BaseModule):
    def __init__(self, client) -> None:
        BaseModule.__init__(self, client, plugin=True)
        # Priority 5 deliberately runs before Gajim's legacy Jingle module. We
        # raise NodeProcessed only for RTP calls owned by this plugin.
        self.handlers = [
            StanzaHandler(
                name="message",
                ns=NS_JMI,
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
        if _controller.handle_jmi(self._account, str(from_jid), event):
            raise nbxmpp.NodeProcessed

    def _on_jingle_iq(self, _con, stanza, _properties) -> None:
        event = parse_jingle(str(stanza))
        if event is None or _controller is None:
            return

        owned = _controller.owns_sid(self._account, event.sid)
        rtp_offer = (
            event.action == "session-initiate"
            and any(section.media in {"audio", "video"} for section in event.description.media)
        )
        if not owned and not rtp_offer:
            # Leave non-call Jingle (for example file transfers) to Gajim.
            return

        from_jid = stanza.getFrom()
        if from_jid is None:
            return

        # Jingle actions are IQ-set and must be acknowledged even when the user
        # has not answered the ringing UI yet.
        response = stanza.buildReply("result")
        query = response.getQuery()
        if query is not None:
            response.delChild(query)
        self._send(response)

        _controller.handle_jingle(self._account, str(from_jid), event)
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
    ) -> None:
        message = Message(to=to_jid, typ="chat")
        message.addChild(
            node=Node(
                node=xml_text(
                    build_jmi(
                        action,
                        sid,
                        media=media,
                        reason=reason,
                        tie_break=tie_break,
                    )
                )
            )
        )
        message.addChild(node=Node(node=xml_text(store_hint())))
        self._send(message)

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
        self._send(iq)


def get_instance(*args: Any, **kwargs: Any) -> tuple[CallsModule, str]:
    return CallsModule(*args, **kwargs), name
