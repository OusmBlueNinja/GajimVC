"""Gajim Calls plugin entry point."""

from __future__ import annotations

from functools import partial
import logging

from gi.repository import Gtk

from gajim.common.modules.contacts import BareContact
from gajim.gtk.alert import InformationAlertDialog
from gajim.gtk.message_actions_box import MessageActionsBox
from gajim.plugins import GajimPlugin

from . import controller as controller_module
from .controller import CallController
from .gtk.config import ConfigDialog
from .media_engine import WebRTCMediaEngine, probe_runtime
from . import module

log = logging.getLogger("gajim.p.gajim_calls")


class GajimCallsPlugin(GajimPlugin):
    def init(self) -> None:
        self.config_default_values = {
            "stun_server": ("", "GStreamer STUN URI"),
            "turn_server": ("", "GStreamer TURN URI"),
        }
        self.description = (
            "Audio and video calls using XMPP Jingle and GStreamer WebRTC"
        )
        self.config_dialog = partial(ConfigDialog, self)

        # controller.py historically imported the media class directly. Keep
        # its public surface stable while selecting the hardened implementation
        # that fixes current GStreamer promise/BUNDLE/ICE interoperability.
        controller_module.WebRTCMediaEngine = WebRTCMediaEngine
        self.controller = CallController(self)
        module.set_controller(self.controller)
        self.modules = [module]
        self.gui_extension_points = {
            "message_actions_box": (
                self._message_actions_box_created,
                self._message_actions_box_destroyed,
            )
        }
        self._boxes: dict[int, tuple[Gtk.Box, Gtk.Button, Gtk.Button]] = {}

        ok, reason = probe_runtime()
        if not ok:
            self.activatable = False
            self.available_text = reason

    def activate(self) -> None:
        log.info("Gajim Calls activated")

    def deactivate(self) -> None:
        self.controller.shutdown()
        for key in list(self._boxes):
            action_box, audio_button, video_button = self._boxes.pop(key)
            try:
                action_box.remove(audio_button)
                action_box.remove(video_button)
            except Exception:
                pass
        module.set_controller(None)

    def _message_actions_box_created(
        self, message_actions_box: MessageActionsBox, action_box: Gtk.Box
    ) -> None:
        key = id(message_actions_box)
        if key in self._boxes:
            return

        audio = Gtk.Button.new_from_icon_name("call-start-symbolic")
        audio.set_tooltip_text("Start audio call")
        audio.add_css_class("flat")
        audio.connect(
            "clicked",
            lambda _button: self._start_from_box(message_actions_box, video=False),
        )

        video = Gtk.Button.new_from_icon_name("camera-video-symbolic")
        video.set_tooltip_text("Start video call")
        video.add_css_class("flat")
        video.connect(
            "clicked",
            lambda _button: self._start_from_box(message_actions_box, video=True),
        )

        action_box.append(audio)
        action_box.append(video)
        self._boxes[key] = (action_box, audio, video)

    def _message_actions_box_destroyed(
        self, message_actions_box: MessageActionsBox, _action_box: Gtk.Box
    ) -> None:
        entry = self._boxes.pop(id(message_actions_box), None)
        if entry is None:
            return
        action_box, audio, video = entry
        try:
            action_box.remove(audio)
            action_box.remove(video)
        except Exception:
            pass

    def _start_from_box(
        self, message_actions_box: MessageActionsBox, *, video: bool
    ) -> None:
        try:
            contact = message_actions_box.get_current_contact()
        except Exception:
            return

        if not isinstance(contact, BareContact):
            InformationAlertDialog(
                "Calls are available in one-to-one chats",
                "Open a direct chat with a contact before starting a call.",
            )
            return

        self.controller.start_outgoing(contact.account, contact.jid, video=video)
