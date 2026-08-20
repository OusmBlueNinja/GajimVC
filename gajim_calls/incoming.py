"""Pure helpers for incoming Jingle call negotiation."""

from __future__ import annotations

from .protocol import JingleEvent


def is_ice_transport_info(event: JingleEvent) -> bool:
    """Return True for trickled ICE-UDP candidates belonging to a call.

    This is intentionally dependency-free so the network module can decide
    whether an otherwise-unowned transport-info stanza should be preserved
    without importing GStreamer or GTK.
    """
    if event.action != "transport-info":
        return False
    return any(section.candidates for section in event.description.media)


class RemoteCandidateBuffer:
    """Preserve trickled ICE candidates until the media engine exists.

    Conversations can trickle transport-info while an incoming call is still
    ringing, and in racey/direct-Jingle flows a transport-info stanza can even
    be observed before session-initiate has caused the plugin to claim the SID.
    Keep candidates keyed by SID so one call can never consume another call's
    ICE candidates.
    """

    def __init__(self) -> None:
        self._items: dict[str, list[tuple[str, str]]] = {}

    def add_event(self, event: JingleEvent) -> int:
        if not is_ice_transport_info(event):
            return 0

        items = self._items.setdefault(event.sid, [])
        added = 0
        for section in event.description.media:
            for candidate in section.candidates:
                items.append((section.mid, candidate.to_sdp()))
                added += 1
        return added

    def flush_to(self, media, sid: str) -> int:
        items = self._items.pop(sid, [])
        for mid, candidate in items:
            media.add_remote_candidate(mid, candidate)
        return len(items)

    def discard(self, sid: str) -> None:
        self._items.pop(sid, None)

    def clear(self) -> None:
        self._items.clear()

    def count(self, sid: str | None = None) -> int:
        if sid is not None:
            return len(self._items.get(sid, []))
        return sum(len(items) for items in self._items.values())

    def __len__(self) -> int:
        return self.count()
