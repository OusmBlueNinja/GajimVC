from gajim_calls.protocol import parse_jingle
from gajim_calls.sdp import build_sdp


def test_description_level_rtcp_feedback_applies_to_every_payload() -> None:
    xml = """
    <jingle xmlns='urn:xmpp:jingle:1' action='session-initiate' sid='feedback'>
      <content creator='initiator' name='video'>
        <description xmlns='urn:xmpp:jingle:apps:rtp:1' media='video'>
          <payload-type id='96' name='VP8' clockrate='90000'>
            <rtcp-fb xmlns='urn:xmpp:jingle:apps:rtp:rtcp-fb:0'
                     type='nack' subtype='pli'/>
          </payload-type>
          <payload-type id='97' name='VP9' clockrate='90000'/>
          <rtcp-fb xmlns='urn:xmpp:jingle:apps:rtp:rtcp-fb:0'
                   type='nack' subtype='pli'/>
          <rtcp-fb xmlns='urn:xmpp:jingle:apps:rtp:rtcp-fb:0'
                   type='ccm' subtype='fir'/>
        </description>
      </content>
    </jingle>
    """

    parsed = parse_jingle(xml)

    assert parsed is not None
    codecs = parsed.description.media[0].codecs
    assert len(codecs) == 2
    assert codecs[0].rtcp_feedback.count(("nack", "pli")) == 1
    assert ("ccm", "fir") in codecs[0].rtcp_feedback
    assert ("nack", "pli") in codecs[1].rtcp_feedback
    assert ("ccm", "fir") in codecs[1].rtcp_feedback

    rebuilt = build_sdp(parsed.description)
    assert "a=rtcp-fb:96 nack pli\r\n" in rebuilt
    assert "a=rtcp-fb:96 ccm fir\r\n" in rebuilt
    assert "a=rtcp-fb:97 nack pli\r\n" in rebuilt
    assert "a=rtcp-fb:97 ccm fir\r\n" in rebuilt
