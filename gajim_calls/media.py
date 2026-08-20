"""GStreamer WebRTC media engine.

GStreamer is imported lazily so the plugin can show a useful dependency error
instead of making Gajim fail while scanning plugins.
"""

from __future__ import annotations

from collections.abc import Callable
import logging

from .sdp import SessionDescription, build_sdp, parse_sdp

log = logging.getLogger("gajim.p.gajim_calls.media")


class MediaUnavailable(RuntimeError):
    pass


def _load_gst():
    try:
        import gi

        gi.require_version("Gst", "1.0")
        gi.require_version("GstWebRTC", "1.0")
        gi.require_version("GstSdp", "1.0")
        from gi.repository import Gst, GstSdp, GstWebRTC
    except Exception as exc:
        raise MediaUnavailable(
            "GStreamer WebRTC bindings are missing (Gst, GstWebRTC, GstSdp)"
        ) from exc
    Gst.init(None)
    return Gst, GstSdp, GstWebRTC


def probe_runtime() -> tuple[bool, str]:
    try:
        Gst, _, _ = _load_gst()
    except MediaUnavailable as exc:
        return False, str(exc)

    required = (
        "webrtcbin",
        "opusenc",
        "rtpopuspay",
        "autoaudiosrc",
        "autoaudiosink",
        "decodebin",
    )
    missing = [
        name for name in required if Gst.ElementFactory.find(name) is None
    ]
    if missing:
        return False, "Missing GStreamer elements: " + ", ".join(missing)
    return True, ""


