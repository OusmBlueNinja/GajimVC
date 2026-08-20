from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_call_button_startup_position_and_icon_regressions():
    source = (ROOT / "gajim_calls" / "plugin.py").read_text(encoding="utf-8")
    assert "_schedule_startup_registration()" in source
    assert "toolbar.insert_child_after(button, previous)" in source
    assert "-gtk-icon-transform: scaleX(-1)" in source
    assert "button.set_visible(self._supports_calls(message_actions_box))" in source


def test_connecting_cancel_blocks_late_media_callbacks():
    source = (ROOT / "gajim_calls" / "controller_runtime.py").read_text(
        encoding="utf-8"
    )
    assert "context.state == CallState.NEGOTIATING" in source
    assert 'reason="cancel"' in source
    assert "Ignoring late media-connected callback" in source


def test_incoming_alerts_are_wired_into_call_lifecycle():
    source = (ROOT / "gajim_calls" / "controller_runtime.py").read_text(
        encoding="utf-8"
    )
    assert "_start_incoming_alerts()" in source
    assert "_stop_incoming_alerts(context.sid)" in source
