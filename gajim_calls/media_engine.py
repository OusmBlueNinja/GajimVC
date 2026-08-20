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
)
from .sdp import IceCandidate, SessionDescription, parse_sdp

log = logging.getLogger("gajim.p.gajim_calls.media")

__all__ = ["MediaUnavailable", "WebRTCMediaEngine", "probe_runtime"]


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
        # The base media code's decoded-pad signal is emitted from a GStreamer
        # streaming thread. Never let its GTK paintable callback touch GTK there.
        remote_video = kwargs.get("on_remote_video")
        if remote_video is not None:
            kwargs["on_remote_video"] = lambda paintable: _idle(remote_video, paintable)

        super().__init__(*args, **kwargs)

        # BALANCED is not implemented by some current webrtcbin builds.
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
        # set-local-description/set-remote-description may complete successfully
        # with a NULL GstStructure reply.
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

    def _fail(self, reason: str) -> None:
        if self._failed:
            return
        self._failed = True
        details = (
            f"{reason}; local ICE candidates={self._local_candidate_count}, "
            f"remote ICE candidates={self._remote_candidate_count}"
        )
        log.error("WebRTC failure: %s", details)
        self._dispatch(self._on_failed, details)

    @staticmethod
    def _is_udp_candidate(candidate: str) -> bool:
        try:
            return IceCandidate.from_sdp(candidate).protocol == "udp"
        except (ValueError, TypeError):
            return False

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

    def _on_local_ice(
        self, _element, mline_index: int, candidate: str
    ) -> None:
        # XEP-0176 is ICE-UDP. Ignore ICE-TCP candidates emitted by libnice.
        if not self._is_udp_candidate(candidate):
            log.debug("Ignoring non-UDP local ICE candidate: %s", candidate)
            return

        self._local_candidate_count += 1
        local = self.webrtc.get_property("local-description")
        mid = str(mline_index)
        if local is not None:
            parsed = parse_sdp(local.sdp.as_text())
            if mline_index < len(parsed.media):
                mid = parsed.media[mline_index].mid
        log.info("TX ICE candidate mid=%s %s", mid, candidate)
        self._dispatch(self._on_ice_candidate, mid, candidate)

    def add_remote_candidate(self, mid: str, candidate: str) -> None:
        if not self._is_udp_candidate(candidate):
            log.debug("Ignoring non-UDP remote ICE candidate: %s", candidate)
            return
        super().add_remote_candidate(mid, candidate)

    def _on_decoded_pad(self, decode, pad) -> None:
        # gtk4paintablesink/gtksink create GTK/GDK objects. GStreamer's
        # decodebin emits pad-added from a streaming thread, so video sink setup
        # must be moved to GTK's main context. Audio has no GTK objects.
        caps = pad.get_current_caps() or pad.query_caps(None)
        text = caps.to_string() if caps is not None else ""
        if text.startswith("video/") and not self._test_mode:
            self._dispatch(self._attach_decoded_video_on_main, decode, pad)
            return
        super()._on_decoded_pad(decode, pad)

    def _attach_decoded_video_on_main(self, decode, pad) -> None:
        super()._on_decoded_pad(decode, pad)


class WebRTCMediaEngine:
    """Non-blocking facade around the GStreamer media engine.

    GStreamer device probing and state changes can take long enough on Windows
    to starve GTK. Construction/start/close therefore happen on a daemon worker
    while all application callbacks are marshalled back to GLib's main loop.
    """

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

        # Signal Jingle first; only then apply a very-early remote answer.
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

        # State -> NULL can block while Windows audio/video devices unwind.
        # Never make the GTK close-request callback wait for that.
        threading.Thread(
            target=engine.close,
            name="gajim-calls-media-stop",
            daemon=True,
        ).start()
