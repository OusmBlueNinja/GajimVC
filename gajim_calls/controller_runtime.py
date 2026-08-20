"""Runtime controller fixes that depend on Gajim/GTK."""

from __future__ import annotations

import logging

from gi.repository import GLib

from .controller import CallController
from .incoming import RemoteCandidateBuffer
from .peer_validation import jingle_sender_allowed, jmi_sender_allowed
from .protocol import JMIEvent, JingleEvent
from .sdp import SessionDescription
from .state import CallState
from .timeout_policy import TimeoutPhase, plan_timeout

log = logging.getLogger("gajim.p.gajim_calls.controller")

_TERMINAL_STATES = {CallState.ENDED, CallState.FAILED}
_RINGING_TIMEOUT_SECONDS = 60
_NEGOTIATION_TIMEOUT_SECONDS = 35


class RuntimeCallController(CallController):
    """CallController with runtime UI, alert, trickle-ICE, and timeout behaviour."""

    def __init__(self, plugin) -> None:
        super().__init__(plugin)
        self._early_remote_candidates = RemoteCandidateBuffer()
        self._phase_timeout_id: int | None = None
        self._phase_timeout_sid: str | None = None
        self._phase_timeout_phase: TimeoutPhase | None = None

    @staticmethod
    def _uses_jmi(context) -> bool:
        return context.metadata.get("signaling") == "jmi"

    def accepts_jingle_sender(
        self, account: str, sid: str, sender: str, action: str
    ) -> bool:
        context = self.context
        if (
            context is None
            or context.account != account
            or context.sid != sid
            or context.state in _TERMINAL_STATES
        ):
            return False
        return jingle_sender_allowed(
            peer_bare=context.peer_bare,
            peer_full=context.peer_full,
            sender=sender,
            action=action,
            incoming=context.incoming,
        )

    def _cancel_phase_timeout(self) -> None:
        source_id = self._phase_timeout_id
        self._phase_timeout_id = None
        self._phase_timeout_sid = None
        self._phase_timeout_phase = None
        if source_id is None:
            return
        try:
            GLib.source_remove(source_id)
        except Exception:
            log.debug("Unable to remove call phase timeout", exc_info=True)

    def _arm_phase_timeout(self, phase: TimeoutPhase) -> None:
        context = self.context
        if context is None or context.state in _TERMINAL_STATES:
            self._cancel_phase_timeout()
            return

        seconds = (
            _RINGING_TIMEOUT_SECONDS
            if phase == "ringing"
            else _NEGOTIATION_TIMEOUT_SECONDS
        )
        self._cancel_phase_timeout()
        sid = context.sid
        self._phase_timeout_sid = sid
        self._phase_timeout_phase = phase
        self._phase_timeout_id = GLib.timeout_add_seconds(
            seconds, self._on_phase_timeout, sid, phase
        )
        log.debug("Armed %s timeout for sid=%s in %ss", phase, sid, seconds)

    def _jingle_started(self, context) -> bool:
        if context.incoming:
            return self._pending_offer is not None
        return self._local_description is not None

    def _on_phase_timeout(self, sid: str, phase: TimeoutPhase) -> bool:
        if self._phase_timeout_sid == sid and self._phase_timeout_phase == phase:
            self._phase_timeout_id = None
            self._phase_timeout_sid = None
            self._phase_timeout_phase = None

        context = self.context
        if context is None or context.sid != sid or context.state in _TERMINAL_STATES:
            return GLib.SOURCE_REMOVE

        if phase == "ringing":
            if context.state not in {CallState.PROPOSING, CallState.RINGING}:
                return GLib.SOURCE_REMOVE
        elif phase == "negotiating":
            if context.state != CallState.NEGOTIATING:
                return GLib.SOURCE_REMOVE
        else:
            return GLib.SOURCE_REMOVE

        plan = plan_timeout(
            phase,
            incoming=context.incoming,
            uses_jmi=self._uses_jmi(context),
            jingle_started=self._jingle_started(context),
        )
        module = self._module(context.account)
        peer = context.peer_full or context.peer_bare

        try:
            if plan.terminate_jingle and context.peer_full is not None:
                module.send_jingle(
                    context.peer_full,
                    "session-terminate",
                    context.sid,
                    initiator=context.initiator or self._own_jid(context.account),
                    responder=context.responder,
                    reason=plan.jingle_reason,
                )
                log.info(
                    "TX timeout session-terminate sid=%s to=%s",
                    context.sid,
                    context.peer_full,
                )

            if plan.jmi_action is not None:
                target = context.peer_bare if plan.jmi_action == "retract" else peer
                module.send_jmi(
                    target,
                    plan.jmi_action,
                    context.sid,
                    reason=plan.jmi_reason,
                )
                log.info(
                    "TX timeout JMI %s sid=%s to=%s",
                    plan.jmi_action,
                    context.sid,
                    target,
                )
        except Exception:
            log.exception(
                "Unable to signal %s timeout sid=%s peer=%s",
                phase,
                context.sid,
                peer,
            )

        self._stop_incoming_alerts(context.sid)
        self._get_window().set_status("Call timed out")
        callback = getattr(self.plugin, "set_call_active", None)
        if callable(callback):
            callback(False)
        self._finish_local(CallState.ENDED)
        return GLib.SOURCE_REMOVE

    def start_outgoing(self, account: str, peer_jid, *, video: bool) -> None:
        previous_sid = self.context.sid if self.context is not None else None
        super().start_outgoing(account, peer_jid, video=video)
        context = self.context
        if (
            context is not None
            and context.sid != previous_sid
            and context.state == CallState.PROPOSING
        ):
            context.metadata["signaling"] = "jmi"
            self._arm_phase_timeout("ringing")

    def _start_incoming_alerts(self) -> None:
        context = self.context
        if (
            context is None
            or not context.incoming
            or context.state != CallState.RINGING
        ):
            return
        callback = getattr(self.plugin, "incoming_call_started", None)
        if callable(callback):
            callback(
                context.account,
                context.sid,
                context.peer_bare,
                video=context.has_video,
            )

    def _stop_incoming_alerts(self, sid: str | None = None) -> None:
        callback = getattr(self.plugin, "incoming_call_stopped", None)
        if callable(callback):
            callback(sid)

    def handle_jmi(self, account: str, from_jid: str, event: JMIEvent) -> bool:
        context = self.context
        if event.action != "propose" and context is not None and event.id == context.sid:
            if context.account != account or not jmi_sender_allowed(
                peer_bare=context.peer_bare, sender=from_jid
            ):
                log.warning(
                    "Ignoring JMI %s sid=%s from unexpected sender=%s",
                    event.action,
                    event.id,
                    from_jid,
                )
                return False

        handled = super().handle_jmi(account, from_jid, event)
        context = self.context
        if handled and event.action == "propose":
            if context is not None and context.sid == event.id and context.incoming:
                context.metadata["signaling"] = "jmi"
                self._start_incoming_alerts()
                self._arm_phase_timeout("ringing")
        elif handled and event.action == "proceed":
            if context is not None and context.sid == event.id:
                self._arm_phase_timeout("negotiating")
        return handled

    def handle_jingle(self, account: str, from_jid: str, event: JingleEvent) -> None:
        context = self.context
        if context is not None and event.sid == context.sid:
            if not self.accepts_jingle_sender(account, event.sid, from_jid, event.action):
                log.warning(
                    "Ignoring Jingle %s sid=%s from unexpected sender=%s",
                    event.action,
                    event.sid,
                    from_jid,
                )
                return

        if event.action == "transport-info" and self.media is None:
            context = self.context
            if (
                context is None
                or context.account != account
                or context.sid != event.sid
                or context.state in _TERMINAL_STATES
            ):
                log.debug(
                    "Ignoring unowned early ICE sid=%s account=%s",
                    event.sid,
                    account,
                )
                return
            added = self._early_remote_candidates.add_event(event)
            if added:
                log.info(
                    "Buffered %d remote ICE candidate(s) for sid=%s before media startup",
                    added,
                    event.sid,
                )
            return

        super().handle_jingle(account, from_jid, event)
        context = self.context
        if event.action == "session-initiate":
            if context is not None and context.sid == event.sid:
                # Treat the authenticated stanza sender as the initiator unless
                # another protocol explicitly authorizes redirection.
                if context.incoming:
                    context.peer_full = from_jid
                    context.initiator = from_jid
                self._start_incoming_alerts()
                if context.state == CallState.RINGING:
                    self._arm_phase_timeout("ringing")
                elif context.state == CallState.NEGOTIATING:
                    self._arm_phase_timeout("negotiating")

    def accept(self) -> None:
        context = self.context
        if context is not None:
            self._stop_incoming_alerts(context.sid)
        super().accept()
        context = self.context
        if context is not None and context.state == CallState.NEGOTIATING:
            self._arm_phase_timeout("negotiating")

    def decline(self) -> None:
        context = self.context
        if context is not None:
            self._stop_incoming_alerts(context.sid)
        super().decline()

    def _start_media(self, *, offerer: bool, remote_offer=None) -> None:
        context = self.context
        super()._start_media(offerer=offerer, remote_offer=remote_offer)
        if self.media is None or context is None or context.state in _TERMINAL_STATES:
            return

        flushed = self._early_remote_candidates.flush_to(self.media, context.sid)
        if flushed:
            log.info(
                "Applied %d buffered remote ICE candidate(s) for sid=%s",
                flushed,
                context.sid,
            )

    def _send_local_candidate(self, mid: str, candidate_text: str) -> None:
        if not candidate_text.strip():
            log.debug("Local ICE gathering complete for mid=%s", mid)
            return
        context = self.context
        if context is None or context.state in _TERMINAL_STATES:
            log.debug("Ignoring late local ICE candidate for cancelled/ended call")
            return
        super()._send_local_candidate(mid, candidate_text)

    def _on_local_description(self, description: SessionDescription) -> None:
        context = self.context
        if context is None or context.state in _TERMINAL_STATES:
            log.debug("Ignoring late local description for cancelled/ended call")
            return
        super()._on_local_description(description)

    def _on_media_connected(self) -> None:
        context = self.context
        if context is None or context.state in _TERMINAL_STATES:
            log.debug("Ignoring late media-connected callback for cancelled/ended call")
            return
        self._cancel_phase_timeout()
        super()._on_media_connected()

    @staticmethod
    def _failure_reason(reason: str) -> str:
        if "ICE connectivity checks failed" in reason:
            return "connectivity-error"
        if "encoder" in reason.lower() or "media" in reason.lower():
            return "failed-application"
        return "general-error"

    def _terminate_remote_failure(self, reason: str) -> None:
        context = self.context
        if context is None:
            return

        wire_reason = self._failure_reason(reason)
        peer = context.peer_full or context.peer_bare
        module = self._module(context.account)

        try:
            if context.peer_full is not None and context.state in {
                CallState.NEGOTIATING,
                CallState.CONNECTED,
            }:
                module.send_jingle(
                    context.peer_full,
                    "session-terminate",
                    context.sid,
                    initiator=context.initiator or self._own_jid(context.account),
                    responder=context.responder,
                    reason=wire_reason,
                )
                log.info(
                    "TX failure session-terminate sid=%s to=%s reason=%s",
                    context.sid,
                    context.peer_full,
                    wire_reason,
                )

            if self._uses_jmi(context):
                module.send_jmi(peer, "finish", context.sid, reason=wire_reason)
                log.info(
                    "TX failure JMI finish sid=%s to=%s reason=%s",
                    context.sid,
                    peer,
                    wire_reason,
                )
        except Exception:
            log.exception(
                "Unable to signal remote call failure sid=%s peer=%s reason=%s",
                context.sid,
                peer,
                wire_reason,
            )

    def hangup(self) -> None:
        context = self.context
        if context is not None:
            self._stop_incoming_alerts(context.sid)
        if context is None or context.state in _TERMINAL_STATES:
            super().hangup()
            callback = getattr(self.plugin, "set_call_active", None)
            if callable(callback):
                callback(False)
            return

        if context.state in {CallState.PROPOSING, CallState.RINGING}:
            log.info(
                "Cancelling ringing call sid=%s peer=%s",
                context.sid,
                context.peer_bare,
            )
            callback = getattr(self.plugin, "set_call_active", None)
            if callable(callback):
                callback(False)
            super().hangup()
            return

        if context.state == CallState.NEGOTIATING:
            peer = context.peer_full or context.peer_bare
            module = self._module(context.account)
            try:
                if context.peer_full is not None and self._jingle_started(context):
                    module.send_jingle(
                        context.peer_full,
                        "session-terminate",
                        context.sid,
                        initiator=context.initiator or self._own_jid(context.account),
                        responder=context.responder,
                        reason="cancel",
                    )
                    log.info(
                        "TX cancel session-terminate sid=%s to=%s",
                        context.sid,
                        context.peer_full,
                    )
                if self._uses_jmi(context):
                    module.send_jmi(peer, "finish", context.sid, reason="cancel")
                    log.info("TX cancel JMI finish sid=%s to=%s", context.sid, peer)
            except Exception:
                log.exception(
                    "Unable to signal call cancellation sid=%s peer=%s",
                    context.sid,
                    peer,
                )

            callback = getattr(self.plugin, "set_call_active", None)
            if callable(callback):
                callback(False)
            self._finish_local(CallState.ENDED)
            return

        callback = getattr(self.plugin, "set_call_active", None)
        if callable(callback):
            callback(False)
        super().hangup()

    def _on_media_failed(self, reason: str) -> None:
        context = self.context
        if context is None or context.state in _TERMINAL_STATES:
            log.debug("Ignoring late media failure after call ended: %s", reason)
            return

        log.error("Call failed: %s", reason)
        self._terminate_remote_failure(reason)
        self._get_window().set_status(f"Call failed: {reason}")
        self._finish_local(CallState.FAILED, hide=False)

    def _finish_local(self, state: CallState, *, hide: bool = True) -> None:
        self._cancel_phase_timeout()
        context = self.context
        if context is not None:
            self._stop_incoming_alerts(context.sid)
        self._early_remote_candidates.clear()
        super()._finish_local(state, hide=hide)

    def _cleanup(self, terminal: bool = True) -> None:
        self._cancel_phase_timeout()
        context = self.context
        if context is not None:
            self._stop_incoming_alerts(context.sid)
        else:
            self._stop_incoming_alerts()
        self._early_remote_candidates.clear()
        super()._cleanup(terminal=terminal)
