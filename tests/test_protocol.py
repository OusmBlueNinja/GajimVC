from gajim_calls.protocol import (
    build_jingle,
    build_jmi,
    parse_jingle,
    parse_jmi,
    xml_text,
)
from gajim_calls.sdp import (
    Codec,
    Fingerprint,
    IceCandidate,
    MediaSection,
    SessionDescription,
    build_sdp,
    parse_sdp,
)


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


def test_jmi_parses_from_normal_client_message_wrapper():
    event = parse_jmi(
        """
        <message xmlns='jabber:client'
                 from='phone@example.test/Conversations'
                 to='desktop@example.test/Gajim'
                 type='chat'>
          <proceed xmlns='urn:xmpp:jingle-message:0' id='accepted-call'/>
        </message>
        """
    )

    assert event is not None
    assert event.action == "proceed"
    assert event.id == "accepted-call"


def test_non_jmi_message_is_ignored_by_broad_message_handler_parser():
    event = parse_jmi(
        """
        <message xmlns='jabber:client'
                 from='phone@example.test/Conversations'
                 to='desktop@example.test/Gajim'
                 type='chat'>
          <body>hello</body>
        </message>
        """
    )

    assert event is None


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
    assert event.description.bundle == ("audio",)
    assert event.description.media[0].codecs[0].name == "OPUS"
    assert event.description.media[0].fingerprint is not None
    assert event.description.media[0].fingerprint.value == "AA:BB:CC"
    assert event.description.media[0].candidates[0].port == 50000


def test_ice_udp_transport_filters_non_udp_candidates_on_send():
    desc = description()
    desc.media[0].candidates.append(
        IceCandidate(
            "2",
            1,
            "tcp",
            1694498815,
            "10.0.0.2",
            9,
            "host",
            tcp_type="active",
        )
    )

    event = parse_jingle(
        xml_text(
            build_jingle(
                "session-initiate",
                "udp-only",
                initiator="a@example.test/desktop",
                responder="b@example.test/phone",
                description=desc,
            )
        )
    )

    assert event is not None
    candidates = event.description.media[0].candidates
    assert [(item.protocol, item.port) for item in candidates] == [("udp", 50000)]


def test_ice_udp_transport_ignores_non_udp_candidates_on_receive():
    xml = """
    <jingle xmlns='urn:xmpp:jingle:1' action='transport-info' sid='udp-only'
            initiator='a@example.test/desktop' responder='b@example.test/phone'>
      <content creator='initiator' name='audio'>
        <transport xmlns='urn:xmpp:jingle:transports:ice-udp:1' ufrag='u' pwd='p'>
          <candidate component='1' foundation='1' generation='0' id='udp'
                     ip='10.0.0.2' network='0' port='50000' priority='2130706431'
                     protocol='udp' type='host'/>
          <candidate component='1' foundation='2' generation='0' id='tcp'
                     ip='10.0.0.2' network='0' port='9' priority='1694498815'
                     protocol='tcp' type='host' tcptype='active'/>
        </transport>
      </content>
    </jingle>
    """

    event = parse_jingle(xml)

    assert event is not None
    candidates = event.description.media[0].candidates
    assert [(item.protocol, item.port) for item in candidates] == [("udp", 50000)]


def test_audio_only_bundle_survives_sdp_jingle_sdp_round_trip():
    original_sdp = """v=0
 o=- 1 1 IN IP4 0.0.0.0
 s=-
 t=0 0
 a=group:BUNDLE audio
 m=audio 9 UDP/TLS/RTP/SAVPF 111
 c=IN IP4 0.0.0.0
 a=mid:audio
 a=sendrecv
 a=rtcp-mux
 a=ice-ufrag:abc
 a=ice-pwd:abcdefghijklmnopqrstuv
 a=fingerprint:sha-256 AA:BB:CC
 a=setup:actpass
 a=rtpmap:111 OPUS/48000/2
 """.replace("\n ", "\n")
    local = parse_sdp(original_sdp)
    assert local.bundle == ("audio",)

    event = parse_jingle(
        xml_text(
            build_jingle(
                "session-initiate",
                "audio-only",
                initiator="desktop@example.test/Gajim",
                responder="phone@example.test/Conversations",
                description=local,
            )
        )
    )
    assert event is not None
    assert event.description.bundle == ("audio",)

    rebuilt = build_sdp(event.description)
    assert "a=group:BUNDLE audio\r\n" in rebuilt
    assert parse_sdp(rebuilt).bundle == ("audio",)


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


def test_unknown_jingle_action_is_rejected_before_it_can_be_claimed():
    parsed = parse_jingle(
        """
        <jingle xmlns='urn:xmpp:jingle:1' action='definitely-not-jingle' sid='owned-call'>
          <content creator='initiator' name='audio'>
            <description xmlns='urn:xmpp:jingle:apps:rtp:1' media='audio'/>
          </content>
        </jingle>
        """
    )

    assert parsed is None


def test_jingle_action_validation_keeps_standard_call_actions_usable():
    for action in (
        "session-initiate",
        "session-accept",
        "transport-info",
        "session-terminate",
    ):
        parsed = parse_jingle(
            f"<jingle xmlns='urn:xmpp:jingle:1' action='{action}' sid='call'/>"
        )
        assert parsed is not None
        assert parsed.action == action
        assert parsed.sid == "call"
