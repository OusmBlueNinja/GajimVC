"""Thread-safe WebRTC compatibility layer for Gajim.

The public WebRTCMediaEngine is intentionally asynchronous: device discovery,
GStreamer pipeline startup and teardown must never block GTK's main loop.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from gi.repository import GLib

from .media import (
    MediaUnavailable,
    WebRTCMediaEngine as _BaseWebRTCMediaEngine,
    probe_runtime,
    probe_video_runtime,
)
from .sdp import IceCandidate, SessionDescription, parse_sdp

log = logging.getLogger("gajim.p.gajim_calls.media")

__all__ = [
    "MediaUnavailable",
    "WebRTCMediaEngine",
    "probe_runtime",
    "probe_video_runtime",
]


def _idle(callback: Callable, *args) -> None:
    """Invoke a Python/GTK callback from GLib's main context."""

    def invoke():
        try:
            callback(*args)
        except Exception:
            log.exception("Unhandled exception in WebRTC application callback")
        return GLib.SOURCE_REMOVE

    GLib.idle_add(invoke)


class _HardenedWebRTCMediaEngine(_BaseWebRTCMediaEngine):
    """Actual GStreamer engine with Jingle/GTK interoperability fixes."""

    def __init__(self, *args, **kwargs) -> None:
        remote_video = kwargs.get("on_remote_video")
        if remote_video is not None:
            kwargs["on_remote_video"] = lambda paintable: _idle(remote_video, paintable)

        self._candidate_stats: dict[str, dict[str, int]] = {
            "local": {},
            "remote": {},
        }
        self._stun_configured = bool(kwargs.get("stun_server"))
        self._turn_configured = bool(kwargs.get("turn_server"))

        super().__init__(*args, **kwargs)

        try:
            self.webrtc.set_property(
                "bundle-policy", self.GstWebRTC.WebRTCBundlePolicy.MAX_BUNDLE
            )
        except Exception:
            log.warning("Could not enable MAX_BUNDLE", exc_info=True)

    @staticmethod
    def _dispatch(callback: Callable, *args) -> None:
        _idle(callback, *args)

    @staticmethod
    def _reply_error(reply) -> str | None:
        if reply is None:
            return None
        try:
            error = reply.get_value("error")
        except Exception:
            error = None
        return None if error is None else str(error)

    def _state(self, text: str) -> None:
        log.info("WebRTC state: %s", text)
        if self._on_state is not None:
            self._dispatch(self._on_state, text)

    @staticmethod
    def _candidate_key(candidate: str) -> str:
        try:
            parsed = IceCandidate.from_sdp(candidate)
        except (ValueError, TypeError):
            return "invalid"
        return f"{parsed.type}/{parsed.protocol}"

    def _record_candidate(self, side: str, candidate: str) -> None:
        key = self._candidate_key(candidate)
        stats = self._candidate_stats[side]
        stats[key] = stats.get(key, 0) + 1

    def _format_candidate_stats(self, side: str) -> str:
        stats = self._candidate_stats[side]
        if not stats:
            return "none"
        return ",".join(f"{key}={stats[key]}" for key in sorted(stats))

    def _fail(self, reason: str) -> None:
        if self._failed:
            return
        self._failed = True
        details = (
            f"{reason}; local ICE candidates={self._local_candidate_count} "
            f"({self._format_candidate_stats('local')}), "
            f"remote ICE candidates={self._remote_candidate_count} "
            f"({self._format_candidate_stats('remote')}), "
            f"STUN={'yes' if self._stun_configured else 'no'}, "
            f"TURN={'yes' if self._turn_configured else 'no'}"
        )
        log.error("WebRTC failure: %s", details)
        self._dispatch(self._on_failed, details)

    def _on_local_offer_set(self, promise, _offer, _notify) -> None:
        error = self._reply_error(promise.get_reply())
        if error is not None:
            self._fail(f"Could not set local WebRTC offer: {error}")
            return
        local = self.webrtc.get_property("local-description")
        if local is None or local.sdp is None:
            self._fail("GStreamer did not retain the local WebRTC offer")
            return
        description = parse_sdp(local.sdp.as_text())
        self._state("local offer ready")
        self._dispatch(self._on_local_description, description)

    def _on_local_answer_set(self, promise, _answer, _notify) -> None:
        error = self._reply_error(promise.get_reply())
        if error is not None:
            self._fail(f"Could not set local WebRTC answer: {error}")
            return
        local = self.webrtc.get_property("local-description")
        if local is None or local.sdp is None:
            self._fail("GStreamer did not retain the local WebRTC answer")
            return
        description = parse_sdp(local.sdp.as_text())
        self._state("local answer ready")
        self._dispatch(self._on_local_description, description)

    def _on_connection_state(self, element, _pspec) -> None:
        state = element.get_property("connection-state")
        name = getattr(state, "value_nick", str(state)).lower()
        self._state(f"connection={name}")
        if name == "connected":
            self._dispatch(self._on_connected)
        elif name in {"failed", "closed"}:
            self._fail(f"WebRTC connection state: {name}")

    def _on_local_ice(self, element, mline_index: int, candidate: str) -> None:
        self._record_candidate("local", candidate)
        super()._on_local_ice(element, mline_index, candidate)

    def add_remote_candidate(self, mid: str, candidate: str) -> None:
        self._record_candidate("remote", candidate)
        super().add_remote_candidate(mid, candidate)

    def _on_decoded_pad(self, decode, pad) -> None:
        caps = pad.get_current_caps() or pad.query_caps(None)
        text = caps.to_string() if caps is not None else ""
        if text.startswith("video/") and not self._test_mode:
            self._dispatch(self._attach_decoded_video_on_main, decode, pad)
            return
        super()._on_decoded_pad(decode, pad)

    def _attach_decoded_video_on_main(self, decode, pad) -> None:
        super()._on_decoded_pad(decode, pad)


