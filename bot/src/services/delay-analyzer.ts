// Significance scoring and freshness rules for collector events and direct
// SIRI diagnostic ingest. Prediction is used only by the direct-ingest mode.

import { DateTime } from 'luxon';
import { logger, PerformanceTimer, TARGET_TIMEZONE, logSummary, logDetailed, logAlways } from '../utils/logging.js';
import { cleanStopName } from '../utils/stop-name-cleaner.js';
import { ApplicationState } from './application-state.js';
import type { DatabaseManager } from './database-manager.js';
import type {
  BusEvent,
  SIRIVehicleActivity,
  DelayHistory,
  DelayReport,
  BusVehicleDetails
} from '../types/bus-types.js';

// Yield between CPU-heavy batches to keep the service responsive.
function yieldNow(): Promise<void> {
  return new Promise((resolve) => setImmediate(resolve));
}

/**
 * Simplified Kalman filter for delay smoothing. The direct-SIRI diagnostic path
 * only; collector events carry observed, unblended delays.
 */
class SimplifiedKalmanFilter {
  public variance: number;
  public estimate: number;
  private gain = 0.5;

  constructor(initialEstimate = 0, initialVariance = 100) {
    this.estimate = initialEstimate;
    this.variance = initialVariance;
  }

  predict() {
    this.variance += 0.1;
    return this.estimate;
  }

  update(measurement: number) {
    this.gain = this.variance / (this.variance + 20);
    this.estimate = this.estimate + this.gain * (measurement - this.estimate);
    this.variance = (1 - this.gain) * this.variance;
    return this.estimate;
  }
}

/**
 * Delay predictor blending Kalman smoothing with historical averages.
 * Direct-SIRI diagnostic path only; never applied to collector events.
 */
class DelayPredictor {
  private kalmanStates = new Map<string, SimplifiedKalmanFilter>();
  private databaseManager: DatabaseManager;

  constructor(databaseManager: DatabaseManager) {
    this.databaseManager = databaseManager;
  }

  private async getKalmanFilter(journeyCode: string): Promise<SimplifiedKalmanFilter> {
    if (!this.kalmanStates.has(journeyCode)) {
      const state = await this.databaseManager.loadKalmanState(journeyCode);
      const filter = state ? new SimplifiedKalmanFilter(state.estimate, state.variance) : new SimplifiedKalmanFilter();
      this.kalmanStates.set(journeyCode, filter);
    }
    return this.kalmanStates.get(journeyCode)!;
  }

  async predictDelay(baseRecord: any): Promise<{ delay: number; confidence: number }> {
    const { datedJourneyRef, delayMinutes, lastStopCode, timestamp } = baseRecord;
    if (!datedJourneyRef) return { delay: delayMinutes, confidence: 0.1 };

    // Record historical delay
    await this.databaseManager.recordHistoricalDelay(datedJourneyRef, lastStopCode, delayMinutes, timestamp);

    const historicalDelays = await this.databaseManager.getHistoricalDelays(datedJourneyRef, lastStopCode);
    const avgHistDelay = historicalDelays.length > 0 ? historicalDelays.reduce((s, d) => s + d, 0) / historicalDelays.length : null;

    const kalmanFilter = await this.getKalmanFilter(datedJourneyRef);
    const smoothedDelay = kalmanFilter.update(delayMinutes);

    let combinedDelay = smoothedDelay;
    if (avgHistDelay !== null) {
      const now = DateTime.now().setZone(TARGET_TIMEZONE);
      const isRushHour = ([7, 8, 9, 16, 17, 18].includes(now.hour) && now.weekday <= 5);
      const histWeight = isRushHour ? 0.5 : 0.3;
      combinedDelay = (smoothedDelay * (1 - histWeight)) + (avgHistDelay * histWeight);
    }

    await this.databaseManager.saveKalmanState(datedJourneyRef, kalmanFilter.variance, kalmanFilter.estimate);
    logDetailed('info', `[DelayPredictor] J:${datedJourneyRef.slice(-5)} Orig:${delayMinutes}, Smooth:${smoothedDelay.toFixed(1)}, Hist:${avgHistDelay?.toFixed(1)}, Combo:${combinedDelay.toFixed(1)}`);
    return { delay: Math.round(combinedDelay), confidence: delayMinutes > 60 ? 0.6 : 0.8 };
  }
}

