from xml.etree import ElementTree as ET

import pytest

from gajim_calls.constants import NS_JINGLE
from gajim_calls.protocol import build_jingle, parse_jingle, qname, xml_text
from gajim_calls.sdp import Codec, MediaSection, SessionDescription, build_sdp, parse_sdp


@pytest.mark.parametrize(
    ("action", "direction", "expected_senders"),
    [
        ("session-initiate", "sendrecv", "both"),
        ("session-initiate", "sendonly", "initiator"),
        ("session-initiate", "recvonly", "responder"),
        ("session-initiate", "inactive", "none"),
        ("session-accept", "sendrecv", "both"),
        ("session-accept", "sendonly", "responder"),
        ("session-accept", "recvonly", "initiator"),
        ("session-accept", "inactive", "none"),
    ],
)
def test_jingle_senders_preserves_sdp_direction(
    action: str, direction: str, expected_senders: str
) -> None:
    description = SessionDescription(
        media=[
            MediaSection(
                media="audio",
                mid="audio",
                codecs=[Codec(111, "OPUS", 48000, 2)],
                direction=direction,
            )
        ],
        bundle=("audio",),
    )

    payload = build_jingle(
        action,
        "direction-test",
        initiator="initiator@example.test/Desktop",
        responder="responder@example.test/Phone",
        description=description,
    )
    content = payload.find(qname(NS_JINGLE, "content"))
    assert content is not None
    assert content.attrib["senders"] == expected_senders

    event = parse_jingle(xml_text(payload))
    assert event is not None
    assert event.description.media[0].direction == direction

    rebuilt = build_sdp(event.description, answer=action == "session-accept")
    assert f"a={direction}\r\n" in rebuilt
    assert parse_sdp(rebuilt).media[0].direction == direction


def test_missing_jingle_senders_defaults_to_sendrecv() -> None:
    xml = """
    <jingle xmlns='urn:xmpp:jingle:1' action='session-initiate' sid='default-direction'
            initiator='a@example.test/Desktop' responder='b@example.test/Phone'>
      <content creator='initiator' name='audio'>
        <description xmlns='urn:xmpp:jingle:apps:rtp:1' media='audio'>
          <payload-type id='111' name='OPUS' clockrate='48000' channels='2'/>
        </description>
        <transport xmlns='urn:xmpp:jingle:transports:ice-udp:1' ufrag='u' pwd='p'/>
      </content>
    </jingle>
    """
    event = parse_jingle(ET.fromstring(xml))
    assert event is not None
    assert event.description.media[0].direction == "sendrecv"


def test_transport_info_does_not_restate_senders() -> None:
    description = SessionDescription(
        media=[MediaSection(media="audio", mid="audio", direction="sendonly")]
    )
    payload = build_jingle(
        "transport-info",
        "transport-direction",
        initiator="a@example.test/Desktop",
        responder="b@example.test/Phone",
        description=description,
    )
    content = payload.find(qname(NS_JINGLE, "content"))
    assert content is not None
    assert "senders" not in content.attrib


def test_session_level_sdp_direction_is_inherited_and_translated_to_jingle() -> None:
    sdp = """v=0
o=- 1 1 IN IP4 0.0.0.0
s=-
t=0 0
a=recvonly
m=audio 9 UDP/TLS/RTP/SAVPF 111
c=IN IP4 0.0.0.0
a=mid:audio
a=rtpmap:111 OPUS/48000/2
m=video 9 UDP/TLS/RTP/SAVPF 96
c=IN IP4 0.0.0.0
a=mid:video
a=rtpmap:96 VP8/90000
"""

    description = parse_sdp(sdp)
    assert [section.direction for section in description.media] == [
        "recvonly",
        "recvonly",
    ]

    payload = build_jingle(
        "session-initiate",
        "session-direction",
        initiator="initiator@example.test/Desktop",
        responder="responder@example.test/Phone",
        description=description,
    )
    contents = payload.findall(qname(NS_JINGLE, "content"))
    assert [content.attrib["senders"] for content in contents] == [
        "responder",
        "responder",
    ]


def test_media_level_sdp_direction_overrides_session_default() -> None:
    sdp = """v=0
o=- 1 1 IN IP4 0.0.0.0
s=-
t=0 0
a=inactive
m=audio 9 UDP/TLS/RTP/SAVPF 111
c=IN IP4 0.0.0.0
a=mid:audio
a=sendonly
a=rtpmap:111 OPUS/48000/2
m=video 9 UDP/TLS/RTP/SAVPF 96
c=IN IP4 0.0.0.0
a=mid:video
a=rtpmap:96 VP8/90000
"""

    description = parse_sdp(sdp)

    assert description.media[0].direction == "sendonly"
    assert description.media[1].direction == "inactive"
