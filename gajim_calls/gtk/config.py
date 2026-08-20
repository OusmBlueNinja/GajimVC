"""Plugin configuration dialog."""

from __future__ import annotations

from gi.repository import Gtk


class ConfigDialog(Gtk.Window):
    def __init__(self, plugin, transient) -> None:
        super().__init__(title="Gajim Calls")
        self._plugin = plugin
        self.set_transient_for(transient)
        self.set_modal(True)
        self.set_default_size(520, 260)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.set_margin_top(18)
        root.set_margin_bottom(18)
        root.set_margin_start(18)
        root.set_margin_end(18)
        self.set_child(root)

        intro = Gtk.Label(
            label=(
                "STUN is optional on simple networks. TURN is strongly recommended "
                "for reliable calls across restrictive NAT/firewalls."
            ),
            wrap=True,
            xalign=0,
        )
        root.append(intro)

        grid = Gtk.Grid(column_spacing=12, row_spacing=12)
        root.append(grid)

        grid.attach(Gtk.Label(label="STUN server", xalign=0), 0, 0, 1, 1)
        self._stun = Gtk.Entry()
        self._stun.set_placeholder_text("stun://stun.example.org:3478")
        self._stun.set_text(str(plugin.config["stun_server"] or ""))
        self._stun.set_hexpand(True)
        grid.attach(self._stun, 1, 0, 1, 1)

        grid.attach(Gtk.Label(label="TURN server", xalign=0), 0, 1, 1, 1)
        self._turn = Gtk.Entry()
        self._turn.set_placeholder_text(
            "turn://user:password@turn.example.org:3478"
        )
        self._turn.set_text(str(plugin.config["turn_server"] or ""))
        self._turn.set_hexpand(True)
        grid.attach(self._turn, 1, 1, 1, 1)

        note = Gtk.Label(
            label=(
                "TURN credentials are stored in Gajim's plugin configuration. "
                "Use short-lived credentials when your TURN service supports them."
            ),
            wrap=True,
            xalign=0,
        )
        note.add_css_class("dim-label")
        root.append(note)

        buttons = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            halign=Gtk.Align.END,
        )
        root.append(buttons)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.close())
        buttons.append(cancel)

        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", self._save)
        buttons.append(save)

        self.present()

    def _save(self, _button) -> None:
        self._plugin.config["stun_server"] = self._stun.get_text().strip()
        self._plugin.config["turn_server"] = self._turn.get_text().strip()
        self.close()
