// Bristol Bus Bot - Application State Management

import { DateTime } from 'luxon';
import { logger, TARGET_TIMEZONE } from '../utils/logging.js';
import type { BusEvent, DelayHistory, NetworkStatus, SystemMetrics, SIRIVehicleActivity } from '../types/bus-types.js';

/**
 * Application State Manager - Singleton
 * Central in-memory state shared across services: event collection,
 * daily counters, lookup data, delay history and dashboard metrics.
 */
export class ApplicationState {
    private static instance: ApplicationState;

    // Event collection and daily counters
    public busEventCollector: BusEvent[] = [];
    public lastSummaryPostTimestamp: number = 0;
    public postsTodayCount: number = 0;
    public lastResetDate: string;
    public aiCallsToday: number = 0;
    public lastAIResetDate: string;
    public summariesPosted: number = 0;
    
    // Data loading state
    public dbIsReloading: boolean = false;

    // Bus details and lookup data
    public busDetailsLookup: { results: any[] } = { results: [] };
    public routeDetails: { [routeNumber: string]: any } = {};  // Route/stop details for AI context
    public terminusStopNames: Set<string> = new Set();
    public zenComments: any = {};
    public wittyCommentData: { [key: string]: string[] } = { on_time: ["running normally."] };
    
    // Delay tracking
    public delayHistoryMap: Map<string, DelayHistory> = new Map();

    // Vehicle memory: keeps recently seen vehicles so brief feed dropouts
    // don't make dashboard/map entries flicker in and out.
    public vehicleMemory: Map<string, { activity: SIRIVehicleActivity, lastSeen: number }> = new Map();

    // Dashboard data - Two-step AI generation
    public lastAIPrompt: string | null = null;
    public lastAIResponse: string | null = null;  // Final response
    public lastAIDraftPrompt: string | null = null;  // Draft agent prompt
    public lastAIDraftOutput: string | null = null;  // Draft agent output (3 options)
    public lastAICriticPrompt: string | null = null;  // Critic agent prompt
    public lastAICriticOutput: string | null = null;  // Critic agent output (final selection)
    public lastWeatherContext: string | null = null;
    public recentRouteSummary: any[] = [];

    // Social media stats
    public blueskyFollowerCount: number = 0;
    public threadsFollowerCount: number = 0;
    public lastFollowerUpdate: DateTime | null = null;

    // Recent post history for variety tracking
    public recentPosts: string[] = [];
    public editorialContextStatus: {
        loaded: boolean;
        path: string;
        sha256: string | null;
        updated_at: string | null;
        counts: { facts: number; occasions: number; news: number };
        error?: string;
    } | null = null;

    // Rolling event window for persistent network statistics (survives collector clearing)
    private rollingEventWindow: Array<{ event: BusEvent, timestamp: number }> = [];
    private readonly ROLLING_WINDOW_MS = 60 * 60 * 1000; // 60 minutes

    // Filter statistics (updated each SIRI cycle for dashboard)
    public filterStats = {
        delaysAccepted: 0,
        delaysRejected: 0,
        staleData: 0,
        gpsRejected: 0,
        terminusFiltered: 0,
        extremeFiltered: 0,
        lastUpdated: DateTime.now()
    };

    // Activity log (last 20 SIRI cycles for dashboard)
    public activityLog: Array<{
        timestamp: string;
        activitiesProcessed: number;
        eventsCollected: number;
        filterStats: {
            accepted: number;
            rejected: number;
            stale: number;
            gps: number;
            terminus: number;
            extreme: number;
        };
    }> = [];

    // System metrics and monitoring
    private siriMetrics = {
        totalFetches: 0,
        totalResponseTime: 0,
        lastFetch: null as DateTime | null,
        averageResponseTime: 0
    };
    
    private errorMetrics = {
        total: 0,
        byType: new Map<string, number>(),
        lastError: null as Error | null
    };
    
    private constructor() {
        const currentDate = DateTime.now().setZone(TARGET_TIMEZONE).toISODate() || '';
        this.lastResetDate = currentDate;
        this.lastAIResetDate = currentDate;
        
        logger.info('Application State initialized', {
            currentDate,
            timezone: TARGET_TIMEZONE
        });
    }
    
    /**
     * Get singleton instance
     */
    public static getInstance(): ApplicationState {
        if (!ApplicationState.instance) {
            ApplicationState.instance = new ApplicationState();
        }
        return ApplicationState.instance;
    }
    
