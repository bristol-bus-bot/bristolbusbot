// Bristol Bus Bot - Database Manager Service
// Handles both timetable.db (read-only) and app_data.db (read-write)

import sqlite3 from 'sqlite3';
import * as fs from 'fs';
import * as path from 'path';
import { DateTime } from 'luxon';
import { logger, PerformanceTimer, TARGET_TIMEZONE, logSummary, logDetailed, logAlways } from '../utils/logging.js';
import { ApplicationState } from './application-state.js';
import { DatabaseError } from '../types/bus-types.js';
import type { 
    DatabaseStop, 
    DatabaseRoute, 
    DatabaseJourney, 
    DatabaseStopTime,
    BusVehicleDetails
} from '../types/bus-types.js';

/**
 * Database Manager Service
 * Owns the bot's SQLite connections: the timetable is opened read-only
 * (the pipeline owns its contents); app_data.db is the bot's own store.
 */
export class DatabaseManager {
    private databaseConfig: any;
    private appState: ApplicationState;

    private timetableDb: sqlite3.Database | null = null;  // Read-only access to static timetable data
    private appDataDb: sqlite3.Database | null = null;    // Read-write access to dynamic application data
    
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
        return new Promise((resolve, reject) => {
            this.timetableDb = new sqlite3.Database(
                this.databaseConfig.timetablePath, 
                sqlite3.OPEN_READONLY, 
                (err) => {
                    if (err) {
                        logger.error(`FATAL: Could not connect to timetable database: ${err.message}`, { 
                            err,
                            path: this.databaseConfig.timetablePath 
                        });
                        reject(err);
                    } else {
                        logger.info('Successfully connected to the timetable database (READ-ONLY).');
                        resolve();
                    }
                }
            );
        });
    }
    
    /**
     * Connect to the app data database (read-write, created if absent).
     */
    private connectAppDataDb(): Promise<void> {
        return new Promise((resolve, reject) => {
            this.appDataDb = new sqlite3.Database(
                this.databaseConfig.appDataPath, 
                sqlite3.OPEN_READWRITE | sqlite3.OPEN_CREATE, 
                (err) => {
                    if (err) {
                        logger.error(`FATAL: Could not connect to app data database: ${err.message}`, { 
                            err,
                            path: this.databaseConfig.appDataPath 
                        });
                        reject(err);
                    } else {
                        logger.info('Successfully connected to the app data database (READ-WRITE).');
                        resolve();
                    }
                }
            );
        });
    }
    
    /**
     * Create app data tables and indexes if they do not already exist.
     */
    private initializeAppDataDatabase(): Promise<void> {
        return new Promise((resolve, reject) => {
            if (!this.appDataDb) {
                reject(new Error('App data database not connected'));
                return;
            }
            
            this.appDataDb.serialize(() => {
                const createStmts = [
                    `CREATE TABLE IF NOT EXISTS historical_delays (id INTEGER PRIMARY KEY AUTOINCREMENT, journey_code TEXT NOT NULL, stop_code TEXT NOT NULL, delay_minutes INTEGER NOT NULL, timestamp TEXT NOT NULL);`,
                    `CREATE TABLE IF NOT EXISTS kalman_state (journey_code TEXT PRIMARY KEY, variance REAL NOT NULL, estimate REAL NOT NULL, last_updated TEXT NOT NULL);`,
                    `CREATE TABLE IF NOT EXISTS engagement_analytics (id INTEGER PRIMARY KEY AUTOINCREMENT, post_content TEXT, post_type TEXT, significance_score INTEGER, timestamp TEXT, vehicle_ref TEXT, post_uri TEXT);`,
                    `CREATE TABLE IF NOT EXISTS delay_reports (id INTEGER PRIMARY KEY AUTOINCREMENT, route_id TEXT NOT NULL, delay_minutes INTEGER NOT NULL, last_stop_name TEXT, trend TEXT, report_time TEXT NOT NULL);`,
                    `CREATE INDEX IF NOT EXISTS idx_historical_delays ON historical_delays(journey_code, stop_code, timestamp);`,
                    `CREATE INDEX IF NOT EXISTS idx_engagement_analytics ON engagement_analytics(timestamp);`,
                    `CREATE INDEX IF NOT EXISTS idx_kalman_state_last_updated ON kalman_state(last_updated);`,
                    `CREATE INDEX IF NOT EXISTS idx_delay_reports ON delay_reports(route_id, report_time);`
                ];
                
                this.appDataDb!.exec(createStmts.join('\n'), (err) => {
                    if (err) {
                        logger.error("Error initializing app data database tables and indexes.", { err });
                        return reject(err);
                    }
                    logger.info("App data database tables and indexes are ready.");
                    resolve();
                });
            });
        });
    }
    
    /**
     * Load the fleet details lookup from fbribuses.json.
     */
    async loadBusDetailsLookup(): Promise<void> {
        const timer = new PerformanceTimer('load_bus_details', logger);
        
        try {
            const busDetailsPath = path.join(process.cwd(), 'fbribuses.json');
            
            if (!fs.existsSync(busDetailsPath)) {
                logger.warn(`Bus details file not found at ${busDetailsPath}`);
                this.appState.busDetailsLookup = { results: [] };
                return;
            }
            
            const rawBusData = JSON.parse(fs.readFileSync(busDetailsPath, 'utf-8'));

            // Accept either a root-level array or a { results: [...] } wrapper.
            if (Array.isArray(rawBusData)) {
                this.appState.busDetailsLookup.results = rawBusData;
                logger.info(`[BUS_DETAILS] Loaded ${this.appState.busDetailsLookup.results.length} vehicle details from 'fbribuses.json'.`);
            } else {
                // This handles the case where the file might be { "results": [...] } in the future
                logger.warn(`[BUS_DETAILS] 'fbribuses.json' is not a root-level array. Looking for a 'results' property.`);
                this.appState.busDetailsLookup.results = rawBusData.results || [];
                logger.info(`[BUS_DETAILS] Loaded ${this.appState.busDetailsLookup.results.length} vehicle details from 'fbribuses.json'.`);
            }
            
            timer.complete({
                vehiclesLoaded: this.appState.busDetailsLookup.results.length
            });
            
        } catch (error: any) {
            timer.fail(error);
            logger.error(`Could not load or parse 'fbribuses.json'`, { err: error });
            this.appState.busDetailsLookup = { results: [] }; // Ensure it's a predictable empty state on error
        }
    }
    
    /**
     * Load route details from JSON for AI context enhancement
     */
    async loadRouteDetails(): Promise<void> {
        const timer = new PerformanceTimer('load_route_details', logger);

        try {
            const routeDetailsPath = path.join(process.cwd(), 'route_details.json');

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
            
            if (!this.timetableDb || !(this.timetableDb as any).open) { 
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
            
            const rows = await new Promise<Array<{ common_name: string }>>((resolve, reject) => 
                this.timetableDb!.all(query, [], (e, r: any[]) => e ? reject(e) : resolve(r))
            );
            
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
    
    /**
     * Look up the scheduled stop list for a journey: first by trip id,
     * then by a ±2 minute departure-time window. Handles GTFS 24+ hour
     * times for overnight services.
     */
    async querySchedule(
        datedJourneyRef: string | null,
        opCode: string,
        lineName: string,
        dirRef: string,
        originAimedDepartureTime: string
    ): Promise<any[] | null> {
        const timer = new PerformanceTimer('schedule_query', logger);

        try {
            if (this.appState.dbIsReloading || !this.timetableDb || !(this.timetableDb as any).open) {
                return null;
            }

            const fields = `st.arrival_time as arr, st.departure_time as dep, s.stop_code as stop, s.stop_name as stop_name, s.stop_lat as latitude, s.stop_lon as longitude`;

            const originDateTime = DateTime.fromISO(originAimedDepartureTime, { zone: 'UTC' }).setZone(TARGET_TIMEZONE);
            const targetDayOfWeek = originDateTime.toFormat('ccc'); // e.g., 'Sat' or 'Sun' from the live data
            const hour = originDateTime.hour;
            const dayMapping: Record<string, string> = {
                'Sun': 'sunday', 'Mon': 'monday', 'Tue': 'tuesday', 'Wed': 'wednesday',
                'Thu': 'thursday', 'Fri': 'friday', 'Sat': 'saturday'
            };
            const exactServiceDays = [originDateTime];
            if (hour >= 0 && hour < 6) {
                exactServiceDays.unshift(originDateTime.minus({ days: 1 }));
            }

            // Try with trip_id (GTFS equivalent of datedJourneyRef)
            // Attempt with FBRI operator first, then without (SIRI operator refs don't always match GTFS)
            if (datedJourneyRef) {
                for (const agencyFilter of ['FBRI', null]) {
                    const agencyClause = agencyFilter
                        ? `a.agency_noc = '${agencyFilter}' AND`
                        : '';
                    for (const serviceDay of exactServiceDays) {
                        const dayColumn = dayMapping[serviceDay.toFormat('ccc')];
                        if (!dayColumn) continue;
                        const dateStr = serviceDay.toFormat('yyyyMMdd');
                        const trip = await new Promise<{ trip_id: string } | null>((res) => this.timetableDb!.get(
                            `SELECT DISTINCT t.trip_id FROM trips t
                             JOIN routes r ON t.route_id = r.route_id
                             JOIN agency a ON r.agency_id = a.agency_id
                             WHERE ${agencyClause}
                             (t.trip_id = ? OR (
                                 t.vehicle_journey_code = ?
                                 AND r.route_short_name = ?
                             ))
                             AND (
                                 EXISTS (
                                     SELECT 1 FROM calendar c
                                     WHERE c.service_id=t.service_id
                                       AND c.${dayColumn}=1
                                       AND c.start_date<=? AND c.end_date>=?
                                       AND t.service_id NOT IN (
                                           SELECT service_id FROM calendar_dates
                                           WHERE date=? AND exception_type=2
                                       )
                                 )
                                 OR EXISTS (
                                     SELECT 1 FROM calendar_dates cd
                                     WHERE cd.service_id=t.service_id
                                       AND cd.date=? AND cd.exception_type=1
                                 )
                             )
                             ORDER BY CASE WHEN t.trip_id=? THEN 0 ELSE 1 END
                             LIMIT 1;`,
                            [
                                datedJourneyRef, datedJourneyRef, lineName,
                                dateStr, dateStr, dateStr, dateStr,
                                datedJourneyRef
                            ],
                            (e, r: any) => res(e ? null : (r || null))
                        ));
                        if (!trip) continue;
                        const rows = await new Promise<any[]>((res) => this.timetableDb!.all(
                            `SELECT ${fields} FROM stop_times st
                             JOIN stops s ON st.stop_id = s.stop_id
                             WHERE st.trip_id = ?
                             ORDER BY st.stop_sequence ASC;`,
                            [trip.trip_id],
                            (e, r: any[]) => res(e ? [] : r)
                        ));

                        if (rows.length > 0) {
                            timer.complete({
                                method: agencyFilter ? 'trip_id' : 'trip_id_no_operator',
                                rowsFound: rows.length,
                                dayOfWeek: targetDayOfWeek
                            });
                            return rows;
                        }
                    }
                }
            }

            // Fallback to time-based lookup
            const depTimeKey = originDateTime.toFormat('HH:mm:ss');
            const lower = originDateTime.minus({ minutes: 2 }).toFormat('HH:mm:ss'); // TIME_WINDOW_MINUTES = 2
            const upper = originDateTime.plus({ minutes: 2 }).toFormat('HH:mm:ss');

            // GTFS uses 24+ hour notation for services past midnight (e.g., 24:20:00 = 00:20)
            // For times between 00:00-05:59, also search for 24+ hour equivalents
            const minute = originDateTime.minute;
            const second = originDateTime.second;
            const searchTimes: Array<{key: string, lower: string, upper: string, dayOfWeek: string, serviceDate: string}> = [{
                key: depTimeKey,
                lower,
                upper,
                dayOfWeek: targetDayOfWeek,
                serviceDate: originDateTime.toFormat('yyyyMMdd')
            }];

            if (hour >= 0 && hour < 6) {
                // Convert to GTFS 24+ hour format
                const gtfsHour = hour + 24;
                const gtfsDepTimeKey = `${gtfsHour.toString().padStart(2, '0')}:${minute.toString().padStart(2, '0')}:${second.toString().padStart(2, '0')}`;

                // Calculate lower bound (subtract 2 minutes)
                const lowerDateTime = originDateTime.minus({ minutes: 2 });
                const gtfsLowerHour = lowerDateTime.hour + 24;
                const gtfsLower = `${gtfsLowerHour.toString().padStart(2, '0')}:${lowerDateTime.minute.toString().padStart(2, '0')}:${lowerDateTime.second.toString().padStart(2, '0')}`;

                // Calculate upper bound (add 2 minutes)
                const upperDateTime = originDateTime.plus({ minutes: 2 });
                const gtfsUpperHour = upperDateTime.hour + 24;
                const gtfsUpper = `${gtfsUpperHour.toString().padStart(2, '0')}:${upperDateTime.minute.toString().padStart(2, '0')}:${upperDateTime.second.toString().padStart(2, '0')}`;

                // GTFS service day: times 24:00+ belong to previous calendar day
                const gtfsDayOfWeek = originDateTime.minus({ days: 1 }).toFormat('ccc');
                searchTimes.push({
                    key: gtfsDepTimeKey,
                    lower: gtfsLower,
                    upper: gtfsUpper,
                    dayOfWeek: gtfsDayOfWeek,
                    serviceDate: originDateTime.minus({ days: 1 }).toFormat('yyyyMMdd')
                });
            }

            // Try with FBRI operator first, then without operator filter
            // SIRI OperatorRef often doesn't match GTFS agency_noc, so fallback catches more routes
            for (const agencyFilter of ['FBRI', null]) {
                const agencyClause = agencyFilter
                    ? `a.agency_noc = '${agencyFilter}' AND`
                    : '';

                let trip: { trip_id: string } | null = null;
                for (const timeSet of searchTimes) {
                    const dayColumn = dayMapping[timeSet.dayOfWeek];
                    if (!dayColumn) continue;

                    // Try calendar table first (weekly patterns)
                    const calendarSql = `SELECT DISTINCT t.trip_id
                         FROM trips t
                         JOIN routes r ON t.route_id = r.route_id
                         JOIN agency a ON r.agency_id = a.agency_id
                         JOIN calendar c ON t.service_id = c.service_id
                         JOIN stop_times st ON t.trip_id = st.trip_id
                         WHERE ${agencyClause}
                         r.route_short_name = ?
                         AND st.departure_time BETWEEN ? AND ?
                         AND c.${dayColumn} = 1
                         AND c.start_date <= ? AND c.end_date >= ?
                         AND t.service_id NOT IN (
                             SELECT service_id FROM calendar_dates
                             WHERE date = ? AND exception_type = 2
                         )
                         ORDER BY ABS(strftime('%s', '1970-01-01 ' || st.departure_time) - strftime('%s', '1970-01-01 ' || ?)) ASC
                         LIMIT 1;`;

                    trip = await new Promise<{ trip_id: string } | null>((res) => this.timetableDb!.get(
                        calendarSql,
                        [
                            lineName, timeSet.lower, timeSet.upper,
                            timeSet.serviceDate, timeSet.serviceDate,
                            timeSet.serviceDate, timeSet.key
                        ],
                        (e, r: any) => res(e ? null : (r || null))
                    ));

                    if (trip) break; // Found a match in calendar, stop searching

                    // If no match in calendar, try calendar_dates (specific date exceptions/additions)
                    const calendarDatesSql = `SELECT DISTINCT t.trip_id
                         FROM trips t
                         JOIN routes r ON t.route_id = r.route_id
                         JOIN agency a ON r.agency_id = a.agency_id
                         JOIN calendar_dates cd ON t.service_id = cd.service_id
                         JOIN stop_times st ON t.trip_id = st.trip_id
                         WHERE ${agencyClause}
                         r.route_short_name = ?
                         AND st.departure_time BETWEEN ? AND ?
                         AND cd.date = ?
                         AND cd.exception_type = 1
                         ORDER BY ABS(strftime('%s', '1970-01-01 ' || st.departure_time) - strftime('%s', '1970-01-01 ' || ?)) ASC
                         LIMIT 1;`;

                    trip = await new Promise<{ trip_id: string } | null>((res) => this.timetableDb!.get(
                        calendarDatesSql,
                        [
                            lineName, timeSet.lower, timeSet.upper,
                            timeSet.serviceDate, timeSet.key
                        ],
                        (e, r: any) => res(e ? null : (r || null))
                    ));

                    if (trip) break; // Found a match in calendar_dates, stop searching
                }

                if (trip) {
                    const stops = await new Promise<any[]>((res) => this.timetableDb!.all(
                        `SELECT ${fields} FROM stop_times st
                         JOIN stops s ON st.stop_id = s.stop_id
                         WHERE st.trip_id = ?
                         ORDER BY st.stop_sequence ASC;`,
                        [trip.trip_id],
                        (e, r: any[]) => res(e ? [] : r)
                    ));

                    if (stops.length > 0) {
                        const method = agencyFilter ? 'time_fallback' : 'time_fallback_no_operator';
                        logDetailed('info', `Used ${method} schedule for Line: ${lineName}, Time: ${depTimeKey} on ${targetDayOfWeek}`);

                        timer.complete({
                            method,
                            rowsFound: stops.length,
                            dayOfWeek: targetDayOfWeek,
                            depTime: depTimeKey
                        });
                        return stops;
                    }
                }
            }

            logger.warn(`No schedule found for Op:${opCode}, Line:${lineName}, Time:${depTimeKey}, Day:${targetDayOfWeek}`);
            timer.complete({
                method: 'not_found',
                rowsFound: 0
            });
            return null;

        } catch (error: any) {
            timer.fail(error);
            logger.error(`Error during schedule lookup`, { err: error });
            return null;
        }
    }
    
    /**
     * Fuzzy trip matching: find a trip id by line and first-stop departure
     * time window. Handles GTFS 24-hour notation for overnight services.
     */
    async queryTripFuzzy(lineName: string, directionRef: string, originTime: string): Promise<string | null> {
        if (this.appState.dbIsReloading || !this.timetableDb) return null;

        const originDateTime = DateTime.fromISO(originTime, { zone: 'UTC' }).setZone(TARGET_TIMEZONE);

        // Create time windows for BOTH standard and GTFS 24-hour formats
        const hour = originDateTime.hour;

        // Standard time window (e.g., 01:30 ± 15 mins)
        const standardLower = originDateTime.minus({ minutes: 15 }).toFormat('HH:mm:ss');
        const standardUpper = originDateTime.plus({ minutes: 15 }).toFormat('HH:mm:ss');
        const standardDay = originDateTime.toFormat('ccc'); // e.g., 'Mon'

        // Day mapping (same as querySchedule)
        const dayMapping: Record<string, string> = {
            'Sun': 'sunday', 'Mon': 'monday', 'Tue': 'tuesday', 'Wed': 'wednesday',
            'Thu': 'thursday', 'Fri': 'friday', 'Sat': 'saturday'
        };

        const standardDayColumn = dayMapping[standardDay];
        if (!standardDayColumn) return null;

        // Try with FBRI operator first, then without (SIRI operator refs don't always match GTFS)
        for (const agencyFilter of ['FBRI', null]) {
            const agencyClause = agencyFilter
                ? `a.agency_noc = '${agencyFilter}' AND`
                : '';

            // Try standard time first
            const standardQuery = `
                SELECT t.trip_id
                FROM trips t
                JOIN routes r ON t.route_id = r.route_id
                JOIN agency a ON r.agency_id = a.agency_id
                JOIN stop_times st ON t.trip_id = st.trip_id
                JOIN calendar c ON t.service_id = c.service_id
                WHERE ${agencyClause}
                r.route_short_name = ?
                AND st.stop_sequence = 1
                AND st.departure_time BETWEEN ? AND ?
                AND c.${standardDayColumn} = 1
                LIMIT 1
            `;

            const standardResult = await new Promise<string | null>((resolve) => {
                this.timetableDb!.get(standardQuery, [lineName, standardLower, standardUpper], (err, row: any) => {
                    resolve((err || !row) ? null : row.trip_id);
                });
            });

            if (standardResult) {
                if (!agencyFilter) logSummary('info', `[FUZZY_NO_OPERATOR] Matched ${lineName} without operator filter`);
                return standardResult;
            }

            // GTFS OVERNIGHT HANDLING: If time is 00:00-05:59, also try 24+ hour format
            if (hour >= 0 && hour < 6) {
                // Calculate GTFS time windows (add 24 hours to hour component)
                const lowerDateTime = originDateTime.minus({ minutes: 15 });
                const gtfsLowerHour = lowerDateTime.hour + 24;
                const gtfsLower = `${gtfsLowerHour.toString().padStart(2, '0')}:${lowerDateTime.minute.toString().padStart(2, '0')}:${lowerDateTime.second.toString().padStart(2, '0')}`;

                const upperDateTime = originDateTime.plus({ minutes: 15 });
                const gtfsUpperHour = upperDateTime.hour + 24;
                const gtfsUpper = `${gtfsUpperHour.toString().padStart(2, '0')}:${upperDateTime.minute.toString().padStart(2, '0')}:${upperDateTime.second.toString().padStart(2, '0')}`;

                // GTFS service day: 24:00+ times belong to PREVIOUS calendar day
                const gtfsDay = originDateTime.minus({ days: 1 }).toFormat('ccc');
                const gtfsDayColumn = dayMapping[gtfsDay];

                if (gtfsDayColumn) {
                    const gtfsQuery = `
                        SELECT t.trip_id
                        FROM trips t
                        JOIN routes r ON t.route_id = r.route_id
                        JOIN agency a ON r.agency_id = a.agency_id
                        JOIN stop_times st ON t.trip_id = st.trip_id
                        JOIN calendar c ON t.service_id = c.service_id
                        WHERE ${agencyClause}
                        r.route_short_name = ?
                        AND st.stop_sequence = 1
                        AND st.departure_time BETWEEN ? AND ?
                        AND c.${gtfsDayColumn} = 1
                        LIMIT 1
                    `;

                const gtfsResult = await new Promise<string | null>((resolve) => {
                    this.timetableDb!.get(gtfsQuery, [lineName, gtfsLower, gtfsUpper], (err, row: any) => {
                        resolve((err || !row) ? null : row.trip_id);
                    });
                });

                    if (gtfsResult) {
                        if (!agencyFilter) logSummary('info', `[FUZZY_GTFS_NO_OPERATOR] Matched overnight ${lineName} without operator filter`);
                        logSummary('info', `[FUZZY_GTFS] Found overnight service using 24+ hour format (${gtfsLower}-${gtfsUpper} on ${gtfsDay})`);
                        return gtfsResult;
                    }
                }
            }
        } // end operator fallback loop

        return null;
    }

    /**
     * Fuzzy schedule matching: get the full stop list for a fuzzy-matched trip.
     */
    async queryScheduleFuzzy(lineRef: string, directionRef: string, originTime: string): Promise<any[]> {
        const tripId = await this.queryTripFuzzy(lineRef, directionRef, originTime);
        if (!tripId) return [];

        const fields = `st.arrival_time as arr, st.departure_time as dep, s.stop_code as stop, s.stop_name as stop_name, s.stop_lat as latitude, s.stop_lon as longitude`;

        return new Promise<any[]>((resolve) => {
            this.timetableDb!.all(
                `SELECT ${fields} FROM stop_times st
                 JOIN stops s ON st.stop_id = s.stop_id
                 WHERE st.trip_id = ?
                 ORDER BY st.stop_sequence ASC`,
                [tripId],
                (err, rows) => resolve(err ? [] : rows)
            );
        });
    }

    /**
     * Record a historical delay observation.
     */
    async recordHistoricalDelay(journeyCode: string, stopCode: string, delayMinutes: number, timestamp: string): Promise<void> {
        try {
            if (this.appState.dbIsReloading || !this.appDataDb || !(this.appDataDb as any).open) {
                return;
            }
            
            this.appDataDb.run(
                `INSERT INTO historical_delays (journey_code, stop_code, delay_minutes, timestamp) VALUES (?, ?, ?, ?);`, 
                [journeyCode, stopCode, delayMinutes, timestamp], 
                (err) => {
                    if (err && !err.message.includes("no such table")) {
                        logger.error("[History] Failed to insert record.", { err });
                    }
                }
            );
            
        } catch (error: any) {
            logger.error('Error recording historical delay', { error: error.message });
        }
    }
    
    /**
     * Get historical delays for journey and stop
     * Used by delay predictor for Kalman filtering
     */
    async getHistoricalDelays(journeyCode: string, stopCode: string): Promise<number[]> {
        try {
            if (!journeyCode || !stopCode || this.appState.dbIsReloading || !this.appDataDb || !(this.appDataDb as any).open) {
                return [];
            }
            
            const currentDayOfWeek = DateTime.now().setZone(TARGET_TIMEZONE).weekday;
            
            return new Promise<number[]>((resolve) => {
                this.appDataDb!.all(
                    `SELECT delay_minutes, timestamp FROM historical_delays WHERE journey_code = ? AND stop_code = ? ORDER BY timestamp DESC LIMIT 50;`, 
                    [journeyCode, stopCode], 
                    (err, rows: any[]) => {
                        if (err) { 
                            logger.error("[History] Failed to fetch historical delays.", { err }); 
                            return resolve([]); 
                        }
                        
                        const filtered = rows.filter(r => DateTime.fromISO(r.timestamp).weekday === currentDayOfWeek);
                        resolve(filtered.slice(0, 10).map(r => r.delay_minutes));
                    }
                );
            });
            
        } catch (error: any) {
            logger.error('Error getting historical delays', { error: error.message });
            return [];
        }
    }
    
    /**
     * Store a delay report row.
     */
    async storeDelayReport(routeId: string, delayMinutes: number, lastStopName: string | null, trend: string): Promise<void> {
        try {
            if (!this.appDataDb) {
                return;
            }
            
            const stmt = this.appDataDb.prepare(
                `INSERT INTO delay_reports (route_id, delay_minutes, last_stop_name, trend, report_time) 
                 VALUES (?, ?, ?, ?, ?)`
            );
            
            stmt.run(
                routeId,
                delayMinutes,
                lastStopName || null,
                trend,
                DateTime.now().setZone(TARGET_TIMEZONE).toISO() ?? ''
            );
            
            stmt.finalize();
            
        } catch (error: any) {
            logger.warn("Failed to store delay report", { err: error });
        }
    }
    
    /**
     * Store engagement analytics
     * Used for tracking social media post performance
     */
    async storeEngagementRecord(postContent: string, postType: string, significance: number, vehicleRef?: string, postUri?: string): Promise<void> {
        try {
            if (!this.appDataDb) {
                return;
            }

            this.appDataDb.run(
                `INSERT INTO engagement_analytics (post_content, post_type, significance_score, timestamp, vehicle_ref, post_uri) VALUES (?, ?, ?, ?, ?, ?);`,
                [postContent, postType, significance, DateTime.now().toISO() ?? '', vehicleRef || null, postUri || null],
                (err) => {
                    if (err) {
                        logger.error('Failed to store engagement record', { err });
                    }
                }
            );
            
        } catch (error: any) {
            logger.error('Error storing engagement record', { error: error.message });
        }
    }
    
    /**
     * Get recent posts with vehicle refs and post URIs
     * Used by the /api/recent-posts endpoint for live-buses integration
     */
    async getRecentPosts(limit: number = 5): Promise<Array<{ vehicleRef: string; postUri: string; postContent: string; postType: string; timestamp: string }>> {
        try {
            if (!this.appDataDb) return [];

            return new Promise<any[]>((resolve) => {
                this.appDataDb!.all(
                    `SELECT vehicle_ref, post_uri, post_content, post_type, timestamp FROM engagement_analytics WHERE vehicle_ref IS NOT NULL AND post_uri IS NOT NULL ORDER BY timestamp DESC LIMIT ?;`,
                    [limit],
                    (err, rows: any[]) => {
                        if (err) {
                            logger.error('Failed to fetch recent posts', { err });
                            return resolve([]);
                        }
                        resolve((rows || []).map(r => ({
                            vehicleRef: r.vehicle_ref,
                            postUri: r.post_uri,
                            postContent: r.post_content,
                            postType: r.post_type,
                            timestamp: r.timestamp
                        })));
                    }
                );
            });
        } catch (error: any) {
            logger.error('Error getting recent posts', { error: error.message });
            return [];
        }
    }

    /**
     * Load Kalman state for journey
     */
    async loadKalmanState(journeyCode: string): Promise<{ variance: number; estimate: number } | null> {
        try {
            if (this.appState.dbIsReloading || !this.appDataDb || !(this.appDataDb as any).open) {
                return null;
            }
            
            return new Promise((resolve) => {
                this.appDataDb!.get(
                    `SELECT variance, estimate FROM kalman_state WHERE journey_code = ?;`, 
                    [journeyCode], 
                    (err, row: any) => {
                        if (err) { 
                            logger.error("[Kalman] Error loading state", { err }); 
                            resolve(null); 
                        } else {
                            resolve(row ? { variance: row.variance, estimate: row.estimate } : null);
                        }
                    }
                );
            });
            
        } catch (error: any) {
            logger.error('Error loading Kalman state', { error: error.message });
            return null;
        }
    }
    
    /**
     * Save Kalman state for journey
     */
    async saveKalmanState(journeyCode: string, variance: number, estimate: number): Promise<void> {
        try {
            if (this.appState.dbIsReloading || !this.appDataDb || !(this.appDataDb as any).open) {
                return;
            }
            
            this.appDataDb.run(
                `INSERT OR REPLACE INTO kalman_state (journey_code, variance, estimate, last_updated) VALUES (?, ?, ?, ?);`, 
                [journeyCode, variance, estimate, DateTime.now().toISO() ?? ''], 
                (err) => {
                    if (err) {
                        logger.error("[Kalman] Error saving state", { err });
                    }
                }
            );
            
        } catch (error: any) {
            logger.error('Error saving Kalman state', { error: error.message });
        }
    }
    
    /**
     * Daily cleanup: delete app-data rows older than 30 days.
     */
    async cleanupOldData(): Promise<void> {
        const timer = new PerformanceTimer('database_cleanup', logger);
        
        try {
            if (this.appState.dbIsReloading || !this.appDataDb || !(this.appDataDb as any).open) {
                return;
            }
            
            logger.info("Running daily cleanup of old app data records...");
            const thirtyDaysAgo = DateTime.now().minus({ days: 30 }).toISO() ?? '';
            const tables = ['historical_delays', 'engagement_analytics', 'kalman_state'];
            const timeColumn = (table: string) => table === 'kalman_state' ? 'last_updated' : 'timestamp';
            
            let totalCleaned = 0;
            
            for (const table of tables) {
                const sql = `DELETE FROM ${table} WHERE ${timeColumn(table)} < ?`;
                const result = await new Promise<number>((resolve) => {
                    this.appDataDb!.run(sql, [thirtyDaysAgo], function(err) {
                        if (err) {
                            logger.error(`Failed to clean up old data from ${table}`, { err });
                            resolve(0);
                        } else {
                            const changes = this.changes;
                            if (changes > 0) {
                                logger.info(`Cleaned up ${changes} old records from ${table}.`);
                            }
                            resolve(changes);
                        }
                    });
                });
                totalCleaned += result;
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
            if (this.timetableDb && (this.timetableDb as any).open) {
                logger.info('Closing timetable database for reload...');
                await new Promise<void>((resolve) => {
                    this.timetableDb!.close((err) => {
                        if (err) logger.error('Error closing timetable database', { err });
                        resolve();
                    });
                });
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
                connected: !!this.timetableDb && (this.timetableDb as any).open,
                path: this.databaseConfig.timetablePath,
                readonly: true
            },
            appData: {
                connected: !!this.appDataDb && (this.appDataDb as any).open,
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
            
            const promises: Promise<void>[] = [];
            
            // Close timetable database
            if (this.timetableDb && (this.timetableDb as any).open) {
                logger.info('Closing timetable database connection...');
                promises.push(new Promise<void>((resolve) => {
                    this.timetableDb!.close((err) => {
                        if (err) logger.error('Error closing timetable database', { err });
                        else logger.info('Timetable database connection closed.');
                        resolve();
                    });
                }));
            }
            
            // Close app data database
            if (this.appDataDb && (this.appDataDb as any).open) {
                logger.info('Closing app data database connection...');
                promises.push(new Promise<void>((resolve) => {
                    this.appDataDb!.close((err) => {
                        if (err) logger.error('Error closing app data database', { err });
                        else logger.info('App data database connection closed.');
                        resolve();
                    });
                }));
            }
            
            await Promise.all(promises);
            
            this.timetableDb = null;
            this.appDataDb = null;
            
            logger.info('All database connections closed.');
            
            timer.complete({
                connectionsClosed: promises.length
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
