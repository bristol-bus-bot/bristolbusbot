import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'fs';
import { dirname } from 'path';
import { DateTime } from 'luxon';
import { logger } from '../utils/logging.js';

/** Days with no bus service: monitoring pauses and one greeting is posted. */
export type HolidayDay = 'Christmas Day' | 'Boxing Day';

const HOLIDAY_ZONE = 'Europe/London';
/** The greeting goes out from this UK hour onwards. */
export const HOLIDAY_POST_HOUR = 8;

export function noServiceDay(now: DateTime): HolidayDay | null {
    const local = now.setZone(HOLIDAY_ZONE);
    if (local.month !== 12) return null;
    if (local.day === 25) return 'Christmas Day';
    if (local.day === 26) return 'Boxing Day';
    return null;
}

/** No resumption date: when normal service returns isn't the same every year. */
export function holidayMessage(day: HolidayDay): string {
    return day === 'Christmas Day'
        ? 'Merry Christmas from Bristol! 🎄 No bus service today - even the buses are taking a well-earned break.'
        : 'Happy Boxing Day! 🎁 No bus service today - the buses are still recovering from Christmas.';
}

export interface HolidayHooks {
    startMonitoring(): void;
    stopMonitoring(): void;
    /** Publish through the normal path (daily cap included); true if it went out. */
    post(message: string, now: DateTime): Promise<boolean>;
}

/**
 * Checked on a timer, not only at startup, so the bot pauses at midnight going
 * into Christmas Day, resumes at midnight after Boxing Day, and posts each
 * greeting once per day even across restarts.
 */
export class HolidaySchedule {
    private monitoring: boolean | null = null;
    private posting = false;

    constructor(private readonly statePath: string, private readonly hooks: HolidayHooks) {}

    async tick(now: DateTime = DateTime.now()): Promise<void> {
        const local = now.setZone(HOLIDAY_ZONE);
        const day = noServiceDay(local);
        if (day && this.monitoring !== false) {
            logger.info(`🎄 ${day}: no bus service, monitoring paused`);
            this.hooks.stopMonitoring();
            this.monitoring = false;
        } else if (!day && this.monitoring !== true) {
            if (this.monitoring === false) logger.info('Bus service day: monitoring resumed');
            this.hooks.startMonitoring();
            this.monitoring = true;
        }
        const today = local.toISODate();
        if (!day || !today || local.hour < HOLIDAY_POST_HOUR || this.posting || this.postedOn(today)) return;
        this.posting = true;
        try {
            const sent = await this.hooks.post(holidayMessage(day), local);
            if (sent) {
                this.recordPosted(today, day);
                logger.info(`✅ ${day} post sent`);
            } else {
                logger.warn(`${day} post not sent; will try again`);
            }
        } catch (error: any) {
            logger.error(`Failed to post ${day} message`, { error: error.message });
        } finally {
            this.posting = false;
        }
    }

    private postedOn(date: string): boolean {
        return Boolean(this.readState()[date]);
    }

    private readState(): Record<string, string> {
        try {
            if (!existsSync(this.statePath)) return {};
            const value = JSON.parse(readFileSync(this.statePath, 'utf8'));
            return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
        } catch {
            return {};
        }
    }

    private recordPosted(date: string, day: HolidayDay): void {
        // Keep the last few years only; the file stays tiny.
        const state = Object.fromEntries(
            Object.entries({ ...this.readState(), [date]: day }).sort().slice(-10));
        try {
            mkdirSync(dirname(this.statePath), { recursive: true });
            const temporary = `${this.statePath}.new-${process.pid}`;
            writeFileSync(temporary, `${JSON.stringify(state, null, 2)}\n`, { encoding: 'utf8', mode: 0o600 });
            renameSync(temporary, this.statePath);
        } catch (error: any) {
            logger.warn('Could not record the holiday post', { error: error.message });
        }
    }
}