    /**
     * Add bus events to the collector and the rolling window used for
     * persistent network stats.
     */
    public addBusEvents(events: BusEvent[]): void {
        const previousSize = this.busEventCollector.length;
        this.busEventCollector.push(...events);
        const newSize = this.busEventCollector.length;

        // Add to rolling window with timestamp
        const now = Date.now();
        for (const event of events) {
            this.rollingEventWindow.push({ event, timestamp: now });
        }

        // Prune old events from rolling window
        this.pruneRollingWindow();

        if (events.length > 0) {
            logger.info(`[STATE_CHANGE] Event collector updated: ${previousSize} → ${newSize} (+${events.length} new events)`);
        }
    }

    /**
     * Prune old events from rolling window
     */
    private pruneRollingWindow(): void {
        const now = Date.now();
        const cutoff = now - this.ROLLING_WINDOW_MS;
        this.rollingEventWindow = this.rollingEventWindow.filter(item => item.timestamp > cutoff);
    }
    
    /**
     * Return all collected events and clear the collector.
     */
    public getAndClearBusEvents(): BusEvent[] {
        const events = this.busEventCollector;
        const previousSize = this.busEventCollector.length;
        this.busEventCollector = [];
        
        if (previousSize > 0) {
            logger.info(`[STATE_CHANGE] Event collector retrieved and cleared: ${previousSize} → 0 events`);
        }
        
        return events;
    }
    
    /**
     * Clear the bus event collector (used after summary posting).
     */
    public clearEventCollector(): void {
        const previousSize = this.busEventCollector.length;
        this.busEventCollector = [];
        logger.info(`[STATE_CHANGE] Event collector cleared: ${previousSize} → 0 events`);
    }
    
    /**
     * Update delay history for a route.
     */
    public updateDelayHistory(routeKey: string, history: DelayHistory): void {
        this.delayHistoryMap.set(routeKey, history);
        logger.debug(`Delay history updated for route ${routeKey}`, {
            trend: history.trend,
            consecutiveReports: history.consecutiveReports,
            lastDelay: history.lastReportedDelay
        });
    }
    
    /**
     * Get delay history for a route
     */
    public getDelayHistory(routeKey: string): DelayHistory | undefined {
        return this.delayHistoryMap.get(routeKey);
    }
    
    /**
     * Increment the daily post counter.
     */
    public incrementPostCount(): void {
        const previousCount = this.postsTodayCount;
        this.postsTodayCount++;
        logger.info(`[STATE_CHANGE] Daily posts: ${previousCount} → ${this.postsTodayCount}`);
    }
    
    /**
     * Increment the daily AI call counter (quota tracking).
     */
    public incrementAICallCount(): void {
        const previousCount = this.aiCallsToday;
        this.aiCallsToday++;
        logger.info(`[AI_QUOTA] Daily usage: ${this.aiCallsToday}`);
    }

    /**
     * Increment summary count
     */
    public incrementSummaryCount(): void {
        this.summariesPosted++;
        logger.info(`[STATE_CHANGE] Summaries posted: ${this.summariesPosted}`);
    }

    /**
     * Update filter statistics and activity log after SIRI cycle
     * For dashboard real-time monitoring
     */
    public updateActivityLog(activitiesProcessed: number, eventsCollected: number, filterStats: any): void {
        // Update current filter stats
        this.filterStats = {
            delaysAccepted: filterStats.delaysAccepted || 0,
            delaysRejected: filterStats.delaysRejected || 0,
            staleData: filterStats.staleData || 0,
            gpsRejected: filterStats.gpsRejected || 0,
            terminusFiltered: filterStats.terminusFiltered || 0,
            extremeFiltered: filterStats.extremeFiltered || 0,
            lastUpdated: DateTime.now()
        };

        // Add to activity log
        this.activityLog.unshift({
            timestamp: DateTime.now().setZone(TARGET_TIMEZONE).toISO() || '',
            activitiesProcessed,
            eventsCollected,
            filterStats: {
                accepted: filterStats.delaysAccepted || 0,
                rejected: filterStats.delaysRejected || 0,
                stale: filterStats.staleData || 0,
                gps: filterStats.gpsRejected || 0,
                terminus: filterStats.terminusFiltered || 0,
                extreme: filterStats.extremeFiltered || 0
            }
        });

        // Keep only last 20 cycles
        if (this.activityLog.length > 20) {
            this.activityLog = this.activityLog.slice(0, 20);
        }
    }
    
