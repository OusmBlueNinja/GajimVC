"""Small GTK4 call window integrated with Gajim's application."""

from __future__ import annotations

from gi.repository import Gtk


class CallWindow(Gtk.Window):
    def __init__(self, controller) -> None:
        super().__init__()
        self._controller = controller
        self.set_title("Gajim Call")
        self.set_default_size(560, 420)
        self.set_resizable(True)

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
        self._accept.connect("clicked", lambda _b: controller.accept())
        self._controls.append(self._accept)

        self._decline = Gtk.Button(label="Decline")
        self._decline.add_css_class("destructive-action")
        self._decline.connect("clicked", lambda _b: controller.decline())
        self._controls.append(self._decline)

        self._hangup = Gtk.Button(label="Hang Up")
        self._hangup.add_css_class("destructive-action")
        self._hangup.connect("clicked", lambda _b: controller.hangup())
        self._controls.append(self._hangup)

        self.connect("close-request", self._on_close)

    def _on_close(self, _window) -> bool:
        self._controller.hangup()
        return True

    def show_outgoing(self, peer: str, video: bool) -> None:
        self._title.set_text(peer)
        self._status.set_text("Calling…")
        self._accept.set_visible(False)
        self._decline.set_visible(False)
        self._hangup.set_visible(True)
        self._video.set_visible(video)
        self.present()

    def show_incoming(self, peer: str, video: bool) -> None:
        self._title.set_text(peer)
        self._status.set_text("Incoming video call" if video else "Incoming audio call")
        self._accept.set_visible(True)
        self._decline.set_visible(True)
        self._hangup.set_visible(False)
        self._video.set_visible(video)
        self.present()

    def set_status(self, text: str) -> None:
        self._status.set_text(text)

    def connected(self, video: bool) -> None:
        self._status.set_text("Connected")
        self._accept.set_visible(False)
        self._decline.set_visible(False)
        self._hangup.set_visible(True)
        self._video.set_visible(video)

    def set_remote_paintable(self, paintable) -> None:
        self._video.set_paintable(paintable)
        self._video.set_visible(True)

    def close_call(self) -> None:
        self.set_visible(False)
