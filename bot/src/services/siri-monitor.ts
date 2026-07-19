// Bristol Bus Bot - SIRI Monitor Service
// Direct-ingest diagnostic path: fetches and parses SIRI-VM from BODS.
// Production uses the shared collector's events (see ingest/event-reader.ts);
// this remains only as an explicit diagnostic fallback (INGEST_MODE=siri).

// Centralized HTTP client with keep-alive + backoff
import { httpFetch } from '../utils/http-client.js';
import { XMLParser } from 'fast-xml-parser';
import { DateTime } from 'luxon';
import { logger, PerformanceTimer, TARGET_TIMEZONE, logSummary, logDetailed, logAlways } from '../utils/logging.js';
import { ApplicationState } from './application-state.js';
import type { SIRIVehicleActivity, BusEvent } from '../types/bus-types.js';


/** Minimal watchdog to pinpoint hangs in the analyze phase */
function startWatchdog(step: string) {
    const t0 = Date.now();
    const warn = setTimeout(() => {
        logger.warn('⏱️ step:>10s', { step, ms: Date.now() - t0 });
    }, 10_000);
    const hard = setTimeout(() => {
        logger.error('⛔ step:>60s (likely hung)', { step, ms: Date.now() - t0 });
    }, 60_000);
    logger.info('➡️ step:start', { step });
    return {
        clear() {
            clearTimeout(warn);
            clearTimeout(hard);
            logger.info('⬅️ step:end', { step, ms: Date.now() - t0 });
        }
    };
}

/**
 * SIRI Monitor Service
 * Handles fetching and parsing SIRI-VM data from the BODS API.
 */
export class SIRIMonitor {
    private siriConfig: any;
    private appState: ApplicationState;
    private xmlParser: XMLParser;

    constructor(siriConfig: any, appState: ApplicationState) {
        this.siriConfig = siriConfig;
        this.appState = appState;

        this.xmlParser = new XMLParser({
            ignoreAttributes: false,
            attributeNamePrefix: "",
            removeNSPrefix: true
        });

        logger.info('SIRI Monitor initialized', {
            operatorRef: this.siriConfig.operatorRef,
            boundingBox: this.siriConfig.boundingBox,
            timeout: this.siriConfig.timeout
        });
    }

    /**
     * Fetch vehicle activities from BODS SIRI-VM API with retry logic
     * Fetch SIRI data with bounded retries.
     */
    async fetchVehicleActivities(retryCount: number = 0): Promise<any[]> {
        const timer = new PerformanceTimer('siri_api_fetch', logger);

        try {
            if (!this.siriConfig.apiKey) {
                throw new Error('BODS_API_KEY not found in configuration');
            }

            logger.info('Fetching BODS data...', {
                requestUrl: '[REDACTED]',
                operatorRef: this.siriConfig.operatorRef,
                boundingBox: this.siriConfig.boundingBox
            });

            // Fetch via shared client (keep-alive, concurrency, backoff, timeout)
            const response = await httpFetch(this.siriConfig.requestUrl, {
                timeoutMs: this.siriConfig.timeout
            });

            if (!response.ok) {
                throw new Error(`BODS API Error: ${response.status} ${response.statusText}`);
            }

            const responseText = await response.text();
            const apiDuration = timer.getElapsed();

            // Update SIRI metrics for dashboard monitoring
            this.appState.updateSIRIMetrics(apiDuration);

            logger.info(`[PERFORMANCE] BODS API call completed in ${apiDuration}ms`);

            const parseStartTime = Date.now();
            const parsed = this.xmlParser.parse(responseText);
            const parseDuration = Date.now() - parseStartTime;

            const activities = [].concat(
                parsed?.Siri?.ServiceDelivery?.VehicleMonitoringDelivery?.VehicleActivity || []
            );

            logger.info(`[PERFORMANCE] XML parsing completed in ${parseDuration}ms, found ${activities.length} activities`);

            if (activities.length === 0) {
                logger.info('No vehicle activities in SIRI feed.');
            }

            timer.complete({
                apiDurationMs: apiDuration,
                parseDurationMs: parseDuration,
                activitiesFound: activities.length
            });

            return activities;

        } catch (error: any) {
            timer.fail(error);

            if (error.name === 'AbortError') {
                // Retry logic for timeouts on Pi network
                if (retryCount < 2) { // Max 2 retries for timeouts
                    const retryDelay = (retryCount + 1) * 10000; // 10s, 20s delays
                    logger.warn(`SIRI API timeout (${this.siriConfig.timeout}ms), retrying in ${retryDelay / 1000}s (attempt ${retryCount + 1}/2)`, {
                        operatorRef: this.siriConfig.operatorRef,
                        retryCount: retryCount + 1
                    });

                    await new Promise(resolve => setTimeout(resolve, retryDelay));
                    return this.fetchVehicleActivities(retryCount + 1);
                }

                logger.error(`SIRI API final timeout after ${retryCount + 1} attempts`, {
                    timeout: this.siriConfig.timeout,
                    operatorRef: this.siriConfig.operatorRef,
                    totalAttempts: retryCount + 1
                });
            } else if (error.code === 'ECONNRESET' || error.code === 'ENOTFOUND' || error.message.includes('network')) {
                // Retry logic for network errors
                if (retryCount < 1) {
                    const retryDelay = 5000; // 5s delay for network errors
                    logger.warn(`SIRI network error, retrying in ${retryDelay / 1000}s`, {
                        error: error.message,
                        code: error.code,
                        retryCount: retryCount + 1
                    });

                    await new Promise(resolve => setTimeout(resolve, retryDelay));
                    return this.fetchVehicleActivities(retryCount + 1);
                }

                logger.error('SIRI network error - max retries exceeded', {
                    error: error.message,
                    code: error.code,
                    totalAttempts: retryCount + 1
                });
            } else {
                logger.error('Error fetching SIRI data', {
                    error: error.message,
                    stack: error.stack,
                    retryCount
                });
            }

            throw error;
        }
    }