class WebRTCMediaEngine:
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
    ) -> None:
        self.Gst, self.GstSdp, self.GstWebRTC = _load_gst()
        self.video = video
        self._on_local_description = on_local_description
        self._on_ice_candidate = on_ice_candidate
        self._on_connected = on_connected
        self._on_failed = on_failed
        self._on_remote_video = on_remote_video
        self._making_answer = False
        self._remote_description_set = False
        self._queued_remote_candidates: list[tuple[str, str]] = []

        self.pipeline = self.Gst.Pipeline.new("gajim-calls")
        self.webrtc = self.Gst.ElementFactory.make("webrtcbin", "webrtc")
        if self.webrtc is None:
            raise MediaUnavailable("GStreamer webrtcbin is unavailable")

        try:
            self.webrtc.set_property(
                "bundle-policy", self.GstWebRTC.WebRTCBundlePolicy.BALANCED
            )
        except Exception:
            log.debug("Could not set BALANCED bundle policy", exc_info=True)
        if stun_server:
            self.webrtc.set_property("stun-server", stun_server)
        if turn_server:
            self.webrtc.set_property("turn-server", turn_server)

        self.pipeline.add(self.webrtc)
        self.webrtc.connect("on-negotiation-needed", self._on_negotiation_needed)
        self.webrtc.connect("on-ice-candidate", self._on_local_ice)
        self.webrtc.connect("pad-added", self._on_incoming_pad)
        self.webrtc.connect("notify::connection-state", self._on_connection_state)

        self._add_audio_source()
        if video:
            self._add_video_source()

    def _parse_bin(self, description: str):
        try:
            return self.Gst.parse_bin_from_description(description, True)
        except Exception as exc:
            raise MediaUnavailable(f"Unable to build media pipeline: {exc}") from exc

    def _add_audio_source(self) -> None:
        src = self._parse_bin(
            "autoaudiosrc ! audioconvert ! audioresample ! "
            "queue ! opusenc inband-fec=true ! rtpopuspay pt=111"
        )
        self.pipeline.add(src)
        pad = src.get_static_pad("src")
        sink = self.webrtc.request_pad_simple("sink_%u")
        if pad is None or sink is None or pad.link(sink) != self.Gst.PadLinkReturn.OK:
            raise MediaUnavailable("Unable to connect microphone to WebRTC")

    def _add_video_source(self) -> None:
        if self.Gst.ElementFactory.find("vp8enc") is None:
            raise MediaUnavailable("VP8 encoder (vp8enc) is missing")
        if self.Gst.ElementFactory.find("rtpvp8pay") is None:
            raise MediaUnavailable("RTP VP8 payloader is missing")
        src = self._parse_bin(
            "autovideosrc ! videoconvert ! videoscale ! "
            "video/x-raw,width=1280,height=720,framerate=30/1 ! "
            "queue ! vp8enc deadline=1 cpu-used=8 keyframe-max-dist=60 ! "
            "rtpvp8pay pt=96 picture-id-mode=15-bit"
        )
        self.pipeline.add(src)
        pad = src.get_static_pad("src")
        sink = self.webrtc.request_pad_simple("sink_%u")
        if pad is None or sink is None or pad.link(sink) != self.Gst.PadLinkReturn.OK:
            raise MediaUnavailable("Unable to connect camera to WebRTC")

    def start_offer(self) -> None:
        self._making_answer = False
        self.pipeline.set_state(self.Gst.State.PLAYING)

    def start_answer(self, remote: SessionDescription) -> None:
        self._making_answer = True
        self.pipeline.set_state(self.Gst.State.PLAYING)
        self._set_remote_description(remote, answer=False, then_answer=True)

    def set_remote_answer(self, remote: SessionDescription) -> None:
        self._set_remote_description(remote, answer=True, then_answer=False)

    def add_remote_candidate(self, mid: str, candidate: str) -> None:
        if not self._remote_description_set:
            self._queued_remote_candidates.append((mid, candidate))
            return
        self._add_remote_candidate_now(mid, candidate)

    def _add_remote_candidate_now(self, mid: str, candidate: str) -> None:
        index = 0
        # GStreamer's signal takes m-line index, so map the mid from the current
        # local SDP when possible.
        local = self.webrtc.get_property("local-description")
        if local is not None:
            parsed = parse_sdp(local.sdp.as_text())
            for idx, section in enumerate(parsed.media):
                if section.mid == mid:
                    index = idx
                    break
        self.webrtc.emit("add-ice-candidate", index, candidate)

    def _on_negotiation_needed(self, _element) -> None:
        if self._making_answer:
            return
        promise = self.Gst.Promise.new_with_change_func(
            self._on_offer_created, None, None
        )
        self.webrtc.emit("create-offer", None, promise)

    def _on_offer_created(self, promise, _user_data, _notify) -> None:
        reply = promise.get_reply()
        offer = reply.get_value("offer")
        self.webrtc.emit("set-local-description", offer, self.Gst.Promise.new())
        self._on_local_description(parse_sdp(offer.sdp.as_text()))

    def _create_answer(self) -> None:
        promise = self.Gst.Promise.new_with_change_func(
            self._on_answer_created, None, None
        )
        self.webrtc.emit("create-answer", None, promise)

    def _on_answer_created(self, promise, _user_data, _notify) -> None:
        reply = promise.get_reply()
        answer = reply.get_value("answer")
        self.webrtc.emit("set-local-description", answer, self.Gst.Promise.new())
        self._on_local_description(parse_sdp(answer.sdp.as_text()))

    def _set_remote_description(
        self,
        description: SessionDescription,
        *,
        answer: bool,
        then_answer: bool,
    ) -> None:
        text = build_sdp(description, answer=answer)
        result, sdp_message = self.GstSdp.SDPMessage.new()
        if result != self.GstSdp.SDPResult.OK:
            self._on_failed("Could not allocate SDP message")
            return
        result = self.GstSdp.sdp_message_parse_buffer(
            text.encode("utf-8"), sdp_message
        )
        if result != self.GstSdp.SDPResult.OK:
            self._on_failed("Peer sent an SDP/Jingle description we could not parse")
            return
        sdp_type = (
            self.GstWebRTC.WebRTCSDPType.ANSWER
            if answer
            else self.GstWebRTC.WebRTCSDPType.OFFER
        )
        session = self.GstWebRTC.WebRTCSessionDescription.new(sdp_type, sdp_message)
        promise = self.Gst.Promise.new_with_change_func(
            self._on_remote_set if then_answer else self._on_remote_set_noop,
            None,
            None,
        )
        self.webrtc.emit("set-remote-description", session, promise)

    def _flush_remote_candidates(self) -> None:
        queued = self._queued_remote_candidates
        self._queued_remote_candidates = []
        for mid, candidate in queued:
            self._add_remote_candidate_now(mid, candidate)

    def _on_remote_set(self, _promise, _user_data, _notify) -> None:
        self._remote_description_set = True
        self._flush_remote_candidates()
        self._create_answer()

    def _on_remote_set_noop(self, _promise, _user_data, _notify) -> None:
        self._remote_description_set = True
        self._flush_remote_candidates()

    def _on_local_ice(self, _element, mline_index: int, candidate: str) -> None:
        local = self.webrtc.get_property("local-description")
        mid = str(mline_index)
        if local is not None:
            parsed = parse_sdp(local.sdp.as_text())
            if mline_index < len(parsed.media):
                mid = parsed.media[mline_index].mid
        self._on_ice_candidate(mid, candidate)

    def _on_connection_state(self, element, _pspec) -> None:
        state = element.get_property("connection-state")
        name = getattr(state, "value_nick", str(state)).lower()
        if "connected" == name:
            self._on_connected()
        elif name in {"failed", "closed"}:
            self._on_failed(f"WebRTC connection state: {name}")

    def _on_incoming_pad(self, _element, pad) -> None:
        if pad.get_direction() != self.Gst.PadDirection.SRC:
            return
        decode = self.Gst.ElementFactory.make("decodebin")
        if decode is None:
            self._on_failed("decodebin is missing")
            return
        self.pipeline.add(decode)
        decode.sync_state_with_parent()
        pad.link(decode.get_static_pad("sink"))
        decode.connect("pad-added", self._on_decoded_pad)

    def _on_decoded_pad(self, _decode, pad) -> None:
        caps = pad.get_current_caps() or pad.query_caps(None)
        text = caps.to_string() if caps is not None else ""
        if text.startswith("audio/"):
            sink_bin = self._parse_bin("queue ! audioconvert ! audioresample ! autoaudiosink")
            self.pipeline.add(sink_bin)
            sink_bin.sync_state_with_parent()
            pad.link(sink_bin.get_static_pad("sink"))
            return

        if not text.startswith("video/"):
            return

        sink = None
        if self.Gst.ElementFactory.find("gtk4paintablesink") is not None:
            sink = self.Gst.ElementFactory.make("gtk4paintablesink")
        elif self.Gst.ElementFactory.find("gtksink") is not None:
            sink = self.Gst.ElementFactory.make("gtksink")

        if sink is not None:
            self.pipeline.add(sink)
            sink.sync_state_with_parent()
            pad.link(sink.get_static_pad("sink"))
            try:
                paintable = sink.get_property("paintable")
            except Exception:
                paintable = None
            if paintable is not None and self._on_remote_video is not None:
                self._on_remote_video(paintable)
            return

        sink_bin = self._parse_bin("queue ! videoconvert ! autovideosink")
        self.pipeline.add(sink_bin)
        sink_bin.sync_state_with_parent()
        pad.link(sink_bin.get_static_pad("sink"))

    def set_microphone_enabled(self, enabled: bool) -> None:
        # webrtcbin transceivers expose direction, but muting at source level is
        # safer across GStreamer versions. Locate autoaudiosrc and block output.
        iterator = self.pipeline.iterate_elements()
        while True:
            result, element = iterator.next()
            if result != self.Gst.IteratorResult.OK:
                break
            factory = element.get_factory()
            if factory is not None and factory.get_name() == "autoaudiosrc":
                element.set_property("mute", not enabled) if element.find_property("mute") else None

    def close(self) -> None:
        self.pipeline.set_state(self.Gst.State.NULL)
