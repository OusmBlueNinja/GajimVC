from __future__ import annotations

import importlib
import sys
import types


class FakeGLib:
    SOURCE_REMOVE = False
    queued: list[object] = []

    @classmethod
    def idle_add(cls, callback):
        cls.queued.append(callback)
        return len(cls.queued)

    @classmethod
    def flush(cls) -> None:
        while cls.queued:
            callback = cls.queued.pop(0)
            callback()


class FakeBaseMediaEngine:
    pass


def load_media_engine(monkeypatch):
    FakeGLib.queued.clear()

    gi = types.ModuleType("gi")
    repository = types.ModuleType("gi.repository")
    repository.GLib = FakeGLib
    gi.repository = repository
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repository)

    media = types.ModuleType("gajim_calls.media")
    media.MediaUnavailable = RuntimeError
    media.WebRTCMediaEngine = FakeBaseMediaEngine
    media.probe_runtime = lambda: (True, "")
    media.probe_video_runtime = lambda: (True, "")
    monkeypatch.setitem(sys.modules, "gajim_calls.media", media)

    sys.modules.pop("gajim_calls.media_engine", None)
    return importlib.import_module("gajim_calls.media_engine")


def make_facade(module, received):
    return module.WebRTCMediaEngine(
        video=False,
        on_local_description=lambda _description: None,
        on_ice_candidate=lambda mid, candidate: received.append((mid, candidate)),
        on_connected=lambda: None,
        on_failed=lambda _reason: None,
    )


def test_local_ice_is_not_delivered_synchronously_from_gstreamer_callback(monkeypatch):
    module = load_media_engine(monkeypatch)
    received: list[tuple[str, str]] = []
    facade = make_facade(module, received)

    gstreamer_callback = facade._kwargs["on_ice_candidate"]
    gstreamer_callback("audio", "candidate:1 1 UDP 1 127.0.0.1 5000 typ host")

    assert received == []
    assert len(FakeGLib.queued) == 1

    FakeGLib.flush()
    assert received == [
        ("audio", "candidate:1 1 UDP 1 127.0.0.1 5000 typ host")
    ]


def test_queued_local_ice_is_dropped_after_media_close(monkeypatch):
    module = load_media_engine(monkeypatch)
    received: list[tuple[str, str]] = []
    facade = make_facade(module, received)

    facade._kwargs["on_ice_candidate"](
        "audio", "candidate:1 1 UDP 1 127.0.0.1 5000 typ host"
    )
    assert len(FakeGLib.queued) == 1

    facade.close()
    FakeGLib.flush()

    assert received == []


def test_late_gstreamer_ice_after_close_is_not_even_queued(monkeypatch):
    module = load_media_engine(monkeypatch)
    received: list[tuple[str, str]] = []
    facade = make_facade(module, received)
    facade.close()

    facade._kwargs["on_ice_candidate"](
        "audio", "candidate:1 1 UDP 1 127.0.0.1 5000 typ host"
    )

    assert FakeGLib.queued == []
    assert received == []
