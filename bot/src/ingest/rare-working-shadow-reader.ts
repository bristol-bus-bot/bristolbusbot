/**
 * Consumer for nightly rare-working observations.
 *
 * The pipeline writes accepted observations to the same small JSON snapshot
 * used by the site. This reader never opens audit.db. It records each
 * observation once in the bot state database. Only when it is given a
 * publisher (BBB_RARE_WORKING_POSTS=true, planning #67) does it post the
 * pipeline's "visitor" cases, at most two a day.
 */
import fs from 'node:fs';
import Database from 'better-sqlite3';
import { DateTime } from 'luxon';
import { logAlways } from '../utils/logging.js';
import { chooseVisitorPosts, type RareWorkingEvent } from '../services/visitor-posts.js';

export type VisitorPublisher = (text: string) => Promise<boolean>;

interface RareEvent {
    event_id: string;
    service_date: string;
    operator: string;
    vehicle_ref: string;
    route: string;
    profile_slug?: string | null;
    evidence?: unknown;
}

interface IntegrationSnapshot {
    schema: number;
    published_at: string | null;
    rare_workings?: { mode?: string; events?: RareEvent[] };
}

export class RareWorkingShadowReader {
    private readonly stateDb: Database.Database;
    /** True when this reader opened the handle itself and so closes it. */
    private readonly ownsStateDb: boolean;
    private readonly snapshotPath: string;
    private readonly intervalMs: number;
    private timer: NodeJS.Timeout | null = null;
    private readonly publish: VisitorPublisher | null;
    private publishing = false;

    /**
     * `stateDb` is normally the bot's one shared app_data.db handle (from
     * DatabaseManager), so the process never holds two handles that compete
     * for the same file's lock. A path is still accepted for tests and tools.
     */
    constructor(snapshotPath: string, stateDb: string | Database.Database,
                intervalMs = 5 * 60_000, publish: VisitorPublisher | null = null) {
        this.snapshotPath = snapshotPath;
        this.intervalMs = intervalMs;
        this.publish = publish;
        this.ownsStateDb = typeof stateDb === 'string';
        this.stateDb = typeof stateDb === 'string' ? new Database(stateDb) : stateDb;
        if (this.ownsStateDb) this.stateDb.pragma('busy_timeout = 2000');
        this.stateDb.exec(`
            CREATE TABLE IF NOT EXISTS rare_working_shadow_seen (
                event_id TEXT PRIMARY KEY,
                observed_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS rare_working_posted (
                event_id TEXT PRIMARY KEY,
                posted_at TEXT NOT NULL
            )
        `);
    }

    /**
     * Post due visitor cases (planning #67). Does nothing without a publisher.
     * Returns how many were posted.
     */
    async publishVisitors(now: DateTime = DateTime.now()): Promise<number> {
        if (!this.publish || this.publishing) return 0;
        this.publishing = true;
        try {
            const dayStart = now.setZone('Europe/London').startOf('day').toUTC().toISO();
            const postedToday = (this.stateDb.prepare(
                'SELECT COUNT(*) AS n FROM rare_working_posted WHERE posted_at >= ?')
                .get(dayStart) as { n: number }).n;
            const already = new Set((this.stateDb.prepare(
                'SELECT event_id FROM rare_working_posted').all() as Array<{ event_id: string }>)
                .map(row => row.event_id));
            const events = (this.stateDb.prepare(
                'SELECT payload_json FROM rare_working_shadow_seen').all() as Array<{ payload_json: string }>)
                .map(row => { try { return JSON.parse(row.payload_json) as RareWorkingEvent; } catch { return null; } })
                .filter((event): event is RareWorkingEvent => event !== null);
            let posted = 0;
            for (const { event, text } of chooseVisitorPosts(events, postedToday, already, now)) {
                if (!await this.publish(text)) break;
                this.stateDb.prepare(
                    'INSERT OR IGNORE INTO rare_working_posted (event_id, posted_at) VALUES (?, ?)')
                    .run(event.event_id, now.toUTC().toISO());
                posted += 1;
                logAlways('info', `[RARE_VISITOR] posted ${event.operator} vehicle on route ${event.route}`,
                    { eventId: event.event_id, serviceDate: event.service_date });
            }
            return posted;
        } finally {
            this.publishing = false;
        }
    }

    start(): void {
        const cycle = () => {
            this.pollOnce();
            this.publishVisitors().catch((error: any) => logAlways('error',
                '[RARE_VISITOR] posting failed', { error: error?.message || String(error) }));
        };
        cycle();
        this.timer = setInterval(cycle, this.intervalMs);
        logAlways('info', this.publish
            ? '[RARE_SHADOW] observation reader enabled; visitor posts ON'
            : '[RARE_SHADOW] observation reader enabled');
    }

    stop(): void {
        if (this.timer) clearInterval(this.timer);
        this.timer = null;
        if (this.ownsStateDb) this.stateDb.close();
    }

    pollOnce(): number {
        let snapshot: IntegrationSnapshot;
        try {
            snapshot = JSON.parse(
                fs.readFileSync(this.snapshotPath, 'utf8')) as IntegrationSnapshot;
        } catch (error: any) {
            if (error?.code !== 'ENOENT') {
                logAlways('error', '[RARE_SHADOW] snapshot read failed', {
                    error: error?.message || String(error),
                });
            }
            return 0;
        }
        if (snapshot.schema !== 1 || !snapshot.published_at) return 0;
        const events = snapshot.rare_workings?.events;
        if (!Array.isArray(events) || events.length === 0) return 0;

        const insert = this.stateDb.prepare(
            `INSERT OR IGNORE INTO rare_working_shadow_seen
                 (event_id, observed_at, payload_json) VALUES (?, ?, ?)`);
        let observed = 0;
        for (const event of events) {
            if (!event || typeof event.event_id !== 'string'
                || typeof event.route !== 'string') continue;
            const result = insert.run(
                event.event_id, new Date().toISOString(), JSON.stringify(event));
            if (result.changes) {
                observed += 1;
                logAlways('info',
                    `[RARE_SHADOW] accepted ${event.operator} vehicle on route ${event.route}`,
                    { eventId: event.event_id, serviceDate: event.service_date,
                      profileSlug: event.profile_slug || null });
            }
        }
        return observed;
    }
}
