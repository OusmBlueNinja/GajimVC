#!/usr/bin/env python3
"""End-to-end Jingle + ICE/DTLS smoke tests with real GStreamer peers."""

from __future__ import annotations

from pathlib import Path
import logging
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gi  # noqa: E402

gi.require_version("GLib", "2.0")
from gi.repository import GLib  # noqa: E402

from gajim_calls.incoming import RemoteCandidateBuffer  # noqa: E402
from gajim_calls.media_engine import WebRTCMediaEngine, probe_runtime  # noqa: E402
from gajim_calls.protocol import JingleEvent, build_jingle, parse_jingle, xml_text  # noqa: E402
from gajim_calls.sdp import IceCandidate, MediaSection, SessionDescription  # noqa: E402


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

INITIATOR = "offerer@example.test/desktop"
RESPONDER = "answerer@example.test/phone"


def jingle_event_round_trip(
    action: str,
    sid: str,
    description: SessionDescription,
) -> JingleEvent:
    """Serialize exactly as the plugin does, then parse like the receiving peer."""
    payload = build_jingle(
        action,
        sid,
        initiator=INITIATOR,
        responder=RESPONDER,
        description=description,
        creator="initiator",
    )
    event = parse_jingle(xml_text(payload))
    if event is None:
        raise RuntimeError(f"Could not parse generated {action} Jingle XML")
    if event.action != action or event.sid != sid:
        raise RuntimeError(
            f"Jingle round-trip mismatch: action={event.action!r} sid={event.sid!r}"
        )
    if event.description.bundle != description.bundle:
        raise RuntimeError(
            f"Jingle {action} changed BUNDLE {description.bundle!r} "
            f"to {event.description.bundle!r}"
        )
    return event


def candidate_event_round_trip(
    sid: str,
    local_description: SessionDescription,
    mid: str,
    candidate_text: str,
) -> JingleEvent:
    """Route one trickle candidate through real transport-info Jingle XML."""
    candidate = IceCandidate.from_sdp(candidate_text)
    local_section = next(
        (section for section in local_description.media if section.mid == mid),
        None,
    )
    if local_section is None:
        raise RuntimeError(f"No local media section for ICE mid {mid!r}")

    section = MediaSection(
        media=local_section.media,
        mid=mid,
        ice_ufrag=local_section.ice_ufrag,
        ice_pwd=local_section.ice_pwd,
        fingerprint=local_section.fingerprint,
        candidates=[candidate],
    )
    return jingle_event_round_trip(
        "transport-info",
        sid,
        SessionDescription(media=[section], bundle=()),
    )


