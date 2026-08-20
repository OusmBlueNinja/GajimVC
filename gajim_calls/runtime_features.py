"""Runtime call UX/media fixes kept separate from the protocol core."""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("gajim.p.gajim_calls.runtime_features")
_installed = False


def _patch_media(media_module) -> None:
    engine = media_module.WebRTCMediaEngine

    def add_audio_source(self) -> None:
        source = (
            "audiotestsrc is-live=true wave=silence"
            if self._test_mode
            else "autoaudiosrc"
        )
        src = self._parse_bin(
            f"{source} ! audioconvert ! audioresample ! "
            "volume name=microphone_volume volume=1.0 ! "
            "queue ! opusenc inband-fec=true ! rtpopuspay pt=111"
        )
        self._microphone_volume = src.get_by_name("microphone_volume")
        if self._microphone_volume is None:
            raise media_module.MediaUnavailable(
                "Unable to create microphone volume control"
            )
        self.pipeline.add(src)
        pad = src.get_static_pad("src")
        sink = self.webrtc.request_pad_simple("sink_%u")
        if (
            pad is None
            or sink is None
            or pad.link(sink) != self.Gst.PadLinkReturn.OK
        ):
            raise media_module.MediaUnavailable(
                "Unable to connect microphone to WebRTC"
            )

    def set_microphone_enabled(self, enabled: bool) -> None:
        self._microphone_volume.set_property(
            "volume", 1.0 if enabled else 0.0
        )

    engine._add_audio_source = add_audio_source
    engine.set_microphone_enabled = set_microphone_enabled


def _patch_controller(controller_module) -> None:
    controller = controller_module.CallController
    original_init = controller.__init__
    original_start_media = controller._start_media
    original_finish_local = controller._finish_local
    original_cleanup = controller._cleanup

    def init(self, plugin) -> None:
        original_init(self, plugin)
        self._muted = False

    def get_muted(self) -> bool:
        return bool(getattr(self, "_muted", False))

    def set_muted(self, muted: bool) -> bool:
        self._muted = bool(muted)
        if self.media is not None:
            self.media.set_microphone_enabled(not self._muted)
        return self._muted

    def toggle_mute(self) -> bool:
        return set_muted(self, not get_muted(self))

    def start_media(self, *, offerer: bool, remote_offer=None) -> None:
        original_start_media(
            self, offerer=offerer, remote_offer=remote_offer
        )
        if self.media is not None:
            self.media.set_microphone_enabled(not get_muted(self))

    def finish_local(self, state, *, hide: bool = True) -> None:
        try:
            original_finish_local(self, state, hide=hide)
        finally:
            self._muted = False

    def cleanup(self, terminal: bool = True) -> None:
        try:
            original_cleanup(self, terminal=terminal)
        finally:
            self._muted = False

    controller.__init__ = init
    controller.muted = property(get_muted)
    controller.set_muted = set_muted
    controller.toggle_mute = toggle_mute
    controller._start_media = start_media
    controller._finish_local = finish_local
    controller._cleanup = cleanup


