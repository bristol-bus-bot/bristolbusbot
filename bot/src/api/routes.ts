// Bristol Bus Bot - Express API routes: health monitoring, logs and
// authenticated runtime control. Loopback-only in production.

import type { Request, Response, NextFunction } from 'express';
import { Router } from 'express';
import { exec, execFile } from 'child_process';
import { timingSafeEqual } from 'crypto';
import { logger, PerformanceTimer } from '../utils/logging.js';
import { ApplicationState } from '../services/application-state.js';
import { HealthMonitor } from '../services/health-monitor.js';
import { DatabaseManager } from '../services/database-manager.js';

/**
 * Express API routes service: health, logs, runtime control and
 * database reload. Control endpoints require the bearer token.
 */
export class APIRoutes {
    private router: Router;
    private appState: ApplicationState;
    private healthMonitor: HealthMonitor;
    private databaseManager: DatabaseManager | null = null;
    private socialMediaManager: any = null;
    private aiCommentary: any = null;
    private readonly systemdUnit = 'bbb-bot.service';
    private authToken: string | null;

    constructor(appState: ApplicationState, healthMonitor: HealthMonitor) {
        this.router = Router();
        this.appState = appState;
        this.healthMonitor = healthMonitor;
        this.authToken = process.env.API_AUTH_TOKEN || null;

        if (!this.authToken) {
            logger.warn('API_AUTH_TOKEN not set — control endpoints are disabled until it is configured.');
        }

        this.initializeRoutes();

        logger.info('API Routes service initialized', {
            runtimeManager: 'systemd',
            serviceName: this.systemdUnit,
            routesCount: this.getRouteCount(),
            authEnabled: !!this.authToken
        });
    }

    /**
     * Middleware to authenticate control requests using a Bearer token.
     * Missing server configuration fails closed.
     */
    private requireAuth(req: Request, res: Response, next: NextFunction): void {
        // Control endpoints are unavailable unless a token is configured.
        if (!this.authToken) {
            logger.warn('Control endpoint refused: API_AUTH_TOKEN not set', { path: req.path });
            res.status(503).json({
                success: false,
                error: 'Control endpoints disabled: API_AUTH_TOKEN is not configured on the server.'
            });
            return;
        }

        const authHeader = req.headers.authorization;
        if (!authHeader || !authHeader.startsWith('Bearer ')) {
            logger.warn('Unauthorized API request', { path: req.path, ip: req.ip });
            res.status(401).json({ success: false, error: 'Missing or invalid Authorization header. Use: Bearer <token>' });
            return;
        }

        const token = authHeader.slice(7);
        const supplied = Buffer.from(token);
        const expected = Buffer.from(this.authToken);
        if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) {
            logger.warn('Invalid API token', { path: req.path, ip: req.ip });
            res.status(403).json({ success: false, error: 'Invalid API token' });
            return;
        }