/**
 * Delay Analyzer Service
 * Handles delay calculation, significance scoring and vehicle activity
 * analysis.
 */
export class DelayAnalyzer {
  private processingConfig: any;
  private appState: ApplicationState;
  private databaseManager!: DatabaseManager;
  private delayPredictor!: DelayPredictor;

  constructor(processingConfig: any, appState: ApplicationState) {
    this.processingConfig = processingConfig;
    this.appState = appState;

    logger.info('Delay Analyzer initialized', {
      lateThreshold: this.processingConfig.lateThreshold,
      earlyThreshold: this.processingConfig.earlyThreshold,
      significantThreshold: this.processingConfig.significantThreshold,
      maxJourneyAge: this.processingConfig.maxJourneyAge,
      timeWindow: this.processingConfig.timeWindow,
      maxDistance: this.processingConfig.maxDistance
    });
  }

  async initialize(): Promise<void> {
    logger.info('Delay Analyzer initializing...');
  }

  /**
   * Set the database manager used for timetable lookups.
   */
  setDatabaseManager(databaseManager: DatabaseManager): void {
    this.databaseManager = databaseManager;
    this.delayPredictor = new DelayPredictor(databaseManager);
  }

  /**
   * Batch-process activities to prevent event-loop starvation on the Pi.
   * Calls analyzeVehicleActivity() per item, yields between batches,
   * and enforces a hard time cap per cycle.
   */
  public async processActivities(
    activities: SIRIVehicleActivity[],
    counters?: any
  ): Promise<BusEvent[]> {
    const BATCH = 25;              // Pi-friendly
    const HARD_LIMIT_MS = 45_000;  // hard cap per cycle
    const start = Date.now();
    const events: BusEvent[] = [];

    for (let i = 0; i < activities.length; i += BATCH) {
      const batch = activities.slice(i, i + BATCH);

      for (const act of batch) {
        try {
          const evt = await this.analyzeVehicleActivity(act, counters);
          if (evt) events.push(evt);
        } catch (err) {
          logger.warn('analyze:processOne failed, skipping', { err: String(err) });
        }
      }

      // yield so the Pi doesn’t hard-freeze
      await yieldNow();

      if (Date.now() - start > HARD_LIMIT_MS) {
        logger.warn('analyze:cutoff after HARD_LIMIT_MS', { processed: i + batch.length, elapsed: Date.now() - start });
        break;
      }
    }

    logger.info('analyze:done', { processed: events.length, ms: Date.now() - start });
    return events;
  }

