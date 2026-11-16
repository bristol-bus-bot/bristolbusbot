import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { logger } from '../utils/logging.js';

/**
 * Where the bot has got to in the collector's events table.
 *
 * The bot only reads live.db; it remembers the last event it handed on here
 * instead of marking rows in the collector's database. The created_at of that
 * event is kept as well: event ids restart from 1 whenever the table empties
 * (a week-long outage, or a live.db rebuild), and then the saved id either no
 * longer exists or belongs to a different event. Either way the cursor is not
 * trusted and reading restarts from the beginning; the reader's age gate
 * stops any backlog being posted.
 */
export interface EventCursorState {
    lastEventId: number;
    lastCreatedAt: string | null;
}

export class EventCursor {
    constructor(private readonly path: string | undefined) {}

    load(): EventCursorState | null {
        if (!this.path || !existsSync(this.path)) return null;
        try {
            const data = JSON.parse(readFileSync(this.path, 'utf8'));
            if (data?.version === 1 && Number.isSafeInteger(data.lastEventId) && data.lastEventId >= 0) {
                return { lastEventId: data.lastEventId,
                         lastCreatedAt: typeof data.lastCreatedAt === 'string' ? data.lastCreatedAt : null };
            }
        } catch { /* unreadable: treated as absent */ }
        logger.warn('[EVENT_CURSOR] Saved event position unreadable; starting from the newest event');
        return null;
    }

    /** Written atomically, so a crash never leaves half a position. */
    save(state: EventCursorState): void {
        if (!this.path) return;
        try {
            mkdirSync(dirname(this.path), { recursive: true });
            const temporary = `${this.path}.new`;
            writeFileSync(temporary, JSON.stringify({ version: 1, ...state }), { mode: 0o600 });
            renameSync(temporary, this.path);
        } catch (error: any) {
            logger.warn('[EVENT_CURSOR] Could not save the event position; kept in memory only',
                { error: error?.message });
        }
    }
}
