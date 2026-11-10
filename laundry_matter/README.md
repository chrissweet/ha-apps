# Laundry Matter sensors

Presents each appliance as its own Matter contact sensor, so an Amazon Echo (or any Matter controller) can use it without an Amazon login in Home Assistant and without Home Assistant Cloud.

- Closed while the appliance runs, open when it has finished or is idle. An Alexa routine on "opens" announces the end; "Alexa, is the washer on?" answers with the contact state.
- Running: the power entity reads above `threshold_w`. Finished: at or below `threshold_w` for `off_delay_min` since the last reading above it.
- Power comes from Home Assistant over its websocket (Supervisor token, `homeassistant_api`); no long-lived token is stored.
- Each appliance is a separate Matter node (own port, own pairing code). Setup codes are random, generated on first start and kept in `/data` with the Matter pairing keys; the state survives restarts, so a restart does not cause a false "finished".
- Needs host networking (Matter uses mDNS and UDP).

## Pairing

Start the app and read its log: each unpaired appliance prints a manual pairing code and QR code. In the Alexa app: Devices, plus, Add Device, Matter, then enter the code. Keep "Matter" out of device names (Alexa mishears it).

## Options

| Option | Meaning |
|---|---|
| `name` | Device name shown to Alexa |
| `entity` | Power sensor in watts |
| `threshold_w` | Above this the appliance is running |
| `off_delay_min` | Minutes at or below the threshold before it counts as finished |
| `port` | Matter UDP port, one per appliance |

Defaults are for the SEM-Meter washer (circuit 11, 15 W, 5 min: washer pauses at up to 11 W for up to 2.7 min) and dryer (circuit 12, 50 W, 10 min: gaps between heating bursts grow to 4 min near the end).
