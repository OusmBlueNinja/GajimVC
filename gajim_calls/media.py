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


def _missing_elements(Gst, names: tuple[str, ...]) -> list[str]:
    return [name for name in names if Gst.ElementFactory.find(name) is None]


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
    missing = _missing_elements(Gst, required)
    if missing:
        return False, "Missing GStreamer elements: " + ", ".join(missing)

    if (
        Gst.ElementFactory.find("nicesrc") is None
        or Gst.ElementFactory.find("nicesink") is None
    ):
        return False, "GStreamer libnice ICE plugin is missing (nicesrc/nicesink)"

    return True, ""


def probe_video_runtime() -> tuple[bool, str]:
    """Check the concrete VP8 capture/send/receive pieces we advertise."""
    ok, reason = probe_runtime()
    if not ok:
        return False, reason
    try:
        Gst, _, _ = _load_gst()
    except MediaUnavailable as exc:
        return False, str(exc)

    required = (
        "autovideosrc",
        "videoconvert",
        "videoscale",
        "vp8enc",
        "rtpvp8pay",
        "rtpvp8depay",
        "vp8dec",
    )
    missing = _missing_elements(Gst, required)
    if missing:
        return False, "Missing GStreamer video elements: " + ", ".join(missing)
    return True, ""


def _enum_name(value) -> str:
    name = getattr(value, "value_nick", None)
    if name is not None:
        return str(name).lower()
    return str(value).rsplit(".", 1)[-1].lower()


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
        on_state: Callable[[str], None] | None = None,
        test_mode: bool = False,
    ) -> None:
        self.Gst, self.GstSdp, self.GstWebRTC = _load_gst()
        self.video = video
        self._on_local_description = on_local_description
        self._on_ice_candidate = on_ice_candidate
        self._on_connected = on_connected
        self._on_failed = on_failed
        self._on_remote_video = on_remote_video
        self._on_state = on_state
        self._test_mode = test_mode
        self._making_answer = False
        self._remote_description_set = False
        self._queued_remote_candidates: list[tuple[str, str]] = []
        self._local_candidate_count = 0
        self._remote_candidate_count = 0
        self._failed = False

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
            log.info("Using configured STUN server %s", stun_server)
            self.webrtc.set_property("stun-server", stun_server)
        if turn_server:
            log.info("Using configured TURN server")
            self.webrtc.set_property("turn-server", turn_server)

        self.pipeline.add(self.webrtc)
        self.webrtc.connect("on-negotiation-needed", self._on_negotiation_needed)
        self.webrtc.connect("on-ice-candidate", self._on_local_ice)
        self.webrtc.connect("pad-added", self._on_incoming_pad)
        self.webrtc.connect(
            "notify::connection-state", self._on_connection_state
        )
        self.webrtc.connect(
            "notify::ice-connection-state", self._on_ice_connection_state
        )
        self.webrtc.connect(
            "notify::ice-gathering-state", self._on_ice_gathering_state
        )
        self.webrtc.connect("notify::signaling-state", self._on_signaling_state)

        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus_message)
        self._bus = bus

        self._add_audio_source()
        if video:
            self._add_video_source()

    def _state(self, text: str) -> None:
        log.info("WebRTC state: %s", text)
        if self._on_state is not None:
            self._on_state(text)

    def _fail(self, reason: str) -> None:
        if self._failed:
            return
        self._failed = True
        details = (
            f"{reason}; local ICE candidates={self._local_candidate_count}, "
            f"remote ICE candidates={self._remote_candidate_count}"
        )
        log.error("WebRTC failure: %s", details)
        self._on_failed(details)

    def _parse_bin(self, description: str):
        try:
            return self.Gst.parse_bin_from_description(description, True)
        except Exception as exc:
            raise MediaUnavailable(
                f"Unable to build media pipeline: {exc}"
            ) from exc

    def _add_audio_source(self) -> None:
        if self._test_mode:
            source = "audiotestsrc is-live=true wave=silence"
        else:
            source = "autoaudiosrc"
        src = self._parse_bin(
            f"{source} ! audioconvert ! audioresample ! "
            "queue ! opusenc inband-fec=true ! rtpopuspay pt=111"
        )
        self.pipeline.add(src)
        pad = src.get_static_pad("src")
        sink = self.webrtc.request_pad_simple("sink_%u")
        if (
            pad is None
            or sink is None
            or pad.link(sink) != self.Gst.PadLinkReturn.OK
        ):
            raise MediaUnavailable("Unable to connect microphone to WebRTC")

    def _add_video_source(self) -> None:
        if self.Gst.ElementFactory.find("vp8enc") is None:
            raise MediaUnavailable("VP8 encoder (vp8enc) is missing")
        if self.Gst.ElementFactory.find("rtpvp8pay") is None:
            raise MediaUnavailable("RTP VP8 payloader is missing")
        source = "videotestsrc is-live=true" if self._test_mode else "autovideosrc"
        src = self._parse_bin(
            f"{source} ! videoconvert ! videoscale ! "
            "video/x-raw,width=1280,height=720,framerate=30/1 ! "
            "queue ! vp8enc deadline=1 cpu-used=8 keyframe-max-dist=60 ! "
            "rtpvp8pay pt=96 picture-id-mode=15-bit"
        )
        self.pipeline.add(src)
        pad = src.get_static_pad("src")
        sink = self.webrtc.request_pad_simple("sink_%u")
        if (
            pad is None
            or sink is None
            or pad.link(sink) != self.Gst.PadLinkReturn.OK
        ):
            raise MediaUnavailable("Unable to connect camera to WebRTC")

    def start_offer(self) -> None:
        self._making_answer = False
        result = self.pipeline.set_state(self.Gst.State.PLAYING)
        if result == self.Gst.StateChangeReturn.FAILURE:
            self._fail("GStreamer pipeline failed to enter PLAYING")

    def start_answer(self, remote: SessionDescription) -> None:
        self._making_answer = True
        result = self.pipeline.set_state(self.Gst.State.PLAYING)
        if result == self.Gst.StateChangeReturn.FAILURE:
            self._fail("GStreamer pipeline failed to enter PLAYING")
            return
        self._set_remote_description(remote, answer=False, then_answer=True)

    def set_remote_answer(self, remote: SessionDescription) -> None:
        self._set_remote_description(remote, answer=True, then_answer=False)

    def add_remote_candidate(self, mid: str, candidate: str) -> None:
        self._remote_candidate_count += 1
        log.info("RX ICE candidate mid=%s %s", mid, candidate)
        if not self._remote_description_set:
            self._queued_remote_candidates.append((mid, candidate))
            return
        self._add_remote_candidate_now(mid, candidate)

    def _add_remote_candidate_now(self, mid: str, candidate: str) -> None:
        index: int | None = None
        remote = self.webrtc.get_property("remote-description")
        if remote is not None and remote.sdp is not None:
            parsed = parse_sdp(remote.sdp.as_text())
            for idx, section in enumerate(parsed.media):
                if section.mid == mid:
                    index = idx
                    break

        if index is None:
            local = self.webrtc.get_property("local-description")
            if local is not None and local.sdp is not None:
                parsed = parse_sdp(local.sdp.as_text())
                for idx, section in enumerate(parsed.media):
                    if section.mid == mid:
                        index = idx
                        break
                if index is None and len(parsed.media) == 1:
                    index = 0

        if index is None:
            log.warning(
                "Ignoring remote ICE candidate with unknown MID %s instead of "
                "routing it to the wrong m-line",
                mid,
            )
            return

        log.info("Adding remote ICE candidate mid=%s mline=%d", mid, index)
        self.webrtc.emit("add-ice-candidate", index, candidate)

    def _on_negotiation_needed(self, _element) -> None:
        if self._making_answer or self._failed:
            return
        self._state("creating offer")
        promise = self.Gst.Promise.new_with_change_func(
            self._on_offer_created, None, None
        )
        self.webrtc.emit("create-offer", None, promise)

    @staticmethod
    def _reply_error(reply) -> str | None:
        if reply is None:
            return "GStreamer returned no promise reply"
        try:
            error = reply.get_value("error")
        except Exception:
            error = None
        if error is None:
            return None
        return str(error)

    def _on_offer_created(self, promise, _user_data, _notify) -> None:
        reply = promise.get_reply()
        error = self._reply_error(reply)
        if error is not None:
            self._fail(f"Could not create WebRTC offer: {error}")
            return
        offer = reply.get_value("offer")
        if offer is None:
            self._fail("GStreamer created no WebRTC offer")
            return
        promise = self.Gst.Promise.new_with_change_func(
            self._on_local_offer_set, offer, None
        )
        self.webrtc.emit("set-local-description", offer, promise)

    def _on_local_offer_set(self, promise, offer, _notify) -> None:
        error = self._reply_error(promise.get_reply())
        if error is not None:
            self._fail(f"Could not set local WebRTC offer: {error}")
            return
        description = parse_sdp(offer.sdp.as_text())
        self._state("local offer ready")
        self._on_local_description(description)

    def _create_answer(self) -> None:
        self._state("creating answer")
        promise = self.Gst.Promise.new_with_change_func(
            self._on_answer_created, None, None
        )
        self.webrtc.emit("create-answer", None, promise)

    def _on_answer_created(self, promise, _user_data, _notify) -> None:
        reply = promise.get_reply()
        error = self._reply_error(reply)
        if error is not None:
            self._fail(f"Could not create WebRTC answer: {error}")
            return
        answer = reply.get_value("answer")
        if answer is None:
            self._fail("GStreamer created no WebRTC answer")
            return
        promise = self.Gst.Promise.new_with_change_func(
            self._on_local_answer_set, answer, None
        )
        self.webrtc.emit("set-local-description", answer, promise)

    def _on_local_answer_set(self, promise, answer, _notify) -> None:
        error = self._reply_error(promise.get_reply())
        if error is not None:
            self._fail(f"Could not set local WebRTC answer: {error}")
            return
        description = parse_sdp(answer.sdp.as_text())
        self._state("local answer ready")
        self._on_local_description(description)

    def _set_remote_description(
        self,
        description: SessionDescription,
        *,
        answer: bool,
        then_answer: bool,
    ) -> None:
        text = build_sdp(description, answer=answer)
        log.debug("Applying remote SDP:\n%s", text)
        result, sdp_message = self.GstSdp.SDPMessage.new()
        if result != self.GstSdp.SDPResult.OK:
            self._fail("Could not allocate SDP message")
            return
        result = self.GstSdp.sdp_message_parse_buffer(
            text.encode("utf-8"), sdp_message
        )
        if result != self.GstSdp.SDPResult.OK:
            self._fail("Peer sent an SDP/Jingle description we could not parse")
            return
        sdp_type = (
            self.GstWebRTC.WebRTCSDPType.ANSWER
            if answer
            else self.GstWebRTC.WebRTCSDPType.OFFER
        )
        session = self.GstWebRTC.WebRTCSessionDescription.new(
            sdp_type, sdp_message
        )
        callback = (
            self._on_remote_offer_set
            if then_answer
            else self._on_remote_answer_set
        )
        promise = self.Gst.Promise.new_with_change_func(
            callback, None, None
        )
        self.webrtc.emit("set-remote-description", session, promise)

    def _flush_remote_candidates(self) -> None:
        queued = self._queued_remote_candidates
        self._queued_remote_candidates = []
        for mid, candidate in queued:
            self._add_remote_candidate_now(mid, candidate)

    def _remote_set_common(self, promise) -> bool:
        error = self._reply_error(promise.get_reply())
        if error is not None:
            self._fail(f"Could not set remote WebRTC description: {error}")
            return False
        self._remote_description_set = True
        self._flush_remote_candidates()
        return True

    def _on_remote_offer_set(self, promise, _user_data, _notify) -> None:
        if not self._remote_set_common(promise):
            return
        self._state("remote offer accepted")
        self._create_answer()

    def _on_remote_answer_set(self, promise, _user_data, _notify) -> None:
        if not self._remote_set_common(promise):
            return
        self._state("remote answer accepted")

    def _on_local_ice(
        self, _element, mline_index: int, candidate: str
    ) -> None:
        self._local_candidate_count += 1
        local = self.webrtc.get_property("local-description")
        mid = str(mline_index)
        if local is not None:
            parsed = parse_sdp(local.sdp.as_text())
            if mline_index < len(parsed.media):
                mid = parsed.media[mline_index].mid
        log.info("TX ICE candidate mid=%s %s", mid, candidate)
        self._on_ice_candidate(mid, candidate)

    def _on_connection_state(self, element, _pspec) -> None:
        name = _enum_name(element.get_property("connection-state"))
        self._state(f"connection={name}")
        if name == "connected":
            self._on_connected()
        elif name in {"failed", "closed"}:
            self._fail(f"WebRTC connection state: {name}")

    def _on_ice_connection_state(self, element, _pspec) -> None:
        name = _enum_name(element.get_property("ice-connection-state"))
        self._state(f"ice={name}")
        if name == "failed":
            self._fail("ICE connectivity checks failed")

    def _on_ice_gathering_state(self, element, _pspec) -> None:
        name = _enum_name(element.get_property("ice-gathering-state"))
        self._state(f"ice-gathering={name}")

    def _on_signaling_state(self, element, _pspec) -> None:
        name = _enum_name(element.get_property("signaling-state"))
        self._state(f"signaling={name}")

    def _on_bus_message(self, _bus, message) -> None:
        msg_type = message.type
        if msg_type == self.Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            source = message.src.get_name() if message.src is not None else "unknown"
            detail = f"GStreamer error from {source}: {error}"
            if debug:
                log.error("%s (%s)", detail, debug)
            self._fail(detail)
        elif msg_type == self.Gst.MessageType.WARNING:
            warning, debug = message.parse_warning()
            source = message.src.get_name() if message.src is not None else "unknown"
            log.warning("GStreamer warning from %s: %s (%s)", source, warning, debug)

    def _on_incoming_pad(self, _element, pad) -> None:
        if pad.get_direction() != self.Gst.PadDirection.SRC:
            return
        decode = self.Gst.ElementFactory.make("decodebin")
        if decode is None:
            self._fail("decodebin is missing")
            return
        self.pipeline.add(decode)
        decode.sync_state_with_parent()
        sink_pad = decode.get_static_pad("sink")
        if sink_pad is None or pad.link(sink_pad) != self.Gst.PadLinkReturn.OK:
            self._fail("Unable to connect incoming RTP stream to decoder")
            return
        decode.connect("pad-added", self._on_decoded_pad)

    def _on_decoded_pad(self, _decode, pad) -> None:
        caps = pad.get_current_caps() or pad.query_caps(None)
        text = caps.to_string() if caps is not None else ""
        if text.startswith("audio/"):
            description = (
                "queue ! audioconvert ! audioresample ! fakesink sync=false"
                if self._test_mode
                else "queue ! audioconvert ! audioresample ! autoaudiosink"
            )
            sink_bin = self._parse_bin(description)
            self.pipeline.add(sink_bin)
            sink_bin.sync_state_with_parent()
            pad.link(sink_bin.get_static_pad("sink"))
            return

        if not text.startswith("video/"):
            return

        if self._test_mode:
            sink_bin = self._parse_bin("queue ! videoconvert ! fakesink sync=false")
            self.pipeline.add(sink_bin)
            sink_bin.sync_state_with_parent()
            pad.link(sink_bin.get_static_pad("sink"))
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
        iterator = self.pipeline.iterate_elements()
        while True:
            result, element = iterator.next()
            if result != self.Gst.IteratorResult.OK:
                break
            factory = element.get_factory()
            if factory is None:
                continue
            if factory.get_name() not in {"autoaudiosrc", "audiotestsrc"}:
                continue
            if element.find_property("mute"):
                element.set_property("mute", not enabled)

    def close(self) -> None:
        try:
            self._bus.remove_signal_watch()
        except Exception:
            pass
        self.pipeline.set_state(self.Gst.State.NULL)
