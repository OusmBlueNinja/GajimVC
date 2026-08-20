from gajim_calls.incoming import RemoteCandidateBuffer
from gajim_calls.protocol import JingleEvent
from gajim_calls.sdp import IceCandidate, MediaSection, SessionDescription


class FakeMedia:
    def __init__(self) -> None:
        self.candidates: list[tuple[str, str]] = []

    def add_remote_candidate(self, mid: str, candidate: str) -> None:
        self.candidates.append((mid, candidate))


def test_transport_info_is_preserved_before_accept() -> None:
    candidate = IceCandidate(
        foundation="1",
        component=1,
        protocol="udp",
        priority=2130706431,
        ip="192.168.1.50",
        port=50000,
        type="host",
    )
    event = JingleEvent(
        action="transport-info",
        sid="incoming-call",
        initiator="phone@example.test/Conversations",
        responder=None,
        description=SessionDescription(
            media=[
                MediaSection(
                    media="audio",
                    mid="audio-content",
                    candidates=[candidate],
                )
            ]
        ),
    )

    buffer = RemoteCandidateBuffer()
    assert buffer.add_event(event) == 1
    assert len(buffer) == 1

    media = FakeMedia()
    assert buffer.flush_to(media) == 1
    assert len(buffer) == 0
    assert media.candidates == [("audio-content", candidate.to_sdp())]


def test_non_transport_info_is_not_buffered() -> None:
    event = JingleEvent(
        action="session-initiate",
        sid="incoming-call",
        initiator="phone@example.test/Conversations",
        responder=None,
        description=SessionDescription(media=[]),
    )

    buffer = RemoteCandidateBuffer()
    assert buffer.add_event(event) == 0
    assert len(buffer) == 0