    /** Reset daily counters when the service date changes. */
    public resetDailyCounters(): void {
        const currentDate = DateTime.now().setZone(TARGET_TIMEZONE).toISODate();

        // A failed date conversion must not reset counters.
        if (!currentDate) {
            logger.error('[STATE_CHANGE] Failed to get current date for counter reset');
            return;
        }

        // Reset only when the service date changes.
        if (currentDate !== this.lastResetDate) {
            const previousPostCount = this.postsTodayCount;
            const previousAICount = this.aiCallsToday;

            logger.info(`[STATE_CHANGE] Date changed: ${this.lastResetDate} → ${currentDate}. Resetting daily counters.`);
            logger.info(`[STATE_CHANGE] Daily stats reset - Posts: ${previousPostCount} → 0, AI calls: ${previousAICount} → 0`);

            // Reset all daily counters atomically
            this.postsTodayCount = 0;
            this.aiCallsToday = 0;
            this.summariesPosted = 0;

            // Update date tracking (single field for both counters)
            this.lastResetDate = currentDate;
            this.lastAIResetDate = currentDate;

            logger.info('[STATE_CHANGE] All daily counters reset to 0');
        }
    }
    
    /**
     * Update the last summary post timestamp.
     */
    public updateSummaryTimestamp(): void {
        this.lastSummaryPostTimestamp = Date.now();
        logger.debug('Summary timestamp updated', {
            timestamp: new Date(this.lastSummaryPostTimestamp).toISOString()
        });
    }
    
    /**
     * Check whether the summary interval has elapsed.
     */
    public shouldPostSummary(summaryInterval: number): boolean {
        const now = Date.now();
        
        if (this.lastSummaryPostTimestamp === 0) {
            this.lastSummaryPostTimestamp = now;
            return false;
        }
        
        return (now - this.lastSummaryPostTimestamp) >= summaryInterval;
    }
    
    /**
     * Update SIRI fetch metrics
     */
    public updateSIRIMetrics(responseTime: number): void {
        this.siriMetrics.totalFetches++;
        this.siriMetrics.totalResponseTime += responseTime;
        this.siriMetrics.lastFetch = DateTime.now().setZone(TARGET_TIMEZONE);
        this.siriMetrics.averageResponseTime = 
            Math.round(this.siriMetrics.totalResponseTime / this.siriMetrics.totalFetches);
    }
    
    /**
     * Get SIRI fetch statistics
     */
    public getSIRIFetchCount(): number {
        return this.siriMetrics.totalFetches;
    }
    
    public getLastSIRIFetch(): DateTime | null {
        return this.siriMetrics.lastFetch;
    }
    
    public getAverageSIRIResponseTime(): number {
        return this.siriMetrics.averageResponseTime;
    }
    
    /**
     * Record system error
     */
    public recordError(errorType: string, error: Error): void {
        this.errorMetrics.total++;
        this.errorMetrics.byType.set(errorType, (this.errorMetrics.byType.get(errorType) || 0) + 1);
        this.errorMetrics.lastError = error;
        
        logger.error(`System error recorded: ${errorType}`, {
            error: error.message,
            totalErrors: this.errorMetrics.total,
            errorsOfType: this.errorMetrics.byType.get(errorType)
        });
    }
    
    /**
     * Get network status
     * Analyzes rolling event window for persistent network insights (survives event collector clearing)
     */
    public getNetworkStatus(): NetworkStatus {
        // Use rolling event window (last 60 min) instead of event collector which gets cleared
        // This provides persistent stats even after posting clears the collector
        const events = this.rollingEventWindow.map(item => item.event);
        const totalEvents = events.length;

        if (totalEvents === 0) {
            // Fallback: No events yet, return empty stats
            return {
                totalRoutes: 0,
                operatingRoutes: 0,
                delayedRoutes: 0,
                punctualRoutes: 0,
                averageDelay: 0,
                totalEvents: 0,
                lastUpdate: DateTime.now().setZone(TARGET_TIMEZONE).toISO() ?? '',
                coverage: { monitored: 0, total: 0, percentage: 0 },
                performance: {
                    onTime: 0,
                    delayed: 0,
                    early: 0,
                    percentages: { onTime: 0, delayed: 0, early: 0 }
                }
            };
        }

        // Calculate actual stats from real events
        const delayedEvents = events.filter(e => e.eventType === 'delay');
        const punctualEvents = events.filter(e => e.eventType === 'punctual');
        const earlyEvents = events.filter(e => e.eventType === 'early');

        const uniqueRoutes = new Set(events.map(e => e.line));
        const totalRoutes = uniqueRoutes.size;

        const averageDelay = delayedEvents.length > 0
            ? Math.round(delayedEvents.reduce((sum, e) => sum + e.delayMinutes, 0) / delayedEvents.length)
            : 0;

        return {
            totalRoutes,
            operatingRoutes: totalRoutes,
            delayedRoutes: new Set(delayedEvents.map(e => e.line)).size,
            punctualRoutes: new Set(punctualEvents.map(e => e.line)).size,
            averageDelay,
            totalEvents,
            lastUpdate: DateTime.now().setZone(TARGET_TIMEZONE).toISO() ?? '',
            coverage: {
                monitored: totalRoutes,
                total: totalRoutes,
                percentage: 100
            },
            performance: {
                onTime: punctualEvents.length,
                delayed: delayedEvents.length,
                early: earlyEvents.length,
                percentages: {
                    onTime: totalEvents > 0 ? Math.round((punctualEvents.length / totalEvents) * 100) : 0,
                    delayed: totalEvents > 0 ? Math.round((delayedEvents.length / totalEvents) * 100) : 0,
                    early: totalEvents > 0 ? Math.round((earlyEvents.length / totalEvents) * 100) : 0
                }
            }
        };
    }
    