  /**
   * Analyze one vehicle activity and, where warranted, produce a BusEvent.
   * Direct-SIRI diagnostic path.
   */
  async analyzeVehicleActivity(activity: SIRIVehicleActivity, counters?: any): Promise<BusEvent | null> {
    const timer = new PerformanceTimer('analyze_vehicle_activity', logger);

    try {
      // Pi optimization: Check if data is stale BEFORE expensive database query
      const skipStaleData = process.env.DISABLE_STALE_DATA_PROCESSING?.toLowerCase() === 'true';
      if (skipStaleData) {
        const originAimedDepUtc = DateTime.fromISO(activity.originAimedDepartureTime, { zone: 'UTC' });
        const origDepLocal = originAimedDepUtc.setZone(TARGET_TIMEZONE);
        const journeyAgeHours = DateTime.now().setZone(TARGET_TIMEZONE).diff(origDepLocal, 'hours').hours;

        if (journeyAgeHours > this.processingConfig.maxJourneyAge) {
          if (counters) counters.staleData++;
          logDetailed('info', `🔧 Pi optimization: Skipping ${journeyAgeHours.toFixed(1)}h old journey BEFORE query. Route: ${activity.lineRef}`);
          return null;
        }
      }

      // Skip buses parked at depots — no point doing schedule lookups for them
      if (activity.vehicleLocation) {
        const depotName = this.checkDepot(activity.vehicleLocation.latitude, activity.vehicleLocation.longitude);
        if (depotName) {
          if (counters) counters.depotFiltered = (counters.depotFiltered || 0) + 1;
          logDetailed('info', `[DEPOT] ${activity.lineRef} (${activity.vehicleRef}) parked at ${depotName}, skipping`);
          return null;
        }
      }

      // Get schedule for this vehicle
      let actualSchedule = await this.databaseManager.querySchedule(
        activity.datedJourneyRef || null,
        'O1', // legacy operator code for the old timetable schema
        activity.lineRef.trim().replace(/__$/, ''),
        activity.directionRef.toLowerCase(),
        activity.originAimedDepartureTime
      );

      // Fuzzy fallback if the exact match fails
      if (!actualSchedule || actualSchedule.length === 0) {
        actualSchedule = await this.databaseManager.queryScheduleFuzzy(
          activity.lineRef.trim().replace(/__$/, ''),
          activity.directionRef.toLowerCase(),
          activity.originAimedDepartureTime
        );

        if (actualSchedule && actualSchedule.length > 0) {
          logSummary('info', `[FUZZY_MATCH] Recovered schedule for ${activity.lineRef} (trip ID fuzzy-matched via time window)`);
        }
      }

      if (!actualSchedule || actualSchedule.length === 0) {
        logger.debug(`[DATA_FILTER] No schedule found for route ${activity.lineRef}, journey ${activity.datedJourneyRef}, direction ${activity.directionRef}`);
        return null;
      }

      // Find closest stop using GPS matching
      let selectedStop: any = null;
      let minDistance = Infinity;
      let closestStopIndex = -1;

      if (activity.vehicleLocation) {
        for (let idx = 0; idx < actualSchedule.length; idx++) {
          const stop = actualSchedule[idx];
          if (stop.latitude != null && stop.longitude != null) {
            const distance = this.haversineDistance(
              activity.vehicleLocation.latitude,
              activity.vehicleLocation.longitude,
              stop.latitude,
              stop.longitude
            );
            if (distance < minDistance) {
              minDistance = distance;
              selectedStop = stop;
              closestStopIndex = idx;
            }
          }
        }
      }

      if (!selectedStop) {
        logger.warn(`Could not find a geographically closest stop for route ${activity.lineRef}.`);
        return null;
      }

      // Validate GPS distance
      if (minDistance > this.processingConfig.maxDistance) {
        if (counters) counters.gpsRejected++;
        logDetailed('warn', `[GPS_REJECT] Closest stop is too far away.`, {
          route: activity.lineRef,
          vehicleRef: activity.vehicleRef,
          closestStop: selectedStop.stop_name,
          distanceKm: minDistance.toFixed(2),
          maxDistanceKm: this.processingConfig.maxDistance
        });
        return null;
      }

      // Calculate delay
      const recordedAtLocal = DateTime.fromISO(activity.recordedAtTime, { zone: 'UTC' }).setZone(TARGET_TIMEZONE);
      const originAimedDepUtc = DateTime.fromISO(activity.originAimedDepartureTime, { zone: 'UTC' });
      const selectedSchedLocal = this.parseScheduleTimeLuxon(selectedStop.dep, originAimedDepUtc.setZone(TARGET_TIMEZONE));

      if (!selectedSchedLocal) {
        logger.warn(`Could not parse schedule time for stop ${selectedStop.stop_name} on route ${activity.lineRef}`);
        return null;
      }

      const rawDelay = Math.round(recordedAtLocal.diff(selectedSchedLocal, 'minutes').minutes);
      const origDepLocal = originAimedDepUtc.setZone(TARGET_TIMEZONE);

      // Check journey age
      const journeyAgeHours = DateTime.now().setZone(TARGET_TIMEZONE).diff(origDepLocal, 'hours').hours;
      if (journeyAgeHours > this.processingConfig.maxJourneyAge) {
        if (counters) counters.staleData++;
        logDetailed('warn', `[STALE_DATA] Ignoring ${journeyAgeHours.toFixed(1)}h old journey. Route: ${activity.lineRef}, Origin: ${origDepLocal.toFormat('HH:mm')}`);
        return null;
      }

      // Skip buses waiting at their first stop before departure — they're not early, just waiting
      if (closestStopIndex === 0 && rawDelay < 0) {
        if (counters) counters.waitingAtOrigin = (counters.waitingAtOrigin || 0) + 1;
        logDetailed('info', `[WAITING] ${activity.lineRef} (${activity.vehicleRef}) at first stop ${selectedStop.stop_name}, ${Math.abs(rawDelay)}min before departure — skipping`);
        return null;
      }

      // Check terminus status
      const isAtTerminus = this.appState.terminusStopNames.has(selectedStop.stop_name || '');
      const timeAtStop = recordedAtLocal.diff(selectedSchedLocal, 'minutes').minutes;
      if (isAtTerminus && timeAtStop > 30) {
        if (counters) counters.terminusFiltered++;
        logDetailed('warn', `[TERMINUS_FILTER] Bus at terminus (${selectedStop.stop_name}) for ${timeAtStop}min. Route: ${activity.lineRef}`);
        return null;
      }

      // Log successful schedule match
      logDetailed('info', `[SCHEDULE_MATCH] Route: ${activity.lineRef}, Journey: ${activity.datedJourneyRef}, ` +
        `Origin departure: ${origDepLocal.toFormat('yyyy-MM-dd HH:mm')}, ` +
        `Selected stop: ${selectedStop.stop_name} (${selectedStop.stop}), ` +
        `Scheduled: ${selectedSchedLocal?.toFormat('HH:mm')}, Actual: ${recordedAtLocal.toFormat('HH:mm')}, ` +
        `Raw delay: ${rawDelay} minutes, Current time: ${DateTime.now().setZone(TARGET_TIMEZONE).toFormat('HH:mm')}`);

      // Base record for prediction
      const baseRecord = {
        timestamp: activity.recordedAtTime,
        vehicleRef: activity.vehicleRef,
        datedJourneyRef: activity.datedJourneyRef || '',
        line: activity.lineRef,
        direction: activity.directionRef,
        originAimedDepartureTimeStr: activity.originAimedDepartureTime,
        delayMinutes: rawDelay,
        lastStopCode: selectedStop.stop,
        lastStopTime: selectedSchedLocal.toFormat('HH:mm:ss'),
        lastStopName: cleanStopName(selectedStop.stop_name, selectedStop.stop)
      };

      // Prediction
      const prediction = await this.delayPredictor.predictDelay(baseRecord);

      // Significance
      const eventAnalysis = this.calculateEventSignificance(prediction.delay, recordedAtLocal, counters);
      if (eventAnalysis.type === 'ignore') return null;

      // Bus details
      const busDetails = this.extractBusDetails(activity.vehicleRef || '');

      // Event
      const busEvent: BusEvent = {
        ...baseRecord,
        delayMinutes: prediction.delay,
        eventType: eventAnalysis.type,
        significance: eventAnalysis.score,
        busDetails: busDetails ?? undefined,
        location: activity.vehicleLocation ? {
          latitude: activity.vehicleLocation.latitude,
          longitude: activity.vehicleLocation.longitude
        } : undefined
      };

      timer.complete({
        route: activity.lineRef,
        eventType: eventAnalysis.type,
        significance: eventAnalysis.score,
        delayMinutes: prediction.delay,
        closestStopDistance: minDistance.toFixed(2)
      });

      return busEvent;

    } catch (error: any) {
      timer.fail(error);
      logger.error('Error analyzing vehicle activity', {
        error: error.message,
        vehicleRef: activity.vehicleRef,
        lineRef: activity.lineRef
      });
      return null;
    }
  }

