/**
 * Non-publishing consumer for nightly rare-working observations.
 *
 * The pipeline writes accepted observations to the same small JSON snapshot
 * used by the site. This reader never opens audit.db and never posts; it only
 * records each observation once in the bot state database.
 */
import fs from 'node:fs';
import Database from 'better-sqlite3';
import { logAlways } from '../utils/logging.js';

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
    private readonly snapshotPath: string;
    private readonly intervalMs: number;
    private timer: NodeJS.Timeout | null = null;

    constructor(snapshotPath: string, stateDbPath: string,
                intervalMs = 5 * 60_000) {
        this.snapshotPath = snapshotPath;
        this.intervalMs = intervalMs;
        this.stateDb = new Database(stateDbPath);
        this.stateDb.pragma('busy_timeout = 10000');
        this.stateDb.exec(`
            CREATE TABLE IF NOT EXISTS rare_working_shadow_seen (
                event_id TEXT PRIMARY KEY,
                observed_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
        `);
    }

    start(): void {
        this.pollOnce();
        this.timer = setInterval(() => this.pollOnce(), this.intervalMs);
        logAlways('info', '[RARE_SHADOW] observation reader enabled');
    }

    stop(): void {
        if (this.timer) clearInterval(this.timer);
        this.stateDb.close();
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
