from gajim_calls.state import CallContext, CallState, InvalidTransition


def test_outgoing_happy_path():
    call = CallContext("acc", "sid", "a@example.test", ("audio",), False)
    call.transition(CallState.PROPOSING)
    call.transition(CallState.NEGOTIATING)
    call.transition(CallState.CONNECTED)
    call.transition(CallState.ENDING)
    call.transition(CallState.ENDED)
    assert call.state is CallState.ENDED


def test_invalid_transition_rejected():
    call = CallContext("acc", "sid", "a@example.test", ("audio",), False)
    try:
        call.transition(CallState.CONNECTED)
    except InvalidTransition:
        pass
    else:
        raise AssertionError("invalid transition should fail")


def test_has_video():
    call = CallContext("acc", "sid", "a@example.test", ("audio", "video"), False)
    assert call.has_video
