#!/usr/bin/env python3
"""End-to-end ICE/DTLS smoke test using two real GStreamer webrtcbin peers."""

from __future__ import annotations

from pathlib import Path
import logging
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gi  # noqa: E402

gi.require_version("GLib", "2.0")
from gi.repository import GLib  # noqa: E402

from gajim_calls.media import WebRTCMediaEngine, probe_runtime  # noqa: E402


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


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

    offer_seen = False
    answer_seen = False

    def offerer_description(description) -> None:
        nonlocal offer_seen
        if offer_seen:
            return
        offer_seen = True
        print("offerer: forwarding offer to answerer")
        peers["answerer"].start_answer(description)

    def answerer_description(description) -> None:
        nonlocal answer_seen
        if answer_seen:
            return
        answer_seen = True
        print("answerer: forwarding answer to offerer")
        peers["offerer"].set_remote_answer(description)

    def offerer_candidate(mid: str, candidate: str) -> None:
        peers["answerer"].add_remote_candidate(mid, candidate)

    def answerer_candidate(mid: str, candidate: str) -> None:
        peers["offerer"].add_remote_candidate(mid, candidate)

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
        failures.append("Timed out waiting for ICE/DTLS connection")
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
        print("\nWebRTC loopback FAILED", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        print("\nState history:", file=sys.stderr)
        for item in states:
            print(f"  {item}", file=sys.stderr)
        return 1

    if connected != {"offerer", "answerer"}:
        print(f"Only connected: {sorted(connected)}", file=sys.stderr)
        return 1

    print("WebRTC loopback OK: offer/answer, trickle ICE, DTLS, and RTP connected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
