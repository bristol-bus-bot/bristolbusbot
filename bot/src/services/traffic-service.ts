import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import type { BusEvent, TrafficContext } from '../types/bus-types.js';
import { httpFetch } from '../utils/http-client.js';
import { logger } from '../utils/logging.js';

export interface TrafficConfig { apiKey: string; enabled: boolean; usagePath: string }
interface Usage { day: string; month: string; daily: number; monthly: number; lastRequest: number }
type Point = { latitude: number; longitude: number };
const validPoint = (p: Point | undefined): p is Point => !!p
    && Number.isFinite(p.latitude) && Number.isFinite(p.longitude)
    && Math.abs(p.latitude) <= 90 && Math.abs(p.longitude) <= 180;

// Distance to the whole polyline, not merely the provider's segment endpoints.
export function segmentDistance(point: Point, coordinates: Point[]): number {
    const scale = Math.cos(point.latitude * Math.PI / 180);
    const xy = (p: Point) => [(p.longitude - point.longitude) * 111_320 * scale,
        (p.latitude - point.latitude) * 111_320];
    let minimum = Infinity;
    for (let i = 1; i < coordinates.length; i++) {
        const [ax, ay] = xy(coordinates[i - 1]);
        const [bx, by] = xy(coordinates[i]);
        const dx = bx - ax, dy = by - ay;
        const fraction = Math.max(0, Math.min(1, -(ax * dx + ay * dy) / (dx * dx + dy * dy || 1)));
        minimum = Math.min(minimum, Math.hypot(ax + fraction * dx, ay + fraction * dy));
    }
    return minimum;
}

export class TrafficService {
    private busy = false;
    constructor(private readonly config: TrafficConfig, private readonly fetcher: typeof httpFetch = httpFetch) {}

    // Persist only request counts, never the provider's traffic response. Reserve before
    // networking, counting failures too. Bad/unwritable state disables optional traffic.
    private reserveRequest(now: number): boolean {
        if (!this.config.usagePath) return false;
        try {
            const day = new Date(now).toISOString().slice(0, 10), month = day.slice(0, 7);
            let usage: Usage = { day, month, daily: 0, monthly: 0, lastRequest: 0 };
            if (existsSync(this.config.usagePath)) {
                const raw = readFileSync(this.config.usagePath, 'utf8');
                if (raw.length > 2048) return false;
                usage = JSON.parse(raw);
                if (!usage || !/^\d{4}-\d{2}-\d{2}$/.test(usage.day) || !/^\d{4}-\d{2}$/.test(usage.month)
                    || ![usage.daily, usage.monthly, usage.lastRequest].every(n => Number.isSafeInteger(n) && n >= 0)) return false;
            }
            if (now - usage.lastRequest < 10 * 60_000) return false;
            if (usage.month !== month) { usage.month = month; usage.monthly = 0; }
            if (usage.day !== day) { usage.day = day; usage.daily = 0; }
            if (usage.daily >= 50 || usage.monthly >= 500) return false;
            usage.daily++; usage.monthly++; usage.lastRequest = now;
            mkdirSync(dirname(this.config.usagePath), { recursive: true });
            const temporary = `${this.config.usagePath}.new-${process.pid}`;
            writeFileSync(temporary, JSON.stringify(usage), { mode: 0o600 });
            renameSync(temporary, this.config.usagePath);
            return true;
        } catch {
            logger.warn('Traffic usage guard unavailable; omitting traffic');
            return false;
        }
    }

    async getTraffic(event: BusEvent): Promise<TrafficContext | null> {
        const now = Date.now(), position = event.location;
        const age = now - Date.parse(event.timestamp);
        if (!this.config.enabled || !this.config.apiKey || this.busy || !validPoint(position)
            || !Number.isFinite(age) || age < -30_000 || age > 90_000) return null;
        // Regional bot: do not accidentally query a default (0,0) or an unrelated location.
        if (position.latitude < 50.9 || position.latitude > 51.85 || position.longitude < -3.2 || position.longitude > -2.0) return null;
        if (!this.reserveRequest(now)) return null;
        this.busy = true;
        let deadline: ReturnType<typeof setTimeout> | undefined;
        try {
            const url = new URL('https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/22/json');
            url.search = new URLSearchParams({ key: this.config.apiKey,
                point: `${position.latitude},${position.longitude}`, unit: 'mph', thickness: '1' }).toString();
            const request = async (): Promise<TrafficContext | null> => {
                const response = await this.fetcher(url.toString(), { timeoutMs: 4000, retries: 0 });
                if (!response.ok) {
                    logger.info('Traffic unavailable; using another subject', { status: response.status });
                    return null;
                }
                const date = Date.parse(response.headers.get('date') || '');
                const cacheAge = Number(response.headers.get('age') || 0);
                if (!Number.isFinite(date) || now - date > 120_000 || date - now > 30_000
                    || !Number.isFinite(cacheAge) || cacheAge < 0 || cacheAge > 120) return null;
                const data = (await response.json() as any)?.flowSegmentData;
                if (!data || !Number.isFinite(data.confidence) || data.confidence < 0.8 || data.confidence > 1
                    || data.roadClosure !== false
                    || !Number.isFinite(data.currentSpeed) || data.currentSpeed < 0 || data.currentSpeed > 100
                    || !Number.isFinite(data.freeFlowSpeed) || data.freeFlowSpeed <= 0 || data.freeFlowSpeed > 100) return null;
                const coordinates = data.coordinates?.coordinate;
                if (!Array.isArray(coordinates) || coordinates.length < 2 || coordinates.length > 10_000
                    || !coordinates.every(validPoint) || segmentDistance(position, coordinates) > 100) return null;
                const ratio = data.currentSpeed / data.freeFlowSpeed;
                const condition = ratio >= 0.85 ? 'moving freely' : ratio >= 0.6 ? 'a little slower than on a clear road'
                    : ratio >= 0.3 ? 'moving slowly' : 'moving very slowly';
                return { source: 'TomTom', checkedAt: new Date(now).toISOString(), condition,
                    scope: 'one road segment near the bus GPS position; not matched to its road or direction',
                    key: `${position.latitude.toFixed(3)},${position.longitude.toFixed(3)}:${condition}` };
            };
            return await Promise.race([request(), new Promise<null>(resolve => {
                deadline = setTimeout(() => resolve(null), 5000);
            })]);
        } catch {
            // Never log a request URL or provider error body: these may contain the key.
            logger.info('Traffic request failed; using another subject');
            return null;
        } finally {
            clearTimeout(deadline);
            this.busy = false;
        }
    }
}