    /**
     * Parse SIRI activities into a standardized format.
     */
    async parseSIRIData(activities: any[]): Promise<SIRIVehicleActivity[]> {
        const timer = new PerformanceTimer('siri_data_parse', logger);

        try {
            const parsedActivities: SIRIVehicleActivity[] = [];

            for (const activity of activities) {
                const parsedActivity = this.parseVehicleActivity(activity);
                if (parsedActivity) {
                    parsedActivities.push(parsedActivity);
                }
            }

            timer.complete({
                totalActivities: activities.length,
                parsedActivities: parsedActivities.length,
                filteredOut: activities.length - parsedActivities.length
            });

            return parsedActivities;

        } catch (error: any) {
            timer.fail(error);
            logger.error('Error parsing SIRI activities', { error: error.message });
            throw error;
        }
    }

    /**
     * Parse an individual VehicleActivity element, or return null when
     * required fields are missing.
     */
    private parseVehicleActivity(activity: any): SIRIVehicleActivity | null {
        try {
            const mj = activity?.MonitoredVehicleJourney;
            if (!mj) {
                return null;
            }

            const {
                FramedVehicleJourneyRef,
                LineRef,
                PublishedLineName,
                DirectionRef,
                OriginAimedDepartureTime,
                VehicleRef,
                VehicleLocation,
                OperatorRef
            } = mj;

            const lineLookupKey = PublishedLineName?.toString() || LineRef?.toString();
            if (!lineLookupKey) {
                logger.warn(`[DATA_FILTER] Activity missing line reference`, {
                    PublishedLineName,
                    LineRef
                });
                return null;
            }

            // Both timestamps are required for delay measurement.
            const recordedAtTimeStr = activity?.RecordedAtTime;
            if (!recordedAtTimeStr || !OriginAimedDepartureTime) {
                logger.debug(`[DATA_FILTER] Invalid timestamps`, {
                    RecordedAt: recordedAtTimeStr,
                    OriginAimed: OriginAimedDepartureTime
                });
                return null;
            }

            // Create standardized vehicle activity object
            const vehicleActivity: SIRIVehicleActivity = {
                vehicleRef: VehicleRef?.toString() || '',
                lineRef: lineLookupKey.trim().replace(/__$/, ''), // strip BODS trailing '__'
                directionRef: DirectionRef?.toLowerCase() || '',
                datedJourneyRef: FramedVehicleJourneyRef?.DatedVehicleJourneyRef?.toString() || '',
                operatorRef: OperatorRef?.toString() || this.siriConfig.targetOperator,
                originAimedDepartureTime: OriginAimedDepartureTime,
                recordedAtTime: recordedAtTimeStr,
                validUntilTime: activity?.ValidUntilTime || '',
                vehicleLocation: VehicleLocation ? {
                    longitude: parseFloat(VehicleLocation.Longitude),
                    latitude: parseFloat(VehicleLocation.Latitude)
                } : undefined,
                monitored: true
            };

            return vehicleActivity;

        } catch (error: any) {
            logger.warn('Error parsing individual vehicle activity', {
                error: error.message,
                activityId: activity?.ItemIdentifier
            });
            return null;
        }
    }

