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
        self.description = "Audio calls using XMPP Jingle and GStreamer WebRTC"
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
        self._toolbar_entry: tuple[
            MessageActionsBox, Gtk.Widget, Gtk.Button
        ] | None = None
        self._call_active = False

        ok, reason = probe_runtime()
        if not ok:
            self.activatable = False
            self.available_text = reason

    def activate(self) -> None:
        log.info("Gajim Calls activated")

    def deactivate(self) -> None:
        self.controller.shutdown()
        self._remove_toolbar_control()
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

    def _find_chat_toolbar_target(self) -> tuple[Gtk.Widget | None, Gtk.Widget | None]:
        """Find the conversation toolbar containing Search and Chat Details."""
        try:
            root = app.window
        except Exception:
            return None, None

        search_button: Gtk.Button | None = None
        details_button: Gtk.Button | None = None
        for widget in self._walk_widgets(root):
            if not isinstance(widget, Gtk.Button):
                continue
            tooltip = (widget.get_tooltip_text() or "").strip().lower()
            if search_button is None and tooltip.startswith("search"):
                search_button = widget
            if details_button is None and (
                "chat details" in tooltip or "details and settings" in tooltip
            ):
                details_button = widget

        # Gajim 2.5 places Search and Chat Details and Settings in the same
        # conversation toolbar. Insert immediately after Search so the call
        # action sits with those chat actions rather than in the window caption.
        if search_button is not None:
            return search_button.get_parent(), search_button
        if details_button is not None:
            return details_button.get_parent(), details_button
        return None, None

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

    def _remove_toolbar_control(self) -> None:
        entry = self._toolbar_entry
        self._toolbar_entry = None
        if entry is None:
            return
        _message_actions_box, _container, button = entry
        self._detach(button)

    def set_call_active(self, active: bool) -> None:
        """Swap the chat-toolbar action between call and hang-up states."""
        self._call_active = active
        if self._toolbar_entry is None:
            return

        button = self._toolbar_entry[2]
        image = button.get_child()
        if isinstance(image, Gtk.Image):
            image.set_from_icon_name(
                "call-stop-symbolic" if active else "call-start-symbolic"
            )
        button.set_tooltip_text("Hang up" if active else "Start audio call")
        button.remove_css_class("destructive-action")
        if active:
            button.add_css_class("destructive-action")
        button.set_sensitive(True)

    def _message_actions_box_created(
        self, message_actions_box: MessageActionsBox, action_box: Gtk.Box
    ) -> None:
        # MessageActionsBox is still the supported plugin hook for tracking the
        # active chat. The visible action is placed beside Search/Chat Details.
        if self._toolbar_entry is not None:
            return

        call_button = Gtk.Button.new_from_icon_name("call-start-symbolic")
        call_button.set_tooltip_text("Start audio call")
        call_button.add_css_class("flat")
        call_button.connect(
            "clicked",
            lambda _button: self._on_call_button_clicked(message_actions_box),
        )

        toolbar, anchor = self._find_chat_toolbar_target()
        if toolbar is not None and hasattr(toolbar, "insert_child_after"):
            toolbar.insert_child_after(call_button, anchor)
            container: Gtk.Widget = toolbar
        else:
            # Do not put the control back beside the text input. If Gajim's
            # toolbar cannot be found, leave a clear log entry rather than
            # recreating the old composer placement the plugin is replacing.
            log.error(
                "Could not find chat toolbar beside Search and Chat Details; "
                "call button was not attached"
            )
            container = action_box

        self._toolbar_entry = (message_actions_box, container, call_button)
        self.set_call_active(self._call_active)

    def _message_actions_box_destroyed(
        self, message_actions_box: MessageActionsBox, _action_box: Gtk.Box
    ) -> None:
        entry = self._toolbar_entry
        if entry is None or entry[0] is not message_actions_box:
            return
        self._remove_toolbar_control()

    def _on_call_button_clicked(self, message_actions_box: MessageActionsBox) -> None:
        if self._call_active:
            self.set_call_active(False)
            self.controller.hangup()
            return
        self._start_from_box(message_actions_box)

    def _start_from_box(self, message_actions_box: MessageActionsBox) -> None:
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

        # Video calling remains implemented internally, but the video button is
        # intentionally hidden until the planned video-call feature is ready.
        self.set_call_active(True)
        self.controller.start_outgoing(contact.account, contact.jid, video=False)
