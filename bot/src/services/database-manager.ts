// Bristol Bus Bot - Database Manager Service
// Handles both timetable.db (read-only) and app_data.db (read-write)

import Database from 'better-sqlite3';
import * as fs from 'fs';
import * as path from 'path';
import { createHash } from 'node:crypto';
import { DateTime } from 'luxon';
import { logger, PerformanceTimer, TARGET_TIMEZONE, logSummary, logDetailed, logAlways } from '../utils/logging.js';
import { ApplicationState } from './application-state.js';
import { buildFleetIdentityIndex, emptyFleetIdentityIndex } from './vehicle-identity.js';
import { BOT_DATA_PATHS } from '../config/data-paths.js';
import { DatabaseError } from '../types/bus-types.js';
import { matchedJourneyContext, type JourneyStop } from './journey-context.js';
import type { 
    DatabaseStop, 
    DatabaseRoute, 
    DatabaseJourney, 
    DatabaseStopTime,
    BusVehicleDetails,
    BusEvent
} from '../types/bus-types.js';

export interface EngagementRecord {
    postContent: string;
    postType: string;
    significance: number;
    postUri?: string;
    event?: BusEvent;
}

export interface RecentPost {
    collectorEventId: number | null;
    operatorRef: string;
    vehicleRef: string;
    line: string;
    journeyRef: string;
    originAimedDeparture: string;
    eventTimestamp: string;
    delaySeconds: number | null;
    direction: string;
    stopCode: string;
    stopName: string;
    source: string;
    corroboration: number | null;
    lowConfidence: boolean;
    postUri: string;
    postContent: string;
    postType: string;
    significance: number | null;
    timestamp: string;
}

/**
 * Database Manager Service
 * Owns the bot's SQLite connections: the timetable is opened read-only
 * (the pipeline owns its contents); app_data.db is the bot's own store.
 *
 * One better-sqlite3 library throughout. Its queries run on the main thread,
 * so they are kept small (the heaviest measured on the Pi is ~15 ms, once at
 * start-up). app_data.db has exactly one handle in the process, shared with
 * the rare-working shadow reader, so the bot never competes with itself for
 * that file's lock. It stays in rollback-journal mode: the social curation
 * service may read only the file itself, which a WAL database would not allow.
 */
const APP_DATA_BUSY_TIMEOUT_MS = 2000;

export class DatabaseManager {
    private databaseConfig: any;
    private appState: ApplicationState;

    private timetableDb: Database.Database | null = null;  // Read-only access to static timetable data
    private appDataDb: Database.Database | null = null;    // Read-write access to dynamic application data
    
    constructor(databaseConfig: any) {
        this.databaseConfig = databaseConfig;
        this.appState = ApplicationState.getInstance();
        
        logger.info('Database Manager initialized', {
            timetablePath: this.databaseConfig.timetablePath,
            appDataPath: this.databaseConfig.appDataPath,
            maxConnections: this.databaseConfig.maxConnections
        });
    }
    
    /**
     * Initialize database connections and ensure app-data tables exist.
     */
    async initialize(): Promise<void> {
        const timer = new PerformanceTimer('database_initialization', logger);

        try {
            logger.info('Initializing database connections...');

            await Promise.all([this.connectTimetableDb(), this.connectAppDataDb()]);

            await this.initializeAppDataDatabase();
            
            logger.info('Both database connections established successfully.');
            
            timer.complete({
                timetableConnected: !!this.timetableDb,
                appDataConnected: !!this.appDataDb
            });
            
        } catch (error: any) {
            timer.fail(error);
            logger.error('Failed to initialize databases', { error: error.message });
            throw new DatabaseError('Database initialization failed', { error: error.message });
        }
    }
    
    /**
     * Connect to the timetable database (read-only).
     */
    private connectTimetableDb(): Promise<void> {
        try {
            this.timetableDb = new Database(this.databaseConfig.timetablePath,
                { readonly: true, fileMustExist: true });
            logger.info('Successfully connected to the timetable database (READ-ONLY).');
            return Promise.resolve();
        } catch (err: any) {
            logger.error(`FATAL: Could not connect to timetable database: ${err.message}`, {
                err, path: this.databaseConfig.timetablePath });
            return Promise.reject(err);
        }
    }

