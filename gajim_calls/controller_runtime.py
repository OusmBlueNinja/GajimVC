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

    def _finish_local(self, state: CallState, *, hide: bool = True) -> None:
        self._early_remote_candidates.clear()
        super()._finish_local(state, hide=hide)

    def _cleanup(self, terminal: bool = True) -> None:
        self._early_remote_candidates.clear()
        super()._cleanup(terminal=terminal)