    /**
     * Return system metrics for the dashboard.
     */
    public getSystemMetrics(): SystemMetrics {
        const networkStatus = this.getNetworkStatus();
        
        return {
            uptime: process.uptime(),
            totalEvents: this.busEventCollector.length,
            postsToday: this.postsTodayCount,
            aiCallsToday: this.aiCallsToday,
            summariesPosted: this.summariesPosted,
            averageResponseTime: this.siriMetrics.averageResponseTime,
            errorRate: this.errorMetrics.total > 0 ? this.errorMetrics.total / this.siriMetrics.totalFetches : 0,
            lastSiriUpdate: this.siriMetrics.lastFetch?.toISO() || '',
            databaseHealth: {
                timetableConnected: !this.dbIsReloading,
                appDataConnected: !this.dbIsReloading,
                lastHealthCheck: DateTime.now().setZone(TARGET_TIMEZONE).toISO() ?? ''
            },
            networkStatus,
            performance: {
                avgSiriResponseTime: this.siriMetrics.averageResponseTime,
                avgProcessingTime: 0, // Would be calculated from performance timers
                avgPostingTime: 0, // Would be calculated from social media timers
                memoryUsage: process.memoryUsage(),
                cpuUsage: process.cpuUsage()
            }
        };
    }
    
    /**
     * Get recent delay events within specified minutes
     */
    public getRecentDelayEvents(minutes: number): BusEvent[] {
        const cutoffTime = DateTime.now().setZone(TARGET_TIMEZONE).minus({ minutes });
        
        return this.busEventCollector.filter(event => {
            const eventTime = DateTime.fromISO(event.timestamp).setZone(TARGET_TIMEZONE);
            return event.eventType === 'delay' && eventTime >= cutoffTime;
        });
    }

    /**
     * Update system metrics (called periodically)
     */
    public updateMetrics(): void {
        // Reset daily counters if needed
        this.resetDailyCounters();
        
        // Log current state
        const metrics = this.getSystemMetrics();
        logger.debug('System metrics updated', {
            uptime: Math.round(metrics.uptime),
            totalEvents: metrics.totalEvents,
            postsToday: metrics.postsToday,
            aiCallsToday: metrics.aiCallsToday,
            averageResponseTime: metrics.averageResponseTime
        });
    }
    
    /**
     * Update vehicle memory with new activities. Tracks all vehicles from
     * the raw SIRI feed so brief dropouts don't cause map flickering.
     */
    public updateVehicleMemory(activities: SIRIVehicleActivity[]): void {
        const now = Date.now();
        activities.forEach(act => {
            if (act.vehicleRef) {
                this.vehicleMemory.set(act.vehicleRef, {
                    activity: act,
                    lastSeen: now
                });
            }
        });
    }

    /**
     * Prune vehicles not seen for maxAgeMs (default 10 minutes).
     */
    public pruneVehicleMemory(maxAgeMs: number = 600000): void {
        const now = Date.now();
        for (const [ref, data] of this.vehicleMemory.entries()) {
            if (now - data.lastSeen > maxAgeMs) {
                this.vehicleMemory.delete(ref);
            }
        }
    }

    /**
     * Get all tracked vehicles for dashboard/map display.
     */
    public getAllTrackedVehicles(): SIRIVehicleActivity[] {
        return Array.from(this.vehicleMemory.values()).map(v => v.activity);
    }

    /**
     * Get current state summary for logging
     */
    public getStateSummary(): any {
        return {
            eventCollectorSize: this.busEventCollector.length,
            postsToday: this.postsTodayCount,
            aiCallsToday: this.aiCallsToday,
            summariesPosted: this.summariesPosted,
            delayHistoryEntries: this.delayHistoryMap.size,
            busDetailsLoaded: this.busDetailsLookup.results.length,
            terminusStopsLoaded: this.terminusStopNames.size,
            lastResetDate: this.lastResetDate,
            dbIsReloading: this.dbIsReloading
        };
    }
}
