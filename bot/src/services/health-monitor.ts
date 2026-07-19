// Bristol Bus Bot - Health Monitor Service
// System health monitoring, daily counter resets and metrics collection.

import { DateTime } from 'luxon';
import * as os from 'os';
import { logger, PerformanceTimer, TARGET_TIMEZONE } from '../utils/logging.js';
import { ApplicationState } from './application-state.js';
import { DatabaseManager } from './database-manager.js';

/**
 * Health Monitor Service
 * Handles system health monitoring, daily counter resets, and metrics
 * collection.
 */
export class HealthMonitor {
    private appState: ApplicationState;
    private databaseManager: DatabaseManager | null = null;
    private healthCheckInterval: NodeJS.Timeout | null = null;
    
    constructor(appState: ApplicationState) {
        this.appState = appState;
        
        logger.info('Health Monitor service initialized', {
            nodeVersion: process.version,
            platform: process.platform,
            arch: process.arch
        });
    }
    
    /**
     * Initialize health monitor service
     */
    async initialize(): Promise<void> {
        // Start periodic health checks
        this.startPeriodicHealthChecks();

        this.logStartupHealth();
        
        logger.info('Health Monitor service ready', {
            healthCheckInterval: '5 minutes',
            dailyResetEnabled: true
        });
    }
    
    /**
     * Set the database manager used by health checks.
     */
    setDatabaseManager(databaseManager: DatabaseManager): void {
        this.databaseManager = databaseManager;
    }
    
    /**
     * Ask ApplicationState to check and reset its daily counters.
     */
    checkAndResetPostsTodayCounter(): void {
        const timer = new PerformanceTimer('daily_counter_reset', logger);

        try {
            this.appState.resetDailyCounters();

            timer.complete({
                delegatedToAppState: true
            });
            
        } catch (error: any) {
            timer.fail(error);
            logger.error('Error checking daily counter reset', {
                error: error.message
            });
        }
    }
    
    /**
     * Return health data for the API endpoint.
     */
    getHealthStatus(): any {
        const timer = new PerformanceTimer('health_status_check', logger);
        
        try {
            // Check and reset counters first
            this.checkAndResetPostsTodayCounter();
            
            const currentTime = DateTime.now().setZone(TARGET_TIMEZONE);
            const processUptime = Math.floor(process.uptime());
            const memUsage = process.memoryUsage();
            
            const healthData = {
                success: true,
                timestamp: currentTime.toISO(),
                status: 'running',
                uptime: {
                    process: processUptime,
                    system: Math.floor(os.uptime()),
                    formatted: this.formatUptime(processUptime)
                },
                memory: {
                    used: Math.round(memUsage.heapUsed / 1024 / 1024),
                    total: Math.round(memUsage.heapTotal / 1024 / 1024),
                    external: Math.round(memUsage.external / 1024 / 1024),
                    rss: Math.round(memUsage.rss / 1024 / 1024)
                },
                system: {
                    platform: process.platform,
                    arch: process.arch,
                    nodeVersion: process.version,
                    cpus: os.cpus().length,
                    loadAverage: os.loadavg(),
                    freeMemory: Math.round(os.freemem() / 1024 / 1024),
                    totalMemory: Math.round(os.totalmem() / 1024 / 1024)
                },
                application: {
                    dailyStats: {
                        postsToday: this.appState.postsTodayCount,
                        aiCallsToday: this.appState.aiCallsToday,
                        summariesPosted: this.appState.summariesPosted,
                        lastResetDate: this.appState.lastResetDate
                    },
                    state: {
                        busEventsCollected: this.appState.busEventCollector.length,
                        delayHistoryEntries: this.appState.delayHistoryMap.size,
                        dbIsReloading: this.appState.dbIsReloading,
                        terminusStopsLoaded: this.appState.terminusStopNames.size,
                        busDetailsLoaded: this.appState.busDetailsLookup?.results?.length || 0
                    }
                },
                database: this.databaseManager ? this.databaseManager.getDatabaseHealth() : null,
                lastHealthCheck: currentTime.toISO()
            };
            
            timer.complete({
                memoryUsedMB: healthData.memory.used,
                uptimeSeconds: processUptime,
                busEventsCollected: healthData.application.state.busEventsCollected
            });
            
            return healthData;
            
        } catch (error: any) {
            timer.fail(error);
            logger.error('Error getting health status', {
                error: error.message
            });
            
            return {
                success: false,
                timestamp: DateTime.now().setZone(TARGET_TIMEZONE).toISO() ?? '',
                status: 'error',
                error: error.message,
                details: {
                    stats: {
                        postsToday: this.appState.postsTodayCount,
                        aiCallsToday: this.appState.aiCallsToday
                    }
                }
            };
        }
    }
    
