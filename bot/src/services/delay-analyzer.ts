// Significance scoring and freshness rules for collector events. Delays come
// from the collector as observed; nothing here smooths or predicts them.

import { DateTime } from 'luxon';
import { logger, PerformanceTimer, TARGET_TIMEZONE, logSummary, logDetailed, logAlways } from '../utils/logging.js';
import { ApplicationState } from './application-state.js';
import { resolveFleetVehicle } from './vehicle-identity.js';
import type { DatabaseManager } from './database-manager.js';
import type {
  BusEvent,
  DelayHistory,
  BusVehicleDetails
} from '../types/bus-types.js';

/**
 * Delay Analyzer Service
 * Scores the significance of collector events and keeps the per-route
 * history used to decide whether a delay is worth reporting.
 */
export class DelayAnalyzer {
  private processingConfig: any;
  private appState: ApplicationState;
  private databaseManager!: DatabaseManager;

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

  // public: the EventReader (collector-events ingest) reuses this lookup
  public extractBusDetails(vehicleRef: string, operatorRef = ''): BusVehicleDetails | null {
    return resolveFleetVehicle(
      this.appState.busDetailsLookup, vehicleRef, operatorRef,
    ) as BusVehicleDetails | null;
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
      delayHistoryEntries: this.appState.delayHistoryMap.size
    };
  }

  async close(): Promise<void> {
    logger.info('Delay Analyzer service stopped');
  }
}
