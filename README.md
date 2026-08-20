# Gajim Calls

Audio and video calls for **Gajim 2.4.x**, implemented as a normal Gajim plugin.

The plugin adds audio/video call buttons to one-to-one chats and implements a
modern media path with **GStreamer `webrtcbin`**, while using standard XMPP
Jingle signaling on the wire.

## Features

- Audio calls (Opus)
- Video calls (VP8 + Opus)
- XEP-0353 Jingle Message Initiation: propose, ringing, proceed, reject, retract,
  and finish
- XEP-0166 Jingle session signaling
- XEP-0167 Jingle RTP descriptions
- XEP-0176 ICE-UDP candidate exchange, including trickle ICE
- XEP-0320 DTLS-SRTP fingerprints
- Direct-Jingle fallback for peers that skip XEP-0353
- Incoming call UI, accept/decline, hang-up, and embedded remote video when
  `gtk4paintablesink` is available
- Optional STUN and TURN configuration
- Headless unit tests for call state, SDP/Jingle conversion, ICE parsing, and
  Gajim archive packaging
- Gitea Actions build that produces a directly installable plugin artifact

## Why this plugin exists

Gajim 2.x still contains a substantial amount of its historical Jingle
signaling/UI code, but its old audio/video implementation depended on
Farstream and is disabled. This plugin does **not** re-enable Farstream.
Instead it owns its call session IDs before Gajim's legacy Jingle handler and
uses GStreamer's maintained WebRTC stack for ICE, DTLS-SRTP, RTP, codecs, and
device capture/playback.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design.

## Install from a Gitea Actions artifact

1. Open **Actions** in this repository.
2. Open the latest successful **Build Gajim Calls** run.
3. Download the `gajim-calls` artifact.
4. In Gajim, open **Preferences → Plugins**.
5. Choose **Install from File / Install from ZIP** and select the downloaded
   artifact ZIP.
6. Enable **Gajim Calls**.

The workflow deliberately uploads a directory whose only top-level entry is
`gajim_calls/`. That matches Gajim 2.4's archive installer, so the downloaded
Actions artifact itself has the correct plugin layout.

For local builds:

```bash
python scripts/build_plugin.py --output dist/gajim_calls.zip
python scripts/verify_archive.py dist/gajim_calls.zip
```

Then install `dist/gajim_calls.zip` in Gajim.

## Linux media dependencies

A native Ubuntu/Debian Gajim install needs GStreamer WebRTC plus common codec
and device plugins. On Ubuntu, the relevant packages are typically:

```bash
sudo apt install \
  gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad \
  gstreamer1.0-libav \
  gstreamer1.0-nice \
  gir1.2-gst-plugins-bad-1.0
```

The plugin probes the required GStreamer elements when Gajim loads it. If
`webrtcbin`, Opus, microphone capture, or playback support is missing, Gajim
shows the dependency problem instead of activating a half-working plugin.

## STUN / TURN

Open **Preferences → Plugins → Gajim Calls → Configure**.

Examples:

```text
STUN: stun://stun.example.net:3478
TURN: turn://username:password@turn.example.net:3478
```

STUN is often enough for ordinary home NAT. TURN is recommended if you need
calls to work reliably through restrictive NAT, CGNAT, or enterprise
firewalls.

TURN credentials are stored in Gajim's plugin configuration. Prefer
short-lived TURN credentials where possible.

## Compatibility target

The manifest intentionally targets:

```text
gajim >= 2.4.2, < 2.5.0
```

Gajim's plugin/UI internals are not a stable public ABI, so a new Gajim minor
series should be tested before widening the requirement.

The signaling is designed around interoperable XMPP standards, so the intended
peer set includes clients implementing modern Jingle RTP/JMI such as
Conversations-family and Dino-family clients.

## Testing

```bash
python -m pytest -q
python -m compileall -q gajim_calls
python scripts/build_plugin.py --output dist/gajim_calls.zip
python scripts/verify_archive.py dist/gajim_calls.zip
```

CI also runs Ruff for Python errors and uploads the installable artifact only
after tests and archive validation pass.

## Security

Media uses DTLS-SRTP negotiated through XEP-0320 fingerprints. ICE candidates
can reveal local/network addresses to the peer as part of normal peer-to-peer
call establishment. XMPP signaling is carried over the account's normal XMPP
connection; this plugin does not claim that Jingle signaling stanzas are
OMEMO-encrypted.

## License

GPL-3.0.