    /**
     * Reject journeys older than the configured maximum age.
     */
    validateJourneyAge(originAimedDepartureTime: string, maxAgeHours: number): boolean {
        try {
            const originAimedDepUtc = DateTime.fromISO(originAimedDepartureTime, { zone: 'UTC' });
            const origDepLocal = originAimedDepUtc.setZone(TARGET_TIMEZONE);
            const journeyAgeHours = DateTime.now().setZone(TARGET_TIMEZONE).diff(origDepLocal, 'hours').hours;

            if (journeyAgeHours > maxAgeHours) {
                logger.warn(`[STALE_DATA] Ignoring ${journeyAgeHours.toFixed(1)}h old journey`, {
                    originDeparture: origDepLocal.toFormat('HH:mm'),
                    ageHours: journeyAgeHours.toFixed(1),
                    maxAgeHours
                });
                return false;
            }

            return true;

        } catch (error: any) {
            logger.warn('Error validating journey age', {
                error: error.message,
                originAimedDepartureTime
            });
            return false;
        }
    }

    /**
     * Reject buses sat at a terminus for an extended period.
     */
    validateTerminusStatus(stopName: string, timeAtStop: number): boolean {
        const isAtTerminus = this.appState.terminusStopNames.has(stopName || '');

        if (isAtTerminus && timeAtStop > 30) {
            logger.warn(`[TERMINUS_FILTER] Bus at terminus for ${timeAtStop}min`, {
                stopName,
                timeAtStopMinutes: timeAtStop
            });
            return false;
        }

        return true;
    }

    /**
     * Reject readings whose closest stop is beyond the distance gate.
     */
    validateGPSDistance(distance: number, maxDistance: number): boolean {
        if (distance > maxDistance) {
            logger.warn(`[GPS_REJECT] Closest stop is too far away`, {
                distanceKm: distance.toFixed(2),
                maxDistanceKm: maxDistance
            });
            return false;
        }

        return true;
    }

    /**
     * Get service status
     */
    getStatus(): any {
        return {
            name: 'SIRI Monitor',
            status: 'running',
            config: {
                operatorRef: this.siriConfig.operatorRef,
                boundingBox: this.siriConfig.boundingBox,
                timeout: this.siriConfig.timeout
            },
            lastFetch: this.appState.getLastSIRIFetch(),
            metrics: {
                totalFetches: this.appState.getSIRIFetchCount(),
                averageResponseTime: this.appState.getAverageSIRIResponseTime()
            }
        };
    }

    private databaseManager: any;
    private delayAnalyzer: any;
    private monitoringInterval: NodeJS.Timeout | null = null;

    // Summary mode counters
    private counters = {
        delaysAccepted: 0,
        delaysRejected: 0,
        staleData: 0,
        gpsRejected: 0,
        terminusFiltered: 0,
        extremeFiltered: 0,
        eventsCollected: 0,
        totalProcessed: 0
    };

    setDatabaseManager(databaseManager: any): void {
        this.databaseManager = databaseManager;
    }

    setDelayAnalyzer(delayAnalyzer: any): void {
        this.delayAnalyzer = delayAnalyzer;
    }

    async initialize(): Promise<void> {
        logger.info('SIRI Monitor initializing...');
        // Any initialization logic
    }

    startMonitoring(): void {
        logger.info('SIRI Monitor started monitoring');

        // Initial fetch
        this.fetchAndProcessData();

        // Set up interval using configuration (respects SIRI_VM_POLL_INTERVAL env var)
        const pollInterval = parseInt(process.env.SIRI_VM_POLL_INTERVAL || '120000', 10);
        this.monitoringInterval = setInterval(() => {
            this.fetchAndProcessData();
        }, pollInterval);
    }

    stopMonitoring(): void {
        logger.info('SIRI Monitor stopped monitoring');
        if (this.monitoringInterval) {
            clearInterval(this.monitoringInterval);
            this.monitoringInterval = null;
        }
    }