    /**
     * Connect to the app data database (read-write, created if absent).
     */
    private connectAppDataDb(): Promise<void> {
        try {
            this.appDataDb = new Database(this.databaseConfig.appDataPath);
            // Short: a wait blocks the event loop. Other processes only read it.
            this.appDataDb.pragma(`busy_timeout = ${APP_DATA_BUSY_TIMEOUT_MS}`);
            logger.info('Successfully connected to the app data database (READ-WRITE).');
            return Promise.resolve();
        } catch (err: any) {
            logger.error(`FATAL: Could not connect to app data database: ${err.message}`, {
                err, path: this.databaseConfig.appDataPath });
            return Promise.reject(err);
        }
    }

    /**
     * The process's one app_data.db handle, for anything else in the bot that
     * uses the same file (the rare-working shadow reader). Null until initialised.
     */
    getAppDataDb(): Database.Database | null {
        return this.appDataDb;
    }

    /**
     * Create app data tables and indexes if they do not already exist.
     */
    private initializeAppDataDatabase(): Promise<void> {
        if (!this.appDataDb) return Promise.reject(new Error('App data database not connected'));
        try {
            this.appDataDb.exec([
                `CREATE TABLE IF NOT EXISTS engagement_analytics (id INTEGER PRIMARY KEY AUTOINCREMENT, post_content TEXT, post_type TEXT, significance_score INTEGER, timestamp TEXT, vehicle_ref TEXT, post_uri TEXT, collector_event_id INTEGER, operator_ref TEXT, line TEXT, journey_ref TEXT, origin_aimed_departure TEXT, event_timestamp TEXT, delay_seconds INTEGER, direction TEXT, stop_code TEXT, stop_name TEXT, source TEXT, corroboration INTEGER, low_confidence INTEGER);`,
                // The old direct-SIRI mode's smoothed-delay tables. Older
                // copies of their data remain in the encrypted backups.
                `DROP TABLE IF EXISTS historical_delays;`,
                `DROP TABLE IF EXISTS kalman_state;`,
                `DROP TABLE IF EXISTS delay_reports;`,
            ].join('\n'));
            this.migrateEngagementAnalytics();
            logger.info("App data database tables and indexes are ready.");
            return Promise.resolve();
        } catch (err: any) {
            logger.error("Error initializing app data database tables and indexes.", { err });
            return Promise.reject(err);
        }
    }

    /** Add any provenance columns an older installation is missing. */
    private migrateEngagementAnalytics(): void {
        const expected: Record<string, string> = {
            collector_event_id: 'INTEGER',
            operator_ref: 'TEXT',
            line: 'TEXT',
            journey_ref: 'TEXT',
            origin_aimed_departure: 'TEXT',
            event_timestamp: 'TEXT',
            delay_seconds: 'INTEGER',
            direction: 'TEXT',
            stop_code: 'TEXT',
            stop_name: 'TEXT',
            source: 'TEXT',
            corroboration: 'INTEGER',
            low_confidence: 'INTEGER',
        };
        const rows = this.appDataDb!.prepare('PRAGMA table_info(engagement_analytics)').all() as Array<{ name: string }>;
        const present = new Set(rows.map(row => row.name));
        const additions = Object.entries(expected)
            .filter(([name]) => !present.has(name))
            .map(([name, type]) => `ALTER TABLE engagement_analytics ADD COLUMN ${name} ${type}`);
        this.appDataDb!.exec([
            ...additions,
            'CREATE INDEX IF NOT EXISTS idx_engagement_analytics ON engagement_analytics(timestamp)',
            'CREATE INDEX IF NOT EXISTS idx_engagement_vehicle_journey ON engagement_analytics(operator_ref, vehicle_ref, timestamp)',
        ].join(';\n'));
        if (additions.length) logger.info('Added engagement provenance columns', { count: additions.length });
    }