        next();
    }
    
    /**
     * Initialize all API routes
     */
    private initializeRoutes(): void {
        // Health endpoint - unauthenticated so external monitors can poll it
        this.router.get('/api/health', this.handleHealthCheck.bind(this));

        // Logs endpoint — protected by auth token
        this.router.get('/api/logs', this.requireAuth.bind(this), this.handleLogs.bind(this));
        
        // Runtime control endpoint.
        this.router.post('/api/restart', this.requireAuth.bind(this), this.handleRestart.bind(this));

        // Database reload endpoint — protected by auth token
        this.router.post('/api/reload-db', this.requireAuth.bind(this), this.handleReloadDatabase.bind(this));

        // Dashboard endpoints.
        this.router.get('/api/dashboard/status', this.requireAuth.bind(this), this.handleDashboardStatus.bind(this));
        this.router.get('/api/dashboard/prompt', this.requireAuth.bind(this), this.handleDashboardPrompt.bind(this));
        this.router.get('/api/dashboard/routes', this.requireAuth.bind(this), this.handleDashboardRoutes.bind(this));
        this.router.get('/api/dashboard/activity', this.requireAuth.bind(this), this.handleDashboardActivity.bind(this));
        this.router.get('/api/dashboard/collector', this.requireAuth.bind(this), this.handleDashboardCollector.bind(this));
        this.router.get('/api/dashboard/locations', this.requireAuth.bind(this), this.handleDashboardLocations.bind(this));
        this.router.get('/api/dashboard/metrics', this.requireAuth.bind(this), this.handleDashboardMetrics.bind(this));
        this.router.get('/api/dashboard/system', this.requireAuth.bind(this), this.handleDashboardSystem.bind(this));

        // Recent posts - for live-buses integration
        this.router.get('/api/recent-posts', this.handleRecentPosts.bind(this));

        // Test endpoint - trigger posting immediately — protected by auth token
        this.router.post('/api/test/post-now', this.requireAuth.bind(this), this.handleTestPostNow.bind(this));
    }
    
    /**
     * Set the database manager used by API actions.
     */
    setDatabaseManager(databaseManager: DatabaseManager): void {
        this.databaseManager = databaseManager;
    }

    /**
     * Set social media manager reference (for test endpoint)
     */
    setSocialMediaManager(socialMediaManager: any): void {
        this.socialMediaManager = socialMediaManager;
    }

    /**
     * Set AI commentary reference (for dashboard config access)
     */
    setAICommentary(aiCommentary: any): void {
        this.aiCommentary = aiCommentary;
    }
    
    /** Process-native health; the service manager is checked externally. */
    private handleHealthCheck(req: Request, res: Response): void {
        const timer = new PerformanceTimer('api_health_check', logger);

        try {
            this.healthMonitor.checkAndResetPostsTodayCounter();
            const healthData = this.healthMonitor.getHealthStatus();
            const success = healthData.success === true;
            const memory = process.memoryUsage();
            const payload = {
                success,
                runtime: 'systemd',
                service_name: this.systemdUnit,
                status: success ? 'online' : 'degraded',
                details: {
                    stats: {
                        postsToday: this.appState.postsTodayCount,
                        aiCallsToday: this.appState.aiCallsToday
                    },
                    process: {
                        pid: process.pid,
                        uptime_seconds: Math.floor(process.uptime()),
                        node_version: process.version,
                        rss_bytes: memory.rss
                    },
                    healthData
                }
            };
            timer.complete({
                runtime: 'systemd',
                status: payload.status,
                postsToday: this.appState.postsTodayCount,
                aiCallsToday: this.appState.aiCallsToday
            });
            res.status(success ? 200 : 503).json(payload);
        } catch (error: any) {
            timer.fail(error);
            logger.error('Error in health check endpoint', { error: error.message });
            res.status(500).json({
                success: false,
                runtime: 'systemd',
                service_name: this.systemdUnit,
                status: 'error',
                error: error.message,
                details: {
                    stats: {
                        postsToday: this.appState.postsTodayCount,
                        aiCallsToday: this.appState.aiCallsToday
                    }
                }
            });
        }
    }
    
    /** Return recent service logs from journald. */
    private handleLogs(req: Request, res: Response): void {
        const timer = new PerformanceTimer('api_logs', logger);
        
        try {
            // Bound caller-controlled input and pass it as an argv value.
            const requested = parseInt(String(req.query.lines ?? '200'), 10);
            const lines = Number.isFinite(requested)
                ? Math.min(Math.max(requested, 1), 5000)
                : 200;

            const logCommand = 'journalctl';
            const logArgs = ['-u', this.systemdUnit, '-n', String(lines),
                             '--no-pager', '--output=short-iso'];
            // argv form means no caller-controlled text is parsed by a shell.
            execFile(logCommand, logArgs,
                { maxBuffer: 1024 * 1024 * 5 },
                (error, stdout, stderr): void => {
                    if (error) {
                        timer.fail(error);
                        res.status(500).json({ 
                            success: false, 
                            error: 'Failed to read systemd logs.',
                            details: stderr || error.message 
                        });
                        return;
                    }
                    
                    try {
                        // Each line is either structured JSON or raw text;
                        // wrap raw lines so the dashboard renders both.
                        const logs = stdout.trim().split('\\n').map(line => {
                            if (!line.trim()) return null;
                            try { 
                                return JSON.parse(line); 
                            } catch (e) { 
                                return { level: 30, time: Date.now(), msg: line, type: 'raw' }; 
                            }
                        });
                        
                        const filteredLogs = logs.filter(Boolean);
                        
                        timer.complete({
                            linesRequested: lines,
                            logsReturned: filteredLogs.length
                        });
                        
                        res.json(filteredLogs);
                        
                    } catch (e: any) { 
                        timer.fail(e);
                        res.status(500).json({ 
                            error: 'Internal server error while processing logs.', 
                            details: e.message 
                        }); 
                    }
                }
            );
            
        } catch (error: any) {
            timer.fail(error);
            logger.error('Error in logs endpoint', { error: error.message });
            res.status(500).json({
                success: false,
                error: error.message
            });
        }
    }
    
    /** Restart the managed process. */
    private handleRestart(req: Request, res: Response): void {
        res.json({
            success: true,
            message: 'Restart acknowledged; systemd will recover the service.'
        });
        setTimeout(() => process.kill(process.pid, 'SIGTERM'), 2000);
    }

    /**
     * Reload the timetable database and dependent caches.
     */
    private async handleReloadDatabase(req: Request, res: Response, next: NextFunction): Promise<void> {
        const timer = new PerformanceTimer('api_reload_database', logger);
        
        try {
            logger.info('API: Received request to reload database.');
            
            // Refuse concurrent reloads; the flag is cleared by the reload itself.
            if (this.appState.dbIsReloading) {
                timer.fail(new Error('Database reload already in progress'));
                res.status(409).json({ 
                    success: false, 
                    message: 'Conflict: A database reload is already in progress.' 
                }); 
                return; 
            }
            
            if (!this.databaseManager) {
                throw new Error('Database manager not available');
            }
            
            await this.databaseManager.reloadTimetableData();
            
            timer.complete({ reloadSuccessful: true });
            
            res.json({ 
                success: true, 
                message: 'Database and dependent data reloaded successfully.' 
            });
            
        } catch (error: any) { 
            timer.fail(error);
            res.status(500).json({ 
                success: false, 
                message: `Failed to reload database: ${error.message}` 
            }); 
        }
    }
    
    /**
     * Get the Express router with all routes configured
     */
    getRouter(): Router {
        return this.router;
    }
    
    /**
     * Dashboard status endpoint - overview of bot activity
     */
    private handleDashboardStatus(req: Request, res: Response): void {
        try {
            // Get AI daily limit from AI service config if available
            const aiDailyLimit = this.aiCommentary?.getConfig?.()?.dailyLimit ||
                                parseInt(process.env.AI_DAILY_LIMIT || '200', 10);

            const status = {
                timestamp: new Date().toISOString(),
                stats: {
                    postsToday: this.appState.postsTodayCount,
                    aiCallsToday: this.appState.aiCallsToday,
                    aiDailyLimit: aiDailyLimit,
                    eventsInCollector: this.appState.busEventCollector.length,
                    summariesPosted: this.appState.summariesPosted
                },
                lastActivity: {
                    lastAIPrompt: this.appState.lastAIPrompt ? 'Available' : 'None',
                    lastWeatherContext: this.appState.lastWeatherContext || 'Not fetched',
                    recentRoutes: this.appState.recentRouteSummary.length
                }
            };
            res.json(status);
        } catch (error: any) {
            res.status(500).json({ error: error.message });
        }
    }

    /**
     * Return the latest commentary draft and review details.
     */
    private handleDashboardPrompt(req: Request, res: Response): void {
        try {
            res.json({
                timestamp: new Date().toISOString(),
                // Compatibility fields consumed by existing dashboard clients.
                prompt: this.appState.lastAIPrompt || 'No prompts yet',
                response: this.appState.lastAIResponse || 'No responses yet',
                // Draft and review details used by the current dashboard.
                draft: {
                    prompt: this.appState.lastAIDraftPrompt || 'No draft prompt yet',
                    output: this.appState.lastAIDraftOutput || 'No draft output yet'
                },
                critic: {
                    prompt: this.appState.lastAICriticPrompt || 'No critic prompt yet',
                    output: this.appState.lastAICriticOutput || 'No critic output yet'
                },
                weatherContext: this.appState.lastWeatherContext || 'No weather data'
            });
        } catch (error: any) {
            res.status(500).json({ error: error.message });
        }
    }

    /**
     * Return route summaries from the event collector.
     */
    private handleDashboardRoutes(req: Request, res: Response): void {
        try {
            // Use the full current event set for route summaries.
            const allEvents = this.appState.busEventCollector || [];

            // Map to route summaries
            const routes = allEvents.map((event: any) => ({
                line: event.line,
                direction: event.direction,
                lastStopName: event.lastStopName,
                delayMinutes: event.delayMinutes,
                eventType: event.eventType,
                timestamp: event.timestamp
            }));

            res.json({
                timestamp: new Date().toISOString(),
                routes: routes,
                count: routes.length
            });
        } catch (error: any) {
            res.status(500).json({ error: error.message });
        }
    }

    /**
     * Dashboard activity endpoint - shows recent SIRI cycles with filter stats
     */
    private handleDashboardActivity(req: Request, res: Response): void {
        try {
            res.json({
                timestamp: new Date().toISOString(),
                activityLog: this.appState.activityLog,
                currentFilterStats: this.appState.filterStats
            });
        } catch (error: any) {
            res.status(500).json({ error: error.message });
        }
    }

    /**
     * Dashboard collector endpoint - shows event collector with journey grouping
     */
    private handleDashboardCollector(req: Request, res: Response): void {
        try {
            const events = this.appState.busEventCollector || [];

            // Group events by vehicleRef for journey tracking
            const journeyMap = new Map<string, any[]>();

            events.forEach(event => {
                const vehicleRef = event.vehicleRef || 'unknown';
                if (!journeyMap.has(vehicleRef)) {
                    journeyMap.set(vehicleRef, []);
                }
                journeyMap.get(vehicleRef)!.push(event);
            });

            // Convert to array with journey info
            const journeys = Array.from(journeyMap.entries()).map(([vehicleRef, events]) => {
                const busDetails = this.extractBusDetails(vehicleRef);

                return {
                    vehicleRef,
                    line: events[0]?.line || 'Unknown',
                    direction: events[0]?.direction || 'Unknown',
                    eventCount: events.length,
                    latestDelay: events[events.length - 1]?.delayMinutes || 0,
                    latestStop: events[events.length - 1]?.lastStopName || 'Unknown',
                    eventType: events[events.length - 1]?.eventType || 'unknown',
                    firstSeen: events[0]?.timestamp,
                    lastSeen: events[events.length - 1]?.timestamp,
                    // Livery data for journey card styling
                    livery: busDetails?.livery || null,
                    fleetNumber: busDetails?.fleet_number || null,
                    model: busDetails?.vehicle?.name || null,
                    events: events.map(e => ({
                        stopName: e.lastStopName,
                        delayMinutes: e.delayMinutes,
                        eventType: e.eventType,
                        timestamp: e.timestamp,
                        significance: e.significance
                    }))
                };
            });

            // Sort by most recent activity
            journeys.sort((a, b) => {
                const aTime = new Date(a.lastSeen || 0).getTime();
                const bTime = new Date(b.lastSeen || 0).getTime();
                return bTime - aTime;
            });

            res.json({
                timestamp: new Date().toISOString(),
                totalEvents: events.length,
                totalJourneys: journeys.length,
                journeys: journeys
            });
        } catch (error: any) {
            res.status(500).json({ error: error.message });
        }
    }

    /**
     * Extract bus details from vehicle reference
     * Looks up fleet number in busDetailsLookup to get livery and other bus info
     */
    private extractBusDetails(vehicleRef: string): any | null {
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

    /**
     * Dashboard locations endpoint - returns all current bus locations with GPS coordinates
     * For map visualization of active buses
     */
    private handleDashboardLocations(req: Request, res: Response): void {
        try {
            const events = this.appState.busEventCollector || [];

            // Create a map of latest location for each vehicle
            const vehicleLocationMap = new Map<string, any>();

            events.forEach(event => {
                if (event.location && event.location.latitude && event.location.longitude) {
                    const vehicleRef = event.vehicleRef || 'unknown';

                    // Only keep the most recent location for each vehicle
                    const existing = vehicleLocationMap.get(vehicleRef);
                    if (!existing || new Date(event.timestamp) > new Date(existing.timestamp)) {
                        const busDetails = this.extractBusDetails(vehicleRef);

                        vehicleLocationMap.set(vehicleRef, {
                            vehicleRef,
                            line: event.line,
                            direction: event.direction,
                            latitude: event.location.latitude,
                            longitude: event.location.longitude,
                            delayMinutes: event.delayMinutes,
                            eventType: event.eventType,
                            lastStopName: event.lastStopName,
                            timestamp: event.timestamp,
                            // Livery data for map marker styling
                            livery: busDetails?.livery || null,
                            fleetNumber: busDetails?.fleet_number || null,
                            model: busDetails?.vehicle?.name || null
                        });
                    }
                }
            });

            // Convert map to array
            const locations = Array.from(vehicleLocationMap.values());

            res.json({
                timestamp: new Date().toISOString(),
                totalBuses: locations.length,
                locations: locations
            });
        } catch (error: any) {
            res.status(500).json({ error: error.message });
        }
    }

    /**
     * Dashboard system and network metrics.
     */
    private handleDashboardMetrics(req: Request, res: Response): void {
        try {
            const systemMetrics = this.appState.getSystemMetrics();
            const networkStatus = this.appState.getNetworkStatus();

            res.json({
                timestamp: new Date().toISOString(),
                system: {
                    uptime: Math.round(systemMetrics.uptime),
                    uptimeFormatted: this.formatUptime(systemMetrics.uptime),
                    avgSiriResponseTime: systemMetrics.performance.avgSiriResponseTime,
                    totalSiriFetches: this.appState.getSIRIFetchCount(),
                    lastSiriFetch: this.appState.getLastSIRIFetch()?.toISO() || 'Never',
                    memoryUsage: {
                        heapUsed: Math.round(systemMetrics.performance.memoryUsage.heapUsed / 1024 / 1024),
                        heapTotal: Math.round(systemMetrics.performance.memoryUsage.heapTotal / 1024 / 1024),
                        rss: Math.round(systemMetrics.performance.memoryUsage.rss / 1024 / 1024)
                    }
                },
                network: {
                    totalRoutes: networkStatus.totalRoutes,
                    operatingRoutes: networkStatus.operatingRoutes,
                    delayedRoutes: networkStatus.delayedRoutes,
                    punctualRoutes: networkStatus.punctualRoutes,
                    averageDelay: networkStatus.averageDelay,
                    coverage: networkStatus.coverage,
                    performance: networkStatus.performance
                },
                stats: {
                    totalEvents: systemMetrics.totalEvents,
                    postsToday: systemMetrics.postsToday,
                    aiCallsToday: systemMetrics.aiCallsToday,
                    summariesPosted: systemMetrics.summariesPosted
                }
            });
        } catch (error: any) {
            res.status(500).json({ error: error.message });
        }
    }

    /**
     * Format uptime in human-readable format
     */
    private formatUptime(seconds: number): string {
        const days = Math.floor(seconds / 86400);
        const hours = Math.floor((seconds % 86400) / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);

        if (days > 0) return `${days}d ${hours}h ${minutes}m`;
        if (hours > 0) return `${hours}h ${minutes}m`;
        return `${minutes}m`;
    }

    /**
     * Dashboard system endpoint - Pi hardware metrics (CPU temp, etc)
     */
    private handleDashboardSystem(req: Request, res: Response): void {
        try {
            // Execute vcgencmd to get CPU temperature
            exec('vcgencmd measure_temp', (error, stdout, stderr) => {
                if (error) {
                    res.status(500).json({
                        error: 'Failed to read CPU temperature',
                        details: stderr || error.message
                    });
                    return;
                }

                // Parse temperature from output (format: "temp=46.2'C")
                const tempMatch = stdout.match(/temp=([\d.]+)/);
                const cpuTemp = tempMatch ? parseFloat(tempMatch[1]) : null;

                res.json({
                    timestamp: new Date().toISOString(),
                    cpuTemp: cpuTemp,
                    cpuTempFormatted: cpuTemp ? `${cpuTemp.toFixed(1)}°C` : 'N/A',
                    throttled: cpuTemp && cpuTemp > 80 // Pi throttles at 80°C
                });
            });
        } catch (error: any) {
            res.status(500).json({ error: error.message });
        }
    }

    /**
     * Recent posts - returns recent posts with vehicle refs and Bluesky URLs
     * Used by bristol-live-buses for highlighting posted buses on the map
     */
    private async handleRecentPosts(req: Request, res: Response): Promise<void> {
        try {
            if (!this.databaseManager) {
                res.status(500).json({ error: 'Database not available' });
                return;
            }

            const limit = Math.min(parseInt(req.query.limit as string) || 5, 20);
            const posts = await this.databaseManager.getRecentPosts(limit);

            // Build web URLs from AT Protocol URIs
            const handle = this.socialMediaManager?.getHandle() || 'bristolbusbot.live';
            const enriched = posts.map(p => {
                const rkey = p.postUri.split('/').pop() || '';
                return {
                    vehicleRef: p.vehicleRef,
                    postUrl: rkey ? `https://bsky.app/profile/${handle}/post/${rkey}` : null,
                    postContent: p.postContent,
                    postType: p.postType,
                    timestamp: p.timestamp
                };
            });

            res.json({ posts: enriched });
        } catch (error: any) {
            logger.error('Error handling recent-posts request', { error: error.message });
            res.status(500).json({ error: 'Internal error' });
        }
    }

    /**
     * Test endpoint - trigger posting immediately
     */
    private async handleTestPostNow(req: Request, res: Response): Promise<void> {
        try {
            logger.info('[TEST] Manual post trigger requested');

            if (!this.socialMediaManager) {
                res.status(500).json({
                    success: false,
                    error: 'Social media manager not available'
                });
                return;
            }

            // Trigger posting immediately (don't await to respond quickly)
            this.socialMediaManager.processEventCollector().catch((error: any) => {
                logger.error('[TEST] Error in manual post trigger', { error: error.message });
            });

            res.json({
                success: true,
                message: 'Posting cycle triggered',
                eventsInCollector: this.appState.busEventCollector.length
            });

        } catch (error: any) {
            logger.error('[TEST] Error handling post-now request', { error: error.message });
            res.status(500).json({
                success: false,
                error: error.message
            });
        }
    }

    /**
     * Get route count for diagnostics
     */
    private getRouteCount(): number {
        // Count the routes we've defined
        return 14;
    }
    
    /**
     * Get API endpoints summary
     */
    getEndpointsSummary(): any {
        return {
            endpoints: [
                { method: 'GET', path: '/api/health', description: 'Application and runtime health status' },
                { method: 'GET', path: '/api/logs', description: 'Retrieve service logs' },
                { method: 'POST', path: '/api/restart', description: 'Restart the managed application' },
                { method: 'POST', path: '/api/reload-db', description: 'Reload timetable database' }
            ],
            totalEndpoints: 4
        };
    }
    
    /**
     * Get service status
     */
    getStatus(): any {
        return {
            name: 'API Routes',
            status: 'running',
            config: { endpointsRegistered: this.getRouteCount() },
            endpoints: this.getEndpointsSummary().endpoints.map((ep: any) => `${ep.method} ${ep.path}`)
        };
    }
    
    /**
     * Close service and cleanup resources
     */
    async close(): Promise<void> {
        logger.info('API Routes service stopped', {
            endpointsRegistered: this.getRouteCount()
        });
    }
}
