"""Call state model kept independent from Gajim/GStreamer for easy testing."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


MediaKind = Literal["audio", "video"]


class CallState(str, Enum):
    IDLE = "idle"
    PROPOSING = "proposing"
    RINGING = "ringing"
    NEGOTIATING = "negotiating"
    CONNECTED = "connected"
    ENDING = "ending"
    ENDED = "ended"
    FAILED = "failed"


_ALLOWED: dict[CallState, set[CallState]] = {
    CallState.IDLE: {CallState.PROPOSING, CallState.RINGING},
    CallState.PROPOSING: {
        CallState.NEGOTIATING,
        CallState.ENDING,
        CallState.ENDED,
        CallState.FAILED,
    },
    CallState.RINGING: {
        CallState.NEGOTIATING,
        CallState.ENDING,
        CallState.ENDED,
        CallState.FAILED,
    },
    CallState.NEGOTIATING: {
        CallState.CONNECTED,
        CallState.ENDING,
        CallState.ENDED,
        CallState.FAILED,
    },
    CallState.CONNECTED: {
        CallState.ENDING,
        CallState.ENDED,
        CallState.FAILED,
    },
    CallState.ENDING: {CallState.ENDED, CallState.FAILED},
    CallState.ENDED: set(),
    CallState.FAILED: {CallState.ENDED},
}


class InvalidTransition(ValueError):
    pass


@dataclass(slots=True)
class CallContext:
    account: str
    sid: str
    peer_bare: str
    media: tuple[MediaKind, ...]
    incoming: bool
    peer_full: str | None = None
    state: CallState = CallState.IDLE
    initiator: str | None = None
    responder: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def transition(self, new_state: CallState) -> None:
        if new_state == self.state:
            return
        if new_state not in _ALLOWED[self.state]:
            raise InvalidTransition(f"{self.state.value} -> {new_state.value}")
        self.state = new_state

    @property
    def has_video(self) -> bool:
        return "video" in self.media