    /**
     * Load the fleet details lookup from fbribuses.json.
     */
    async loadBusDetailsLookup(): Promise<void> {
        const timer = new PerformanceTimer('load_bus_details', logger);
        
        try {
            const busDetailsPath = BOT_DATA_PATHS.fleet;
            
            if (!fs.existsSync(busDetailsPath)) {
                logger.warn(`Bus details file not found at ${busDetailsPath}`);
                this.appState.busDetailsLookup = emptyFleetIdentityIndex();
                this.appState.enrichmentDataStatus.fleet = {
                    loaded: false,
                    path: busDetailsPath,
                    sha256: null,
                    records: 0,
                    error: 'fleet data file is absent',
                };
                return;
            }
            
            const rawFleetBytes = fs.readFileSync(busDetailsPath);
            const rawBusData = JSON.parse(rawFleetBytes.toString('utf-8'));

            // Accept either a root-level array or a { results: [...] } wrapper.
            let records: any[];
            if (Array.isArray(rawBusData)) {
                records = rawBusData;
            } else {
                logger.warn(`[BUS_DETAILS] 'fbribuses.json' is not a root-level array. Looking for a 'results' property.`);
                records = rawBusData.results || [];
            }
            this.appState.busDetailsLookup = buildFleetIdentityIndex(records);
            this.appState.enrichmentDataStatus.fleet = {
                loaded: true,
                path: busDetailsPath,
                sha256: createHash('sha256').update(rawFleetBytes).digest('hex'),
                records: records.length,
            };
            const sharedCodeGroups = [
                ...this.appState.busDetailsLookup.codeOwners.values(),
            ].filter(owners => owners.size > 1).length;
            logger.info(`[BUS_DETAILS] Loaded ${this.appState.busDetailsLookup.results.length} operator-scoped vehicle details from 'fbribuses.json'.`, {
                sharedCodeGroups,
            });
            
            timer.complete({
                vehiclesLoaded: this.appState.busDetailsLookup.results.length
            });
            
        } catch (error: any) {
            timer.fail(error);
            logger.error(`Could not load or parse 'fbribuses.json'`, { err: error });
            this.appState.busDetailsLookup = emptyFleetIdentityIndex();
            this.appState.enrichmentDataStatus.fleet = {
                loaded: false,
                path: BOT_DATA_PATHS.fleet,
                sha256: null,
                records: 0,
                error: String(error?.message || error).slice(0, 200),
            };
        }
    }
    
    /**
     * Load route details from JSON for AI context enhancement
     */
    async loadRouteDetails(): Promise<void> {
        const timer = new PerformanceTimer('load_route_details', logger);

        try {
            const routeDetailsPath = BOT_DATA_PATHS.routeDetails;

            if (!fs.existsSync(routeDetailsPath)) {
                logger.warn(`Route details file not found at ${routeDetailsPath}`);
                this.appState.routeDetails = {};
                return;
            }

            const rawRouteData = JSON.parse(fs.readFileSync(routeDetailsPath, 'utf-8'));
            this.appState.routeDetails = rawRouteData;

            const routeCount = Object.keys(this.appState.routeDetails).length;
            logger.info(`[ROUTE_DETAILS] Loaded ${routeCount} route details for AI context`);

            if (routeCount > 0) {
                const sampleRoutes = Object.keys(this.appState.routeDetails).slice(0, 5).join(', ');
                logger.info(`[ROUTE_DETAILS] Sample routes: ${sampleRoutes}`);
            }

            timer.complete({
                routesLoaded: routeCount
            });

        } catch (error: any) {
            timer.fail(error);
            logger.error(`Could not load or parse 'route_details.json'`, { err: error });
            this.appState.routeDetails = {};
        }
    }

    /**
     * Load terminus stop names from the timetable database. Used to
     * suppress delay events for vehicles sat at a terminus.
     */
    async loadTerminusStops(): Promise<void> {
        const timer = new PerformanceTimer('load_terminus_stops', logger);
        
        try {
            logger.info("Loading terminus stops...");
            
            if (!this.timetableDb || !this.timetableDb.open) { 
                logger.warn("Terminus stops load skipped: Timetable DB not connected."); 
                this.appState.terminusStopNames = new Set(); 
                return; 
            }
            
            const query = `SELECT DISTINCT stop_name as common_name FROM stops WHERE stop_name IS NOT NULL AND (
                stop_name LIKE '%Depot%' OR 
                stop_name LIKE '%Station%' OR 
                stop_name LIKE '%Bus Station%' OR 
                stop_name LIKE '%Interchange%' OR 
                stop_name LIKE '%The Centre%' OR
                stop_name LIKE '%Centre%' OR
                stop_name LIKE '%Terminal%'
            );`;
            
            const rows = this.timetableDb.prepare(query).all() as Array<{ common_name: string }>;

            this.appState.terminusStopNames = new Set(rows.map(r => r.common_name));
            
            logger.info(`Loaded ${this.appState.terminusStopNames.size} unique terminus stop names.`);
            if (this.appState.terminusStopNames.size > 0) {
                logger.info(`Sample terminus stops: ${Array.from(this.appState.terminusStopNames).slice(0, 3).join(', ')}`);
            }
            
            timer.complete({
                terminusStopsLoaded: this.appState.terminusStopNames.size
            });
            
        } catch (error: any) { 
            timer.fail(error);
            logger.error(`Error loading terminus stops`, { err: error }); 
            this.appState.terminusStopNames = new Set(); 
        }
    }
    
