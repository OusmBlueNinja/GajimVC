"""XEP-0353/Jingle RTP XML helpers.

This module contains no Gajim imports. Runtime code converts the resulting XML
elements to nbxmpp Nodes. Keeping protocol encoding here makes the wire format
straightforward to unit test.
"""

from __future__ import annotations

from dataclasses import dataclass
from xml.etree import ElementTree as ET
import uuid

from .constants import (
    NS_DTLS,
    NS_GROUPING,
    NS_HINTS,
    NS_ICE_UDP,
    NS_JINGLE,
    NS_JMI,
    NS_RTCP_FB,
    NS_RTP,
)
from .sdp import Codec, Fingerprint, IceCandidate, MediaSection, SessionDescription


for prefix, namespace in (
    ("jingle", NS_JINGLE),
    ("rtp", NS_RTP),
    ("ice", NS_ICE_UDP),
    ("dtls", NS_DTLS),
):
    ET.register_namespace(prefix, namespace)


def qname(namespace: str, local: str) -> str:
    return f"{{{namespace}}}{local}"


@dataclass(slots=True, frozen=True)
class JMIEvent:
    action: str
    id: str
    media: tuple[str, ...] = ()
    reason: str | None = None
    tie_break: bool = False


@dataclass(slots=True, frozen=True)
class JingleEvent:
    action: str
    sid: str
    initiator: str | None
    responder: str | None
    description: SessionDescription
    reason: str | None = None


def _as_element(xml_or_element: str | ET.Element) -> ET.Element:
    if isinstance(xml_or_element, ET.Element):
        return xml_or_element
    return ET.fromstring(xml_or_element)


def build_jmi(
    action: str,
    sid: str,
    *,
    media: tuple[str, ...] = (),
    reason: str | None = None,
    tie_break: bool = False,
) -> ET.Element:
    if not sid:
        raise ValueError("JMI id is required")
    node = ET.Element(qname(NS_JMI, action), {"id": sid})
    if action == "propose":
        for kind in media:
            ET.SubElement(node, qname(NS_RTP, "description"), {"media": kind})
    elif reason is not None:
        reason_node = ET.SubElement(node, qname(NS_JINGLE, "reason"))
        ET.SubElement(reason_node, qname(NS_JINGLE, reason))
    if tie_break:
        ET.SubElement(node, qname(NS_JMI, "tie-break"))
    return node


def parse_jmi(xml_or_element: str | ET.Element) -> JMIEvent | None:
    root = _as_element(xml_or_element)
    candidates = [root] + list(root)
    for node in candidates:
        if not node.tag.startswith("{" + NS_JMI + "}"):
            continue
        action = node.tag.rsplit("}", 1)[1]
        if action not in {"propose", "ringing", "proceed", "reject", "retract", "finish"}:
            continue
        sid = node.attrib.get("id", "")
        if not sid:
            return None
        media = tuple(
            child.attrib.get("media", "")
            for child in node.findall(qname(NS_RTP, "description"))
            if child.attrib.get("media") in {"audio", "video"}
        )
        reason = None
        reason_node = node.find(qname(NS_JINGLE, "reason"))
        if reason_node is not None:
            for child in reason_node:
                if child.tag.startswith("{" + NS_JINGLE + "}") and not child.tag.endswith("}text"):
                    reason = child.tag.rsplit("}", 1)[1]
                    break
        tie_break = node.find(qname(NS_JMI, "tie-break")) is not None
        return JMIEvent(
            action=action, id=sid, media=media, reason=reason, tie_break=tie_break
        )
    return None


def make_candidate_id() -> str:
    return uuid.uuid4().hex[:12]


def _codec_to_xml(parent: ET.Element, codec: Codec) -> None:
    attrs = {
        "id": str(codec.payload_type),
        "name": codec.name,
        "clockrate": str(codec.clockrate),
    }
    if codec.channels > 1:
        attrs["channels"] = str(codec.channels)
    payload = ET.SubElement(parent, qname(NS_RTP, "payload-type"), attrs)
    for key, value in codec.parameters:
        ET.SubElement(
            payload,
            qname(NS_RTP, "parameter"),
            {"name": key, "value": value},
        )
    for fb_type, fb_subtype in codec.rtcp_feedback:
        fb_attrs = {"type": fb_type}
        if fb_subtype:
            fb_attrs["subtype"] = fb_subtype
        ET.SubElement(payload, qname(NS_RTCP_FB, "rtcp-fb"), fb_attrs)


