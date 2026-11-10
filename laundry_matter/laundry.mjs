// Washer and dryer as Matter contact sensors (one Matter node each), driven by Home Assistant power sensors.
// Contact sensor: closed (BooleanState true) while running, open when finished; an Alexa routine on "opens" announces.
import fs from "node:fs";
import crypto from "node:crypto";
import { Environment, ServerNode, VendorId } from "@matter/main";
import { ContactSensorDevice } from "@matter/main/devices/contact-sensor";
import { Appliance } from "./appliance.mjs";

const DATA = process.env.DATA_DIR ?? "/data";
const HA_WS = process.env.HA_WS ?? "ws://supervisor/core/websocket";
const HA_TOKEN = process.env.HA_TOKEN ?? process.env.SUPERVISOR_TOKEN;
const STATE_FILE = `${DATA}/state.json`;
const log = (...a) => console.log(new Date().toISOString(), ...a);

const opts = JSON.parse(fs.readFileSync(`${DATA}/options.json`, "utf8"));
const saved = fs.existsSync(STATE_FILE) ? JSON.parse(fs.readFileSync(STATE_FILE, "utf8")) : {};
saved.appliances ??= {};
const save = () => {
    fs.writeFileSync(`${STATE_FILE}.tmp`, JSON.stringify(saved, null, 2));
    fs.renameSync(`${STATE_FILE}.tmp`, STATE_FILE);
};

// Random setup code per device, kept in /data (spec forbids 00000000, 11111111 ... 99999999, 12345678, 87654321).
const BAD = new Set([0, 11111111, 22222222, 33333333, 44444444, 55555555, 66666666, 77777777, 88888888,
    99999999, 12345678, 87654321]);
function newPasscode() {
    for (;;) { const p = crypto.randomInt(1, 99999999); if (!BAD.has(p)) return p; }
}

const env = Environment.default;
env.vars.set("storage.path", `${DATA}/matter`);
env.vars.set("log.level", process.env.MATTER_LOG_LEVEL ?? "notice");

const appliances = [];
for (const [i, cfg] of opts.appliances.entries()) {
    const id = cfg.name.toLowerCase().replace(/[^a-z0-9]+/g, "-");
    const st = (saved.appliances[cfg.name] ??= {});
    st.passcode ??= newPasscode();
    st.discriminator ??= crypto.randomInt(0, 4096);
    st.running ??= false;
    save();

    const node = await ServerNode.create({
        id,
        network: { port: cfg.port },
        commissioning: { passcode: st.passcode, discriminator: st.discriminator },
        productDescription: { name: cfg.name, deviceType: ContactSensorDevice.deviceType },
        basicInformation: {
            vendorName: "Madison Rd", vendorId: VendorId(0xfff1),
            productName: cfg.name, productId: 0x8001 + i, nodeLabel: cfg.name,
            serialNumber: `madison-rd-${id}`, uniqueId: `madison-rd-${id}-unique`,
        },
    });
    const contact = await node.add(ContactSensorDevice, { id: "contact", booleanState: { stateValue: st.running } });
    await node.start();

    const c = node.state.commissioning;
    log(`${cfg.name}: Matter port ${cfg.port}, ${st.running ? "running (closed)" : "idle (open)"},`,
        c.commissioned ? "paired" : `NOT paired, manual pairing code ${c.pairingCodes.manualPairingCode}`);

    appliances.push(new Appliance(cfg, st.running, Date.now(), async (a, now, why) => {
        log(`${a.name}: ${a.running ? "RUNNING, sensor closed" : "FINISHED, sensor open"} (${why})`);
        st.running = a.running;
        save();
        await contact.set({ booleanState: { stateValue: a.running } });
    }));
}

const byEntity = new Map(appliances.map(a => [a.entity, a]));
setInterval(() => { for (const a of appliances) a.check(Date.now()); }, 15000);

function connectHA() {
    const ws = new WebSocket(HA_WS);
    ws.onmessage = ev => {
        const m = JSON.parse(ev.data);
        const send = o => ws.send(JSON.stringify(o));
        if (m.type === "auth_required") send({ type: "auth", access_token: HA_TOKEN });
        else if (m.type === "auth_invalid") log("Home Assistant auth failed:", m.message);
        else if (m.type === "auth_ok") {
            log("Home Assistant connected");
            send({ id: 1, type: "get_states" });
            send({ id: 2, type: "subscribe_trigger", trigger: { trigger: "state", entity_id: [...byEntity.keys()] } });
        } else if (m.type === "result" && !m.success) log("Home Assistant error:", JSON.stringify(m.error));
        else if (m.type === "result" && m.id === 1) {
            for (const s of m.result) byEntity.get(s.entity_id)?.reading(s.state, Date.now());
        } else if (m.type === "event" && m.id === 2) {
            const s = m.event.variables.trigger.to_state;
            if (s) byEntity.get(s.entity_id)?.reading(s.state, Date.now());
        }
    };
    ws.onclose = () => {
        for (const a of appliances) a.power = null; // no decisions on stale readings
        log("Home Assistant disconnected, retry in 10 s");
        setTimeout(connectHA, 10000);
    };
    ws.onerror = () => {};
}
connectHA();