    async enrichStoryJourney(event: BusEvent): Promise<BusEvent> {
        if (!this.timetableDb || this.appState.dbIsReloading || !event.collectorTripId) return event;
        let stops: JourneyStop[] = [];
        try {
            stops = this.timetableDb.prepare(`SELECT st.stop_sequence, s.stop_code, s.stop_name
                FROM stop_times st JOIN stops s ON s.stop_id = st.stop_id
                JOIN trips t ON t.trip_id = st.trip_id JOIN routes r ON r.route_id = t.route_id
                WHERE st.trip_id = ? AND r.route_short_name = ? ORDER BY st.stop_sequence`)
                .all(event.collectorTripId, event.line) as JourneyStop[];
        } catch { stops = []; }
        return { ...event, journeyContext: matchedJourneyContext(event, stops) };
    }

    /**
     * Store engagement analytics
     * Used for tracking social media post performance
     */
    async storeEngagementRecord(record: EngagementRecord): Promise<void> {
        if (!this.appDataDb) return;
        const event = record.event;
        try {
            this.appDataDb.prepare(
                `INSERT INTO engagement_analytics
                 (post_content, post_type, significance_score, timestamp,
                  vehicle_ref, post_uri, collector_event_id, operator_ref,
                  line, journey_ref, origin_aimed_departure, event_timestamp,
                  delay_seconds, direction, stop_code, stop_name, source,
                  corroboration, low_confidence)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);`).run(
                [record.postContent, record.postType, record.significance,
                 DateTime.now().toISO() ?? '', event?.vehicleRef || null,
                 record.postUri || null, event?.collectorEventId ?? null,
                 event?.operatorRef || null, event?.line || null,
                 event?.datedJourneyRef || null,
                 event?.originAimedDepartureTimeStr || null,
                 event?.timestamp || null,
                 event ? (event.delaySeconds ?? Math.round(event.delayMinutes * 60)) : null,
                 event?.direction || null, event?.lastStopCode || null,
                 event?.lastStopName || null,
                 event?.source || null, event?.corroboration ?? null,
                 event ? (event.lowConfidence ? 1 : 0) : null]);
        } catch (error: any) {
            logger.error('Failed to store engagement record', { error: error.message });
        }
    }
    
    /**
     * Get recent posts with vehicle refs and post URIs
     * Used by the /api/recent-posts endpoint for live-buses integration
     */
    async getRecentPosts(limit: number = 5): Promise<RecentPost[]> {
        try {
            if (!this.appDataDb) return [];

            const rows = this.appDataDb.prepare(
                `SELECT collector_event_id, operator_ref, vehicle_ref, line,
                        journey_ref, origin_aimed_departure, event_timestamp,
                        delay_seconds, direction, stop_code, stop_name,
                        source, corroboration, low_confidence, post_uri,
                        post_content, post_type, significance_score, timestamp
                   FROM engagement_analytics
                  WHERE vehicle_ref IS NOT NULL AND post_uri IS NOT NULL
                  ORDER BY timestamp DESC LIMIT ?;`).all(limit) as any[];
            return rows.map(r => ({
                collectorEventId: r.collector_event_id,
                operatorRef: r.operator_ref || '',
                vehicleRef: r.vehicle_ref,
                line: r.line || '',
                journeyRef: r.journey_ref || '',
                originAimedDeparture: r.origin_aimed_departure || '',
                eventTimestamp: r.event_timestamp || '',
                delaySeconds: r.delay_seconds,
                direction: r.direction || '',
                stopCode: r.stop_code || '',
                stopName: r.stop_name || '',
                source: r.source || '',
                corroboration: r.corroboration,
                lowConfidence: Boolean(r.low_confidence),
                postUri: r.post_uri,
                postContent: r.post_content,
                postType: r.post_type,
                significance: r.significance_score,
                timestamp: r.timestamp
            }));
        } catch (error: any) {
            logger.error('Error getting recent posts', { error: error.message });
            return [];
        }
    }

