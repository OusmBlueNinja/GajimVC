from __future__ import annotations

import sys
import types
from pathlib import Path

from gajim_calls import runtime_features


class _FakeMedia:
    def __init__(self) -> None:
        self.microphone_enabled: list[bool] = []

    def set_microphone_enabled(self, enabled: bool) -> None:
        self.microphone_enabled.append(enabled)


class _FakeController:
    def __init__(self, _plugin) -> None:
        self.media = None
        self.finished = []
        self.cleaned = []

    def _start_media(self, *, offerer: bool, remote_offer=None) -> None:
        del offerer, remote_offer
        self.media = _FakeMedia()

    def _finish_local(self, state, *, hide: bool = True) -> None:
        self.finished.append((state, hide))

    def _cleanup(self, terminal: bool = True) -> None:
        self.cleaned.append(terminal)


def test_controller_mute_toggles_real_media_gate() -> None:
    module = types.SimpleNamespace(CallController=_FakeController)
    runtime_features._patch_controller(module)
    controller = module.CallController(object())

    controller._start_media(offerer=True)
    assert controller.media.microphone_enabled == [True]
    assert controller.muted is False

    assert controller.toggle_mute() is True
    assert controller.media.microphone_enabled[-1] is False
    assert controller.toggle_mute() is False
    assert controller.media.microphone_enabled[-1] is True

    controller.set_muted(True)
    controller._finish_local("ended")
    assert controller.muted is False


class _FakeVolume:
    def __init__(self) -> None:
        self.values: list[float] = []

    def set_property(self, name: str, value: float) -> None:
        assert name == "volume"
        self.values.append(value)


class _FakeBaseMedia:
    def set_microphone_enabled(self, _enabled: bool) -> None:
        raise AssertionError("old microphone implementation should be replaced")


def test_media_patch_uses_deterministic_volume_element() -> None:
    module = types.SimpleNamespace(
        WebRTCMediaEngine=_FakeBaseMedia,
        MediaUnavailable=RuntimeError,
    )
    runtime_features._patch_media(module)
    engine = module.WebRTCMediaEngine()
    engine._microphone_volume = _FakeVolume()

    engine.set_microphone_enabled(False)
    engine.set_microphone_enabled(True)
    assert engine._microphone_volume.values == [0.0, 1.0]


class _FakePlayer:
    def __init__(self) -> None:
        self.properties = {}
        self.callbacks = {}
        self.states = []

    def set_property(self, name, value) -> None:
        self.properties[name] = value

    def connect(self, name, callback) -> None:
        self.callbacks[name] = callback

    def set_state(self, state):
        self.states.append(state)
        return "ok"


def test_ringtone_adapter_uses_gstreamer_playbin_and_loops(monkeypatch, tmp_path: Path) -> None:
    player = _FakePlayer()

    class Gst:
        class State:
            PLAYING = "playing"
            NULL = "null"

        class StateChangeReturn:
            FAILURE = "failure"

        class ElementFactory:
            @staticmethod
            def make(_name, _instance):
                return player

        @staticmethod
        def init(_value) -> None:
            return None

    gi = types.ModuleType("gi")
    gi.require_version = lambda *_args: None
    repository = types.ModuleType("gi.repository")
    repository.Gst = Gst
    gi.repository = repository
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repository)

    path = tmp_path / "ringtone.ogg"
    path.write_bytes(b"ring")
    media = runtime_features._GstMediaAdapter(path)
    media.play()

    assert player.properties["uri"] == path.resolve().as_uri()
    assert player.properties["volume"] == 1.0
    assert player.states[-1] == "playing"

    player.properties["uri"] = "changed"
    player.callbacks["about-to-finish"](player)
    assert player.properties["uri"] == path.resolve().as_uri()

    media.set_loop(False)
    player.properties["uri"] = "changed-again"
    player.callbacks["about-to-finish"](player)
    assert player.properties["uri"] == "changed-again"

    media.pause()
    assert player.states[-1] == "null"
