"""One-deep incoming call waiting for the runtime call controller."""

from __future__ import annotations

import logging
from typing import TypeVar

from .peer_validation import jingle_sender_allowed, jmi_sender_allowed
from .protocol import JMIEvent, JingleEvent
from .sdp import SessionDescription
from .state import CallContext, CallState

log = logging.getLogger("gajim.p.gajim_calls.call_waiting")

_TERMINAL = {CallState.ENDED, CallState.FAILED}
_WAITING_TIMEOUT_SECONDS = 60
T = TypeVar("T")


def with_call_waiting(base: type[T]) -> type[T]:
    """Return an idempotently decorated runtime controller class."""
    if getattr(base, "_gajim_calls_call_waiting", False):
        return base

    class CallWaitingController(base):  # type: ignore[misc, valid-type]
        _gajim_calls_call_waiting = True

        def __init__(self, plugin) -> None:
            super().__init__(plugin)
            self._waiting_context: CallContext | None = None
            self._waiting_offer: SessionDescription | None = None
            self._waiting_timeout_id: int | None = None
            self._waiting_timeout_sid: str | None = None

        @property
        def waiting_context(self) -> CallContext | None:
            return self._waiting_context

        def _is_active(self) -> bool:
            return self.context is not None and self.context.state not in _TERMINAL

        def _is_connected(self) -> bool:
            return self.context is not None and self.context.state == CallState.CONNECTED

        def owns_sid(self, account: str, sid: str) -> bool:
            if super().owns_sid(account, sid):
                return True
            waiting = self._waiting_context
            return (
                waiting is not None
                and waiting.account == account
                and waiting.sid == sid
                and waiting.state not in _TERMINAL
            )

        def accepts_jingle_sender(
            self, account: str, sid: str, sender: str, action: str
        ) -> bool:
            waiting = self._waiting_context
            if waiting is not None and waiting.sid == sid:
                if waiting.account != account or waiting.state in _TERMINAL:
                    return False
                return jingle_sender_allowed(
                    peer_bare=waiting.peer_bare,
                    peer_full=waiting.peer_full,
                    sender=sender,
                    action=action,
                    incoming=True,
                )
            return super().accepts_jingle_sender(account, sid, sender, action)

        def _cancel_waiting_timeout(self) -> None:
            source_id = self._waiting_timeout_id
            self._waiting_timeout_id = None
            self._waiting_timeout_sid = None
            if source_id is None:
                return
            try:
                from gi.repository import GLib

                GLib.source_remove(source_id)
            except Exception:
                log.debug("Unable to remove waiting-call timeout", exc_info=True)

        def _arm_waiting_timeout(self, sid: str) -> None:
            self._cancel_waiting_timeout()
            self._waiting_timeout_sid = sid
            try:
                from gi.repository import GLib

                self._waiting_timeout_id = GLib.timeout_add_seconds(
                    _WAITING_TIMEOUT_SECONDS,
                    self._on_waiting_timeout,
                    sid,
                )
            except (ImportError, ModuleNotFoundError):
                # Headless unit tests deliberately run without PyGObject. They
                # invoke _on_waiting_timeout synchronously instead.
                self._waiting_timeout_id = None

        def _on_waiting_timeout(self, sid: str) -> bool:
            if self._waiting_timeout_sid == sid:
                self._waiting_timeout_id = None
                self._waiting_timeout_sid = None
            waiting = self._waiting_context
            if waiting is None or waiting.sid != sid:
                return False
            log.info("Waiting call timed out sid=%s peer=%s", sid, waiting.peer_bare)
            self._decline_waiting(reason="timeout")
            return False

        def _start_waiting_alert(self, context: CallContext) -> None:
            callback = getattr(self.plugin, "incoming_call_started", None)
            if callable(callback):
                callback(
                    context.account,
                    context.sid,
                    context.peer_bare,
                    video=context.has_video,
                )

        def _show_waiting(
            self,
            account: str,
            from_jid: str,
            sid: str,
            media: tuple[str, ...],
            *,
            offer: SessionDescription | None,
            signaling: str,
        ) -> None:
            waiting = CallContext(
                account=account,
                sid=sid,
                peer_bare=self._bare(from_jid),
                peer_full=from_jid,
                media=media,  # type: ignore[arg-type]
                incoming=True,
            )
            waiting.transition(CallState.RINGING)
            waiting.initiator = from_jid
            waiting.responder = self._own_jid(account)
            waiting.metadata["signaling"] = signaling
            self._waiting_context = waiting
            self._waiting_offer = offer

            if signaling == "jmi":
                self._module(account).send_jmi(from_jid, "ringing", sid)

            self._start_waiting_alert(waiting)
            self._get_window().show_incoming(waiting.peer_bare, waiting.has_video)
            # show_incoming() marks the toolbar as a not-yet-connected call.
            # The original connected call is still live until Accept is clicked.
            self._restore_active_indicator()
            self._arm_waiting_timeout(sid)
            log.info("Incoming call waiting sid=%s peer=%s", sid, waiting.peer_bare)

        def _restore_active_indicator(self) -> None:
            context = self.context
            callback = getattr(self.plugin, "set_call_active", None)
            if callable(callback):
                callback(
                    context is not None and context.state not in _TERMINAL,
                    connected=context is not None and context.state == CallState.CONNECTED,
                )

        def _clear_waiting(self, *, restore_active: bool = True) -> None:
            waiting = self._waiting_context
            if waiting is None:
                return
            self._cancel_waiting_timeout()
            self._stop_incoming_alerts(waiting.sid)
            self._early_remote_candidates.discard(waiting.sid)
            self._waiting_context = None
            self._waiting_offer = None
            if self.window is not None:
                self.window.close_call()
            if restore_active:
                self._restore_active_indicator()

        def _decline_waiting(self, reason: str = "busy") -> None:
            waiting = self._waiting_context
            if waiting is None:
                return
            module = self._module(waiting.account)
            target = waiting.peer_full or waiting.peer_bare
            try:
                if waiting.metadata.get("signaling") == "jmi":
                    module.send_jmi(target, "reject", waiting.sid, reason=reason)
                elif waiting.peer_full is not None:
                    module.send_jingle(
                        waiting.peer_full,
                        "session-terminate",
                        waiting.sid,
                        initiator=waiting.initiator or waiting.peer_full,
                        responder=waiting.responder or self._own_jid(waiting.account),
                        reason="decline" if reason == "busy" else reason,
                    )
            except Exception:
                log.exception(
                    "Unable to decline waiting call sid=%s reason=%s",
                    waiting.sid,
                    reason,
                )
            self._clear_waiting()

        def _reject_extra_call(
            self,
            account: str,
            from_jid: str,
            event: JMIEvent | JingleEvent,
        ) -> None:
            module = self._module(account)
            if isinstance(event, JMIEvent):
                module.send_jmi(from_jid, "reject", event.id, reason="busy")
                return
            module.send_jingle(
                from_jid,
                "session-terminate",
                event.sid,
                initiator=event.initiator or from_jid,
                responder=self._own_jid(account),
                reason="busy",
            )

        def _end_active_for_switch(self) -> None:
            context = self.context
            if context is None:
                return
            module = self._module(context.account)
            peer = context.peer_full or context.peer_bare
            uses_jmi = context.metadata.get("signaling") == "jmi"

            try:
                if context.state == CallState.PROPOSING:
                    module.send_jmi(
                        context.peer_bare, "retract", context.sid, reason="cancel"
                    )
                elif context.state == CallState.RINGING and context.incoming:
                    if uses_jmi:
                        module.send_jmi(peer, "reject", context.sid, reason="busy")
                    elif context.peer_full is not None:
                        module.send_jingle(
                            context.peer_full,
                            "session-terminate",
                            context.sid,
                            initiator=context.initiator or context.peer_full,
                            responder=context.responder
                            or self._own_jid(context.account),
                            reason="decline",
                        )
                else:
                    wire_reason = (
                        "success" if context.state == CallState.CONNECTED else "cancel"
                    )
                    if context.peer_full is not None:
                        module.send_jingle(
                            context.peer_full,
                            "session-terminate",
                            context.sid,
                            initiator=context.initiator
                            or self._own_jid(context.account),
                            responder=context.responder,
                            reason=wire_reason,
                        )
                    if uses_jmi:
                        module.send_jmi(
                            peer, "finish", context.sid, reason=wire_reason
                        )
            except Exception:
                log.exception("Unable to terminate active call while switching")

            self._cancel_phase_timeout()
            self._stop_incoming_alerts(context.sid)
            if self.media is not None:
                self.media.close()
                self.media = None
            self._early_remote_candidates.discard(context.sid)
            self._pending_offer = None
            self._local_description = None
            self._pending_local_candidates = []
            self.context = None

        def handle_jmi(self, account: str, from_jid: str, event: JMIEvent) -> bool:
            waiting = self._waiting_context
            if waiting is not None and event.id == waiting.sid:
                if waiting.account != account or not jmi_sender_allowed(
                    peer_bare=waiting.peer_bare, sender=from_jid
                ):
                    return False
                if event.action in {"reject", "retract", "finish"}:
                    self._clear_waiting()
                return True

            if event.action == "propose" and self._is_active():
                context = self.context
                assert context is not None
                same_peer_tie_break = (
                    context.state == CallState.PROPOSING
                    and context.account == account
                    and context.peer_bare == self._bare(from_jid)
                )
                if same_peer_tie_break:
                    return super().handle_jmi(account, from_jid, event)
                if context.state != CallState.CONNECTED or waiting is not None:
                    self._reject_extra_call(account, from_jid, event)
                    return True
                media = tuple(
                    item for item in event.media if item in {"audio", "video"}
                ) or ("audio",)
                self._show_waiting(
                    account,
                    from_jid,
                    event.id,
                    media,
                    offer=None,
                    signaling="jmi",
                )
                return True

            return super().handle_jmi(account, from_jid, event)

        def handle_jingle(
            self, account: str, from_jid: str, event: JingleEvent
        ) -> None:
            waiting = self._waiting_context
            if waiting is not None and event.sid == waiting.sid:
                if not self.accepts_jingle_sender(
                    account, event.sid, from_jid, event.action
                ):
                    return
                if event.action == "session-initiate":
                    waiting.peer_full = from_jid
                    waiting.initiator = from_jid
                    self._waiting_offer = event.description
                elif event.action == "transport-info":
                    self._early_remote_candidates.add_event(event)
                elif event.action == "session-terminate":
                    self._clear_waiting()
                return

            if (
                event.action == "session-initiate"
                and self._is_active()
                and (self.context is None or event.sid != self.context.sid)
            ):
                if not self._is_connected() or waiting is not None:
                    self._reject_extra_call(account, from_jid, event)
                    return
                media = tuple(
                    section.media
                    for section in event.description.media
                    if section.media in {"audio", "video"}
                ) or ("audio",)
                self._show_waiting(
                    account,
                    from_jid,
                    event.sid,
                    media,
                    offer=event.description,
                    signaling="jingle",
                )
                return

            super().handle_jingle(account, from_jid, event)

        def accept(self) -> None:
            waiting = self._waiting_context
            if waiting is not None:
                offer = self._waiting_offer
                self._cancel_waiting_timeout()
                self._stop_incoming_alerts(waiting.sid)
                self._end_active_for_switch()
                self.context = waiting
                self._pending_offer = offer
                self._waiting_context = None
                self._waiting_offer = None
                log.info("Switching to waiting call sid=%s", waiting.sid)
            super().accept()

        def decline(self) -> None:
            if self._waiting_context is not None:
                self._decline_waiting()
                return
            super().decline()

        def _finish_local(self, state: CallState, *, hide: bool = True) -> None:
            waiting = self._waiting_context
            waiting_offer = self._waiting_offer
            super()._finish_local(state, hide=hide)
            if waiting is not None:
                self._cancel_waiting_timeout()
                # The active call ended while another caller was waiting. Make
                # that call the normal ringing context instead of leaving a
                # special waiting state behind.
                self.context = waiting
                self._pending_offer = waiting_offer
                self._waiting_context = None
                self._waiting_offer = None
                self._get_window().show_incoming(
                    waiting.peer_bare, waiting.has_video
                )
                self._arm_phase_timeout("ringing")

        def _cleanup(self, terminal: bool = True) -> None:
            waiting = self._waiting_context
            if waiting is not None:
                self._stop_incoming_alerts(waiting.sid)
            self._cancel_waiting_timeout()
            self._waiting_context = None
            self._waiting_offer = None
            super()._cleanup(terminal=terminal)

    CallWaitingController.__name__ = base.__name__
    CallWaitingController.__qualname__ = base.__qualname__
    CallWaitingController.__module__ = base.__module__
    return CallWaitingController  # type: ignore[return-value]
