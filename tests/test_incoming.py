from gajim_calls.incoming import (
    RemoteCandidateBuffer,
    is_ice_transport_info,
    should_claim_jingle,
)
from gajim_calls.protocol import JingleEvent
from gajim_calls.sdp import IceCandidate, MediaSection, SessionDescription


class FakeMedia:
    def __init__(self) -> None:
        self.candidates: list[tuple[str, str]] = []

    def add_remote_candidate(self, mid: str, candidate: str) -> None:
        self.candidates.append((mid, candidate))


def _candidate(ip: str, port: int) -> IceCandidate:
    return IceCandidate(
        foundation="1",
        component=1,
        protocol="udp",
        priority=2130706431,
        ip=ip,
        port=port,
        type="host",
    )


def _transport_event(sid: str, mid: str, candidate: IceCandidate) -> JingleEvent:
    return JingleEvent(
        action="transport-info",
        sid=sid,
        initiator="phone@example.test/Conversations",
        responder=None,
        description=SessionDescription(
            media=[
                MediaSection(
                    media="audio",
                    mid=mid,
                    ice_ufrag="phoneufrag",
                    ice_pwd="phonepassword",
                    candidates=[candidate],
                )
            ]
        ),
    )


def test_owned_transport_info_is_claimed_but_unknown_transport_info_is_not() -> None:
    event = _transport_event("call-a", "0", _candidate("192.168.1.50", 50000))
    assert is_ice_transport_info(event)
    assert should_claim_jingle(event, owns_sid=True)
    assert not should_claim_jingle(event, owns_sid=False)


def test_unowned_rtp_session_initiate_is_claimed_as_new_call() -> None:
    event = JingleEvent(
        action="session-initiate",
        sid="direct-call",
        initiator="phone@example.test/Conversations",
        responder=None,
        description=SessionDescription(
            media=[MediaSection(media="audio", mid="audio")]
        ),
    )
    assert should_claim_jingle(event, owns_sid=False)


def test_transport_info_is_preserved_before_accept() -> None:
    candidate = _candidate("192.168.1.50", 50000)
    event = _transport_event("incoming-call", "audio-content", candidate)

    assert is_ice_transport_info(event)

    buffer = RemoteCandidateBuffer()
    assert buffer.add_event(event) == 1
    assert len(buffer) == 1
    assert buffer.count("incoming-call") == 1

    media = FakeMedia()
    assert buffer.flush_to(media, "incoming-call") == 1
    assert len(buffer) == 0
    assert media.candidates == [("audio-content", candidate.to_sdp())]


def test_pre_session_candidates_are_scoped_by_sid() -> None:
    first = _candidate("192.168.1.50", 50000)
    second = _candidate("192.168.1.51", 50002)

    buffer = RemoteCandidateBuffer()
    assert buffer.add_event(_transport_event("call-a", "0", first)) == 1
    assert buffer.add_event(_transport_event("call-b", "0", second)) == 1
    assert len(buffer) == 2

    media = FakeMedia()
    assert buffer.flush_to(media, "call-b") == 1
    assert media.candidates == [("0", second.to_sdp())]
    assert buffer.count("call-a") == 1
    assert buffer.count("call-b") == 0

    assert buffer.flush_to(media, "call-a") == 1
    assert media.candidates[-1] == ("0", first.to_sdp())
    assert len(buffer) == 0


def test_non_transport_info_is_not_buffered() -> None:
    event = JingleEvent(
        action="session-initiate",
        sid="incoming-call",
        initiator="phone@example.test/Conversations",
        responder=None,
        description=SessionDescription(media=[]),
    )

    assert not is_ice_transport_info(event)
    buffer = RemoteCandidateBuffer()
    assert buffer.add_event(event) == 0
    assert len(buffer) == 0


def test_empty_transport_info_is_not_claimed_as_call_ice() -> None:
    event = JingleEvent(
        action="transport-info",
        sid="unrelated",
        initiator="peer@example.test/resource",
        responder=None,
        description=SessionDescription(
            media=[
                MediaSection(
                    media="audio",
                    mid="0",
                    ice_ufrag="ufrag",
                    ice_pwd="password",
                    candidates=[],
                )
            ]
        ),
    )

    assert not is_ice_transport_info(event)
    assert not should_claim_jingle(event, owns_sid=False)
    buffer = RemoteCandidateBuffer()
    assert buffer.add_event(event) == 0
    assert len(buffer) == 0
