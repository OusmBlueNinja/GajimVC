from gajim_calls.sdp import IceCandidate, build_sdp, parse_sdp


SAMPLE = """v=0
o=- 123 2 IN IP4 127.0.0.1
s=-
t=0 0
a=group:BUNDLE 0 1
a=fingerprint:sha-256 AA:BB:CC
a=setup:actpass
m=audio 9 UDP/TLS/RTP/SAVPF 111
c=IN IP4 0.0.0.0
a=mid:0
a=ice-ufrag:abc
a=ice-pwd:defghijklmnop
a=rtcp-mux
a=sendrecv
a=rtpmap:111 OPUS/48000/2
a=fmtp:111 minptime=10;useinbandfec=1
a=candidate:1 1 UDP 2130706431 10.0.0.2 50000 typ host generation 0
m=video 9 UDP/TLS/RTP/SAVPF 96
c=IN IP4 0.0.0.0
a=mid:1
a=ice-ufrag:ghi
a=ice-pwd:jklmnopqrstuv
a=rtcp-mux
a=sendrecv
a=rtcp-fb:* nack pli
a=rtpmap:96 VP8/90000
a=rtcp-fb:96 nack
a=rtcp-fb:96 nack pli
"""


def test_candidate_round_trip():
    text = (
        "candidate:2 1 UDP 1694498815 203.0.113.2 45664 typ srflx "
        "raddr 10.0.0.2 rport 50000 generation 0"
    )
    candidate = IceCandidate.from_sdp(text)
    assert candidate.type == "srflx"
    assert candidate.rel_addr == "10.0.0.2"
    reparsed = IceCandidate.from_sdp(candidate.to_sdp())
    assert reparsed == candidate


def test_sdp_parse_and_build_round_trip():
    description = parse_sdp(SAMPLE)
    assert [item.media for item in description.media] == ["audio", "video"]
    assert description.bundle == ("0", "1")
    assert description.media[0].codecs[0].name == "OPUS"
    assert description.media[0].codecs[0].channels == 2
    assert ("useinbandfec", "1") in description.media[0].codecs[0].parameters
    assert ("nack", "pli") in description.media[1].codecs[0].rtcp_feedback

    rebuilt = build_sdp(description)
    again = parse_sdp(rebuilt)
    assert [item.mid for item in again.media] == ["0", "1"]
    assert again.media[0].ice_ufrag == "abc"
    assert again.media[0].fingerprint is not None
    assert again.media[0].fingerprint.value == "AA:BB:CC"
    assert ("nack", "pli") in again.media[1].codecs[0].rtcp_feedback


def test_wildcard_rtcp_feedback_applies_to_every_payload_even_before_rtpmap():
    sample = """v=0
 o=- 1 1 IN IP4 127.0.0.1
 s=-
 t=0 0
 m=video 9 UDP/TLS/RTP/SAVPF 96 97
 c=IN IP4 0.0.0.0
 a=mid:video
 a=rtcp-fb:* nack pli
 a=rtpmap:96 VP8/90000
 a=rtpmap:97 VP9/90000
 """.replace("\n ", "\n")
    description = parse_sdp(sample)
    assert len(description.media[0].codecs) == 2
    assert all(
        ("nack", "pli") in codec.rtcp_feedback
        for codec in description.media[0].codecs
    )

    rebuilt = build_sdp(description)
    assert "a=rtcp-fb:96 nack pli\r\n" in rebuilt
    assert "a=rtcp-fb:97 nack pli\r\n" in rebuilt


def test_sdp_without_bundle_stays_unbundled():
    sample = """v=0
 o=- 1 1 IN IP4 127.0.0.1
 s=-
 t=0 0
 m=audio 9 UDP/TLS/RTP/SAVPF 111
 c=IN IP4 0.0.0.0
 a=mid:audio
 a=ice-ufrag:u
 a=ice-pwd:passwordpassword
 a=rtpmap:111 OPUS/48000/2
 """.replace("\n ", "\n")
    description = parse_sdp(sample)
    assert description.bundle == ()
    rebuilt = build_sdp(description)
    assert "a=group:BUNDLE" not in rebuilt
