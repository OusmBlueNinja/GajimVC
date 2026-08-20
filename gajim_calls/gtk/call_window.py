"""Small non-modal GTK4 call window integrated with Gajim."""

from __future__ import annotations

from gi.repository import GLib, Gtk

from gajim.common import app


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
        self.set_title("Gajim Call")
        self.set_default_size(560, 420)
        self.set_resizable(True)

        # This must behave like a normal app window, not a modal dialog. On
        # Windows a modal/transient call popup can make the rest of Gajim look
        # frozen while media negotiation is happening.
        self.set_modal(False)
        self.set_hide_on_close(True)
        self.set_destroy_with_parent(False)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.set_margin_top(18)
        root.set_margin_bottom(18)
        root.set_margin_start(18)
        root.set_margin_end(18)
        self.set_child(root)

        self._title = Gtk.Label()
        self._title.add_css_class("title-2")
        root.append(self._title)

        self._status = Gtk.Label()
        self._status.add_css_class("dim-label")
        root.append(self._status)

        self._video = Gtk.Picture()
        self._video.set_hexpand(True)
        self._video.set_vexpand(True)
        self._video.set_can_shrink(True)
        root.append(self._video)

        self._controls = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            halign=Gtk.Align.CENTER,
        )
        root.append(self._controls)

        self._accept = Gtk.Button(label="Accept")
        self._accept.add_css_class("suggested-action")
        self._accept.connect("clicked", self._on_accept)
        self._controls.append(self._accept)

        self._decline = Gtk.Button(label="Decline")
        self._decline.add_css_class("destructive-action")
        self._decline.connect("clicked", self._on_decline)
        self._controls.append(self._decline)

        self._hangup = Gtk.Button(label="Hang Up")
        self._hangup.add_css_class("destructive-action")
        self._hangup.connect("clicked", self._on_hangup)
        self._controls.append(self._hangup)

        self.connect("close-request", self._on_close)

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

    def _on_accept(self, _button) -> None:
        # Update the UI first. controller.accept() can kick off device/media
        # negotiation, so never make the click handler wait before GTK repaints.
        self._accept.set_visible(False)
        self._decline.set_visible(False)
        self._hangup.set_visible(True)
        self._set_buttons_sensitive(False)
        self._status.set_text("Connecting…")
        self._queue(self._controller.accept)

    def _on_decline(self, _button) -> None:
        self._set_buttons_sensitive(False)
        self._status.set_text("Declining…")
        self._queue(self._controller.decline)

    def _on_hangup(self, _button) -> None:
        self._set_buttons_sensitive(False)
        self._status.set_text("Ending call…")
        self._queue(self._controller.hangup)

    def _on_close(self, _window) -> bool:
        # Hide immediately; media teardown happens asynchronously.
        self.set_visible(False)
        self._queue(self._controller.hangup)
        return True

    def _prepare_show(self) -> None:
        self._set_buttons_sensitive(True)

    def show_outgoing(self, peer: str, video: bool) -> None:
        self._prepare_show()
        self._title.set_text(peer)
        self._status.set_text("Calling…")
        self._accept.set_visible(False)
        self._decline.set_visible(False)
        self._hangup.set_visible(True)
        self._video.set_visible(video)
        self.present()

    def show_incoming(self, peer: str, video: bool) -> None:
        self._prepare_show()
        self._title.set_text(peer)
        self._status.set_text("Incoming video call" if video else "Incoming audio call")
        self._accept.set_visible(True)
        self._decline.set_visible(True)
        self._hangup.set_visible(False)
        self._video.set_visible(video)
        self.present()

    def set_status(self, text: str) -> None:
        self._status.set_text(text)
        if text.startswith("Connecting"):
            self._accept.set_visible(False)
            self._decline.set_visible(False)
            self._hangup.set_visible(True)
            self._hangup.set_sensitive(True)
        elif text.startswith("Call failed") or text in {"Call declined", "Call ended"}:
            self._set_buttons_sensitive(True)

    def connected(self, video: bool) -> None:
        self._status.set_text("Connected")
        self._accept.set_visible(False)
        self._decline.set_visible(False)
        self._hangup.set_visible(True)
        self._hangup.set_sensitive(True)
        self._video.set_visible(video)

    def set_remote_paintable(self, paintable) -> None:
        # media_engine guarantees this method is called from GLib's main loop.
        self._video.set_paintable(paintable)
        self._video.set_visible(True)

    def close_call(self) -> None:
        self.set_visible(False)
