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
        # Conversations commonly trickles ICE candidates while the incoming
        # call is still ringing. The base controller used to silently drop
        # transport-info until the user clicked Accept because self.media did
        # not exist yet. Preserve those candidates and feed them into the media
        # engine as soon as answering begins.
        if event.action == "transport-info" and self.media is None:
            added = self._early_remote_candidates.add_event(event)
            if added:
                log.info(
                    "Buffered %d remote ICE candidate(s) before incoming call accept",
                    added,
                )
            return

        super().handle_jingle(account, from_jid, event)

    def _start_media(self, *, offerer: bool, remote_offer=None) -> None:
        super()._start_media(offerer=offerer, remote_offer=remote_offer)
        if self.media is None:
            return

        flushed = self._early_remote_candidates.flush_to(self.media)
        if flushed:
            log.info(
                "Applied %d buffered remote ICE candidate(s) after call accept",
                flushed,
            )

    def _finish_local(self, state: CallState, *, hide: bool = True) -> None:
        self._early_remote_candidates.clear()
        super()._finish_local(state, hide=hide)

    def _cleanup(self, terminal: bool = True) -> None:
        self._early_remote_candidates.clear()
        super()._cleanup(terminal=terminal)
