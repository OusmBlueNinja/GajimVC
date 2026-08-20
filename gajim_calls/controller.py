"""Call orchestration between GTK, XMPP signaling, and GStreamer."""

from __future__ import annotations

import logging
import uuid

from nbxmpp.protocol import JID

from gajim.common import app

from .media import WebRTCMediaEngine
from .protocol import JMIEvent, JingleEvent
from .sdp import IceCandidate, MediaSection, SessionDescription
from .state import CallContext, CallState

log = logging.getLogger("gajim.p.gajim_calls.controller")


class CallController:
    def __init__(self, plugin) -> None:
        self.plugin = plugin
        self.context: CallContext | None = None
        self.media: WebRTCMediaEngine | None = None
        self.window = None
        self._pending_offer: SessionDescription | None = None
        self._local_description: SessionDescription | None = None
        self._pending_local_candidates: list[tuple[str, str]] = []

    def _get_window(self):
        if self.window is None:
            from .gtk.call_window import CallWindow

            self.window = CallWindow(self)
            try:
                self.window.set_transient_for(app.window)
            except Exception:
                pass
        return self.window

    @staticmethod
    def _bare(jid: str) -> str:
        return str(JID.from_string(jid).new_as_bare())

    @staticmethod
    def _octet_less(left: str, right: str) -> bool:
        """Compare identifiers using the octet ordering required by JMI tie-breaks."""
        return left.encode("utf-8") < right.encode("utf-8")

    def _module(self, account: str):
        return app.get_client(account).get_module("DeauthCalls")  # type: ignore[arg-type]

    def _own_jid(self, account: str) -> str:
        return str(app.get_client(account).get_own_jid())

    def owns_sid(self, account: str, sid: str) -> bool:
        return (
            self.context is not None
            and self.context.account == account
            and self.context.sid == sid
            and self.context.state not in {CallState.ENDED, CallState.FAILED}
        )

    def start_outgoing(self, account: str, peer_jid, *, video: bool) -> None:
        if self.context is not None and self.context.state not in {
            CallState.ENDED,
            CallState.FAILED,
        }:
            self._get_window().set_status("Another call is already active")
            self._get_window().present()
            return

        sid = str(uuid.uuid4())
        media = ("audio", "video") if video else ("audio",)
        peer_bare = self._bare(str(peer_jid))
        self.context = CallContext(
            account=account,
            sid=sid,
            peer_bare=peer_bare,
            media=media,
            incoming=False,
        )
        self.context.transition(CallState.PROPOSING)
        self.context.initiator = self._own_jid(account)
        self._get_window().show_outgoing(peer_bare, video)
        self._module(account).send_jmi(peer_bare, "propose", sid, media=media)

    def handle_jmi(self, account: str, from_jid: str, event: JMIEvent) -> bool:
        if event.action == "propose":
            # XEP-0353 tie-break applies only when both sides are proposing the
            # same peer relationship. Lower session ID wins; if IDs are equal,
            # the lower JID wins using i;octet ordering.
            same_peer_proposal = (
                self.context is not None
                and self.context.state == CallState.PROPOSING
                and self.context.account == account
                and self.context.peer_bare == self._bare(from_jid)
            )
            if same_peer_proposal:
                assert self.context is not None
                remote_wins = event.id < self.context.sid or (
                    event.id == self.context.sid
                    and self._octet_less(from_jid, self._own_jid(account))
                )
                if not remote_wins:
                    self._module(account).send_jmi(
                        from_jid,
                        "reject",
                        event.id,
                        reason="expired",
                        tie_break=True,
                    )
                    return True
                self._module(account).send_jmi(
                    self.context.peer_bare,
                    "retract",
                    self.context.sid,
                    reason="expired",
                    tie_break=True,
                )
                self._cleanup(terminal=False)

            if self.context is not None and self.context.state not in {
                CallState.ENDED,
                CallState.FAILED,
            }:
                self._module(account).send_jmi(
                    from_jid, "reject", event.id, reason="busy"
                )
                return True

            media = tuple(
                item for item in event.media if item in {"audio", "video"}
            ) or ("audio",)
            self.context = CallContext(
                account=account,
                sid=event.id,
                peer_bare=self._bare(from_jid),
                peer_full=from_jid,
                media=media,  # type: ignore[arg-type]
                incoming=True,
            )
            self.context.transition(CallState.RINGING)
            self.context.initiator = from_jid
            self._module(account).send_jmi(from_jid, "ringing", event.id)
            self._get_window().show_incoming(
                self.context.peer_bare, self.context.has_video
            )
            return True

        if self.context is None or event.id != self.context.sid:
            return False

        if event.action == "ringing" and self.context.state == CallState.PROPOSING:
            self._get_window().set_status("Ringing…")
            return True

        if event.action == "proceed" and not self.context.incoming:
            self.context.peer_full = from_jid
            self.context.responder = from_jid
            self.context.transition(CallState.NEGOTIATING)
            self._get_window().set_status("Connecting…")
            self._start_media(offerer=True)
            return True

        if event.action in {"reject", "retract"}:
            self._get_window().set_status("Call declined")
            self._finish_local(CallState.ENDED)
            return True

        if event.action == "finish":
            self._get_window().set_status("Call ended")
            self._finish_local(CallState.ENDED)
            return True

        return True

    def handle_jingle(self, account: str, from_jid: str, event: JingleEvent) -> None:
        if self.context is None or event.sid != self.context.sid:
            # Direct-Jingle fallback for peers not using XEP-0353.
            if event.action != "session-initiate":
                return
            media = tuple(
                section.media
                for section in event.description.media
                if section.media in {"audio", "video"}
            ) or ("audio",)
            self.context = CallContext(
                account=account,
                sid=event.sid,
                peer_bare=self._bare(from_jid),
                peer_full=from_jid,
                media=media,  # type: ignore[arg-type]
                incoming=True,
            )
            self.context.transition(CallState.RINGING)
            self.context.initiator = event.initiator or from_jid
            self.context.responder = self._own_jid(account)
            self._pending_offer = event.description
            self._get_window().show_incoming(
                self.context.peer_bare, self.context.has_video
            )
            return

        if event.action == "session-initiate":
            self.context.peer_full = from_jid
            self.context.initiator = event.initiator or from_jid
            self.context.responder = self._own_jid(account)
            self._pending_offer = event.description
            if self.context.state == CallState.NEGOTIATING:
                self._start_media(offerer=False, remote_offer=event.description)
            return

        if event.action == "session-accept":
            if self.media is not None:
                self.media.set_remote_answer(event.description)
            return

        if event.action == "transport-info" and self.media is not None:
            for section in event.description.media:
                for candidate in section.candidates:
                    self.media.add_remote_candidate(section.mid, candidate.to_sdp())
            return

        if event.action == "session-terminate":
            self._get_window().set_status("Call ended")
            self._finish_local(CallState.ENDED)

    def accept(self) -> None:
        context = self.context
        if context is None or context.state != CallState.RINGING:
            return
        context.transition(CallState.NEGOTIATING)
        window = self._get_window()
        window.set_status("Connecting…")

        if self._pending_offer is not None:
            self._start_media(offerer=False, remote_offer=self._pending_offer)
            return

        # JMI: the proceed message selects this full resource. The initiator
        # will now send session-initiate to us.
        assert context.peer_full is not None
        self._module(context.account).send_jmi(
            context.peer_full, "proceed", context.sid
        )

    def decline(self) -> None:
        context = self.context
        if context is None:
            return
        if context.incoming and context.peer_full is not None:
            if self._pending_offer is None:
                self._module(context.account).send_jmi(
                    context.peer_full, "reject", context.sid, reason="busy"
                )
            else:
                self._module(context.account).send_jingle(
                    context.peer_full,
                    "session-terminate",
                    context.sid,
                    initiator=context.initiator or context.peer_full,
                    responder=self._own_jid(context.account),
                    reason="decline",
                )
        self._finish_local(CallState.ENDED)

    def hangup(self) -> None:
        context = self.context
        if context is None or context.state in {CallState.ENDED, CallState.FAILED}:
            if self.window is not None:
                self.window.close_call()
            return

        peer = context.peer_full or context.peer_bare
        if context.state == CallState.PROPOSING:
            self._module(context.account).send_jmi(
                context.peer_bare, "retract", context.sid, reason="cancel"
            )
        elif context.state == CallState.RINGING and context.incoming:
            self._module(context.account).send_jmi(
                peer, "reject", context.sid, reason="busy"
            )
        else:
            self._module(context.account).send_jingle(
                peer,
                "session-terminate",
                context.sid,
                initiator=context.initiator or self._own_jid(context.account),
                responder=context.responder,
                reason="success",
            )
            self._module(context.account).send_jmi(
                peer, "finish", context.sid, reason="success"
            )
        self._finish_local(CallState.ENDED)

    def _start_media(
        self,
        *,
        offerer: bool,
        remote_offer: SessionDescription | None = None,
    ) -> None:
        context = self.context
        if context is None:
            return

        try:
            self.media = WebRTCMediaEngine(
                video=context.has_video,
                stun_server=str(self.plugin.config["stun_server"] or ""),
                turn_server=str(self.plugin.config["turn_server"] or ""),
                on_local_description=self._on_local_description,
                on_ice_candidate=self._on_ice_candidate,
                on_connected=self._on_media_connected,
                on_failed=self._on_media_failed,
                on_remote_video=self._on_remote_video,
            )
            if offerer:
                self.media.start_offer()
            else:
                assert remote_offer is not None
                self.media.start_answer(remote_offer)
        except Exception as exc:
            log.exception("Unable to start media")
            self._on_media_failed(str(exc))

    def _on_local_description(self, description: SessionDescription) -> None:
        self._local_description = description
        context = self.context
        if context is None or context.peer_full is None:
            return

        own = self._own_jid(context.account)
        if context.incoming:
            action = "session-accept"
            initiator = context.initiator or context.peer_full
            responder = own
            creator = "initiator"
        else:
            action = "session-initiate"
            initiator = own
            responder = context.peer_full
            creator = "initiator"
            context.initiator = own
            context.responder = context.peer_full

        self._module(context.account).send_jingle(
            context.peer_full,
            action,
            context.sid,
            initiator=initiator,
            responder=responder,
            description=description,
            creator=creator,
        )

        queued = self._pending_local_candidates
        self._pending_local_candidates = []
        for mid, candidate_text in queued:
            self._send_local_candidate(mid, candidate_text)

    def _on_ice_candidate(self, mid: str, candidate_text: str) -> None:
        if self._local_description is None:
            self._pending_local_candidates.append((mid, candidate_text))
            return
        self._send_local_candidate(mid, candidate_text)

    def _send_local_candidate(self, mid: str, candidate_text: str) -> None:
        context = self.context
        if context is None or context.peer_full is None:
            return
        try:
            candidate = IceCandidate.from_sdp(candidate_text)
        except ValueError:
            log.warning("Ignoring malformed local candidate: %s", candidate_text)
            return

        local_section = next(
            (
                item
                for item in (self._local_description.media if self._local_description else [])
                if item.mid == mid
            ),
            None,
        )
        section = MediaSection(
            media=local_section.media if local_section is not None else "audio",
            mid=mid,
            ice_ufrag=local_section.ice_ufrag if local_section is not None else "",
            ice_pwd=local_section.ice_pwd if local_section is not None else "",
            fingerprint=local_section.fingerprint if local_section is not None else None,
            candidates=[candidate],
        )
        description = SessionDescription(media=[section], bundle=())
        self._module(context.account).send_jingle(
            context.peer_full,
            "transport-info",
            context.sid,
            initiator=context.initiator or self._own_jid(context.account),
            responder=context.responder,
            description=description,
            creator="initiator",
        )

    def _on_media_connected(self) -> None:
        if self.context is None:
            return
        if self.context.state == CallState.NEGOTIATING:
            self.context.transition(CallState.CONNECTED)
        self._get_window().connected(self.context.has_video)

    def _on_media_failed(self, reason: str) -> None:
        if self.context is not None:
            log.error("Call failed: %s", reason)
            self._get_window().set_status(f"Call failed: {reason}")
            self._finish_local(CallState.FAILED, hide=False)

    def _on_remote_video(self, paintable) -> None:
        self._get_window().set_remote_paintable(paintable)

    def _finish_local(self, state: CallState, *, hide: bool = True) -> None:
        context = self.context
        if self.media is not None:
            self.media.close()
            self.media = None
        if context is not None and context.state not in {CallState.ENDED, CallState.FAILED}:
            try:
                context.transition(state)
            except Exception:
                context.state = state
        self._pending_offer = None
        self._local_description = None
        self._pending_local_candidates = []
        if hide and self.window is not None:
            self.window.close_call()

    def _cleanup(self, terminal: bool = True) -> None:
        if self.media is not None:
            self.media.close()
            self.media = None
        self._pending_offer = None
        self._local_description = None
        self._pending_local_candidates = []
        if terminal and self.window is not None:
            self.window.close_call()
        self.context = None

    def shutdown(self) -> None:
        self._cleanup()
