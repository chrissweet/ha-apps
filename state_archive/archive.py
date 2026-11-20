"""State archive: keep every state change of chosen Home Assistant entities, in hourly compressed files.

A sibling of the SEM archive, kept separate on purpose: its own app, its own folder, its own files.
Writes <base>/YYYY/MM/DD/HH.jsonl (UTC hour), one JSON object per line, flushed as written. When the
hour ends the file is appended to HH.jsonl.xz as one xz stream and the plain file is removed.
Reads from Home Assistant over its WebSocket API; never writes to Home Assistant; never sets the clock.

Line fields: t (arrival time at this app, UTC ISO 8601), type, and per type:
  state   entity_id, state, attributes, last_changed, last_updated, snapshot
          the entity's state exactly as Home Assistant reports it (state is a string, e.g. "69.08",
          "unavailable"). snapshot=true for the current value of every entity written at the start
          of each file and after every (re)connect; false for a change as it arrives.
  clock   server, offset_s, delay_s   SNTP check of this host's clock (or error)
  status  event, detail               start, stop, connect, disconnect, missing
"""
import asyncio
import json
import lzma
import os
import signal
import socket
import struct
import sys
import threading
import time
from datetime import datetime, timezone

import websockets

NTP_SERVERS = ["time.cloudflare.com", "time.nist.gov", "time-nw.nist.gov"]
NTP_EPOCH = 2208988800  # seconds from 1900-01-01 to 1970-01-01


def log(text):
    print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {text}", flush=True)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def sntp_offset(server, timeout=3.0):
    """Return (offset_s, delay_s) of this host's clock against an SNTP server."""
    packet = b"\x23" + 47 * b"\0"  # LI 0, version 4, mode 3 (client)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        t0 = time.time()
        s.sendto(packet, (server, 123))
        data, _ = s.recvfrom(512)
        t3 = time.time()
    if len(data) < 48:
        raise ValueError("short reply")

    def ts(offset):
        sec, frac = struct.unpack("!II", data[offset:offset + 8])
        return sec - NTP_EPOCH + frac / 2**32

    t1, t2 = ts(32), ts(40)  # server receive, server transmit
    return ((t1 - t0) + (t2 - t3)) / 2, (t3 - t0) - (t2 - t1)


def state_line(s, snapshot):
    return {"t": now_iso(), "type": "state", "entity_id": s["entity_id"], "state": s.get("state"),
            "attributes": s.get("attributes", {}), "last_changed": s.get("last_changed"),
            "last_updated": s.get("last_updated"), "snapshot": snapshot}


class Archive:
    def __init__(self, base, period="hour"):
        self.base = base
        self.period = period  # "hour"; "minute" only for testing rollover
        self.lock = threading.Lock()
        self.latest = {}  # entity_id -> last state object seen
        self.key = None
        self.f = None
        self.counts = {}  # type -> lines written to the current file

    def _key(self):
        n = datetime.now(timezone.utc)
        return n.strftime("%Y/%m/%d/%H" if self.period == "hour" else "%Y/%m/%d/%H-%M")

    def _path(self, key):
        return os.path.join(self.base, key + ".jsonl")

    def _write(self, obj):
        self.f.write(json.dumps(obj, separators=(",", ":")) + "\n")
        self.f.flush()
        self.counts[obj["type"]] = self.counts.get(obj["type"], 0) + 1
        if obj["type"] in ("status", "clock"):
            log(f"{obj['type']}: " + json.dumps({k: v for k, v in obj.items() if k not in ("t", "type")}))

    def report(self):
        with self.lock:
            log(f"file {self._path(self.key)}: {self.counts}")

    def _open(self, key):
        path = self._path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.f = open(path, "a", encoding="utf-8")
        self.key = key
        self.counts = {}
        log(f"writing {path}")
        for _, s in sorted(self.latest.items()):  # every file can be read on its own
            self._write(state_line(s, True))
        threading.Thread(target=self.clock_check, daemon=True).start()

    def _rollover(self):
        key = self._key()
        if key == self.key:
            return
        old = self.key
        if self.f:
            self.f.close()
            self.f = None
        self._open(key)
        if old:
            compress(self._path(old))
            log(f"compressed {self._path(old)}.xz")

    def write(self, obj):
        with self.lock:
            self._rollover()
            self._write(obj)

    def tick(self):
        with self.lock:
            self._rollover()

    def state(self, s, snapshot=False):
        with self.lock:
            self.latest[s["entity_id"]] = s
            self._rollover()
            self._write(state_line(s, snapshot))

    def clock_check(self):
        err = "no server tried"
        for server in NTP_SERVERS:
            try:
                offset, delay = sntp_offset(server)
                self.write({"t": now_iso(), "type": "clock", "server": server,
                            "offset_s": round(offset, 6), "delay_s": round(delay, 6)})
                return
            except Exception as e:  # best effort: log and try the next server
                err = f"{server}: {e}"
        self.write({"t": now_iso(), "type": "clock", "error": err})

    def close(self):
        with self.lock:
            if self.f:
                self.f.close()
                self.f = None  # current hour stays plain; compressed after restart


