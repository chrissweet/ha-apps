// Running/finished detection from one power entity. Pure logic, no I/O (replay-tested).
//   running:  any reading above threshold_w
//   finished: running, and the time-weighted mean of the last mean_s seconds at or below off_w
//             (default threshold_w) for off_delay_min since it was last above
export class Appliance {
    constructor({ name, entity, threshold_w, off_w, off_delay_min, mean_s = 60 }, running, now, onChange) {
        Object.assign(this, { name, entity, threshold: threshold_w, off: off_w ?? threshold_w,
                              meanMs: mean_s * 1000, delayMs: off_delay_min * 60000, onChange });
        this.running = running;
        this.lastAbove = now; // after a restart, wait a full delay before deciding "finished"
        this.power = null;    // null = no usable reading
        this.buf = [];        // [time, watts] readings inside the averaging window
    }

    reading(value, now) {
        const w = Number.parseFloat(value);
        if (!Number.isFinite(w)) { this.power = null; this.buf = []; return; }
        this.power = w;
        this.buf.push([now, w]);
        while (this.buf.length > 1 && this.buf[1][0] <= now - this.meanMs) this.buf.shift();
        if (w > this.threshold) {
            this.lastAbove = now;
            if (!this.running) this.set(true, now, `${w} W`);
        } else if (this.level(now) > this.off) {
            this.lastAbove = now;
        }
    }

    // time-weighted mean over the window: each reading holds until the next, the newest until now
    level(now) {
        const b = this.buf;
        if (!b.length) return this.power;
        const start = Math.max(b[0][0], now - this.meanMs);
        let sum = 0;
        for (let i = 0; i < b.length; i++) {
            const t0 = Math.max(b[i][0], start), t1 = i + 1 < b.length ? b[i + 1][0] : now;
            if (t1 > t0) sum += b[i][1] * (t1 - t0);
        }
        return now > start ? sum / (now - start) : b[b.length - 1][1];
    }

    check(now) {
        if (this.running && this.power !== null && this.level(now) <= this.off
            && now - this.lastAbove >= this.delayMs) {
            this.set(false, now, `${this.meanMs / 1000} s mean at or below ${this.off} W for ${this.delayMs / 60000} min`);
        }
    }

    set(running, now, why) {
        this.running = running;
        this.onChange(this, now, why);
    }
}
