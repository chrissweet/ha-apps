// Running/finished detection from one power entity. Pure logic, no I/O (replay-tested).
//   running:  any reading above threshold_w
//   finished: running, and readings at or below threshold_w for off_delay_min since the last one above
export class Appliance {
    constructor({ name, entity, threshold_w, off_delay_min }, running, now, onChange) {
        Object.assign(this, { name, entity, threshold: threshold_w, delayMs: off_delay_min * 60000, onChange });
        this.running = running;
        this.lastAbove = now; // after a restart, wait a full delay before deciding "finished"
        this.power = null;    // null = no usable reading
    }

    reading(value, now) {
        const w = Number.parseFloat(value);
        if (!Number.isFinite(w)) { this.power = null; return; }
        this.power = w;
        if (w > this.threshold) {
            this.lastAbove = now;
            if (!this.running) this.set(true, now, `${w} W`);
        }
    }

    check(now) {
        if (this.running && this.power !== null && this.power <= this.threshold
            && now - this.lastAbove >= this.delayMs) {
            this.set(false, now, `at or below ${this.threshold} W for ${this.delayMs / 60000} min`);
        }
    }

    set(running, now, why) {
        this.running = running;
        this.onChange(this, now, why);
    }
}