    /**
     * Log the startup health summary.
     */
    private logStartupHealth(): void {
        const startupTime = DateTime.now().setZone(TARGET_TIMEZONE);

        logger.info(`[STARTUP_HEALTH] Current time: ${startupTime.toFormat('yyyy-MM-dd HH:mm:ss zzz')}`);
        logger.info(`[STARTUP_HEALTH] Daily counters: Posts ${this.appState.postsTodayCount}, AI calls ${this.appState.aiCallsToday}`);
        logger.info(`[STARTUP_HEALTH] Event collector: ${this.appState.busEventCollector.length} events`);
        
        // Additional system health info
        const memUsage = process.memoryUsage();
        logger.info(`[STARTUP_HEALTH] Memory usage: ${Math.round(memUsage.heapUsed / 1024 / 1024)}MB heap, ${Math.round(memUsage.rss / 1024 / 1024)}MB RSS`);
        logger.info(`[STARTUP_HEALTH] System: ${os.platform()} ${os.arch()}, Node ${process.version}`);
    }
    
    /**
     * Start periodic health checks
     */
    private startPeriodicHealthChecks(): void {
        // Run health check every 5 minutes
        this.healthCheckInterval = setInterval(() => {
            this.performPeriodicHealthCheck();
        }, 5 * 60 * 1000);
        
        logger.info('Periodic health checks started (5 minute interval)');
    }
    
    /**
     * Perform periodic health check
     */
    private performPeriodicHealthCheck(): void {
        const timer = new PerformanceTimer('periodic_health_check', logger);
        
        try {
            const memUsage = process.memoryUsage();
            const uptimeHours = Math.floor(process.uptime() / 3600);
            
            // Check for potential memory leaks
            const heapUsedMB = Math.round(memUsage.heapUsed / 1024 / 1024);
            if (heapUsedMB > 500) {
                logger.warn(`[HEALTH_WARNING] High memory usage: ${heapUsedMB}MB heap`);
            }
            
            // Check daily counter reset
            this.checkAndResetPostsTodayCounter();
            
            // Log periodic health summary
            logger.info(`[PERIODIC_HEALTH] Uptime: ${this.formatUptime(process.uptime())}, Memory: ${heapUsedMB}MB, Events: ${this.appState.busEventCollector.length}`);
            
            timer.complete({
                uptimeHours,
                heapUsedMB,
                busEvents: this.appState.busEventCollector.length,
                dailyPosts: this.appState.postsTodayCount
            });
            
        } catch (error: any) {
            timer.fail(error);
            logger.error('Error in periodic health check', {
                error: error.message
            });
        }
    }
    