    private async fetchAndProcessData(): Promise<void> {
        try {
            logDetailed('info', 'Fetching SIRI vehicle activities...');
            const activities = await this.fetchVehicleActivities();
            let parsed = await this.parseSIRIData(activities);

            // Update vehicle memory with raw SIRI data before filtering.
            // This prevents buses from disappearing from the map if they miss a single update
            this.appState.updateVehicleMemory(parsed);
            this.appState.pruneVehicleMemory();
            logSummary('info', `[VEHICLE_MEMORY] Tracking ${this.appState.vehicleMemory.size} active vehicles (10-min window)`);

            // Pi optimization: Limit activities to process (prevents performance issues)
            const maxActivities = parseInt(process.env.MAX_ACTIVITIES_TO_PROCESS || '999999', 10);
            if (parsed.length > maxActivities) {
                logSummary('info', `🔧 Pi optimization: Limiting ${parsed.length} → ${maxActivities} activities`);
                parsed = parsed.slice(0, maxActivities);
            }

            // Summary: Just show the key numbers
            logSummary('info', `📡 SIRI: ${activities.length} activities → ${parsed.length} parsed`);
            logDetailed('info', `Processed ${parsed.length} vehicle activities`);

            // Process each activity through delay analyzer for schedule matching
            if (this.delayAnalyzer && parsed.length > 0) {
                const analyzeWD = startWatchdog('analyze');
                const events = await this.delayAnalyzer.processActivities(parsed, this.counters);

                // Apply the shared delay freshness policy.
                const reportableEvents: BusEvent[] = [];
                for (const busEvent of events) {
                    if (busEvent.eventType === 'delay') {
                        const history = this.delayAnalyzer.updateDelayHistory(busEvent);
                        if (this.delayAnalyzer.shouldReportDelay(busEvent, history, this.counters)) {
                            reportableEvents.push(busEvent);
                            logDetailed('info', `[EVENT_ACCEPT] ${busEvent.line}: Delay event added to collector (significance: ${busEvent.significance})`);
                        } else {
                            logDetailed('info', `[EVENT_FILTER] ${busEvent.line}: Delay filtered by freshness logic`);
                        }
                    } else {
                        // For early and punctual events, add them directly (they're already significant)
                        reportableEvents.push(busEvent);
                        logDetailed('info', `[EVENT_ACCEPT] ${busEvent.line}: ${busEvent.eventType} event added to collector (significance: ${busEvent.significance})`);
                    }
                }

                if (reportableEvents.length > 0) {
                    this.appState.addBusEvents(reportableEvents);

                    // Update route summary for dashboard (keep last 20 routes)
                    this.appState.recentRouteSummary = reportableEvents.slice(0, 20).map((event: any) => ({
                        line: event.line,
                        direction: event.direction,
                        lastStopName: event.lastStopName,
                        delayMinutes: event.delayMinutes,
                        eventType: event.eventType,
                        timestamp: new Date().toISOString()
                    }));

                    logSummary('info', `📋 COLLECTED: ${reportableEvents.length} events → Total in collector: ${this.appState.busEventCollector.length}`);
                }
                this.showFilterSummary();


                analyzeWD.clear();

            }

        } catch (error: any) {
            logAlways('error', 'Error in SIRI monitoring cycle', { error: error.message });
        }
    }

    /**
     * Show filter summary counters (for summary mode)
     */
    private showFilterSummary(): void {
        logSummary('info', `📊 FILTERS: ✅${this.counters.delaysAccepted} accepted, ❌${this.counters.delaysRejected} rejected, 🕐${this.counters.staleData} stale, 📍${this.counters.gpsRejected} GPS, 🚏${this.counters.terminusFiltered} terminus, ⚡${this.counters.extremeFiltered} extreme`);

        // Update activity log for dashboard before resetting counters
        this.appState.updateActivityLog(
            this.counters.totalProcessed,
            this.counters.eventsCollected,
            this.counters
        );

        // Reset counters for next cycle
        this.counters = {
            delaysAccepted: 0,
            delaysRejected: 0,
            staleData: 0,
            gpsRejected: 0,
            terminusFiltered: 0,
            extremeFiltered: 0,
            eventsCollected: 0,
            totalProcessed: 0
        };
    }

    /**
     * Get counters reference for delay analyzer
     */
    getCounters() {
        return this.counters;
    }

    /**
     * Close service and cleanup resources
     */
    async close(): Promise<void> {
        logger.info('SIRI Monitor service stopped');
    }
}
