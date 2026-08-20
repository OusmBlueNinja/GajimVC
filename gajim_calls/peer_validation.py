"""Pure peer-binding rules for JMI and Jingle call signaling."""

from __future__ import annotations


def bare_jid(jid: str) -> str:
    return jid.split("/", 1)[0]


def jmi_sender_allowed(*, peer_bare: str, sender: str) -> bool:
    """JMI resource selection may use any resource of the intended bare JID."""
    return bare_jid(sender) == peer_bare


def jingle_sender_allowed(
    *,
    peer_bare: str,
    peer_full: str | None,
    sender: str,
    action: str,
    incoming: bool,
) -> bool:
    """Bind Jingle to the selected full resource once it is known.

    For an incoming JMI call, session-initiate is the point where the exact
    initiator resource is finalized, so another resource of the same bare JID
    may legitimately send that one action. All subsequent traffic is exact.
    """
    if bare_jid(sender) != peer_bare:
        return False
    if action == "session-initiate" and incoming:
        return True
    if peer_full is None:
        return True
    return sender == peer_full