    /**
     * Format uptime in human-readable format
     */
    private formatUptime(seconds: number): string {
        const days = Math.floor(seconds / (24 * 3600));
        const hours = Math.floor((seconds % (24 * 3600)) / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        const secs = Math.floor(seconds % 60);
        
        if (days > 0) {
            return `${days}d ${hours}h ${minutes}m ${secs}s`;
        } else if (hours > 0) {
            return `${hours}h ${minutes}m ${secs}s`;
        } else if (minutes > 0) {
            return `${minutes}m ${secs}s`;
        } else {
            return `${secs}s`;
        }
    }
    
    /**
     * Get memory usage analysis
     */
    getMemoryAnalysis(): any {
        const memUsage = process.memoryUsage();
        const systemMem = {
            free: os.freemem(),
            total: os.totalmem()
        };
        
        return {
            process: {
                heapUsed: Math.round(memUsage.heapUsed / 1024 / 1024),
                heapTotal: Math.round(memUsage.heapTotal / 1024 / 1024),
                external: Math.round(memUsage.external / 1024 / 1024),
                rss: Math.round(memUsage.rss / 1024 / 1024),
                heapUtilization: Math.round((memUsage.heapUsed / memUsage.heapTotal) * 100)
            },
            system: {
                free: Math.round(systemMem.free / 1024 / 1024),
                total: Math.round(systemMem.total / 1024 / 1024),
                used: Math.round((systemMem.total - systemMem.free) / 1024 / 1024),
                utilization: Math.round(((systemMem.total - systemMem.free) / systemMem.total) * 100)
            },
            warnings: this.getMemoryWarnings(memUsage, systemMem)
        };
    }
    
    /**
     * Get memory warnings
     */
    private getMemoryWarnings(processMemory: NodeJS.MemoryUsage, systemMemory: { free: number; total: number }): string[] {
        const warnings: string[] = [];
        
        const heapUsedMB = Math.round(processMemory.heapUsed / 1024 / 1024);
        const rssMB = Math.round(processMemory.rss / 1024 / 1024);
        const systemUtilization = ((systemMemory.total - systemMemory.free) / systemMemory.total) * 100;
        
        if (heapUsedMB > 500) {
            warnings.push(`High heap usage: ${heapUsedMB}MB`);
        }
        
        if (rssMB > 1000) {
            warnings.push(`High RSS usage: ${rssMB}MB`);
        }
        
        if (systemUtilization > 90) {
            warnings.push(`High system memory utilization: ${systemUtilization.toFixed(1)}%`);
        }
        
        return warnings;
    }
    
    /**
     * Get application performance metrics
     */
    getPerformanceMetrics(): any {
        const cpuUsage = process.cpuUsage();
        const loadAvg = os.loadavg();
        
        return {
            cpu: {
                user: Math.round(cpuUsage.user / 1000), // Convert to milliseconds
                system: Math.round(cpuUsage.system / 1000),
                total: Math.round((cpuUsage.user + cpuUsage.system) / 1000)
            },
            system: {
                loadAverage: {
                    '1m': loadAvg[0].toFixed(2),
                    '5m': loadAvg[1].toFixed(2),
                    '15m': loadAvg[2].toFixed(2)
                },
                cpuCount: os.cpus().length
            },
            application: {
                uptime: Math.floor(process.uptime()),
                eventLoopDelay: 0, // Could be enhanced with async_hooks
                activeHandles: (process as any)._getActiveHandles?.()?.length || 0,
                activeRequests: (process as any)._getActiveRequests?.()?.length || 0
            }
        };
    }
    
    /**
     * Get service status
     */
    getStatus(): any {
        const health = this.getHealthStatus();
        
        return {
            name: 'Health Monitor',
            status: health.success ? 'running' : 'error',
            lastCheck: health.timestamp,
            config: {
                healthCheckInterval: '5 minutes',
                dailyResetEnabled: true,
                memoryWarningThreshold: '500MB'
            },
            metrics: {
                uptime: health.uptime,
                memory: health.memory,
                dailyStats: health.application.dailyStats
            }
        };
    }
    
    /**
     * Close service and cleanup resources
     */
    async close(): Promise<void> {
        if (this.healthCheckInterval) {
            clearInterval(this.healthCheckInterval);
            this.healthCheckInterval = null;
        }
        
        logger.info('Health Monitor service stopped', {
            finalUptime: this.formatUptime(process.uptime()),
            finalMemoryUsage: Math.round(process.memoryUsage().heapUsed / 1024 / 1024) + 'MB'
        });
    }
}
