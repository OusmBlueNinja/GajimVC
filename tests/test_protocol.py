from gajim_calls.protocol import (
    build_jingle,
    build_jmi,
    parse_jingle,
    parse_jmi,
    xml_text,
)
from gajim_calls.sdp import Codec, Fingerprint, IceCandidate, MediaSection, SessionDescription


def description():
    return SessionDescription(
        media=[
            MediaSection(
                media="audio",
                mid="audio",
                codecs=[Codec(111, "OPUS", 48000, 2, (("minptime", "10"),))],
                ice_ufrag="ufrag",
                ice_pwd="passwordpassword",
                fingerprint=Fingerprint("sha-256", "AA:BB:CC", "actpass"),
                candidates=[
                    IceCandidate(
                        "1", 1, "udp", 2130706431, "10.0.0.2", 50000, "host"
                    )
                ],
            )
        ],
        bundle=("audio",),
    )


def test_jmi_propose_round_trip():
    xml = xml_text(build_jmi("propose", "1234", media=("audio", "video")))
    event = parse_jmi(xml)
    assert event is not None
    assert event.action == "propose"
    assert event.id == "1234"
    assert event.media == ("audio", "video")


def test_jingle_round_trip():
    xml = xml_text(
        build_jingle(
            "session-initiate",
            "1234",
            initiator="a@example.test/desktop",
            responder="b@example.test/phone",
            description=description(),
        )
    )
    event = parse_jingle(xml)
    assert event is not None
    assert event.action == "session-initiate"
    assert event.sid == "1234"
    assert event.initiator == "a@example.test/desktop"
    assert event.description.media[0].codecs[0].name == "OPUS"
    assert event.description.media[0].fingerprint is not None
    assert event.description.media[0].fingerprint.value == "AA:BB:CC"
    assert event.description.media[0].candidates[0].port == 50000


def test_transport_info_has_no_rtp_description():
    desc = description()
    xml = xml_text(
        build_jingle(
            "transport-info",
            "1234",
            initiator="a@example.test/desktop",
            responder="b@example.test/phone",
            description=desc,
        )
    )
    assert "transport" in xml
    # Namespace declarations may contain "apps:rtp", so assert on the tag.
    assert "<rtp:description" not in xml


def test_jmi_tie_break_round_trip():
    xml = xml_text(build_jmi("reject", "higher", reason="expired", tie_break=True))
    event = parse_jmi(xml)
    assert event is not None
    assert event.reason == "expired"
    assert event.tie_break is True


def test_transport_info_preserves_ice_credentials_and_fingerprint():
    desc = description()
    xml = xml_text(
        build_jingle(
            "transport-info",
            "1234",
            initiator="a@example.test/desktop",
            responder="b@example.test/phone",
            description=desc,
        )
    )
    parsed = parse_jingle(xml)
    assert parsed is not None
    section = parsed.description.media[0]
    assert section.ice_ufrag == "ufrag"
    assert section.ice_pwd == "passwordpassword"
    assert section.fingerprint is not None
    assert section.fingerprint.value == "AA:BB:CC"


def test_failure_jingle_reason_round_trip():
    xml = xml_text(
        build_jingle(
            "session-terminate",
            "failed-call",
            initiator="a@example.test/desktop",
            responder="b@example.test/phone",
            reason="connectivity-error",
        )
    )
    parsed = parse_jingle(xml)
    assert parsed is not None
    assert parsed.action == "session-terminate"
    assert parsed.reason == "connectivity-error"


def test_failure_jmi_finish_reason_round_trip():
    xml = xml_text(build_jmi("finish", "failed-call", reason="connectivity-error"))
    parsed = parse_jmi(xml)
    assert parsed is not None
    assert parsed.action == "finish"
    assert parsed.reason == "connectivity-error"