class WebRTCMediaEngine:
    """Non-blocking facade around the GStreamer media engine."""

    def __init__(
        self,
        *,
        video: bool,
        stun_server: str = "",
        turn_server: str = "",
        on_local_description: Callable[[SessionDescription], None],
        on_ice_candidate: Callable[[str, str], None],
        on_connected: Callable[[], None],
        on_failed: Callable[[str], None],
        on_remote_video: Callable[[object], None] | None = None,
        on_state: Callable[[str], None] | None = None,
        test_mode: bool = False,
    ) -> None:
        self._kwargs = {
            "video": video,
            "stun_server": stun_server,
            "turn_server": turn_server,
            "on_local_description": self._local_description_ready,
            "on_ice_candidate": on_ice_candidate,
            "on_connected": on_connected,
            "on_failed": on_failed,
            "on_remote_video": on_remote_video,
            "on_state": on_state,
            "test_mode": test_mode,
        }
        self._user_local_description = on_local_description
        self._on_failed = on_failed
        self._lock = threading.RLock()
        self._engine: _HardenedWebRTCMediaEngine | None = None
        self._starting = False
        self._closed = False
        self._local_ready = False
        self._pending_answer: SessionDescription | None = None
        self._pending_candidates: list[tuple[str, str]] = []
        self._mic_enabled: bool | None = None

    def _local_description_ready(self, description: SessionDescription) -> None:
        with self._lock:
            if self._closed:
                return
            self._local_ready = True
            engine = self._engine
            answer = self._pending_answer
            self._pending_answer = None

        self._user_local_description(description)
        if engine is not None and answer is not None:
            engine.set_remote_answer(answer)

    def _begin(self, mode: str, remote: SessionDescription | None) -> None:
        with self._lock:
            if self._closed or self._starting or self._engine is not None:
                return
            self._starting = True

        def worker() -> None:
            try:
                engine = _HardenedWebRTCMediaEngine(**self._kwargs)
            except Exception as exc:
                with self._lock:
                    self._starting = False
                log.exception("Unable to initialize GStreamer media")
                _idle(self._on_failed, f"Unable to initialize media: {exc}")
                return

            with self._lock:
                self._starting = False
                if self._closed:
                    should_close = True
                    candidates: list[tuple[str, str]] = []
                    mic_enabled = None
                else:
                    should_close = False
                    self._engine = engine
                    candidates = self._pending_candidates
                    self._pending_candidates = []
                    mic_enabled = self._mic_enabled

            if should_close:
                engine.close()
                return

            try:
                if mode == "offer":
                    engine.start_offer()
                else:
                    assert remote is not None
                    engine.start_answer(remote)

                for mid, candidate in candidates:
                    engine.add_remote_candidate(mid, candidate)
                if mic_enabled is not None:
                    engine.set_microphone_enabled(mic_enabled)
            except Exception as exc:
                log.exception("Unable to start GStreamer media")
                _idle(self._on_failed, f"Unable to start media: {exc}")

        threading.Thread(
            target=worker,
            name="gajim-calls-media-start",
            daemon=True,
        ).start()

    def start_offer(self) -> None:
        self._begin("offer", None)

    def start_answer(self, remote: SessionDescription) -> None:
        self._begin("answer", remote)

    def set_remote_answer(self, remote: SessionDescription) -> None:
        with self._lock:
            if self._closed:
                return
            engine = self._engine
            if engine is None or not self._local_ready:
                self._pending_answer = remote
                return
        engine.set_remote_answer(remote)

    def add_remote_candidate(self, mid: str, candidate: str) -> None:
        with self._lock:
            if self._closed:
                return
            engine = self._engine
            if engine is None:
                self._pending_candidates.append((mid, candidate))
                return
        engine.add_remote_candidate(mid, candidate)

    def set_microphone_enabled(self, enabled: bool) -> None:
        with self._lock:
            if self._closed:
                return
            self._mic_enabled = enabled
            engine = self._engine
        if engine is not None:
            engine.set_microphone_enabled(enabled)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            engine = self._engine
            self._engine = None
            self._pending_answer = None
            self._pending_candidates = []

        if engine is None:
            return

        threading.Thread(
            target=engine.close,
            name="gajim-calls-media-stop",
            daemon=True,
        ).start()
