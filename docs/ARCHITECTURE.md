# Architecture

## Goals

Gajim Calls is intentionally a plugin rather than a patched Gajim build. The
design keeps protocol conversion testable without Gajim, GTK, or GStreamer and
loads the media stack only when needed.

## Components

### `plugin.py`

Registers two chat action buttons and the custom per-account Gajim network
module. It also performs an early GStreamer capability probe.

### `module.py`

Registers two nbxmpp stanza handlers:

- XEP-0353 Jingle Message Initiation messages
- XEP-0166 Jingle IQ-set stanzas

The Jingle handler has priority `5`, ahead of Gajim's legacy Jingle handler.
It only consumes RTP `session-initiate` requests and session IDs already owned
by this plugin. File transfer or unrelated Jingle traffic is left to Gajim.

### `controller.py`

Owns the one-call-at-a-time state machine and coordinates:

1. JMI proposal/resource selection
2. Jingle session negotiation
3. GStreamer media setup
4. GTK call UI
5. hang-up/cleanup

For JMI calls, the initial proposal goes to the peer's bare JID. The resource
that answers returns `proceed` from a full JID, and subsequent Jingle IQs are
sent directly to that selected resource.

### `protocol.py`

Dependency-free Jingle/JMI XML model conversion. It implements:

- XEP-0353 proposal lifecycle
- XEP-0166 session actions
- XEP-0167 RTP payload descriptions
- XEP-0176 ICE-UDP transport/candidates
- XEP-0320 DTLS fingerprints
- XEP-0338 BUNDLE grouping when present

### `sdp.py`

Dependency-free translator between GStreamer's SDP and the Jingle-friendly
model. It preserves:

- media sections / MIDs
- RTP payload IDs and codec clock rates/channels
- fmtp parameters
- RTCP feedback
- ICE credentials
- ICE candidates
- DTLS fingerprint/setup role
- RTCP mux
- BUNDLE groups

### `media.py`

Lazily loads GStreamer and builds a `webrtcbin` pipeline.

Audio send:

```text
autoaudiosrc
 → audioconvert
 → audioresample
 → opusenc
 → rtpopuspay
 → webrtcbin
```

Video send:

```text
autovideosrc
 → videoconvert
 → videoscale
 → VP8
 → rtpvp8pay
 → webrtcbin
```

Incoming RTP is decoded with `decodebin`. Audio goes to `autoaudiosink`.
Video prefers `gtk4paintablesink` so it can be embedded into the GTK4 call
window, with an automatic video sink fallback.

## Call flow

### Outgoing JMI call

```text
Gajim A                      Gajim/Peer B
   | -- propose (bare JID) ------> |
   | <------ ringing ------------- |
   | <------ proceed (full JID) -- |
   | -- session-initiate --------> |
   | <------ session-accept ------ |
   | <---- transport-info -------> |
   | ===== DTLS-SRTP media ======= |
   | -- session-terminate/finish > |
```

### Direct Jingle fallback

A peer may send `session-initiate` without XEP-0353. If the stanza contains
RTP audio/video content, the plugin acknowledges it, shows the incoming-call
UI, and sends `session-accept` only after the user accepts.

## Legacy Gajim coexistence

Current Gajim 2.4 still registers its historical `Jingle` module. The plugin
does not unregister or overwrite it. Instead it uses an earlier stanza-handler
priority and raises `NodeProcessed` only for call traffic it owns. This keeps
the plugin isolated and avoids breaking other Jingle users.

## Test strategy

The test suite focuses on deterministic pieces that should not require a
camera, microphone, display server, XMPP account, or TURN server:

- legal/illegal call state transitions
- SDP parsing and reconstruction
- ICE candidate round trips
- JMI XML round trips
- Jingle RTP/ICE/DTLS XML round trips
- Gajim ZIP archive structure

A real two-client integration call is still the final validation for device,
NAT, TURN, and peer-specific behavior.
