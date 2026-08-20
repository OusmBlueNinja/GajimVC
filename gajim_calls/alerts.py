"""Desktop notification and ringtone handling for incoming calls."""

from __future__ import annotations

import logging
from pathlib import Path

from gi.repository import Gio, GLib, Gtk

from gajim.common import app

from .ringtone import resolve_ringtone_path

log = logging.getLogger("gajim.p.gajim_calls.alerts")


class IncomingCallAlerts:
    def __init__(self, plugin) -> None:
        self._plugin = plugin
        self._sid: str | None = None
        self._media: Gtk.MediaFile | None = None
        self._notification_id: str | None = None
        self._notification_application = None

    def _config(self, key: str, default):
        try:
            return self._plugin.config[key]
        except Exception:
            return default

    @staticmethod
    def _application():
        try:
            if app.window is not None:
                application = app.window.get_application()
                if application is not None:
                    return application
        except Exception:
            pass
        try:
            application = getattr(app, "app", None)
            if application is not None:
                return application
        except Exception:
            pass
        try:
            return Gio.Application.get_default()
        except Exception:
            return None

    def start(
        self,
        account: str,
        sid: str,
        peer: str,
        *,
        video: bool,
    ) -> None:
        del account  # Reserved for future per-account alert preferences.
        if self._sid == sid:
            return
        self.stop()
        self._sid = sid

        if bool(self._config("incoming_notifications", True)):
            self._show_notification(sid, peer, video=video)
        if bool(self._config("incoming_ringtone", True)):
            self._start_ringtone()

    def _show_notification(self, sid: str, peer: str, *, video: bool) -> None:
        application = self._application()
        if application is None or not hasattr(application, "send_notification"):
            log.warning("No Gio.Application is available for incoming-call notification")
            return

        notification = Gio.Notification.new("Incoming Gajim Call")
        kind = "video" if video else "audio"
        notification.set_body(f"Incoming {kind} call from {peer}")
        try:
            notification.set_icon(Gio.ThemedIcon.new("call-start-symbolic"))
            notification.set_priority(Gio.NotificationPriority.URGENT)
        except Exception:
            pass

        notification_id = f"gajim-calls-{sid}"
        try:
            application.send_notification(notification_id, notification)
        except Exception:
            log.exception("Unable to show incoming-call notification")
            return
        self._notification_id = notification_id
        self._notification_application = application

    def _start_ringtone(self) -> None:
        custom = str(self._config("ringtone_path", "") or "").strip()
        cache_dir = Path(GLib.get_user_cache_dir()) / "gajim-calls"
        try:
            path = resolve_ringtone_path(custom, cache_dir)
            media = Gtk.MediaFile.new_for_filename(str(path))
            media.set_loop(True)
            media.set_volume(1.0)
            media.play()
            self._media = media
        except Exception:
            log.exception("Unable to play incoming-call ringtone")

    def stop(self, sid: str | None = None) -> None:
        if sid is not None and self._sid is not None and sid != self._sid:
            return

        media = self._media
        self._media = None
        if media is not None:
            try:
                media.set_loop(False)
                media.pause()
            except Exception:
                log.debug("Unable to stop ringtone cleanly", exc_info=True)

        notification_id = self._notification_id
        application = self._notification_application
        self._notification_id = None
        self._notification_application = None
        if notification_id is not None and application is not None:
            try:
                application.withdraw_notification(notification_id)
            except Exception:
                log.debug("Unable to withdraw call notification", exc_info=True)

        self._sid = None