  // Ignore extreme readings, which are more likely to be feed or match errors.
  calculateEventSignificance(delayMinutes: number, time: DateTime, counters?: any): { type: 'delay' | 'early' | 'punctual' | 'ignore', score: number } {
    if (delayMinutes > 30) {
      if (counters) counters.extremeFiltered++;
      logDetailed('warn', `[EXTREME] Excessive delay of ${delayMinutes} minutes. Filtering out.`);
      return { type: 'ignore', score: 0 };
    }
    if (delayMinutes < -15) {
      if (counters) counters.extremeFiltered++;
      logDetailed('warn', `[EXTREME] Excessive early arrival of ${Math.abs(delayMinutes)} minutes. Filtering out.`);
      return { type: 'ignore', score: 0 };
    }

    let score = 0;
    let type: 'delay' | 'early' | 'punctual' | 'ignore' = 'ignore';

    if (delayMinutes >= 4) {
      type = 'delay';
      if (delayMinutes >= 20) score += 5;       // 20-30 min: severe delay
      else if (delayMinutes >= 15) score += 4;   // 15-19 min: major delay
      else if (delayMinutes >= 10) score += 3;   // 10-14 min: significant delay
      else if (delayMinutes >= 7) score += 2;    // 7-9 min: moderate delay
      else if (delayMinutes >= 4) score += 1;    // 4-6 min: minor delay
    } else if (delayMinutes <= -3) {
      type = 'early';
      if (delayMinutes <= -10) score += 4;       // 10-15 min early: very notable
      else if (delayMinutes <= -7) score += 3;   // 7-9 min early: notable
      else if (delayMinutes <= -5) score += 2;   // 5-6 min early: moderate
      else if (delayMinutes <= -3) score += 1;   // 3-4 min early: minor
    } else {
      type = 'punctual';
      score = 1;
    }

    const hour = time.hour;
    if ([7, 8, 9, 17, 18, 19].includes(hour)) {
      score += 1;
    } else if (hour >= 22 || hour <= 6) {
      score += 1;
    }

    return { type, score: Math.max(0, score) };
  }

