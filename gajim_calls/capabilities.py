"""Dependency-free XMPP capability advertisement for Gajim Calls."""

from __future__ import annotations

from .constants import NS_DTLS, NS_ICE_UDP, NS_JINGLE, NS_JMI, NS_RTP

RTP_AUDIO = f"{NS_RTP[:-1]}audio"
RTP_VIDEO = f"{NS_RTP[:-1]}video"
RFC5888_GROUPING = "urn:ietf:rfc:5888"

_BASE_CALL_CAPABILITIES = (
    NS_JINGLE,
    NS_JMI,
    NS_ICE_UDP,
    NS_RTP,
    NS_DTLS,
    RTP_AUDIO,
    RFC5888_GROUPING,
)


def call_capabilities(*, video: bool) -> tuple[str, ...]:
    """Return the features this local media runtime can actually provide."""
    if video:
        return _BASE_CALL_CAPABILITIES + (RTP_VIDEO,)
    return _BASE_CALL_CAPABILITIES


def extend_call_capabilities(features: list[str], *, video: bool) -> None:
    """Append call disco features without creating duplicate entries."""
    present = set(features)
    for feature in call_capabilities(video=video):
        if feature in present:
            continue
        features.append(feature)
        present.add(feature)
