from gajim_calls.peer_validation import jingle_sender_allowed, jmi_sender_allowed


PEER_BARE = "phone@example.test"
PEER_FULL = "phone@example.test/Conversations"


def test_jmi_allows_expected_peer_resources_but_not_another_bare_jid():
    assert jmi_sender_allowed(peer_bare=PEER_BARE, sender=PEER_FULL)
    assert jmi_sender_allowed(
        peer_bare=PEER_BARE,
        sender="phone@example.test/OtherDevice",
    )
    assert not jmi_sender_allowed(
        peer_bare=PEER_BARE,
        sender="attacker@example.test/Conversations",
    )


def test_jingle_is_exactly_bound_after_full_resource_selection():
    assert jingle_sender_allowed(
        peer_bare=PEER_BARE,
        peer_full=PEER_FULL,
        sender=PEER_FULL,
        action="transport-info",
        incoming=False,
    )
    assert not jingle_sender_allowed(
        peer_bare=PEER_BARE,
        peer_full=PEER_FULL,
        sender="phone@example.test/OtherDevice",
        action="transport-info",
        incoming=False,
    )
    assert not jingle_sender_allowed(
        peer_bare=PEER_BARE,
        peer_full=PEER_FULL,
        sender="attacker@example.test/Conversations",
        action="session-accept",
        incoming=False,
    )


def test_incoming_session_initiate_can_finalize_another_same_bare_resource():
    assert jingle_sender_allowed(
        peer_bare=PEER_BARE,
        peer_full=PEER_FULL,
        sender="phone@example.test/OtherDevice",
        action="session-initiate",
        incoming=True,
    )
    assert not jingle_sender_allowed(
        peer_bare=PEER_BARE,
        peer_full=PEER_FULL,
        sender="attacker@example.test/OtherDevice",
        action="session-initiate",
        incoming=True,
    )


def test_before_resource_selection_only_same_bare_jid_is_allowed():
    assert jingle_sender_allowed(
        peer_bare=PEER_BARE,
        peer_full=None,
        sender=PEER_FULL,
        action="session-initiate",
        incoming=True,
    )
    assert not jingle_sender_allowed(
        peer_bare=PEER_BARE,
        peer_full=None,
        sender="attacker@example.test/device",
        action="session-initiate",
        incoming=True,
    )