  updateDelayHistory(delayRecord: BusEvent): DelayHistory {
    const routeKey = `${delayRecord.line}_${delayRecord.direction}`;
    const currentTime = DateTime.now().setZone(TARGET_TIMEZONE);
    const previousHistory = this.appState.getDelayHistory(routeKey);

    let trend: 'worsening' | 'improving' | 'stable' | 'new' = 'new';
    let significantChange = false;
    let consecutiveReports = 1;

    if (previousHistory) {
      const delayDiff = delayRecord.delayMinutes - previousHistory.lastReportedDelay;
      consecutiveReports = previousHistory.consecutiveReports + 1;

      if (Math.abs(delayDiff) >= 5) {
        trend = delayDiff > 0 ? 'worsening' : 'improving';
        significantChange = true;
      } else {
        trend = 'stable';
      }
    }

    const reports = [
      ...(previousHistory?.reports || []),
      {
        timestamp: currentTime,
        delay: delayRecord.delayMinutes,
        location: delayRecord.lastStopName || '',
        significance: delayRecord.significance
      }
    ].slice(-20);

    // Preserve the previous accepted delay until reporting is decided.
    const history: DelayHistory = {
      routeId: routeKey,
      lastReportedDelay: previousHistory?.lastReportedDelay ?? delayRecord.delayMinutes,
      lastReportTime: previousHistory?.lastReportTime ?? currentTime,
      trend,
      consecutiveReports,
      significantChange,
      averageDelay: reports.reduce((sum, report) => sum + report.delay, 0) / reports.length,
      peakDelay: Math.max(...reports.map(report => report.delay)),
      reports
    };

    return history;
  }

  shouldReportDelay(delayRecord: BusEvent, history: DelayHistory, counters?: any): boolean {
    const currentTime = DateTime.now().setZone(TARGET_TIMEZONE);
    const timeSinceLastReport = currentTime.diff(history.lastReportTime, 'minutes').minutes;
    const finish = (accepted: boolean): boolean => {
      this.appState.updateDelayHistory(history.routeId, {
        ...history,
        lastReportedDelay: accepted ? delayRecord.delayMinutes : history.lastReportedDelay,
        lastReportTime: accepted ? currentTime : history.lastReportTime,
        significantChange: false
      });
      return accepted;
    };

    if (history.trend === 'new') {
      if (counters) counters.delaysAccepted++;
      logDetailed('info', `[DELAY_ACCEPT] Route ${delayRecord.line}: NEW delay (${delayRecord.delayMinutes}min) - first occurrence`);
      return finish(true);
    }

    if (history.significantChange) {
      if (counters) counters.delaysAccepted++;
      logDetailed('info', `[DELAY_ACCEPT] Route ${delayRecord.line}: SIGNIFICANT change (${history.lastReportedDelay}→${delayRecord.delayMinutes}min) - trend: ${history.trend}`);
      return finish(true);
    }

    if (history.lastReportedDelay >= this.processingConfig.lateThreshold && delayRecord.delayMinutes < this.processingConfig.lateThreshold) {
      if (counters) counters.delaysAccepted++;
      logDetailed('info', `[DELAY_ACCEPT] Route ${delayRecord.line}: RECOVERY (${history.lastReportedDelay}→${delayRecord.delayMinutes}min) - back to punctual`);
      return finish(true);
    }

    if (timeSinceLastReport < 25) {
      const delayIncrease = delayRecord.delayMinutes - history.lastReportedDelay;
      if (delayIncrease < 8) {
        if (counters) counters.delaysRejected++;
        logDetailed('info', `[DELAY_REJECT] Route ${delayRecord.line}: TOO_RECENT (last report ${timeSinceLastReport.toFixed(1)}min ago, increase only ${delayIncrease}min)`);
        return finish(false);
      } else {
        if (counters) counters.delaysAccepted++;
        logDetailed('info', `[DELAY_ACCEPT] Route ${delayRecord.line}: MAJOR_WORSENING (+${delayIncrease}min since last report)`);
        return finish(true);
      }
    }

    if (history.consecutiveReports > 1 && history.consecutiveReports % 2 === 0) {
      if (counters) counters.delaysAccepted++;
      logDetailed('info', `[DELAY_ACCEPT] Route ${delayRecord.line}: PERSISTENT (cycle ${history.consecutiveReports}, ${delayRecord.delayMinutes}min delay)`);
      return finish(true);
    }

    if (counters) counters.delaysRejected++;
    if (history.consecutiveReports > 1) {
      logDetailed('info', `[DELAY_REJECT] Route ${delayRecord.line}: PERSISTENT_SKIP (cycle ${history.consecutiveReports}, waiting for even cycle)`);
    } else {
      logDetailed('info', `[DELAY_REJECT] Route ${delayRecord.line}: DEFAULT_REJECT (${delayRecord.delayMinutes}min, trend: ${history.trend}, last report: ${timeSinceLastReport.toFixed(1)}min ago)`);
    }

    return finish(false);
  }

