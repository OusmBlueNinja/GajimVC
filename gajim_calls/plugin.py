"""Gajim Calls plugin entry point."""

from __future__ import annotations

from functools import partial
import logging

from gi.repository import Gdk, GLib, Gtk

from gajim.common import app
from gajim.common.modules.contacts import BareContact
from gajim.gtk.alert import InformationAlertDialog
from gajim.gtk.message_actions_box import MessageActionsBox
from gajim.plugins import GajimPlugin

from . import controller as controller_module
from . import module
from .alerts import IncomingCallAlerts
from .capabilities import extend_call_capabilities
from .controller_runtime import RuntimeCallController
from .gtk.config import ConfigDialog
from .media_engine import WebRTCMediaEngine, probe_runtime, probe_video_runtime

log = logging.getLogger("gajim.p.gajim_calls")


class GajimCallsPlugin(GajimPlugin):
    def init(self) -> None:
        self.config_default_values = {
            "stun_server": ("", "GStreamer STUN URI"),
            "turn_server": ("", "GStreamer TURN URI"),
            "incoming_notifications": (True, "Show an incoming-call notification"),
            "incoming_ringtone": (True, "Play a ringtone for incoming calls"),
            "ringtone_path": ("", "Custom incoming-call ringtone path"),
        }
        self.description = "Audio calls using XMPP Jingle and GStreamer WebRTC"
        self.config_dialog = partial(ConfigDialog, self)

        controller_module.WebRTCMediaEngine = WebRTCMediaEngine
        self.incoming_alerts = IncomingCallAlerts(self)
        self.controller = RuntimeCallController(self)
        module.set_controller(self.controller)
        self.modules = [module]
        self.gui_extension_points = {
            "message_actions_box": (
                self._message_actions_box_created,
                self._message_actions_box_destroyed,
            ),
            "update_caps": (self._update_caps, None),
        }
        self._toolbar_entry: tuple[
            MessageActionsBox, Gtk.Widget, Gtk.Button
        ] | None = None
        self._toolbar_retry_id: int | None = None
        self._toolbar_retry_attempts = 0
        self._startup_retry_id: int | None = None
        self._startup_retry_attempts = 0
        self._call_active = False
        self._call_connected = False
        self._video_available = False
        self._css_provider: Gtk.CssProvider | None = None
        self._install_css()

        ok, reason = probe_runtime()
        if not ok:
            self.activatable = False
            self.available_text = reason
            return

        self._video_available, video_reason = probe_video_runtime()
        if not self._video_available:
            log.info("Video calls will not be advertised: %s", video_reason)

    def _install_css(self) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            return
        provider = Gtk.CssProvider()
        provider.load_from_data(
            b".gajim-calls-mirrored-phone { -gtk-icon-transform: scaleX(-1); }"
        )
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._css_provider = provider

    def _update_caps(self, _account: str, features: list[str]) -> None:
        extend_call_capabilities(features, video=self._video_available)

    @staticmethod
    def _refresh_caps() -> None:
        try:
            accounts = app.settings.get_active_accounts()
        except Exception:
            log.debug("Could not enumerate accounts while refreshing caps", exc_info=True)
            return
        for account in accounts:
            try:
                app.get_client(account).get_module("Caps").update_caps()
            except Exception:
                log.debug("Could not refresh call caps for account %s", account, exc_info=True)

    @classmethod
    def _refresh_caps_idle(cls) -> bool:
        cls._refresh_caps()
        return GLib.SOURCE_REMOVE

    def activate(self) -> None:
        log.info("Gajim Calls activated")
        self._schedule_startup_registration()
        # Recalculate XEP-0115 immediately so already-connected resources expose
        # this plugin's Jingle features instead of waiting for a future presence.
        self._refresh_caps()

    def deactivate(self) -> None:
        self.incoming_alerts.stop()
        self.controller.shutdown()
        self._cancel_startup_retry()
        self._remove_toolbar_control()
        module.set_controller(None)
        # Run after the plugin manager finishes removing the update_caps hook,
        # so contacts no longer see stale call support after disabling plugin.
        GLib.idle_add(self._refresh_caps_idle)

    def incoming_call_started(
        self, account: str, sid: str, peer: str, *, video: bool
    ) -> None:
        self.incoming_alerts.start(account, sid, peer, video=video)

    def incoming_call_stopped(self, sid: str | None = None) -> None:
        self.incoming_alerts.stop(sid)

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

    def _find_current_message_actions_box(self) -> MessageActionsBox | None:
        try:
            root = app.window
        except Exception:
            return None
        fallback = None
        for widget in self._walk_widgets(root):
            if not isinstance(widget, MessageActionsBox):
                continue
            fallback = fallback or widget
            try:
                if widget.get_visible() and widget.get_mapped():
                    return widget
            except Exception:
                return widget
        return fallback

    def _find_chat_toolbar_target(self) -> tuple[Gtk.Widget | None, Gtk.Widget | None]:
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

    @staticmethod
    def _supports_calls(message_actions_box: MessageActionsBox) -> bool:
        try:
            return isinstance(message_actions_box.get_current_contact(), BareContact)
        except Exception:
            return False

    def _cancel_startup_retry(self) -> None:
        if self._startup_retry_id is None:
            return
        try:
            GLib.source_remove(self._startup_retry_id)
        except Exception:
            pass
        self._startup_retry_id = None
        self._startup_retry_attempts = 0

    def _ensure_toolbar_registered(self) -> bool:
        if self._toolbar_entry is not None:
            self._startup_retry_id = None
            self._startup_retry_attempts = 0
            self._schedule_toolbar_attach()
            return False

        message_actions_box = self._find_current_message_actions_box()
        if message_actions_box is not None:
            self._startup_retry_id = None
            self._startup_retry_attempts = 0
            self._message_actions_box_created(message_actions_box, message_actions_box)
            return False

        self._startup_retry_attempts += 1
        if self._startup_retry_attempts >= 40:
            self._startup_retry_id = None
            log.debug("No restored MessageActionsBox found during startup retries")
            return False
        return True

    def _schedule_startup_registration(self) -> None:
        self._cancel_startup_retry()
        if self._ensure_toolbar_registered():
            self._startup_retry_id = GLib.timeout_add(
                250, self._ensure_toolbar_registered
            )

    def _cancel_toolbar_retry(self) -> None:
        if self._toolbar_retry_id is None:
            return
        try:
            GLib.source_remove(self._toolbar_retry_id)
        except Exception:
            pass
        self._toolbar_retry_id = None
        self._toolbar_retry_attempts = 0

    def _attach_toolbar_control(self) -> bool:
        entry = self._toolbar_entry
        if entry is None:
            self._toolbar_retry_id = None
            return False

        message_actions_box, _container, button = entry
        button.set_visible(self._supports_calls(message_actions_box))
        toolbar, search_button = self._find_chat_toolbar_target()
        if toolbar is not None and search_button is not None and hasattr(
            toolbar, "insert_child_after"
        ):
            self._detach(button)
            previous = search_button.get_prev_sibling()
            if previous is None and hasattr(toolbar, "prepend"):
                toolbar.prepend(button)
            else:
                toolbar.insert_child_after(button, previous)
            self._toolbar_entry = (message_actions_box, toolbar, button)
            self._toolbar_retry_id = None
            self._toolbar_retry_attempts = 0
            self.set_call_active(self._call_active, connected=self._call_connected)
            log.info("Attached call button immediately left of Search")
            return False

        self._toolbar_retry_attempts += 1
        if self._toolbar_retry_attempts >= 40:
            log.error(
                "Could not find chat toolbar beside Search after startup retries; "
                "call button was not attached"
            )
            self._toolbar_retry_id = None
            return False
        return True

    def _schedule_toolbar_attach(self) -> None:
        self._cancel_toolbar_retry()
        if self._attach_toolbar_control():
            self._toolbar_retry_id = GLib.timeout_add(250, self._attach_toolbar_control)

    def _remove_toolbar_control(self) -> None:
        self._cancel_toolbar_retry()
        entry = self._toolbar_entry
        self._toolbar_entry = None
        if entry is None:
            return
        _message_actions_box, _container, button = entry
        self._detach(button)

    def set_call_active(self, active: bool, *, connected: bool = False) -> None:
        self._call_active = active
        self._call_connected = active and connected
        if self._toolbar_entry is None:
            return

        button = self._toolbar_entry[2]
        image = button.get_child()
        if isinstance(image, Gtk.Image):
            image.set_from_icon_name(
                "call-stop-symbolic" if active else "call-start-symbolic"
            )
            image.remove_css_class("gajim-calls-mirrored-phone")
            if not active:
                image.add_css_class("gajim-calls-mirrored-phone")

        if not active:
            tooltip = "Start audio call"
        elif connected:
            tooltip = "Hang up"
        else:
            tooltip = "Cancel call"
        button.set_tooltip_text(tooltip)
        button.remove_css_class("destructive-action")
        if active:
            button.add_css_class("destructive-action")
        button.set_sensitive(True)

    def _message_actions_box_created(
        self, message_actions_box: MessageActionsBox, action_box: Gtk.Widget
    ) -> None:
        self._cancel_startup_retry()
        entry = self._toolbar_entry
        if entry is not None and entry[0] is message_actions_box:
            entry[2].set_visible(self._supports_calls(message_actions_box))
            self._schedule_toolbar_attach()
            return

        if entry is not None:
            self._remove_toolbar_control()

        image = Gtk.Image.new_from_icon_name("call-start-symbolic")
        image.add_css_class("gajim-calls-mirrored-phone")
        call_button = Gtk.Button()
        call_button.set_child(image)
        call_button.set_tooltip_text("Start audio call")
        call_button.add_css_class("flat")
        call_button.set_visible(self._supports_calls(message_actions_box))
        call_button.connect(
            "clicked",
            lambda _button: self._on_call_button_clicked(message_actions_box),
        )

        self._toolbar_entry = (message_actions_box, action_box, call_button)
        self.set_call_active(self._call_active, connected=self._call_connected)
        self._schedule_toolbar_attach()

    def _message_actions_box_destroyed(
        self, message_actions_box: MessageActionsBox, _action_box: Gtk.Widget
    ) -> None:
        entry = self._toolbar_entry
        if entry is None or entry[0] is not message_actions_box:
            return
        self._remove_toolbar_control()
        self._schedule_startup_registration()

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

        self.set_call_active(True, connected=False)
        self.controller.start_outgoing(contact.account, contact.jid, video=False)