def compress(path):
    """Append a plain hour file to its .xz as one stream, then remove the plain file."""
    if not os.path.exists(path):
        return
    with open(path, "rb") as src:
        data = src.read()
    if data:
        with open(path + ".xz", "ab") as dst:
            dst.write(lzma.compress(data, preset=9 | lzma.PRESET_EXTREME))
            dst.flush()
            os.fsync(dst.fileno())
    os.remove(path)


def compress_leftovers(base, current_key_path):
    for root, _, files in os.walk(base):
        for name in files:
            p = os.path.join(root, name)
            if name.endswith(".jsonl") and p != current_key_path:
                compress(p)


async def session(arc, url, token, entities):
    """One WebSocket session: authenticate, snapshot the entities, then record changes until it drops."""
    wanted = set(entities)
    async with websockets.connect(url, max_size=None, ping_interval=20, ping_timeout=20) as ws:
        hello = json.loads(await ws.recv())
        if hello.get("type") != "auth_required":
            raise RuntimeError(f"unexpected greeting: {hello.get('type')}")
        await ws.send(json.dumps({"type": "auth", "access_token": token}))
        auth = json.loads(await ws.recv())
        if auth.get("type") != "auth_ok":
            raise RuntimeError(f"authentication failed: {auth.get('type')}")
        arc.write({"t": now_iso(), "type": "status", "event": "connect",
                   "detail": f"Home Assistant {auth.get('ha_version', '?')}, {len(wanted)} entities"})
        # Subscribe before the snapshot so no change between the two is missed.
        await ws.send(json.dumps({"id": 1, "type": "subscribe_events", "event_type": "state_changed"}))
        await ws.send(json.dumps({"id": 2, "type": "get_states"}))
        async for raw in ws:
            m = json.loads(raw)
            if m.get("type") == "event":
                d = m["event"].get("data", {})
                if d.get("entity_id") in wanted and d.get("new_state"):
                    arc.state(d["new_state"])
            elif m.get("type") == "result" and m.get("id") == 2:
                if not m.get("success"):
                    raise RuntimeError(f"get_states failed: {m.get('error')}")
                seen = set()
                for s in m["result"]:
                    if s["entity_id"] in wanted:
                        arc.state(s, snapshot=True)
                        seen.add(s["entity_id"])
                if wanted - seen:
                    arc.write({"t": now_iso(), "type": "status", "event": "missing",
                               "detail": ", ".join(sorted(wanted - seen))})
            elif m.get("type") == "result" and not m.get("success"):
                raise RuntimeError(f"command {m.get('id')} failed: {m.get('error')}")


async def run(arc, url, token, entities, stop):
    delay = 1
    while not stop.is_set():
        try:
            await session(arc, url, token, entities)
            reason = "closed by Home Assistant"
        except asyncio.CancelledError:
            raise
        except Exception as e:
            reason = f"{type(e).__name__}: {e}"
        if stop.is_set():
            break
        arc.write({"t": now_iso(), "type": "status", "event": "disconnect", "detail": reason})
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass
        delay = min(delay * 2, 60)


async def main_async():
    opts = {}
    if os.path.exists("/data/options.json"):
        with open("/data/options.json") as f:
            opts = json.load(f)
    entities = opts.get("entities") or [e for e in os.environ.get("ENTITIES", "").split(",") if e]
    base = os.environ.get("ARCHIVE_DIR", "/share/states")
    period = os.environ.get("ARCHIVE_PERIOD", "hour")
    url = os.environ.get("HA_WS", "ws://supervisor/core/websocket")
    token = os.environ.get("HA_TOKEN") or os.environ.get("SUPERVISOR_TOKEN")
    if not entities or not token:
        log("nothing to do: need a list of entities and a Home Assistant token")
        return 1

    arc = Archive(base, period)
    compress_leftovers(base, arc._path(arc._key()))
    arc.tick()
    arc.write({"t": now_iso(), "type": "status", "event": "start", "detail": f"{len(entities)} entities"})

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    async def ticker():
        last_report = time.monotonic()
        while not stop.is_set():
            await asyncio.sleep(1)
            arc.tick()
            if time.monotonic() - last_report >= 600:
                arc.report()
                last_report = time.monotonic()

    worker = asyncio.create_task(run(arc, url, token, entities, stop))
    tick = asyncio.create_task(ticker())
    await stop.wait()
    worker.cancel()
    tick.cancel()
    await asyncio.gather(worker, tick, return_exceptions=True)
    arc.write({"t": now_iso(), "type": "status", "event": "stop", "detail": ""})
    arc.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
