"""Runtime controller fixes that depend on Gajim/GTK."""

from __future__ import annotations

import logging

from .controller import CallController
from .incoming import RemoteCandidateBuffer
from .protocol import JingleEvent
from .state import CallState

log = logging.getLogger("gajim.p.gajim_calls.controller")


class RuntimeCallController(CallController):
    """CallController with correct incoming trickle-ICE behaviour."""

    def __init__(self, plugin) -> None:
        super().__init__(plugin)
        self._early_remote_candidates = RemoteCandidateBuffer()

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

    def _start_media(self, *, offerer: bool, remote_offer=None) -> None:
        context = self.context
        super()._start_media(offerer=offerer, remote_offer=remote_offer)
        if self.media is None or context is None:
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
        super()._send_local_candidate(mid, candidate_text)

    @staticmethod
    def _failure_reason(reason: str) -> str:
        if "ICE connectivity checks failed" in reason:
            return "connectivity-error"
        if "encoder" in reason.lower() or "media" in reason.lower():
            return "failed-application"
        return "general-error"

    def _terminate_remote_failure(self, reason: str) -> None:
        """Tell the peer that a locally failed negotiation is over.

        Without this, Conversations remains in its Connecting state until its
        own timeout because the local media engine disappears without a final
        Jingle/JMI termination signal.
        """
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

            # JMI finish is useful even after Jingle has started: Conversations
            # uses it to clear the higher-level call proposal state promptly.
            module.send_jmi(peer, "finish", context.sid, reason=wire_reason)
            log.info(
                "TX failure JMI finish sid=%s to=%s reason=%s",
                context.sid,
                peer,
                wire_reason,
            )
        except Exception:
            # A signaling failure must never prevent local media cleanup or the
            # user-facing failure dialog from being shown.
            log.exception(
                "Unable to signal remote call failure sid=%s peer=%s reason=%s",
                context.sid,
                peer,
                wire_reason,
            )

    def _on_media_failed(self, reason: str) -> None:
        if self.context is None:
            return

        log.error("Call failed: %s", reason)
        self._terminate_remote_failure(reason)
        self._get_window().set_status(f"Call failed: {reason}")
        self._finish_local(CallState.FAILED, hide=False)

    def _finish_local(self, state: CallState, *, hide: bool = True) -> None:
        self._early_remote_candidates.clear()
        super()._finish_local(state, hide=hide)

    def _cleanup(self, terminal: bool = True) -> None:
        self._early_remote_candidates.clear()
        super()._cleanup(terminal=terminal)
