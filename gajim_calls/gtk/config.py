"""Plugin configuration dialog."""

from __future__ import annotations

from pathlib import Path

from gi.repository import Gio, Gtk

from ..ringtone import BUILTIN_RINGTONES, DEFAULT_BUILTIN_RINGTONE, normalize_builtin_ringtone


class ConfigDialog(Gtk.Window):
    def __init__(self, plugin, transient) -> None:
        super().__init__(title="Gajim Calls")
        self._plugin = plugin
        self._ringtone_chooser = None
        self.set_transient_for(transient)
        self.set_modal(True)
        self.set_default_size(620, 470)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        root.set_margin_top(18)
        root.set_margin_bottom(18)
        root.set_margin_start(18)
        root.set_margin_end(18)
        self.set_child(root)

        intro = Gtk.Label(
            label=(
                "Configure network traversal and how Gajim Calls alerts you about "
                "incoming calls."
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
        grid.attach(self._stun, 1, 0, 2, 1)

        grid.attach(Gtk.Label(label="TURN server", xalign=0), 0, 1, 1, 1)
        self._turn = Gtk.Entry()
        self._turn.set_placeholder_text(
            "turn://user:password@turn.example.org:3478"
        )
        self._turn.set_text(str(plugin.config["turn_server"] or ""))
        self._turn.set_hexpand(True)
        grid.attach(self._turn, 1, 1, 2, 1)

        root.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        alert_title = Gtk.Label(label="Incoming calls", xalign=0)
        alert_title.add_css_class("heading")
        root.append(alert_title)

        alerts = Gtk.Grid(column_spacing=12, row_spacing=12)
        root.append(alerts)

        alerts.attach(Gtk.Label(label="Desktop notification", xalign=0), 0, 0, 1, 1)
        self._notifications = Gtk.Switch()
        self._notifications.set_halign(Gtk.Align.START)
        self._notifications.set_active(bool(plugin.config["incoming_notifications"]))
        alerts.attach(self._notifications, 1, 0, 1, 1)

        alerts.attach(Gtk.Label(label="Play ringtone", xalign=0), 0, 1, 1, 1)
        self._ringtone_enabled = Gtk.Switch()
        self._ringtone_enabled.set_halign(Gtk.Align.START)
        self._ringtone_enabled.set_active(bool(plugin.config["incoming_ringtone"]))
        alerts.attach(self._ringtone_enabled, 1, 1, 1, 1)

        alerts.attach(Gtk.Label(label="Built-in ringtone", xalign=0), 0, 2, 1, 1)
        self._ringtone_builtin = Gtk.ComboBoxText()
        for ringtone_id, label in BUILTIN_RINGTONES:
            self._ringtone_builtin.append(ringtone_id, label)
        try:
            configured = plugin.config["ringtone_builtin"]
        except Exception:
            configured = DEFAULT_BUILTIN_RINGTONE
        self._ringtone_builtin.set_active_id(normalize_builtin_ringtone(configured))
        alerts.attach(self._ringtone_builtin, 1, 2, 2, 1)

        alerts.attach(Gtk.Label(label="Custom ringtone", xalign=0), 0, 3, 1, 1)
        self._ringtone_path = Gtk.Entry()
        self._ringtone_path.set_hexpand(True)
        self._ringtone_path.set_placeholder_text("Optional audio file; overrides built-in")
        self._ringtone_path.set_text(str(plugin.config["ringtone_path"] or ""))
        alerts.attach(self._ringtone_path, 1, 3, 1, 1)

        browse = Gtk.Button(label="Browse…")
        browse.connect("clicked", self._choose_ringtone)
        alerts.attach(browse, 2, 3, 1, 1)

        hint = Gtk.Label(
            label=(
                "Choose one of the three bundled ringtones, or select a custom audio "
                "file. A custom file takes priority. Notification and ringtone options "
                "are independent."
            ),
            wrap=True,
            xalign=0,
        )
        hint.add_css_class("dim-label")
        root.append(hint)

        buttons = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            halign=Gtk.Align.END,
        )
        buttons.set_vexpand(True)
        buttons.set_valign(Gtk.Align.END)
        root.append(buttons)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.close())
        buttons.append(cancel)

        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", self._save)
        buttons.append(save)

        self.present()

    def _choose_ringtone(self, _button) -> None:
        chooser = Gtk.FileChooserNative.new(
            "Choose ringtone",
            self,
            Gtk.FileChooserAction.OPEN,
            "Select",
            "Cancel",
        )
        current = self._ringtone_path.get_text().strip()
        if current and Path(current).is_file():
            try:
                chooser.set_file(Gio.File.new_for_path(current))
            except Exception:
                pass
        audio_filter = Gtk.FileFilter()
        audio_filter.set_name("Audio files")
        audio_filter.add_mime_type("audio/*")
        chooser.add_filter(audio_filter)
        chooser.connect("response", self._ringtone_chosen)
        self._ringtone_chooser = chooser
        chooser.show()

    def _ringtone_chosen(self, chooser, response) -> None:
        if response == Gtk.ResponseType.ACCEPT:
            selected = chooser.get_file()
            if selected is not None:
                path = selected.get_path()
                if path:
                    self._ringtone_path.set_text(path)
        chooser.hide()
        self._ringtone_chooser = None

    def _save(self, _button) -> None:
        self._plugin.config["stun_server"] = self._stun.get_text().strip()
        self._plugin.config["turn_server"] = self._turn.get_text().strip()
        self._plugin.config["incoming_notifications"] = self._notifications.get_active()
        self._plugin.config["incoming_ringtone"] = self._ringtone_enabled.get_active()
        self._plugin.config["ringtone_builtin"] = (
            self._ringtone_builtin.get_active_id() or DEFAULT_BUILTIN_RINGTONE
        )
        self._plugin.config["ringtone_path"] = self._ringtone_path.get_text().strip()
        self.close()
