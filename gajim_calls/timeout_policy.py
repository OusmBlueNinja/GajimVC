"""Pure timeout signaling policy for call lifecycle phases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


TimeoutPhase = Literal["ringing", "negotiating"]


@dataclass(frozen=True, slots=True)
class TimeoutPlan:
    jmi_action: str | None = None
    jmi_reason: str | None = None
    terminate_jingle: bool = False
    jingle_reason: str = "timeout"


def plan_timeout(
    phase: TimeoutPhase,
    *,
    incoming: bool,
    uses_jmi: bool,
    jingle_started: bool,
) -> TimeoutPlan:
    """Return the wire signals needed when a call phase expires.

    XEP-0166 defines ``timeout`` for a request that was not answered. JMI
    proposals use retract/reject before Jingle starts, while a started JMI
    session also needs ``finish`` so other resources can synchronize state.
    """
    if phase == "ringing":
        if uses_jmi:
            return TimeoutPlan(
                jmi_action="reject" if incoming else "retract",
                jmi_reason="timeout",
            )
        return TimeoutPlan(terminate_jingle=jingle_started)

    if phase == "negotiating":
        return TimeoutPlan(
            jmi_action="finish" if uses_jmi else None,
            jmi_reason="timeout" if uses_jmi else None,
            terminate_jingle=jingle_started,
        )

    raise ValueError(f"Unknown timeout phase: {phase}")
