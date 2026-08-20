#!/usr/bin/env python3
"""End-to-end Jingle + ICE/DTLS smoke test with real GStreamer peers."""

from __future__ import annotations

from pathlib import Path
import logging
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gi  # noqa: E402

gi.require_version("GLib", "2.0")
from gi.repository import GLib  # noqa: E402

from gajim_calls.media_engine import (  # noqa: E402
    WebRTCMediaEngine,
    probe_runtime,
)
from gajim_calls.protocol import build_jingle, parse_jingle, xml_text  # noqa: E402
from gajim_calls.sdp import (  # noqa: E402
    IceCandidate,
    MediaSection,
    SessionDescription,
)


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

SID = "ci-jingle-webrtc-loopback"
INITIATOR = "offerer@example.test/desktop"
RESPONDER = "answerer@example.test/phone"


def jingle_round_trip(action: str, description: SessionDescription) -> SessionDescription:
    """Serialize exactly as the plugin does, then parse like the receiving peer."""
    payload = build_jingle(
        action,
        SID,
        initiator=INITIATOR,
        responder=RESPONDER,
        description=description,
        creator="initiator",
    )
    xml = xml_text(payload)
    event = parse_jingle(xml)
    if event is None:
        raise RuntimeError(f"Could not parse generated {action} Jingle XML")
    if event.action != action or event.sid != SID:
        raise RuntimeError(
            f"Jingle round-trip mismatch: action={event.action!r} sid={event.sid!r}"
        )
    return event.description


def candidate_round_trip(
    local_description: SessionDescription,
    mid: str,
    candidate_text: str,
) -> list[tuple[str, str]]:
    """Route a trickle ICE candidate through transport-info Jingle XML."""
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
    parsed = jingle_round_trip(
        "transport-info",
        SessionDescription(media=[section], bundle=()),
    )
    return [
        (parsed_section.mid, parsed_candidate.to_sdp())
        for parsed_section in parsed.media
        for parsed_candidate in parsed_section.candidates
    ]


def main() -> int:
    ok, reason = probe_runtime()
    if not ok:
        print(f"Runtime probe failed: {reason}", file=sys.stderr)
        return 2

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

    def state(peer: str, value: str) -> None:
        line = f"{peer}: {value}"
        states.append(line)
        print(line)

    def failed(peer: str, reason: str) -> None:
        failures.append(f"{peer}: {reason}")
        loop.quit()

    def ready(peer: str) -> None:
        connected.add(peer)
        print(f"{peer}: CONNECTED")
        if connected == {"offerer", "answerer"}:
            loop.quit()

    def deliver_candidate(source: str, target: str, mid: str, candidate: str) -> None:
        local = local_descriptions.get(source)
        if local is None:
            queued_candidates[source].append((mid, candidate))
            return
        for parsed_mid, parsed_candidate in candidate_round_trip(local, mid, candidate):
            peers[target].add_remote_candidate(parsed_mid, parsed_candidate)

    def flush_candidates(source: str, target: str) -> None:
        queued = queued_candidates[source]
        queued_candidates[source] = []
        for mid, candidate in queued:
            deliver_candidate(source, target, mid, candidate)

    offer_seen = False
    answer_seen = False

    def offerer_description(description: SessionDescription) -> None:
        nonlocal offer_seen
        local_descriptions["offerer"] = description
        flush_candidates("offerer", "answerer")
        if offer_seen:
            return
        offer_seen = True
        print("offerer: serializing session-initiate Jingle")
        remote = jingle_round_trip("session-initiate", description)
        peers["answerer"].start_answer(remote)

    def answerer_description(description: SessionDescription) -> None:
        nonlocal answer_seen
        local_descriptions["answerer"] = description
        flush_candidates("answerer", "offerer")
        if answer_seen:
            return
        answer_seen = True
        print("answerer: serializing session-accept Jingle")
        remote = jingle_round_trip("session-accept", description)
        peers["offerer"].set_remote_answer(remote)

    def offerer_candidate(mid: str, candidate: str) -> None:
        deliver_candidate("offerer", "answerer", mid, candidate)

    def answerer_candidate(mid: str, candidate: str) -> None:
        deliver_candidate("answerer", "offerer", mid, candidate)

    peers["offerer"] = WebRTCMediaEngine(
        video=False,
        on_local_description=offerer_description,
        on_ice_candidate=offerer_candidate,
        on_connected=lambda: ready("offerer"),
        on_failed=lambda reason: failed("offerer", reason),
        on_state=lambda value: state("offerer", value),
        test_mode=True,
    )
    peers["answerer"] = WebRTCMediaEngine(
        video=False,
        on_local_description=answerer_description,
        on_ice_candidate=answerer_candidate,
        on_connected=lambda: ready("answerer"),
        on_failed=lambda reason: failed("answerer", reason),
        on_state=lambda value: state("answerer", value),
        test_mode=True,
    )

    def timeout() -> bool:
        failures.append("Timed out waiting for Jingle ICE/DTLS connection")
        loop.quit()
        return GLib.SOURCE_REMOVE

    timeout_id = GLib.timeout_add_seconds(20, timeout)

    try:
        peers["offerer"].start_offer()
        loop.run()
    finally:
        if timeout_id:
            GLib.source_remove(timeout_id)
        for peer in peers.values():
            peer.close()

    if failures:
        print("\nJingle WebRTC loopback FAILED", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        print("\nState history:", file=sys.stderr)
        for item in states:
            print(f"  {item}", file=sys.stderr)
        return 1

    if connected != {"offerer", "answerer"}:
        print(f"Only connected: {sorted(connected)}", file=sys.stderr)
        return 1

    print(
        "Jingle WebRTC loopback OK: session-initiate/session-accept, "
        "transport-info, trickle ICE, DTLS, and RTP connected"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