    /**
     * Daily cleanup: delete app-data rows older than 30 days.
     */
    async cleanupOldData(): Promise<void> {
        const timer = new PerformanceTimer('database_cleanup', logger);
        
        try {
            if (this.appState.dbIsReloading || !this.appDataDb || !this.appDataDb.open) {
                return;
            }
            
            logger.info("Running daily cleanup of old app data records...");
            const thirtyDaysAgo = DateTime.now().minus({ days: 30 }).toISO() ?? '';
            const tables = ['engagement_analytics'];
            
            let totalCleaned = 0;
            
            for (const table of tables) {
                const sql = `DELETE FROM ${table} WHERE timestamp < ?`;
                try {
                    const changes = this.appDataDb.prepare(sql).run(thirtyDaysAgo).changes;
                    if (changes > 0) logger.info(`Cleaned up ${changes} old records from ${table}.`);
                    totalCleaned += changes;
                } catch (err: any) {
                    logger.error(`Failed to clean up old data from ${table}`, { err });
                }
            }
            
            timer.complete({
                totalRecordsCleaned: totalCleaned,
                tablesProcessed: tables.length
            });
            
        } catch (error: any) {
            timer.fail(error);
            logger.error('Error during data cleanup', { error: error.message });
        }
    }
    
    /**
     * Reload the timetable database (after a timetable deployment).
     * The app-data connection is untouched.
     */
    async reloadTimetableData(): Promise<void> {
        const timer = new PerformanceTimer('timetable_reload', logger);
        
        try {
            logger.info("Attempting to reload timetable data (app data remains untouched)...");
            this.appState.dbIsReloading = true;
            
            // Only close and reconnect the timetable database - app data stays connected
            if (this.timetableDb?.open) {
                logger.info('Closing timetable database for reload...');
                try { this.timetableDb.close(); } catch (err) { logger.error('Error closing timetable database', { err }); }
            }
            
            await this.connectTimetableDb();
            await this.loadTerminusStops();
            
            logger.info('Timetable data reloaded successfully. App data connection preserved.');
            
            timer.complete({
                reloadSuccessful: true
            });
            
        } catch (error: any) { 
            timer.fail(error);
            logger.error('A critical error occurred during timetable reload.', { err: error }); 
            throw error; 
        } finally { 
            this.appState.dbIsReloading = false; 
        }
    }
    
    /**
     * Get database health status
     */
    getDatabaseHealth(): any {
        return {
            timetable: {
                connected: Boolean(this.timetableDb?.open),
                path: this.databaseConfig.timetablePath,
                readonly: true
            },
            appData: {
                connected: Boolean(this.appDataDb?.open),
                path: this.databaseConfig.appDataPath,
                readonly: false
            },
            isReloading: this.appState.dbIsReloading,
            lastHealthCheck: DateTime.now().setZone(TARGET_TIMEZONE).toISO() ?? ''
        };
    }
    
    /**
     * Close both database connections.
     */
    async close(): Promise<void> {
        const timer = new PerformanceTimer('database_close', logger);
        
        try {
            logger.info('Closing database connections...');
            
            let closed = 0;
            for (const [name, db] of [['timetable', this.timetableDb], ['app data', this.appDataDb]] as const) {
                if (!db?.open) continue;
                try {
                    db.close();
                    closed += 1;
                    logger.info(`${name} database connection closed.`);
                } catch (err) {
                    logger.error(`Error closing ${name} database`, { err });
                }
            }

            this.timetableDb = null;
            this.appDataDb = null;
            
            logger.info('All database connections closed.');
            
            timer.complete({
                connectionsClosed: closed
            });
            
        } catch (error: any) {
            timer.fail(error);
            logger.error('Error closing database connections', { error: error.message });
            throw error;
        }
    }
    
    /**
     * Get service status
     */
    getStatus(): any {
        return {
            name: 'Database Manager',
            status: 'running',
            health: this.getDatabaseHealth(),
            config: {
                timetablePath: this.databaseConfig.timetablePath,
                appDataPath: this.databaseConfig.appDataPath,
                maxConnections: this.databaseConfig.maxConnections
            }
        };
    }
}
