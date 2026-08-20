import pytest

from gajim_calls.timeout_policy import plan_timeout


def test_outgoing_jmi_ringing_timeout_retracts_without_jingle_terminate():
    plan = plan_timeout(
        "ringing",
        incoming=False,
        uses_jmi=True,
        jingle_started=False,
    )
    assert plan.jmi_action == "retract"
    assert plan.jmi_reason == "timeout"
    assert not plan.terminate_jingle


def test_incoming_jmi_ringing_timeout_rejects_without_jingle_terminate():
    plan = plan_timeout(
        "ringing",
        incoming=True,
        uses_jmi=True,
        jingle_started=False,
    )
    assert plan.jmi_action == "reject"
    assert plan.jmi_reason == "timeout"
    assert not plan.terminate_jingle


def test_direct_jingle_ringing_timeout_terminates_existing_jingle_session():
    plan = plan_timeout(
        "ringing",
        incoming=True,
        uses_jmi=False,
        jingle_started=True,
    )
    assert plan.jmi_action is None
    assert plan.terminate_jingle
    assert plan.jingle_reason == "timeout"


def test_jmi_negotiation_timeout_finishes_and_terminates_started_jingle():
    plan = plan_timeout(
        "negotiating",
        incoming=False,
        uses_jmi=True,
        jingle_started=True,
    )
    assert plan.jmi_action == "finish"
    assert plan.jmi_reason == "timeout"
    assert plan.terminate_jingle


def test_jmi_negotiation_timeout_before_session_initiate_only_finishes_jmi():
    plan = plan_timeout(
        "negotiating",
        incoming=True,
        uses_jmi=True,
        jingle_started=False,
    )
    assert plan.jmi_action == "finish"
    assert not plan.terminate_jingle


def test_direct_jingle_negotiation_timeout_never_sends_jmi():
    plan = plan_timeout(
        "negotiating",
        incoming=True,
        uses_jmi=False,
        jingle_started=True,
    )
    assert plan.jmi_action is None
    assert plan.terminate_jingle


def test_unknown_timeout_phase_is_rejected():
    with pytest.raises(ValueError):
        plan_timeout(  # type: ignore[arg-type]
            "forever",
            incoming=False,
            uses_jmi=True,
            jingle_started=False,
        )