def _candidate_to_xml(parent: ET.Element, candidate: IceCandidate) -> None:
    attrs = {
        "component": str(candidate.component),
        "foundation": candidate.foundation,
        "generation": str(candidate.generation),
        "id": make_candidate_id(),
        "ip": candidate.ip,
        "network": str(candidate.network),
        "port": str(candidate.port),
        "priority": str(candidate.priority),
        "protocol": candidate.protocol,
        "type": candidate.type,
    }
    if candidate.rel_addr:
        attrs["rel-addr"] = candidate.rel_addr
    if candidate.rel_port is not None:
        attrs["rel-port"] = str(candidate.rel_port)
    if candidate.tcp_type:
        attrs["tcptype"] = candidate.tcp_type
    ET.SubElement(parent, qname(NS_ICE_UDP, "candidate"), attrs)


def _description_owner_role(action: str) -> str:
    """Return the Jingle role of the peer that generated this SDP description."""
    return "responder" if action == "session-accept" else "initiator"


def _opposite_role(role: str) -> str:
    return "responder" if role == "initiator" else "initiator"


def _direction_to_senders(direction: str, owner_role: str) -> str:
    """Map local SDP direction to XEP-0166 senders for the description owner."""
    if direction == "sendonly":
        return owner_role
    if direction == "recvonly":
        return _opposite_role(owner_role)
    if direction == "inactive":
        return "none"
    return "both"


def _senders_to_direction(senders: str, owner_role: str) -> str:
    """Map XEP-0166 senders back to SDP direction for the description owner."""
    if senders == "none":
        return "inactive"
    if senders == owner_role:
        return "sendonly"
    if senders == _opposite_role(owner_role):
        return "recvonly"
    return "sendrecv"


def _media_to_content(
    section: MediaSection,
    creator: str,
    *,
    owner_role: str,
    include_description: bool = True,
) -> ET.Element:
    attrs = {"creator": creator, "name": section.mid}
    # transport-info identifies an existing content and carries no RTP
    # description. Do not restate a possibly stale senders value there.
    if include_description:
        attrs["senders"] = _direction_to_senders(section.direction, owner_role)
    content = ET.Element(qname(NS_JINGLE, "content"), attrs)
    if include_description:
        description = ET.SubElement(
            content, qname(NS_RTP, "description"), {"media": section.media}
        )
        for codec in section.codecs:
            _codec_to_xml(description, codec)
        if section.rtcp_mux:
            ET.SubElement(description, qname(NS_RTP, "rtcp-mux"))

    transport = ET.SubElement(
        content,
        qname(NS_ICE_UDP, "transport"),
        {"ufrag": section.ice_ufrag, "pwd": section.ice_pwd},
    )
    if section.fingerprint is not None:
        fp = ET.SubElement(
            transport,
            qname(NS_DTLS, "fingerprint"),
            {
                "hash": section.fingerprint.hash,
                "setup": section.fingerprint.setup,
            },
        )
        fp.text = section.fingerprint.value
    for candidate in section.candidates:
        _candidate_to_xml(transport, candidate)
    return content


def build_jingle(
    action: str,
    sid: str,
    *,
    initiator: str,
    responder: str | None,
    description: SessionDescription | None = None,
    creator: str = "initiator",
    reason: str | None = None,
) -> ET.Element:
    attrs = {"action": action, "sid": sid, "initiator": initiator}
    if responder:
        attrs["responder"] = responder
    root = ET.Element(qname(NS_JINGLE, "jingle"), attrs)

    if description is not None:
        # XEP-0338 maps the SDP group exactly to a Jingle group. A WebRTC
        # audio-only description can legitimately contain a one-member BUNDLE
        # group; dropping it changes the negotiated description on the wire.
        mids = description.bundle
        if mids:
            group = ET.SubElement(
                root, qname(NS_GROUPING, "group"), {"semantics": "BUNDLE"}
            )
            for mid in mids:
                ET.SubElement(group, qname(NS_GROUPING, "content"), {"name": mid})
        owner_role = _description_owner_role(action)
        for section in description.media:
            root.append(
                _media_to_content(
                    section,
                    creator,
                    owner_role=owner_role,
                    include_description=action != "transport-info",
                )
            )

    if reason is not None:
        reason_node = ET.SubElement(root, qname(NS_JINGLE, "reason"))
        ET.SubElement(reason_node, qname(NS_JINGLE, reason))
    return root


