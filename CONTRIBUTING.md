# Contributing

Keep signaling and state logic independent from GTK/GStreamer whenever
possible. That keeps the important wire-format behavior deterministic and
headless-testable.

Before submitting a change:

```bash
python -m pytest -q
python -m compileall -q gajim_calls
python scripts/build_plugin.py --output dist/gajim_calls.zip
python scripts/verify_archive.py dist/gajim_calls.zip
```

For signaling changes, add or update a unit test with a representative stanza
or SDP sample.

Do not add generated archives, credentials, TURN passwords, logs containing
JIDs, or media captures to the repository.
