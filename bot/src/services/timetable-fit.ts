import { readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import type { BusEvent } from '../types/bus-types.js';

/**
 * Background fact for a post about one bus: it has just reached the end of a
 * stretch where the audit finds the timetable consistently allows too little
 * time. Read from the audit's nightly segments/<operator>.json. The post stays
 * about the bus; the fact is context, never a claim about why this bus is late.
 *
 * Off unless BBB_TIMETABLE_FIT_POSTS=true. Only for buses at least five
 * minutes late, so it never nitpicks small delays.
 */
export const MIN_LATE_MINUTES = 5;
const MAX_FILE_AGE_MS = 3 * 86_400_000;

export interface TimetableFitFact {
    route: string;
    from: string;
    to: string;
    timetableMinutes: number;
    typicalMinutes: number;
    lostTwoMinutesShare: number;
    journeys: number;
    when: string;
    scope: string;
}

const BAND_LABELS: Record<string, string> = {
    early: 'before 7am', am_peak: '7-10am', interpeak: '10am-4pm', pm_peak: '4-7pm', evening: 'after 7pm',
};

/** The audit's time band and day type for an observation time, UK clock. */
export function bandFor(iso: string): { band: string; dayType: 'weekday' | 'weekend' } | null {
    const when = new Date(iso);
    if (Number.isNaN(when.getTime())) return null;
    const parts = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', hour: 'numeric', hourCycle: 'h23', weekday: 'short' })
        .formatToParts(when);
    const hour = Number(parts.find(p => p.type === 'hour')?.value);
    const weekday = parts.find(p => p.type === 'weekday')?.value || '';
    const band = hour < 7 ? 'early' : hour < 10 ? 'am_peak' : hour < 16 ? 'interpeak' : hour < 19 ? 'pm_peak' : 'evening';
    return { band, dayType: ['Sat', 'Sun'].includes(weekday) ? 'weekend' : 'weekday' };
}

export class TimetableFit {
    private cache = new Map<string, { mtimeMs: number; data: any }>();

    constructor(private readonly directory = process.env.AUDIT_SEGMENTS_DIR
        || '/var/lib/bristolbusbot/pipeline/audit_site/segments',
        private readonly enabled = (process.env.BBB_TIMETABLE_FIT_POSTS || '').toLowerCase() === 'true') {}

    private load(operator: string): any | null {
        if (!/^[A-Z0-9]{2,8}$/.test(operator)) return null;
        const file = join(this.directory, `${operator}.json`);
        try {
            const stat = statSync(file);
            if (Date.now() - stat.mtimeMs > MAX_FILE_AGE_MS) return null;
            const cached = this.cache.get(operator);
            if (cached && cached.mtimeMs === stat.mtimeMs) return cached.data;
            const data = JSON.parse(readFileSync(file, 'utf8'));
            if (!data || typeof data.routes !== 'object') return null;
            this.cache.set(operator, { mtimeMs: stat.mtimeMs, data });
            return data;
        } catch {
            return null;
        }
    }

    factFor(event: BusEvent): TimetableFitFact | null {
        if (!this.enabled || event.eventType !== 'delay' || event.lowConfidence) return null;
        if (!(Number(event.delayMinutes) >= MIN_LATE_MINUTES) || !event.lastStopCode || !event.operatorRef) return null;
        const slot = bandFor(event.timestamp);
        const data = slot && this.load(event.operatorRef);
        // A number reused in another town is filed as "4 Bath" / "4 Bristol";
        // the stop the bus just passed picks the right one.
        const routes = data?.routes && typeof data.routes === 'object' ? data.routes : {};
        const items: any[] = Object.keys(routes)
            .filter(key => key === event.line || key.startsWith(`${event.line} `))
            .flatMap(key => (Array.isArray(routes[key]) ? routes[key] : []));
        const item = items.find(i => i && i.kind === 'tight' && i.to_stop === event.lastStopCode
            && i.band === slot!.band && i.day_type === slot!.dayType);
        if (!item) return null;
        return {
            route: event.line, from: String(item.from), to: String(item.to),
            timetableMinutes: Math.round(Number(item.timetable_min)), typicalMinutes: Math.round(Number(item.typical_min)),
            lostTwoMinutesShare: Number(item.share_pct), journeys: Number(item.journeys),
            when: `${slot!.dayType === 'weekday' ? 'weekdays' : 'weekends'} ${BAND_LABELS[slot!.band]}`,
            scope: 'How buses on this route usually run on this stretch at this time of day over the last four weeks, '
                + 'from the punctuality audit. It does not prove why this bus is late or where it lost time.',
        };
    }
}