def _codec_from_xml(node: ET.Element) -> Codec:
    params = tuple(
        (item.attrib.get("name", ""), item.attrib.get("value", ""))
        for item in node.findall(qname(NS_RTP, "parameter"))
        if item.attrib.get("name")
    )
    feedback = tuple(
        (item.attrib.get("type", ""), item.attrib.get("subtype", ""))
        for item in node.findall(qname(NS_RTCP_FB, "rtcp-fb"))
        if item.attrib.get("type")
    )
    return Codec(
        payload_type=int(node.attrib["id"]),
        name=node.attrib["name"],
        clockrate=int(node.attrib.get("clockrate", "8000")),
        channels=int(node.attrib.get("channels", "1")),
        parameters=params,
        rtcp_feedback=feedback,
    )


def _candidate_from_xml(node: ET.Element) -> IceCandidate:
    return IceCandidate(
        foundation=node.attrib.get("foundation", "1"),
        component=int(node.attrib.get("component", "1")),
        protocol=node.attrib.get("protocol", "udp").lower(),
        priority=int(node.attrib.get("priority", "1")),
        ip=node.attrib["ip"],
        port=int(node.attrib["port"]),
        type=node.attrib.get("type", "host"),
        rel_addr=node.attrib.get("rel-addr"),
        rel_port=int(node.attrib["rel-port"]) if node.attrib.get("rel-port") else None,
        tcp_type=node.attrib.get("tcptype"),
        generation=int(node.attrib.get("generation", "0")),
        network=int(node.attrib.get("network", "0")),
    )


def parse_jingle(xml_or_element: str | ET.Element) -> JingleEvent | None:
    root = _as_element(xml_or_element)
    if root.tag != qname(NS_JINGLE, "jingle"):
        root = root.find(qname(NS_JINGLE, "jingle"))  # type: ignore[assignment]
        if root is None:
            return None

    action = root.attrib.get("action", "")
    sid = root.attrib.get("sid", "")
    if not action or not sid:
        return None

    owner_role = _description_owner_role(action)
    sections: list[MediaSection] = []
    for content in root.findall(qname(NS_JINGLE, "content")):
        description = content.find(qname(NS_RTP, "description"))
        transport = content.find(qname(NS_ICE_UDP, "transport"))
        if description is None and action != "transport-info":
            continue

        media_kind = (
            description.attrib.get("media", "audio")
            if description is not None
            else "audio"
        )
        section = MediaSection(
            media=media_kind,
            mid=content.attrib.get("name", media_kind),
            codecs=[],
        )
        if description is not None:
            section.direction = _senders_to_direction(
                content.attrib.get("senders", "both"), owner_role
            )
            section.codecs = [
                _codec_from_xml(item)
                for item in description.findall(qname(NS_RTP, "payload-type"))
                if item.attrib.get("id") and item.attrib.get("name")
            ]
            section.rtcp_mux = description.find(qname(NS_RTP, "rtcp-mux")) is not None

        if transport is not None:
            section.ice_ufrag = transport.attrib.get("ufrag", "")
            section.ice_pwd = transport.attrib.get("pwd", "")
            fp_node = transport.find(qname(NS_DTLS, "fingerprint"))
            if fp_node is not None and fp_node.text:
                section.fingerprint = Fingerprint(
                    fp_node.attrib.get("hash", "sha-256"),
                    fp_node.text.strip(),
                    fp_node.attrib.get("setup", "actpass"),
                )
            section.candidates = [
                _candidate_from_xml(item)
                for item in transport.findall(qname(NS_ICE_UDP, "candidate"))
                if item.attrib.get("ip") and item.attrib.get("port")
            ]
        sections.append(section)

    bundle: tuple[str, ...] = ()
    group = root.find(qname(NS_GROUPING, "group"))
    if group is not None and group.attrib.get("semantics") == "BUNDLE":
        bundle = tuple(
            item.attrib["name"]
            for item in group.findall(qname(NS_GROUPING, "content"))
            if item.attrib.get("name")
        )
    reason = None
    reason_node = root.find(qname(NS_JINGLE, "reason"))
    if reason_node is not None:
        for child in reason_node:
            if child.tag.startswith("{" + NS_JINGLE + "}") and not child.tag.endswith("}text"):
                reason = child.tag.rsplit("}", 1)[1]
                break

    return JingleEvent(
        action=action,
        sid=sid,
        initiator=root.attrib.get("initiator"),
        responder=root.attrib.get("responder"),
        description=SessionDescription(media=sections, bundle=bundle),
        reason=reason,
    )


def xml_text(element: ET.Element) -> str:
    return ET.tostring(element, encoding="unicode")


def store_hint() -> ET.Element:
    return ET.Element(qname(NS_HINTS, "store"))
