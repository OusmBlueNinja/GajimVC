"""Gajim Calls plugin entry point."""

from __future__ import annotations

from functools import partial
import logging

from gi.repository import Gtk

from gajim.common import app
from gajim.common.modules.contacts import BareContact
from gajim.gtk.alert import InformationAlertDialog
from gajim.gtk.message_actions_box import MessageActionsBox
from gajim.plugins import GajimPlugin

from . import controller as controller_module
from . import module
from .controller_runtime import RuntimeCallController
from .gtk.config import ConfigDialog
from .media_engine import WebRTCMediaEngine, probe_runtime

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
        self.controller = RuntimeCallController(self)
        module.set_controller(self.controller)
        self.modules = [module]
        self.gui_extension_points = {
            "message_actions_box": (
                self._message_actions_box_created,
                self._message_actions_box_destroyed,
            )
        }
        self._header_entry: tuple[
            MessageActionsBox, Gtk.Widget, Gtk.Button, Gtk.Button
        ] | None = None

        ok, reason = probe_runtime()
        if not ok:
            self.activatable = False
            self.available_text = reason

    def activate(self) -> None:
        log.info("Gajim Calls activated")

    def deactivate(self) -> None:
        self.controller.shutdown()
        self._remove_header_controls()
        module.set_controller(None)

    @staticmethod
    def _walk_widgets(root: Gtk.Widget | None):
        if root is None:
            return
        stack = [root]
        while stack:
            widget = stack.pop()
            yield widget
            child = widget.get_first_child()
            children: list[Gtk.Widget] = []
            while child is not None:
                children.append(child)
                child = child.get_next_sibling()
            stack.extend(reversed(children))

    def _find_header_target(self) -> Gtk.Widget | None:
        """Find Gajim's actual application header without private attributes."""
        try:
            titlebar = app.window.get_titlebar()
        except Exception:
            titlebar = None

        # Prefer the titlebar subtree. Gajim 2.5 uses libadwaita/GTK header
        # bars, both of which expose pack_end(). Searching the widget tree keeps
        # this compatible with the different Windows/Linux header wrappers.
        roots = [titlebar]
        try:
            roots.append(app.window)
        except Exception:
            pass

        seen: set[int] = set()
        for root in roots:
            for widget in self._walk_widgets(root):
                key = id(widget)
                if key in seen:
                    continue
                seen.add(key)
                if type(widget).__name__.endswith("HeaderBar") and hasattr(
                    widget, "pack_end"
                ):
                    return widget
        return None

    @staticmethod
    def _detach(widget: Gtk.Widget) -> None:
        parent = widget.get_parent()
        if parent is None:
            return
        try:
            parent.remove(widget)
            return
        except (AttributeError, TypeError):
            pass
        try:
            if parent.get_child() is widget:
                parent.set_child(None)
        except (AttributeError, TypeError):
            pass

    def _remove_header_controls(self) -> None:
        entry = self._header_entry
        self._header_entry = None
        if entry is None:
            return
        _message_actions_box, _container, audio, video = entry
        self._detach(audio)
        self._detach(video)

    def _message_actions_box_created(
        self, message_actions_box: MessageActionsBox, action_box: Gtk.Box
    ) -> None:
        # MessageActionsBox is the supported Gajim plugin hook we use to track
        # the active chat. The buttons themselves belong in the header now.
        if self._header_entry is not None:
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

        header = self._find_header_target()
        if header is not None:
            # Keep the familiar Discord/modern-messenger placement: call
            # actions live at the trailing edge of the application header.
            header.pack_end(video)
            header.pack_end(audio)
            container: Gtk.Widget = header
        else:
            # This should only be needed for unusual custom window layouts.
            # Keeping a fallback is preferable to silently losing call access.
            log.warning("Could not find Gajim header bar; using message actions fallback")
            action_box.append(audio)
            action_box.append(video)
            container = action_box

        self._header_entry = (message_actions_box, container, audio, video)

    def _message_actions_box_destroyed(
        self, message_actions_box: MessageActionsBox, _action_box: Gtk.Box
    ) -> None:
        entry = self._header_entry
        if entry is None or entry[0] is not message_actions_box:
            return
        self._remove_header_controls()

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
