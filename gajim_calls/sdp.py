"""Small SDP <-> Jingle-friendly model translator.

The media engine uses GStreamer's webrtcbin, which speaks SDP. XMPP peers speak
Jingle RTP XML. This module is deliberately dependency-free so translation can
be unit-tested without GTK, GStreamer, or Gajim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re


@dataclass(slots=True, frozen=True)
class Codec:
    payload_type: int
    name: str
    clockrate: int
    channels: int = 1
    parameters: tuple[tuple[str, str], ...] = ()
    rtcp_feedback: tuple[tuple[str, str], ...] = ()


@dataclass(slots=True, frozen=True)
class Fingerprint:
    hash: str
    value: str
    setup: str = "actpass"


@dataclass(slots=True, frozen=True)
class IceCandidate:
    foundation: str
    component: int
    protocol: str
    priority: int
    ip: str
    port: int
    type: str
    rel_addr: str | None = None
    rel_port: int | None = None
    tcp_type: str | None = None
    generation: int = 0
    network: int = 0

    @classmethod
    def from_sdp(cls, line: str) -> "IceCandidate":
        value = line.strip()
        if value.startswith("a="):
            value = value[2:]
        if value.startswith("candidate:"):
            value = value[len("candidate:") :]
        parts = value.split()
        if len(parts) < 8 or parts[6].lower() != "typ":
            raise ValueError(f"Invalid ICE candidate: {line!r}")
        extras = dict(zip(parts[8::2], parts[9::2], strict=False))
        return cls(
            foundation=parts[0],
            component=int(parts[1]),
            protocol=parts[2].lower(),
            priority=int(parts[3]),
            ip=parts[4],
            port=int(parts[5]),
            type=parts[7].lower(),
            rel_addr=extras.get("raddr"),
            rel_port=int(extras["rport"]) if "rport" in extras else None,
            tcp_type=extras.get("tcptype"),
            generation=int(extras.get("generation", "0")),
            network=int(extras.get("network-id", "0")),
        )

    def to_sdp(self) -> str:
        items = [
            f"candidate:{self.foundation}",
            str(self.component),
            self.protocol.upper(),
            str(self.priority),
            self.ip,
            str(self.port),
            "typ",
            self.type,
        ]
        if self.rel_addr:
            items += ["raddr", self.rel_addr]
        if self.rel_port is not None:
            items += ["rport", str(self.rel_port)]
        if self.tcp_type:
            items += ["tcptype", self.tcp_type]
        items += ["generation", str(self.generation)]
        return " ".join(items)


@dataclass(slots=True)
class MediaSection:
    media: str
    mid: str
    codecs: list[Codec] = field(default_factory=list)
    ice_ufrag: str = ""
    ice_pwd: str = ""
    fingerprint: Fingerprint | None = None
    candidates: list[IceCandidate] = field(default_factory=list)
    rtcp_mux: bool = True
    direction: str = "sendrecv"


@dataclass(slots=True)
class SessionDescription:
    media: list[MediaSection]
    bundle: tuple[str, ...] = ()


_RTPMAP = re.compile(r"^a=rtpmap:(\d+)\s+([^/]+)/(\d+)(?:/(\d+))?$")
_FMTP = re.compile(r"^a=fmtp:(\d+)\s+(.+)$")
_RTCP_FB = re.compile(r"^a=rtcp-fb:(\d+|\*)\s+(\S+)(?:\s+(.+))?$")


def parse_sdp(sdp: str) -> SessionDescription:
    lines = [line.strip() for line in sdp.replace("\r\n", "\n").split("\n") if line.strip()]
    session_ice_ufrag = ""
    session_ice_pwd = ""
    session_fp: Fingerprint | None = None
    session_setup = "actpass"
    session_direction = "sendrecv"
    bundle: tuple[str, ...] = ()
    media: list[MediaSection] = []
    current: MediaSection | None = None
    codec_maps: list[dict[int, Codec]] = []
    wildcard_feedback: list[list[tuple[str, str]]] = []

    for line in lines:
        if line.startswith("m="):
            parts = line[2:].split()
            if len(parts) < 4:
                continue
            current = MediaSection(
                media=parts[0],
                mid=parts[0],
                rtcp_mux=False,
                direction=session_direction,
            )
            current.ice_ufrag = session_ice_ufrag
            current.ice_pwd = session_ice_pwd
            current.fingerprint = session_fp
            media.append(current)
            codec_maps.append({})
            wildcard_feedback.append([])
            continue

        target = current
        if line.startswith("a=group:BUNDLE "):
            bundle = tuple(line.split()[1:])
            continue
        if line in ("a=sendrecv", "a=sendonly", "a=recvonly", "a=inactive"):
            if target is None:
                session_direction = line[2:]
            else:
                target.direction = line[2:]
            continue
        if line.startswith("a=ice-ufrag:"):
            val = line.split(":", 1)[1]
            if target is None:
                session_ice_ufrag = val
            else:
                target.ice_ufrag = val
            continue
        if line.startswith("a=ice-pwd:"):
            val = line.split(":", 1)[1]
            if target is None:
                session_ice_pwd = val
            else:
                target.ice_pwd = val
            continue
        if line.startswith("a=setup:"):
            val = line.split(":", 1)[1]
            if target is None:
                session_setup = val
                if session_fp is not None:
                    session_fp = Fingerprint(session_fp.hash, session_fp.value, val)
            elif target.fingerprint is not None:
                target.fingerprint = Fingerprint(
                    target.fingerprint.hash, target.fingerprint.value, val
                )
            continue
        if line.startswith("a=fingerprint:"):
            body = line.split(":", 1)[1]
            hash_name, value = body.split(None, 1)
            fp = Fingerprint(hash_name.lower(), value.strip(), session_setup)
            if target is None:
                session_fp = fp
            else:
                target.fingerprint = fp
            continue
        if target is None:
            continue
        if line.startswith("a=mid:"):
            target.mid = line.split(":", 1)[1]
            continue
        if line == "a=rtcp-mux":
            target.rtcp_mux = True
            continue
        if line.startswith("a=candidate:"):
            target.candidates.append(IceCandidate.from_sdp(line))
            continue

        match = _RTPMAP.match(line)
        if match:
            pt = int(match.group(1))
            codec = Codec(
                payload_type=pt,
                name=match.group(2),
                clockrate=int(match.group(3)),
                channels=int(match.group(4) or "1"),
            )
            codec_maps[-1][pt] = codec
            continue

        match = _FMTP.match(line)
        if match:
            pt = int(match.group(1))
            codec = codec_maps[-1].get(pt)
            if codec is None:
                continue
            params: list[tuple[str, str]] = []
            for item in match.group(2).split(";"):
                item = item.strip()
                if not item:
                    continue
                if "=" in item:
                    key, value = item.split("=", 1)
                    params.append((key.strip(), value.strip()))
                else:
                    params.append((item, ""))
            codec_maps[-1][pt] = Codec(
                codec.payload_type,
                codec.name,
                codec.clockrate,
                codec.channels,
                tuple(params),
                codec.rtcp_feedback,
            )
            continue

        match = _RTCP_FB.match(line)
        if match:
            feedback = (match.group(2), match.group(3) or "")
            if match.group(1) == "*":
                wildcard_feedback[-1].append(feedback)
                continue
            pt = int(match.group(1))
            codec = codec_maps[-1].get(pt)
            if codec is None:
                continue
            codec_maps[-1][pt] = Codec(
                codec.payload_type,
                codec.name,
                codec.clockrate,
                codec.channels,
                codec.parameters,
                codec.rtcp_feedback + (feedback,),
            )

    for section, mapping, wildcard in zip(media, codec_maps, wildcard_feedback, strict=True):
        if wildcard:
            for pt, codec in list(mapping.items()):
                merged = codec.rtcp_feedback + tuple(
                    feedback for feedback in wildcard if feedback not in codec.rtcp_feedback
                )
                mapping[pt] = Codec(
                    codec.payload_type,
                    codec.name,
                    codec.clockrate,
                    codec.channels,
                    codec.parameters,
                    merged,
                )
        section.codecs = list(mapping.values())
        if section.fingerprint is None:
            section.fingerprint = session_fp
        if not section.ice_ufrag:
            section.ice_ufrag = session_ice_ufrag
        if not section.ice_pwd:
            section.ice_pwd = session_ice_pwd

    return SessionDescription(media=media, bundle=bundle)


def build_sdp(description: SessionDescription, *, answer: bool = False) -> str:
    mids = description.bundle
    lines = [
        "v=0",
        "o=- 0 0 IN IP4 0.0.0.0",
        "s=-",
        "t=0 0",
    ]
    if mids:
        lines.append("a=group:BUNDLE " + " ".join(mids))
    lines.append("a=msid-semantic: WMS *")

    for section in description.media:
        payloads = " ".join(str(codec.payload_type) for codec in section.codecs) or "96"
        lines.extend(
            [
                f"m={section.media} 9 UDP/TLS/RTP/SAVPF {payloads}",
                "c=IN IP4 0.0.0.0",
                f"a=mid:{section.mid}",
                f"a={section.direction}",
                "a=rtcp:9 IN IP4 0.0.0.0",
            ]
        )
        if section.rtcp_mux:
            lines.append("a=rtcp-mux")
        if section.ice_ufrag:
            lines.append(f"a=ice-ufrag:{section.ice_ufrag}")
        if section.ice_pwd:
            lines.append(f"a=ice-pwd:{section.ice_pwd}")
        if section.fingerprint is not None:
            lines.append(
                f"a=fingerprint:{section.fingerprint.hash} {section.fingerprint.value}"
            )
            setup = section.fingerprint.setup
            if answer and setup == "actpass":
                setup = "active"
            lines.append(f"a=setup:{setup}")

        for codec in section.codecs:
            channels = f"/{codec.channels}" if codec.channels > 1 else ""
            lines.append(
                f"a=rtpmap:{codec.payload_type} "
                f"{codec.name}/{codec.clockrate}{channels}"
            )
            if codec.parameters:
                values = []
                for key, value in codec.parameters:
                    values.append(f"{key}={value}" if value else key)
                lines.append(f"a=fmtp:{codec.payload_type} " + ";".join(values))
            for fb_type, fb_subtype in codec.rtcp_feedback:
                suffix = f" {fb_subtype}" if fb_subtype else ""
                lines.append(f"a=rtcp-fb:{codec.payload_type} {fb_type}{suffix}")

        for candidate in section.candidates:
            lines.append("a=" + candidate.to_sdp())
        if section.candidates:
            lines.append("a=end-of-candidates")

    return "\r\n".join(lines) + "\r\n"
