// Event Reader — the bot's production input: the shared collector's
// events table.
//
// The collector has already matched the vehicle, measured the delay
// (observed, corroborated across polls — see docs/plans/COLLECTOR_SPEC.md
// §5) and enriched with stop names. This reader:
//   1. polls live.db for unconsumed events (operator-filtered)
//   2. maps rows to the BusEvent shape the persona code expects
//   3. runs the same freshness + significance selection as direct ingest
//   4. marks rows consumed (never deletes — the collector owns the table)
//
// Downstream services receive the same BusEvent shape as direct ingest.

import Database from 'better-sqlite3';
import { DateTime } from 'luxon';
import { logger, logSummary, logDetailed, logAlways, TARGET_TIMEZONE } from '../utils/logging.js';
import { ApplicationState } from '../services/application-state.js';
import { DelayAnalyzer } from '../services/delay-analyzer.js';
import type { BusEvent } from '../types/bus-types.js';

interface EventRow {
    id: number;
    created_at: string;
    vehicle_ref: string;
    operator_ref: string;
    line: string;
    direction: string;
    journey_ref: string;
    origin_aimed_departure: string;
    stop_code: string | null;
    stop_name: string | null;
    delay_seconds: number;
    event_type: string;       // collector: 'delayed' | 'early' | 'punctual'
    source: string;
    corroboration: number;
    lat: number | null;
    lon: number | null;
    low_confidence: number;
}

export class EventReader {
    private db: Database.Database;
    private appState: ApplicationState;
    private delayAnalyzer: DelayAnalyzer;
    private operators: Set<string>;
    private timer: NodeJS.Timeout | null = null;
    private readonly intervalMs: number;

    private readonly maxAgeMinutes: number;

    constructor(liveDbPath: string, appState: ApplicationState,
                delayAnalyzer: DelayAnalyzer,
                operators: string[] = ['FBRI'], intervalMs = 30_000,
                maxAgeMinutes = 10) {
        // NOT readonly: consuming an event writes consumed_by_bot_at.
        // That column is the reader's ONLY write; everything else is the
        // collector's.
        this.db = new Database(liveDbPath);
        this.db.pragma('busy_timeout = 10000');
        this.appState = appState;
        this.delayAnalyzer = delayAnalyzer;
        this.operators = new Set(operators);
        this.intervalMs = intervalMs;
        this.maxAgeMinutes = maxAgeMinutes;
    }

    start(): void {
        logger.info('EventReader starting (collector-events ingest)', {
            operators: [...this.operators], intervalMs: this.intervalMs });
        this.timer = setInterval(() => this.cycle(), this.intervalMs);
        this.cycle();
    }

    stop(): void {
        if (this.timer) clearInterval(this.timer);
        this.db.close();
    }

    private mapEventType(t: string): BusEvent['eventType'] {
        return t === 'delayed' ? 'delay' : (t === 'early' ? 'early' : 'punctual');
    }

    private toBusEvent(row: EventRow): BusEvent {
        const delayMinutes = Math.round(row.delay_seconds / 60);
        const eventType = this.mapEventType(row.event_type);
        // Score with the shared significance scorer so event selection
        // behaves identically regardless of ingest mode.
        const analysis = this.delayAnalyzer.calculateEventSignificance(
            delayMinutes, DateTime.fromISO(row.created_at).setZone(TARGET_TIMEZONE));
        const busDetails = this.delayAnalyzer.extractBusDetails(row.vehicle_ref || '');
        return {
            timestamp: row.created_at,
            vehicleRef: row.vehicle_ref,
            datedJourneyRef: row.journey_ref || '',
            line: row.line,
            direction: row.direction || '',
            originAimedDepartureTimeStr: row.origin_aimed_departure || '',
            delayMinutes,
            lastStopCode: row.stop_code || '',
            lastStopTime: '',
            lastStopName: row.stop_name || undefined,
            eventType,
            significance: analysis.type === 'ignore' ? 0 : analysis.score,
            busDetails: busDetails ?? undefined,
            location: (row.lat != null && row.lon != null)
                ? { latitude: row.lat, longitude: row.lon } : undefined,
        };
    }

    private cycle(): void {
        try {
            // Apply the age gate before drafting so a restart or outage cannot
            // turn a stale backlog into current posts. Stale rows are consumed
            // without being passed to the commentary service.
            const cutoffIso = DateTime.now()
                .minus({ minutes: this.maxAgeMinutes }).toUTC().toISO();
            const stale = this.db.prepare(
                `UPDATE events SET consumed_by_bot_at = ?
                 WHERE consumed_by_bot_at IS NULL AND created_at < ?`)
                .run(new Date().toISOString(), cutoffIso).changes;
            if (stale > 0) {
                logSummary('info', `⏭️  skipped ${stale} stale event(s) older than ${this.maxAgeMinutes}min`);
            }

            const rows = this.db.prepare(
                `SELECT * FROM events WHERE consumed_by_bot_at IS NULL
                 ORDER BY id ASC LIMIT 200`).all() as EventRow[];
            if (!rows.length) return;

            const markConsumed = this.db.prepare(
                'UPDATE events SET consumed_by_bot_at = ? WHERE id = ?');
            const now = new Date().toISOString();
            const reportable: BusEvent[] = [];

            for (const row of rows) {
                markConsumed.run(now, row.id);
                if (!this.operators.has(row.operator_ref)) continue;

                const busEvent = this.toBusEvent(row);
                if (busEvent.significance === 0) continue;

                // Apply the same freshness selection as direct SIRI ingest.
                if (busEvent.eventType === 'delay') {
                    const history = this.delayAnalyzer.updateDelayHistory(busEvent);
                    if (this.delayAnalyzer.shouldReportDelay(busEvent, history)) {
                        reportable.push(busEvent);
                        logDetailed('info', `[EVENT_ACCEPT] ${busEvent.line}: collector event accepted (sig ${busEvent.significance}, corr ${row.corroboration})`);
                    } else {
                        logDetailed('info', `[EVENT_FILTER] ${busEvent.line}: filtered by freshness logic`);
                    }
                } else {
                    reportable.push(busEvent);
                }
            }

            if (reportable.length) {
                this.appState.addBusEvents(reportable);
                logSummary('info', `📋 COLLECTED (events): ${reportable.length} of ${rows.length} rows → collector total ${this.appState.busEventCollector.length}`);
            }
        } catch (err: any) {
            logAlways('error', 'EventReader cycle failed', { error: err.message });
        }
    }
}
