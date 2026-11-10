"""SEM archive: keep every SEM-Meter MQTT message, raw, in hourly compressed files.

Writes <base>/YYYY/MM/DD/HH.jsonl (UTC hour), one JSON object per line, flushed as
written. When the hour ends the file is appended to HH.jsonl.xz as one xz stream and
the plain file is removed. Depends only on the MQTT broker; never sets the clock.

Line fields: t (arrival time, UTC ISO 8601), type, and per type:
  msg     topic, payload   every SEMMETER/# message, payload exactly as received
  config  topic, payload, snapshot   discovery config (homeassistant/sensor/<serial>_*/config); snapshot=true for the full set
                                     written at the start of each file, false when a
                                     config arrives or changes
  clock   server, offset_s, delay_s  SNTP check of this host's clock (or error)
  status  event, detail              start, stop, connect, disconnect
"""
import json
import lzma
import os
import signal
import socket
import struct
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

NTP_SERVERS = ["time.cloudflare.com", "time.nist.gov", "time-nw.nist.gov"]
NTP_EPOCH = 2208988800  # seconds from 1900-01-01 to 1970-01-01


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


class Archive:
    def __init__(self, base, period="hour"):
        self.base = base
        self.period = period  # "hour"; "minute" only for testing rollover
        self.lock = threading.Lock()
        self.configs = {}  # topic -> payload (latest)
        self.key = None
        self.f = None

    def _key(self):
        n = datetime.now(timezone.utc)
        return n.strftime("%Y/%m/%d/%H" if self.period == "hour" else "%Y/%m/%d/%H-%M")

    def _path(self, key):
        return os.path.join(self.base, key + ".jsonl")

    def _write(self, obj):
        self.f.write(json.dumps(obj, separators=(",", ":")) + "\n")
        self.f.flush()

    def _open(self, key):
        path = self._path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.f = open(path, "a", encoding="utf-8")
        self.key = key
        t = now_iso()
        for topic, payload in sorted(self.configs.items()):
            self._write({"t": t, "type": "config", "topic": topic, "payload": payload, "snapshot": True})
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

    def write(self, obj):
        with self.lock:
            self._rollover()
            self._write(obj)

    def tick(self):
        with self.lock:
            self._rollover()

    def config(self, topic, payload):
        with self.lock:
            changed = self.configs.get(topic) != payload
            self.configs[topic] = payload
        if changed:
            self.write({"t": now_iso(), "type": "config", "topic": topic, "payload": payload, "snapshot": False})

    def clock_check(self):
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


def mqtt_settings():
    """MQTT login: from the Supervisor (Home Assistant app) or from environment (Mac test)."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if token:
        req = urllib.request.Request("http://supervisor/services/mqtt",
                                     headers={"Authorization": f"Bearer {token}"})
        d = json.load(urllib.request.urlopen(req, timeout=10))["data"]
        return d["host"], int(d["port"]), d.get("username"), d.get("password")
    return (os.environ["MQTT_HOST"], int(os.environ.get("MQTT_PORT", "1883")),
            os.environ.get("MQTT_USER"), os.environ.get("MQTT_PASS"))


def main():
    opts = {}
    if os.path.exists("/data/options.json"):
        with open("/data/options.json") as f:
            opts = json.load(f)
    serial = opts.get("serial") or os.environ.get("SEM_SERIAL", "94A9900BEA4E")
    base = os.environ.get("ARCHIVE_DIR", "/share/sem")
    period = os.environ.get("ARCHIVE_PERIOD", "hour")

    arc = Archive(base, period)
    compress_leftovers(base, arc._path(arc._key()))
    arc.tick()
    arc.write({"t": now_iso(), "type": "status", "event": "start", "detail": f"serial {serial}"})

    host, port, user, password = mqtt_settings()
    config_prefix = f"homeassistant/sensor/{serial}_"

    def on_connect(client, userdata, flags, reason, props):
        arc.write({"t": now_iso(), "type": "status", "event": "connect", "detail": str(reason)})
        client.subscribe([("SEMMETER/#", 0), ("homeassistant/sensor/+/config", 0)])

    def on_disconnect(client, userdata, flags, reason, props):
        arc.write({"t": now_iso(), "type": "status", "event": "disconnect", "detail": str(reason)})

    def on_message(client, userdata, m):
        payload = m.payload.decode("utf-8", errors="backslashreplace")
        if m.topic.startswith("homeassistant/"):
            if m.topic.startswith(config_prefix):  # only this meter's discovery configs
                arc.config(m.topic, payload)
        else:
            arc.write({"t": now_iso(), "type": "msg", "topic": m.topic, "payload": payload})

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="sem_archive")
    if user:
        client.username_pw_set(user, password)
    client.on_connect, client.on_disconnect, client.on_message = on_connect, on_disconnect, on_message
    client.reconnect_delay_set(1, 60)
    client.connect_async(host, port, keepalive=30)
    client.loop_start()

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *a: stop.set())
    signal.signal(signal.SIGINT, lambda *a: stop.set())
    while not stop.wait(1):
        arc.tick()

    arc.write({"t": now_iso(), "type": "status", "event": "stop", "detail": ""})
    client.loop_stop()
    client.disconnect()
    arc.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
