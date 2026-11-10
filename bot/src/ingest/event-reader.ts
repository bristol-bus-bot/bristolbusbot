// Event Reader. The bot's production input is the shared collector's
// events table.
//
// The collector has already matched the vehicle, measured the delay
// (observed and corroborated across polls) and enriched with stop names. This
// reader:
//   1. reads live.db read-only for events after its saved position
//   2. maps rows to the BusEvent shape the persona code expects
//   3. runs the freshness + significance selection
//   4. remembers its position in its own state file. It never writes to
//      live.db: the collector is that database's only writer.

import Database from 'better-sqlite3';
import { DateTime } from 'luxon';
import { logger, logSummary, logDetailed, logAlways, TARGET_TIMEZONE } from '../utils/logging.js';
import { ApplicationState } from '../services/application-state.js';
import { DelayAnalyzer } from '../services/delay-analyzer.js';
import type { BusEvent } from '../types/bus-types.js';
import { EventCursor, type EventCursorState } from './event-cursor.js';
import { observationIssue } from '../services/story-brief.js';

interface EventRow {
    trip_id?: string;
    stop_sequence?: number;
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
    private readonly cursor: EventCursor;
    private position: EventCursorState | null;

    constructor(liveDbPath: string, appState: ApplicationState,
                delayAnalyzer: DelayAnalyzer,
                operators: string[] = ['FBRI'], intervalMs = 30_000,
                maxAgeMinutes = 10,
                private readonly onCycleComplete: () => void = () => undefined,
                cursorPath?: string) {
        // Read-only, like the website: the collector is live.db's only writer,
        // and a reader in WAL mode never waits for it.
        this.db = new Database(liveDbPath, { readonly: true, fileMustExist: true });
        this.cursor = new EventCursor(cursorPath);
        this.position = this.cursor.load();
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

    /** Include fresh normal running, even when it produced no exception event. */
    getCurrentStories(now = Date.now()): BusEvent[] {
        try {
            // Loaded once from the timetable at start-up (the collector copies
            // the same corrected names into its events).
            const names = this.appState?.stopNamesByCode ?? new Map<string, string>();
            const rows = this.db.prepare(`SELECT v.*, v.recorded_at AS created_at
                FROM vehicles v WHERE v.recorded_at >= ? AND v.delay_seconds IS NOT NULL
                AND v.event_type IN ('delayed', 'early', 'punctual')
                ORDER BY v.recorded_at DESC LIMIT 1000`)
                .all(new Date(now - 90_000).toISOString()) as any[];
            return rows.filter(row => this.operators.has(row.operator_ref)).map(row => {
                const name = names.get(row.stop_code);
                const event = this.toBusEvent({ ...row, id: undefined, source: 'live_snapshot',
                    stop_name: name || null,
                    corroboration: row.streak_count || 0 });
                event.significance = Math.max(1, event.significance);
                return event;
            }).filter(event => !observationIssue(event, now) && this.isObservationCurrent(event, now));
        } catch (error: any) {
            logger.warn('Could not read current stories', { error: error.message });
            return [];
        }
    }

    /** Read the current collector snapshot; never reinterpret its timing. */
    isObservationCurrent(event: BusEvent, now = Date.now()): boolean {
        return this.validateObservation(event, now, true);
    }

    /** A recent observation remains reportable when its bus moves along the same run. */
    isObservationPublishable(event: BusEvent, now = Date.now()): boolean {
        return !observationIssue(event, now) && this.validateObservation(event, now, false);
    }

    private validateObservation(event: BusEvent, now: number, exactPosition: boolean): boolean {
        try {
            const row = this.db.prepare('SELECT * FROM vehicles WHERE vehicle_ref = ?')
                .get(event.vehicleRef) as any;
            if (!row || row.low_confidence || row.at_depot || row.delay_seconds == null) return false;
            const age = now - Date.parse(row.recorded_at);
            return Number.isFinite(age) && age >= -30_000 && age <= 90_000
                && Date.parse(row.recorded_at) >= Date.parse(event.timestamp) - 30_000
                && row.operator_ref === event.operatorRef && row.line === event.line
                && (row.journey_ref || '') === event.datedJourneyRef
                && (row.origin_aimed_departure || '') === event.originAimedDepartureTimeStr
                && (row.direction || '') === event.direction
                && (!event.collectorTripId || row.trip_id === event.collectorTripId)
                && (!exactPosition || (row.stop_code === event.lastStopCode
                    && (event.collectorStopSequence === undefined || row.stop_sequence === event.collectorStopSequence)
                    && this.mapEventType(row.event_type) === event.eventType
                    && Math.round(row.delay_seconds / 60) === event.delayMinutes));
        } catch (error: any) {
            logger.warn('Could not confirm current story observation', { error: error.message });
            return false;
        }
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
        const busDetails = this.delayAnalyzer.extractBusDetails(
            row.vehicle_ref || '', row.operator_ref || '');
        return {
            collectorEventId: row.id,
            collectorTripId: row.trip_id || undefined,
            collectorStopSequence: row.stop_sequence ?? undefined,
            operatorRef: row.operator_ref,
            timestamp: row.created_at,
            vehicleRef: row.vehicle_ref,
            datedJourneyRef: row.journey_ref || '',
            line: row.line,
            direction: row.direction || '',
            originAimedDepartureTimeStr: row.origin_aimed_departure || '',
            delayMinutes,
            delaySeconds: row.delay_seconds,
            lastStopCode: row.stop_code || '',
            lastStopTime: '',
            lastStopName: row.stop_name || undefined,
            eventType,
            significance: analysis.type === 'ignore' ? 0 : analysis.score,
            source: row.source,
            corroboration: row.corroboration,
            lowConfidence: Boolean(row.low_confidence),
            busDetails: busDetails ?? undefined,
            location: (row.lat != null && row.lon != null)
                ? { latitude: row.lat, longitude: row.lon } : undefined,
        };
    }

    /**
     * The id to read after. A saved position is trusted only while its event
     * still exists with the same created_at; otherwise the table has been
     * emptied or rebuilt and ids have restarted, so reading starts again from
     * the beginning (the age gate drops anything old). With no saved position
     * the reader starts after the newest event: it does not replay history.
     */
    private startingId(): number {
        if (this.position) {
            const saved = this.db.prepare('SELECT created_at FROM events WHERE id = ?')
                .get(this.position.lastEventId) as { created_at: string } | undefined;
            if (saved && saved.created_at === this.position.lastCreatedAt) return this.position.lastEventId;
            if (this.position.lastEventId === 0) return 0;
            logSummary('info', '[EVENT_CURSOR] Saved event is gone (events table emptied or rebuilt); reading from the start');
            this.position = { lastEventId: 0, lastCreatedAt: null };
            this.cursor.save(this.position);
            return 0;
        }
        const newest = this.db.prepare('SELECT id, created_at FROM events ORDER BY id DESC LIMIT 1')
            .get() as { id: number; created_at: string } | undefined;
        this.position = newest
            ? { lastEventId: newest.id, lastCreatedAt: newest.created_at }
            : { lastEventId: 0, lastCreatedAt: null };
        this.cursor.save(this.position);
        logSummary('info', `[EVENT_CURSOR] No saved position; starting after event ${this.position.lastEventId}`);
        return this.position.lastEventId;
    }

    private cycle(): void {
        let completed = false;
        try {
            const after = this.startingId();

            const rows = this.db.prepare(
                'SELECT * FROM events WHERE id > ? ORDER BY id ASC LIMIT 200').all(after) as EventRow[];
            if (!rows.length) {
                completed = true;
                return;
            }

            // Save the new position before handing anything on, so a crash
            // part-way through loses an event rather than posting it twice.
            const last = rows[rows.length - 1]!;
            this.position = { lastEventId: last.id, lastCreatedAt: last.created_at };
            this.cursor.save(this.position);

            // Apply the age gate before drafting so a restart or outage cannot
            // turn a stale backlog into current posts.
            const cutoffIso = DateTime.now()
                .minus({ minutes: this.maxAgeMinutes }).toUTC().toISO() ?? '';
            const reportable: BusEvent[] = [];
            let stale = 0;

            for (const row of rows) {
                if (row.created_at < cutoffIso) { stale += 1; continue; }
                if (!this.operators.has(row.operator_ref)) continue;

                const busEvent = this.toBusEvent(row);
                if (busEvent.significance === 0) continue;

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

            if (stale > 0) {
                logSummary('info', `⏭️  skipped ${stale} stale event(s) older than ${this.maxAgeMinutes}min`);
            }
            if (reportable.length) {
                this.appState.addBusEvents(reportable);
                logSummary('info', `📋 COLLECTED (events): ${reportable.length} of ${rows.length} rows → collector total ${this.appState.busEventCollector.length}`);
            }
            completed = true;
        } catch (err: any) {
            logAlways('error', 'EventReader cycle failed', { error: err.message });
        } finally {
            // This callback runs on Node's main event loop after the database
            // cycle succeeds. A frozen loop, stopped reader or failed local
            // database read cannot falsely keep the service alive.
            if (completed) this.onCycleComplete();
        }
    }
}