  private parseScheduleTimeLuxon(timeStr: string | null | undefined, originDep: DateTime): DateTime | null {
    if (!timeStr || !originDep?.isValid) return null;
    try {
      const [h, m, s = 0] = timeStr.split(':').map(p => parseInt(p, 10));
      return originDep.startOf('day').plus({ days: Math.floor(h / 24), hours: h % 24, minutes: m, seconds: s });
    } catch {
      return null;
    }
  }

  // Known depot locations: [name, lat, lon, radius in metres]
  private static readonly DEPOT_LOCATIONS: [string, number, number, number][] = [
    ['Lawrence Hill',        51.46067, -2.56622, 150],
    ['Hengrove',             51.4205,  -2.5868,  200],
    ['Bath (Weston Island)', 51.3820,  -2.3938,  150],
    ['Weston-super-Mare',    51.3423,  -2.9572,  200],
    ['Keynsham (Gypsy Ln)',  51.3920,  -2.4780,  150],
  ];

  private checkDepot(lat: number, lon: number): string | null {
    for (const [name, dLat, dLon, radiusM] of DelayAnalyzer.DEPOT_LOCATIONS) {
      const distKm = this.haversineDistance(lat, lon, dLat, dLon);
      if (distKm * 1000 <= radiusM) return name;
    }
    return null;
  }

  private haversineDistance(lat1: number, lon1: number, lat2: number, lon2: number): number {
    const R = 6371;
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * Math.sin(dLon / 2) ** 2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    }

  // public: the EventReader (collector-events ingest) reuses this lookup
  public extractBusDetails(vehicleRef: string): BusVehicleDetails | null {
    if (!vehicleRef || !this.appState.busDetailsLookup?.results) return null;
    const fleetMatch = vehicleRef.match(/(\d+)$/);
    if (!fleetMatch) return null;
    const fleetNumber = parseInt(fleetMatch[1]);
    for (const bus of this.appState.busDetailsLookup.results) {
      if (bus.fleet_number === fleetNumber) {
        return bus;
      }
    }
    return null;
  }

  async storeDelayReport(delay: BusEvent, history: DelayHistory): Promise<void> {
    try {
      await this.databaseManager.storeDelayReport(
        history.routeId,
        delay.delayMinutes,
        delay.lastStopName || null,
        history.trend
      );
    } catch (error: any) {
      logger.warn("Failed to store delay report", { err: error });
    }
  }

  getStatus(): any {
    return {
      name: 'Delay Analyzer',
      status: 'running',
      config: {
        lateThreshold: this.processingConfig.lateThreshold,
        earlyThreshold: this.processingConfig.earlyThreshold,
        significantThreshold: this.processingConfig.significantThreshold,
        maxJourneyAge: this.processingConfig.maxJourneyAge,
        timeWindow: this.processingConfig.timeWindow,
        maxDistance: this.processingConfig.maxDistance
      },
      kalmanStates: this.delayPredictor ? this.delayPredictor['kalmanStates'].size : 0,
      delayHistoryEntries: this.appState.delayHistoryMap.size
    };
  }

  async close(): Promise<void> {
    logger.info('Delay Analyzer service stopped');
  }
}
