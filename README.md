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
- Headless unit tests for call state, SDP/Jingle conversion, ICE parsing, manual
  archive packaging, and updater-repository packaging
- Gitea Actions CI with automatic versioned Gitea releases
- Gajim-compatible `package_index.json` repository feed on the
  `plugin-repository` branch

## Why this plugin exists

Gajim 2.x still contains a substantial amount of its historical Jingle
signaling/UI code, but its old audio/video implementation depended on
Farstream and is disabled. This plugin does **not** re-enable Farstream.
Instead it owns its call session IDs before Gajim's legacy Jingle handler and
uses GStreamer's maintained WebRTC stack for ICE, DTLS-SRTP, RTP, codecs, and
device capture/playback.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design.

## Releases

`gajim_calls/plugin-manifest.json` is the source of truth for the plugin
version. After CI passes on `main`, the Gitea workflow checks that version:

- If `v<version>` has never been published, the workflow creates the tag and
  Gitea release automatically.
- If that exact version is already tagged at the current commit, rerunning the
  workflow repairs/replaces generated release assets.
- If the version is already tagged at an older commit, the workflow refuses to
  silently republish changed code under the same version. Bump the manifest
  version first.

Every release contains:

- `gajim_calls-<version>.zip` — **manual install** archive for Gajim's
  **Install from File / Install from ZIP** path.
- `gajim_calls_<version>.zip` — **repository updater** package. Its
  `plugin-manifest.json` is at the ZIP root, as Gajim's repository downloader
  expects.
- `package_index.json` and `images.zip` — Gajim plugin repository metadata.

For normal installation, download `gajim_calls-<version>.zip` from the latest
release, then open **Preferences → Plugins → Install from File / Install from
ZIP** and enable **Gajim Calls**.

## Gajim plugin auto-update repository

The release job also publishes/updates a dedicated `plugin-repository` branch.
Its root has this layout:

```text
package_index.json
images.zip
gajim_calls/
  gajim_calls_<version>.zip
```

This matches Gajim's plugin repository protocol. New versions are retained on
the branch and `package_index.json` is regenerated from every package present.

The repository base URL, when it is anonymously reachable, is:

```text
https://dock-it.dev/Deauth/gajim-calls/raw/branch/plugin-repository
```

**Important:** this Gitea repository is currently private. Stock Gajim does not
send your Gitea credentials when downloading a plugin repository, so the feed
must be exposed anonymously (for example by making this repository/feed public)
before a normal Gajim client can fetch it.

Also, stock Gajim obtains its default plugin-repository URL from Gajim's own
update service. For completely automatic discovery in an unmodified Gajim
installation, Gajim Calls must be included in the official Gajim plugin
repository. The generated branch uses the same package format and is ready to
serve directly in builds/configurations that point Gajim at this repository.

## Build locally

Manual-install ZIP:

```bash
python scripts/build_plugin.py --output dist/gajim_calls.zip
python scripts/verify_archive.py dist/gajim_calls.zip
```

Updater repository:

```bash
python scripts/build_repository.py --output-dir dist/repository
python scripts/verify_repository.py dist/repository
```

The two ZIP layouts are intentionally different. Do not use the updater ZIP
with Gajim's manual ZIP installer and do not add a `gajim_calls/` wrapper to the
repository updater ZIP.

## Install from a Gitea Actions artifact

CI still publishes a directly installable `gajim-calls` Actions artifact for
every successful build. Open the latest successful **Build and Release Gajim
Calls** run, download that artifact, and install the downloaded ZIP through
Gajim's plugin manager.

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
python -m compileall -q gajim_calls scripts
python scripts/build_plugin.py --output dist/gajim_calls.zip
python scripts/verify_archive.py dist/gajim_calls.zip
python scripts/build_repository.py --output-dir dist/repository
python scripts/verify_repository.py dist/repository
```

CI also runs Ruff and only publishes a release after linting, unit tests,
compilation, and both package-layout verifiers pass.

## Security

Media uses DTLS-SRTP negotiated through XEP-0320 fingerprints. ICE candidates
can reveal local/network addresses to the peer as part of normal peer-to-peer
call establishment. XMPP signaling is carried over the account's normal XMPP
connection; this plugin does not claim that Jingle signaling stanzas are
OMEMO-encrypted.

## License

GPL-3.0.
