"""XEP-0353 existing-session migration support."""

from __future__ import annotations

import logging
from typing import TypeVar
from xml.etree import ElementTree as ET

from .constants import NS_JMI
from .protocol import JMIEvent
from .state import CallContext, CallState

log = logging.getLogger("gajim.p.gajim_calls.jmi_migration")

T = TypeVar("T")


def add_migrated_element(node: ET.Element, new_sid: str) -> None:
    """Attach the XEP-0353 migration marker to a finish message."""
    if not new_sid:
        raise ValueError("migrated session id is required")
    ET.SubElement(node, f"{{{NS_JMI}}}migrated", {"to": new_sid})


def with_jmi_migration(base: type[T]) -> type[T]:
    """Decorate a runtime controller with same-peer JMI migration behavior."""
    if getattr(base, "_gajim_calls_jmi_migration", False):
        return base

    class JMIMigrationController(base):  # type: ignore[misc, valid-type]
        _gajim_calls_jmi_migration = True

        def _jingle_has_started_for_migration(self, context: CallContext) -> bool:
            callback = getattr(self, "_jingle_started", None)
            if callable(callback):
                return bool(callback(context))
            return context.state == CallState.CONNECTED

        def _try_jmi_migration(
            self, account: str, from_jid: str, event: JMIEvent
        ) -> bool:
            context = self.context
            if (
                event.action != "propose"
                or context is None
                or context.account != account
                or context.state not in {CallState.NEGOTIATING, CallState.CONNECTED}
                or context.metadata.get("signaling") != "jmi"
                or context.peer_bare != self._bare(from_jid)
            ):
                return False

            old_sid = context.sid
            module = self._module(account)
            old_peer = context.peer_full or context.peer_bare
            try:
                if (
                    context.peer_full is not None
                    and self._jingle_has_started_for_migration(context)
                ):
                    module.send_jingle(
                        context.peer_full,
                        "session-terminate",
                        old_sid,
                        initiator=context.initiator or self._own_jid(account),
                        responder=context.responder,
                        reason="expired",
                    )
                module.send_jmi(
                    old_peer,
                    "finish",
                    old_sid,
                    reason="expired",
                    migrated_to=event.id,
                )
            except Exception:
                log.exception(
                    "Unable to signal JMI migration old_sid=%s new_sid=%s",
                    old_sid,
                    event.id,
                )

            self._cancel_phase_timeout()
            self._stop_incoming_alerts(old_sid)
            if self.media is not None:
                self.media.close()
                self.media = None
            self._early_remote_candidates.discard(old_sid)
            self._pending_offer = None
            self._local_description = None
            self._pending_local_candidates = []

            media = tuple(
                item for item in event.media if item in {"audio", "video"}
            ) or ("audio",)
            replacement = CallContext(
                account=account,
                sid=event.id,
                peer_bare=self._bare(from_jid),
                peer_full=from_jid,
                media=media,  # type: ignore[arg-type]
                incoming=True,
            )
            replacement.transition(CallState.RINGING)
            replacement.transition(CallState.NEGOTIATING)
            replacement.initiator = from_jid
            replacement.responder = self._own_jid(account)
            replacement.metadata["signaling"] = "jmi"
            self.context = replacement

            module.send_jmi(from_jid, "proceed", event.id)
            self._arm_phase_timeout("negotiating")
            window = self._get_window()
            window.set_status("Connecting…")
            callback = getattr(self.plugin, "set_call_active", None)
            if callable(callback):
                callback(True, connected=False)
            log.info(
                "Migrated same-peer JMI session old_sid=%s new_sid=%s peer=%s",
                old_sid,
                event.id,
                replacement.peer_bare,
            )
            return True

        def handle_jmi(self, account: str, from_jid: str, event: JMIEvent) -> bool:
            if self._try_jmi_migration(account, from_jid, event):
                return True
            return super().handle_jmi(account, from_jid, event)

    JMIMigrationController.__name__ = base.__name__
    JMIMigrationController.__qualname__ = base.__qualname__
    JMIMigrationController.__module__ = base.__module__
    return JMIMigrationController  # type: ignore[return-value]
