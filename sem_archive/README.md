# SEM archive

Keeps every MQTT message from a SEM-Meter (Fusion Energy smart home energy monitor), raw, forever.

- Subscribes to `SEMMETER/#` and the meter's Home Assistant discovery configs (`homeassistant/sensor/<serial>_*/config`).
- Writes `/share/sem/YYYY/MM/DD/HH.jsonl` (UTC hour), one JSON line per event, flushed as written.
- At the end of each hour the file is compressed to `HH.jsonl.xz` and the plain file removed.
- Each file starts with the full set of discovery configs (names, array indexes, multipliers), so every hour can be decoded on its own; config changes are written inline as they arrive.
- An SNTP check (time.cloudflare.com, NIST fallback) at the start of each hour records the host clock offset. The app never sets the clock.
- Depends only on the MQTT broker. The MQTT login comes from the Supervisor (`mqtt:need`); no password is stored.

Line types: `msg`, `config`, `clock`, `status` (start, stop, connect, disconnect). See the docstring in `archive.py`.

Option: `serial` (default `94A9900BEA4E`).