def _patch_call_window() -> None:
    from gi.repository import Gtk

    from .gtk.call_window import CallWindow

    original_init = CallWindow.__init__
    original_prepare_show = CallWindow._prepare_show
    original_set_buttons_sensitive = CallWindow._set_buttons_sensitive

    def sync_mute_button(self) -> None:
        muted = bool(getattr(self._controller, "muted", False))
        image = self._mute.get_child()
        if isinstance(image, Gtk.Image):
            image.set_from_icon_name(
                "microphone-sensitivity-muted-symbolic"
                if muted
                else "microphone-sensitivity-high-symbolic"
            )
        self._mute.set_tooltip_text(
            "Unmute microphone" if muted else "Mute microphone"
        )
        self._mute.remove_css_class("destructive-action")
        if muted:
            self._mute.add_css_class("destructive-action")

    def on_mute(self, _button) -> None:
        callback = getattr(self._controller, "toggle_mute", None)
        if callable(callback):
            callback()
            sync_mute_button(self)

    def init(self, controller) -> None:
        original_init(self, controller)
        self._mute = self._control_button(
            "microphone-sensitivity-high-symbolic",
            "Mute microphone",
            "flat",
        )
        self._mute.connect("clicked", on_mute.__get__(self, type(self)))
        previous = self._hangup.get_prev_sibling()
        if previous is None:
            self._active_controls.prepend(self._mute)
        else:
            self._active_controls.insert_child_after(self._mute, previous)
        self._mute.set_visible(False)
        sync_mute_button(self)

    def prepare_show(self, peer: str, video: bool) -> None:
        original_prepare_show(self, peer, video)
        self._mute.set_visible(False)
        sync_mute_button(self)

    def set_buttons_sensitive(self, sensitive: bool) -> None:
        original_set_buttons_sensitive(self, sensitive)
        if hasattr(self, "_mute"):
            self._mute.set_sensitive(sensitive)

    def connected(self, video: bool) -> None:
        self._hangup.set_tooltip_text("Hang up")
        self._mute.set_visible(True)
        self._active_controls.set_visible(True)
        self._incoming_controls.set_visible(False)
        self._set_status_text("Connected")
        sync_mute_button(self)
        self._controller.plugin.set_call_active(True, connected=True)
        self.present()

    CallWindow.__init__ = init
    CallWindow._prepare_show = prepare_show
    CallWindow._set_buttons_sensitive = set_buttons_sensitive
    CallWindow._sync_mute_button = sync_mute_button
    CallWindow._on_mute = on_mute
    CallWindow.connected = connected


class _GstMediaAdapter:
    """Small Gtk.MediaFile-like adapter backed by GStreamer's playbin."""

    def __init__(self, path: Path) -> None:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
        player = Gst.ElementFactory.make("playbin", "gajim-calls-ringtone")
        if player is None:
            raise RuntimeError("GStreamer playbin is unavailable")
        self._Gst = Gst
        self._player = player
        self._uri = path.resolve().as_uri()
        self._loop = True
        player.set_property("uri", self._uri)
        player.set_property("volume", 1.0)
        player.connect("about-to-finish", self._on_about_to_finish)

    def _on_about_to_finish(self, player) -> None:
        if self._loop:
            player.set_property("uri", self._uri)

    def set_loop(self, loop: bool) -> None:
        self._loop = bool(loop)

    def set_volume(self, volume: float) -> None:
        self._player.set_property("volume", float(volume))

    def play(self) -> None:
        result = self._player.set_state(self._Gst.State.PLAYING)
        if result == self._Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("GStreamer ringtone player failed to start")

    def pause(self) -> None:
        self._player.set_state(self._Gst.State.NULL)


def _patch_alerts() -> None:
    from gi.repository import GLib

    from . import alerts as alerts_module
    from .ringtone import DEFAULT_BUILTIN_RINGTONE, resolve_ringtone_path

    def start_ringtone(self) -> None:
        custom = str(self._config("ringtone_path", "") or "").strip()
        builtin = str(
            self._config("ringtone_builtin", DEFAULT_BUILTIN_RINGTONE)
            or DEFAULT_BUILTIN_RINGTONE
        )
        cache_dir = Path(GLib.get_user_cache_dir()) / "gajim-calls"
        try:
            path = resolve_ringtone_path(custom, cache_dir, builtin)
            media = _GstMediaAdapter(path)
            media.set_loop(True)
            media.set_volume(1.0)
            media.play()
            self._media = media
            log.info("Playing incoming-call ringtone %s", path)
        except Exception:
            log.exception("Unable to play incoming-call ringtone")

    alerts_module.IncomingCallAlerts._start_ringtone = start_ringtone


def install_runtime_features(plugin_module) -> None:
    global _installed
    if _installed:
        return

    controller_module = getattr(plugin_module, "controller_module", None)
    if controller_module is None:
        return

    from . import media as media_module

    _patch_media(media_module)
    _patch_controller(controller_module)
    _patch_call_window()
    _patch_alerts()
    _installed = True
