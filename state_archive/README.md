# State archive

Keeps every state change of a chosen list of Home Assistant entities (temperatures and humidities to start with), forever, outside the recorder.

- Separate from the SEM archive on purpose: its own app, its own folder, nothing shared but the layout.
- Connects to Home Assistant's WebSocket API (`homeassistant_api`), listens for `state_changed`, keeps only the listed entities.
- Writes `/share/states/YYYY/MM/DD/HH.jsonl` (UTC hour), one JSON line per event, flushed as written.
- At the end of each hour the file is compressed to `HH.jsonl.xz` and the plain file removed.
- Each file starts with the current value of every entity (`snapshot: true`), so every hour can be read on its own. The same snapshot is written after every reconnect.
- An SNTP check at the start of each hour records the host clock offset. The app never sets the clock.
- Read-only towards Home Assistant: it calls no services and changes nothing.

Line types: `state`, `clock`, `status` (start, stop, connect, disconnect, missing). See the docstring in `archive.py`.

Option: `entities`, the list of entity ids to keep. An id that does not exist is reported once per connection as `missing` and is picked up if it appears later.
