"""Pure helpers for incoming Jingle call negotiation."""

from __future__ import annotations

from .protocol import JingleEvent


class RemoteCandidateBuffer:
    """Preserve trickled ICE candidates until the media engine exists.

    Conversations can send transport-info immediately after session-initiate,
    while the user is still looking at the incoming-call UI. Dropping those
    candidates makes the later session-accept unable to establish ICE.
    """

    def __init__(self) -> None:
        self._items: list[tuple[str, str]] = []

    def add_event(self, event: JingleEvent) -> int:
        if event.action != "transport-info":
            return 0

        added = 0
        for section in event.description.media:
            for candidate in section.candidates:
                self._items.append((section.mid, candidate.to_sdp()))
                added += 1
        return added

    def flush_to(self, media) -> int:
        items = self._items
        self._items = []
        for mid, candidate in items:
            media.add_remote_candidate(mid, candidate)
        return len(items)

    def clear(self) -> None:
        self._items = []

    def __len__(self) -> int:
        return len(self._items)