def run_case(*, video: bool) -> list[str]:
    label = "audio-video" if video else "audio-only"
    sid = f"ci-jingle-webrtc-{label}"
    loop = GLib.MainLoop()
    connected: set[str] = set()
    failures: list[str] = []
    states: list[str] = []
    peers: dict[str, WebRTCMediaEngine] = {}
    local_descriptions: dict[str, SessionDescription] = {}
    queued_candidates: dict[str, list[tuple[str, str]]] = {
        "offerer": [],
        "answerer": [],
    }
    pre_session_ice = RemoteCandidateBuffer()
    answerer_started = False
    offer_description: SessionDescription | None = None
    expected_media = 2 if video else 1

    def state(peer: str, value: str) -> None:
        line = f"{label}/{peer}: {value}"
        states.append(line)
        print(line)

    def failed(peer: str, reason: str) -> None:
        failures.append(f"{label}/{peer}: {reason}")
        loop.quit()

    def ready(peer: str) -> None:
        connected.add(peer)
        print(f"{label}/{peer}: CONNECTED")
        if connected == {"offerer", "answerer"}:
            loop.quit()

    def round_trip(action: str, description: SessionDescription) -> SessionDescription:
        return jingle_event_round_trip(action, sid, description).description

    def start_answer_after_pre_session_ice() -> None:
        nonlocal answerer_started
        if answerer_started or offer_description is None:
            return
        if pre_session_ice.count(sid) == 0:
            return

        answerer_started = True
        print(f"{label}/answerer: receiving session-initiate after pre-session ICE")
        remote = round_trip("session-initiate", offer_description)
        peers["answerer"].start_answer(remote)
        flushed = pre_session_ice.flush_to(peers["answerer"], sid)
        print(f"{label}/answerer: applied {flushed} pre-session ICE candidate(s)")
        if flushed == 0:
            failures.append(f"{label}: pre-session ICE buffer was not exercised")
            loop.quit()

    def deliver_candidate(source: str, target: str, mid: str, candidate: str) -> None:
        local = local_descriptions.get(source)
        if local is None:
            queued_candidates[source].append((mid, candidate))
            return

        event = candidate_event_round_trip(sid, local, mid, candidate)
        if source == "offerer" and target == "answerer" and not answerer_started:
            added = pre_session_ice.add_event(event)
            print(f"{label}/answerer: buffered {added} ICE before session-initiate")
            start_answer_after_pre_session_ice()
            return

        for section in event.description.media:
            for parsed_candidate in section.candidates:
                peers[target].add_remote_candidate(section.mid, parsed_candidate.to_sdp())

    def flush_candidates(source: str, target: str) -> None:
        queued = queued_candidates[source]
        queued_candidates[source] = []
        for mid, candidate in queued:
            deliver_candidate(source, target, mid, candidate)

    answer_seen = False

    def offerer_description_ready(description: SessionDescription) -> None:
        nonlocal offer_description
        local_descriptions["offerer"] = description
        if offer_description is None:
            if len(description.media) != expected_media:
                failures.append(
                    f"{label}: offerer produced {len(description.media)} media sections, "
                    f"expected {expected_media}"
                )
                loop.quit()
                return
            offer_description = description
            print(f"{label}/offerer: local offer ready; waiting for first ICE candidate")
        flush_candidates("offerer", "answerer")

    def answerer_description_ready(description: SessionDescription) -> None:
        nonlocal answer_seen
        local_descriptions["answerer"] = description
        flush_candidates("answerer", "offerer")
        if answer_seen:
            return
        answer_seen = True
        if len(description.media) != expected_media:
            failures.append(
                f"{label}: answerer produced {len(description.media)} media sections, "
                f"expected {expected_media}"
            )
            loop.quit()
            return
        print(f"{label}/answerer: serializing session-accept Jingle")
        remote = round_trip("session-accept", description)
        peers["offerer"].set_remote_answer(remote)

    peers["offerer"] = WebRTCMediaEngine(
        video=video,
        on_local_description=offerer_description_ready,
        on_ice_candidate=lambda mid, candidate: deliver_candidate(
            "offerer", "answerer", mid, candidate
        ),
        on_connected=lambda: ready("offerer"),
        on_failed=lambda reason: failed("offerer", reason),
        on_state=lambda value: state("offerer", value),
        test_mode=True,
    )
    peers["answerer"] = WebRTCMediaEngine(
        video=video,
        on_local_description=answerer_description_ready,
        on_ice_candidate=lambda mid, candidate: deliver_candidate(
            "answerer", "offerer", mid, candidate
        ),
        on_connected=lambda: ready("answerer"),
        on_failed=lambda reason: failed("answerer", reason),
        on_state=lambda value: state("answerer", value),
        test_mode=True,
    )

    def timeout() -> bool:
        failures.append(f"{label}: timed out waiting for Jingle ICE/DTLS connection")
        loop.quit()
        return GLib.SOURCE_REMOVE

    timeout_id = GLib.timeout_add_seconds(25, timeout)
    try:
        peers["offerer"].start_offer()
        loop.run()
    finally:
        GLib.source_remove(timeout_id)
        for peer in peers.values():
            peer.close()

    if connected != {"offerer", "answerer"} and not failures:
        failures.append(f"{label}: only connected {sorted(connected)}")

    if failures:
        print(f"\n{label} state history:", file=sys.stderr)
        for item in states:
            print(f"  {item}", file=sys.stderr)
    else:
        print(
            f"{label}: Jingle WebRTC loopback OK: session-initiate/session-accept, "
            "pre-session trickle ICE, DTLS, and RTP connected"
        )
    return failures


def main() -> int:
    ok, reason = probe_runtime()
    if not ok:
        print(f"Runtime probe failed: {reason}", file=sys.stderr)
        return 2

    failures: list[str] = []
    # Exercise the production audio-call shape first. The previous integration
    # test used video=True exclusively, which masked audio-only signaling bugs.
    failures.extend(run_case(video=False))
    failures.extend(run_case(video=True))

    if failures:
        print("\nJingle WebRTC integration FAILED", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("All Jingle WebRTC integration cases passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
