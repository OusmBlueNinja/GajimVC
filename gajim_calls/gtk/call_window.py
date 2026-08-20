"""Discord-inspired, non-modal GTK4 call window integrated with Gajim."""

from __future__ import annotations

from gi.repository import GLib, Gtk
from nbxmpp.protocol import JID

from gajim.common import app
from gajim.common.const import AvatarSize
from gajim.common.modules.contacts import BareContact


class CallWindow(Gtk.ApplicationWindow):
    def __init__(self, controller) -> None:
        application = None
        try:
            if app.window is not None:
                application = app.window.get_application()
        except Exception:
            pass

        if application is None:
            super().__init__()
        else:
            super().__init__(application=application)

        self._controller = controller
        self._remote_paintable = None
        self.set_title("Gajim Call")
        self.set_default_size(720, 560)
        self.set_resizable(True)

        # A call is a first-class app window, never a modal popup. This avoids
        # blocking Gajim on Windows while media negotiation is running.
        self.set_modal(False)
        self.set_hide_on_close(True)
        self.set_destroy_with_parent(False)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_child(root)

        # Compact identity strip, similar to a voice-channel/call header.
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.set_margin_top(14)
        header.set_margin_bottom(14)
        header.set_margin_start(18)
        header.set_margin_end(18)
        root.append(header)

        self._mini_avatar = Gtk.Image()
        self._mini_avatar.set_pixel_size(40)
        header.append(self._mini_avatar)

        header_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        header_text.set_hexpand(True)
        header.append(header_text)

        self._title = Gtk.Label(xalign=0)
        self._title.add_css_class("title-4")
        self._title.set_ellipsize(3)
        header_text.append(self._title)

        self._address = Gtk.Label(xalign=0)
        self._address.add_css_class("dim-label")
        self._address.set_ellipsize(3)
        header_text.append(self._address)

        self._header_status = Gtk.Label()
        self._header_status.add_css_class("dim-label")
        header.append(self._header_status)

        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        root.append(separator)

        # The center stage cross-fades between the contact identity and remote
        # video. Audio calls stay on the large avatar, like Discord DMs.
        self._stage = Gtk.Stack()
        self._stage.set_hexpand(True)
        self._stage.set_vexpand(True)
        self._stage.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stage.set_transition_duration(180)
        root.append(self._stage)

        identity = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        identity.set_halign(Gtk.Align.CENTER)
        identity.set_valign(Gtk.Align.CENTER)
        identity.set_margin_top(30)
        identity.set_margin_bottom(30)
        identity.set_margin_start(30)
        identity.set_margin_end(30)
        self._stage.add_named(identity, "identity")

        self._avatar = Gtk.Image()
        self._avatar.set_pixel_size(int(AvatarSize.CALL_BIG))
        identity.append(self._avatar)

        self._hero_name = Gtk.Label()
        self._hero_name.add_css_class("title-1")
        self._hero_name.set_ellipsize(3)
        identity.append(self._hero_name)

        self._call_kind = Gtk.Label()
        self._call_kind.add_css_class("dim-label")
        identity.append(self._call_kind)

        self._status = Gtk.Label()
        self._status.add_css_class("title-4")
        self._status.set_wrap(True)
        self._status.set_justify(Gtk.Justification.CENTER)
        self._status.set_max_width_chars(56)
        identity.append(self._status)

        video_overlay = Gtk.Overlay()
        self._stage.add_named(video_overlay, "video")

        self._video = Gtk.Picture()
        self._video.set_hexpand(True)
        self._video.set_vexpand(True)
        self._video.set_can_shrink(True)
        self._video.set_content_fit(Gtk.ContentFit.CONTAIN)
        video_overlay.set_child(self._video)

        video_info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        video_info.set_halign(Gtk.Align.START)
        video_info.set_valign(Gtk.Align.END)
        video_info.set_margin_start(20)
        video_info.set_margin_bottom(18)
        video_overlay.add_overlay(video_info)

        self._video_name = Gtk.Label(xalign=0)
        self._video_name.add_css_class("title-4")
        video_info.append(self._video_name)

        self._video_status = Gtk.Label(xalign=0)
        self._video_status.add_css_class("dim-label")
        video_info.append(self._video_status)

        bottom_separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        root.append(bottom_separator)

        controls_wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        controls_wrap.set_margin_top(16)
        controls_wrap.set_margin_bottom(18)
        controls_wrap.set_margin_start(18)
        controls_wrap.set_margin_end(18)
        root.append(controls_wrap)

        self._incoming_controls = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=18,
            halign=Gtk.Align.CENTER,
        )
        controls_wrap.append(self._incoming_controls)

        self._accept = self._control_button(
            "call-start-symbolic", "Accept call", "suggested-action"
        )
        self._accept.connect("clicked", self._on_accept)
        self._incoming_controls.append(self._accept)

        self._decline = self._control_button(
            "call-stop-symbolic", "Decline call", "destructive-action"
        )
        self._decline.connect("clicked", self._on_decline)
        self._incoming_controls.append(self._decline)

        self._active_controls = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=18,
            halign=Gtk.Align.CENTER,
        )
        controls_wrap.append(self._active_controls)

        self._hangup = self._control_button(
            "call-stop-symbolic", "Hang up", "destructive-action"
        )
        self._hangup.connect("clicked", self._on_hangup)
        self._active_controls.append(self._hangup)

        self.connect("close-request", self._on_close)
        self._stage.set_visible_child_name("identity")

    @staticmethod
    def _control_button(icon_name: str, tooltip: str, css_class: str) -> Gtk.Button:
        button = Gtk.Button()
        button.set_child(Gtk.Image.new_from_icon_name(icon_name))
        button.set_tooltip_text(tooltip)
        button.set_size_request(54, 54)
        button.add_css_class("circular")
        button.add_css_class(css_class)
        return button

    @staticmethod
    def _queue(callback) -> None:
        """Finish the current GTK event/frame before doing call work."""

        def invoke():
            callback()
            return GLib.SOURCE_REMOVE

        GLib.idle_add(invoke)

    def _set_buttons_sensitive(self, sensitive: bool) -> None:
        self._accept.set_sensitive(sensitive)
        self._decline.set_sensitive(sensitive)
        self._hangup.set_sensitive(sensitive)

    def _set_status_text(self, text: str) -> None:
        self._status.set_text(text)
        self._header_status.set_text(text)
        self._video_status.set_text(text)

    def _set_default_avatar(self) -> None:
        self._mini_avatar.set_from_icon_name("avatar-default-symbolic")
        self._avatar.set_from_icon_name("avatar-default-symbolic")

    def _set_peer(self, peer: str) -> None:
        display_name = peer
        self._set_default_avatar()

        try:
            context = self._controller.context
            if context is not None:
                client = app.get_client(context.account)
                contacts = client.get_module("Contacts")
                bare_jid = JID.from_string(peer).new_as_bare()
                contact = contacts.get_contact(bare_jid)
                if isinstance(contact, BareContact):
                    display_name = contact.name
                    scale = self.get_scale_factor()
                    self._mini_avatar.set_from_paintable(
                        contact.get_avatar(
                            AvatarSize.CHAT, scale, add_show=False
                        )
                    )
                    self._avatar.set_from_paintable(
                        contact.get_avatar(
                            AvatarSize.CALL_BIG, scale, add_show=False
                        )
                    )
        except Exception:
            # Calls must remain usable even when avatar/contact metadata is
            # temporarily unavailable (for example during roster refresh).
            pass

        self.set_title(f"Call with {display_name}")
        self._title.set_text(display_name)
        self._hero_name.set_text(display_name)
        self._video_name.set_text(display_name)
        self._address.set_text(peer if display_name != peer else "")

    def _prepare_show(self, peer: str, video: bool) -> None:
        self._set_buttons_sensitive(True)
        self._remote_paintable = None
        self._video.set_paintable(None)
        self._stage.set_visible_child_name("identity")
        self._set_peer(peer)
        self._call_kind.set_text("Video call" if video else "Audio call")

    def _on_accept(self, _button) -> None:
        # Update the UI first. controller.accept() can kick off device/media
        # negotiation, so never make the click handler wait before GTK repaints.
        self._incoming_controls.set_visible(False)
        self._active_controls.set_visible(True)
        self._set_buttons_sensitive(False)
        self._set_status_text("Connecting…")
        self._queue(self._controller.accept)

    def _on_decline(self, _button) -> None:
        self._set_buttons_sensitive(False)
        self._set_status_text("Declining…")
        self._queue(self._controller.decline)

    def _on_hangup(self, _button) -> None:
        self._set_buttons_sensitive(False)
        self._set_status_text("Ending call…")
        self._queue(self._controller.hangup)

    def _on_close(self, _window) -> bool:
        # Hide immediately; media teardown happens asynchronously.
        self.set_visible(False)
        self._queue(self._controller.hangup)
        return True

    def show_outgoing(self, peer: str, video: bool) -> None:
        self._prepare_show(peer, video)
        self._set_status_text("Calling…")
        self._incoming_controls.set_visible(False)
        self._active_controls.set_visible(True)
        self.present()

    def show_incoming(self, peer: str, video: bool) -> None:
        self._prepare_show(peer, video)
        self._set_status_text("Incoming video call" if video else "Incoming audio call")
        self._incoming_controls.set_visible(True)
        self._active_controls.set_visible(False)
        self.present()

    def set_status(self, text: str) -> None:
        self._set_status_text(text)
        if text.startswith("Connecting"):
            self._incoming_controls.set_visible(False)
            self._active_controls.set_visible(True)
            self._hangup.set_sensitive(True)
        elif text.startswith("Call failed") or text in {"Call declined", "Call ended"}:
            self._set_buttons_sensitive(True)

    def connected(self, video: bool) -> None:
        self._set_status_text("Connected")
        self._incoming_controls.set_visible(False)
        self._active_controls.set_visible(True)
        self._hangup.set_sensitive(True)
        if not video or self._remote_paintable is None:
            self._stage.set_visible_child_name("identity")

    def set_remote_paintable(self, paintable) -> None:
        # media_engine guarantees this method is called from GLib's main loop.
        self._remote_paintable = paintable
        self._video.set_paintable(paintable)
        self._stage.set_visible_child_name("video")

    def close_call(self) -> None:
        self.set_visible(False)
        self._remote_paintable = None
        self._video.set_paintable(None)
        self._stage.set_visible_child_name("identity")
