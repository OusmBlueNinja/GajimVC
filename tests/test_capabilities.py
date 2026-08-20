from gajim_calls.capabilities import (
    RFC5888_GROUPING,
    RTP_AUDIO,
    RTP_VIDEO,
    call_capabilities,
    extend_call_capabilities,
)
from gajim_calls.constants import NS_DTLS, NS_ICE_UDP, NS_JINGLE, NS_JMI, NS_RTP


def test_audio_capabilities_match_conversations_call_detection_requirements():
    features = set(call_capabilities(video=False))
    assert {
        NS_JINGLE,
        NS_JMI,
        NS_ICE_UDP,
        NS_RTP,
        NS_DTLS,
        RTP_AUDIO,
        RFC5888_GROUPING,
    } <= features
    assert RTP_VIDEO not in features


def test_video_capability_is_only_added_when_video_runtime_is_available():
    assert RTP_VIDEO not in call_capabilities(video=False)
    assert RTP_VIDEO in call_capabilities(video=True)


def test_extending_caps_is_idempotent_and_preserves_existing_features():
    features = ["urn:example:existing", NS_JINGLE]
    extend_call_capabilities(features, video=True)
    once = list(features)
    extend_call_capabilities(features, video=True)
    assert features == once
    assert features[0] == "urn:example:existing"
    assert features.count(NS_JINGLE) == 1
