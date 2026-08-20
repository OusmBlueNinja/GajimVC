"""Production WebRTC compatibility layer.

This wraps the core media implementation with behaviour required by the
GStreamer versions shipped with current Gajim/Linux and Windows bundles.
"""

from __future__ import annotations

import logging

from gi.repository import GLib

from .media import (
    MediaUnavailable,
    WebRTCMediaEngine as _BaseWebRTCMediaEngine,
    probe_runtime,
)
from .sdp import IceCandidate, parse_sdp

log = logging.getLogger("gajim.p.gajim_calls.media")

__all__ = ["MediaUnavailable", "WebRTCMediaEngine", "probe_runtime"]


class WebRTCMediaEngine(_BaseWebRTCMediaEngine):
    """WebRTC engine with GStreamer/Jingle interoperability fixes."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        # BALANCED is still not implemented by some current webrtcbin builds.
        # MAX_BUNDLE is supported and maps cleanly to Jingle's BUNDLE group.
        try:
            self.webrtc.set_property(
                "bundle-policy", self.GstWebRTC.WebRTCBundlePolicy.MAX_BUNDLE
            )
        except Exception:
            log.warning("Could not enable MAX_BUNDLE", exc_info=True)

    @staticmethod
    def _dispatch(callback, *args) -> None:
        """Run application/XMPP/GTK callbacks on GLib's main thread."""

        def invoke():
            try:
                callback(*args)
            except Exception:
                log.exception("Unhandled exception in WebRTC application callback")
            return GLib.SOURCE_REMOVE

        GLib.idle_add(invoke)

    @staticmethod
    def _reply_error(reply) -> str | None:
        # GstPromise is allowed to be replied with no GstStructure. In
        # particular, set-local-description/set-remote-description commonly
        # complete successfully with a NULL reply. The old implementation
        # incorrectly treated that successful completion as an error.
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

    def _on_local_offer_set(self, promise, offer, _notify) -> None:
        error = self._reply_error(promise.get_reply())
        if error is not None:
            self._fail(f"Could not set local WebRTC offer: {error}")
            return
        description = parse_sdp(offer.sdp.as_text())
        self._state("local offer ready")
        self._dispatch(self._on_local_description, description)

    def _on_local_answer_set(self, promise, answer, _notify) -> None:
        error = self._reply_error(promise.get_reply())
        if error is not None:
            self._fail(f"Could not set local WebRTC answer: {error}")
            return
        description = parse_sdp(answer.sdp.as_text())
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
        # XEP-0176 is ICE-UDP. libnice/webrtcbin also emits ICE-TCP host
        # candidates on some platforms; putting those into an ICE-UDP Jingle
        # transport is invalid and causes interoperability problems with
        # strict XMPP clients. Keep the UDP candidates and ignore ICE-TCP.
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
        # Be defensive if a peer sends ICE-TCP inside an ICE-UDP transport.
        if not self._is_udp_candidate(candidate):
            log.debug("Ignoring non-UDP remote ICE candidate: %s", candidate)
            return
        super().add_remote_candidate(mid, candidate)
