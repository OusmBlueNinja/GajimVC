"""Runtime controller fixes that depend on Gajim/GTK."""

from __future__ import annotations

import logging

from .controller import CallController
from .incoming import RemoteCandidateBuffer
from .protocol import JMIEvent, JingleEvent
from .sdp import SessionDescription
from .state import CallState

log = logging.getLogger("gajim.p.gajim_calls.controller")

_TERMINAL_STATES = {CallState.ENDED, CallState.FAILED}


class RuntimeCallController(CallController):
    """CallController with runtime UI, alert, and trickle-ICE behaviour."""

    def __init__(self, plugin) -> None:
        super().__init__(plugin)
        self._early_remote_candidates = RemoteCandidateBuffer()

    def _start_incoming_alerts(self) -> None:
        context = self.context
        if (
            context is None
            or not context.incoming
            or context.state != CallState.RINGING
        ):
            return
        callback = getattr(self.plugin, "incoming_call_started", None)
        if callable(callback):
            callback(
                context.account,
                context.sid,
                context.peer_bare,
                video=context.has_video,
            )

    def _stop_incoming_alerts(self, sid: str | None = None) -> None:
        callback = getattr(self.plugin, "incoming_call_stopped", None)
        if callable(callback):
            callback(sid)

    def handle_jmi(self, account: str, from_jid: str, event: JMIEvent) -> bool:
        handled = super().handle_jmi(account, from_jid, event)
        if handled and event.action == "propose":
            context = self.context
            if context is not None and context.sid == event.id:
                self._start_incoming_alerts()
        return handled

    def handle_jingle(self, account: str, from_jid: str, event: JingleEvent) -> None:
        # Conversations can trickle ICE while the incoming call is still
        # ringing, and direct-Jingle races can deliver transport-info before
        # session-initiate has created the call context. Preserve it by SID.
        if event.action == "transport-info" and self.media is None:
            added = self._early_remote_candidates.add_event(event)
            if added:
                log.info(
                    "Buffered %d remote ICE candidate(s) for sid=%s before media startup",
                    added,
                    event.sid,
                )
            return

        super().handle_jingle(account, from_jid, event)
        if event.action == "session-initiate":
            context = self.context
            if context is not None and context.sid == event.sid:
                self._start_incoming_alerts()

    def accept(self) -> None:
        context = self.context
        if context is not None:
            self._stop_incoming_alerts(context.sid)
        super().accept()

    def decline(self) -> None:
        context = self.context
        if context is not None:
            self._stop_incoming_alerts(context.sid)
        super().decline()

    def _start_media(self, *, offerer: bool, remote_offer=None) -> None:
        context = self.context
        super()._start_media(offerer=offerer, remote_offer=remote_offer)
        if self.media is None or context is None or context.state in _TERMINAL_STATES:
            return

        flushed = self._early_remote_candidates.flush_to(self.media, context.sid)
        if flushed:
            log.info(
                "Applied %d buffered remote ICE candidate(s) for sid=%s",
                flushed,
                context.sid,
            )

    def _send_local_candidate(self, mid: str, candidate_text: str) -> None:
        # GStreamer/libnice emits an empty candidate when gathering has
        # completed. It is an end-of-candidates marker, not malformed ICE.
        if not candidate_text.strip():
            log.debug("Local ICE gathering complete for mid=%s", mid)
            return
        context = self.context
        if context is None or context.state in _TERMINAL_STATES:
            log.debug("Ignoring late local ICE candidate for cancelled/ended call")
            return
        super()._send_local_candidate(mid, candidate_text)

    def _on_local_description(self, description: SessionDescription) -> None:
        context = self.context
        if context is None or context.state in _TERMINAL_STATES:
            log.debug("Ignoring late local description for cancelled/ended call")
            return
        super()._on_local_description(description)

    def _on_media_connected(self) -> None:
        context = self.context
        if context is None or context.state in _TERMINAL_STATES:
            log.debug("Ignoring late media-connected callback for cancelled/ended call")
            return
        super()._on_media_connected()

    @staticmethod
    def _failure_reason(reason: str) -> str:
        if "ICE connectivity checks failed" in reason:
            return "connectivity-error"
        if "encoder" in reason.lower() or "media" in reason.lower():
            return "failed-application"
        return "general-error"

    def _terminate_remote_failure(self, reason: str) -> None:
        """Tell the peer that a locally failed negotiation is over."""
        context = self.context
        if context is None:
            return

        wire_reason = self._failure_reason(reason)
        peer = context.peer_full or context.peer_bare
        module = self._module(context.account)

        try:
            if context.peer_full is not None and context.state in {
                CallState.NEGOTIATING,
                CallState.CONNECTED,
            }:
                module.send_jingle(
                    context.peer_full,
                    "session-terminate",
                    context.sid,
                    initiator=context.initiator or self._own_jid(context.account),
                    responder=context.responder,
                    reason=wire_reason,
                )
                log.info(
                    "TX failure session-terminate sid=%s to=%s reason=%s",
                    context.sid,
                    context.peer_full,
                    wire_reason,
                )

            module.send_jmi(peer, "finish", context.sid, reason=wire_reason)
            log.info(
                "TX failure JMI finish sid=%s to=%s reason=%s",
                context.sid,
                peer,
                wire_reason,
            )
        except Exception:
            log.exception(
                "Unable to signal remote call failure sid=%s peer=%s reason=%s",
                context.sid,
                peer,
                wire_reason,
            )

    def hangup(self) -> None:
        """Cancel ringing/connecting calls immediately; hang up connected calls."""
        context = self.context
        if context is not None:
            self._stop_incoming_alerts(context.sid)
        if context is None or context.state in _TERMINAL_STATES:
            super().hangup()
            self.plugin.set_call_active(False)
            return

        if context.state in {CallState.PROPOSING, CallState.RINGING}:
            log.info(
                "Cancelling ringing call sid=%s peer=%s",
                context.sid,
                context.peer_bare,
            )
            self.plugin.set_call_active(False)
            super().hangup()
            return

        if context.state == CallState.NEGOTIATING:
            peer = context.peer_full or context.peer_bare
            module = self._module(context.account)
            try:
                if context.peer_full is not None:
                    module.send_jingle(
                        context.peer_full,
                        "session-terminate",
                        context.sid,
                        initiator=context.initiator or self._own_jid(context.account),
                        responder=context.responder,
                        reason="cancel",
                    )
                    log.info(
                        "TX cancel session-terminate sid=%s to=%s",
                        context.sid,
                        context.peer_full,
                    )
                module.send_jmi(peer, "finish", context.sid, reason="cancel")
                log.info("TX cancel JMI finish sid=%s to=%s", context.sid, peer)
            except Exception:
                log.exception(
                    "Unable to signal call cancellation sid=%s peer=%s",
                    context.sid,
                    peer,
                )

            # Mark terminal and close media before any queued GStreamer callback
            # can turn the call back into Connected or show a failure dialog.
            self.plugin.set_call_active(False)
            self._finish_local(CallState.ENDED)
            return

        self.plugin.set_call_active(False)
        super().hangup()

    def _on_media_failed(self, reason: str) -> None:
        context = self.context
        if context is None or context.state in _TERMINAL_STATES:
            log.debug("Ignoring late media failure after call ended: %s", reason)
            return

        log.error("Call failed: %s", reason)
        self._terminate_remote_failure(reason)
        self._get_window().set_status(f"Call failed: {reason}")
        self._finish_local(CallState.FAILED, hide=False)

    def _finish_local(self, state: CallState, *, hide: bool = True) -> None:
        context = self.context
        if context is not None:
            self._stop_incoming_alerts(context.sid)
        self._early_remote_candidates.clear()
        super()._finish_local(state, hide=hide)

    def _cleanup(self, terminal: bool = True) -> None:
        context = self.context
        if context is not None:
            self._stop_incoming_alerts(context.sid)
        else:
            self._stop_incoming_alerts()
        self._early_remote_candidates.clear()
        super()._cleanup(terminal=terminal)
