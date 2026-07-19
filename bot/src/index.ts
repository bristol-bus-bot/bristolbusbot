// Load environment variables before application imports.
import dotenv from 'dotenv';
dotenv.config();

// Check for summary mode command line argument
const isSummaryMode = process.argv.includes('--summary') || process.argv.includes('-s');

// Application imports.
import express from 'express';
import { DateTime } from 'luxon';
import { loadConfig } from './config/app-config.js';
import { ApplicationState } from './services/application-state.js';
import { DatabaseManager } from './services/database-manager.js';
import { SIRIMonitor } from './services/siri-monitor.js';
import { DelayAnalyzer } from './services/delay-analyzer.js';
import { AICommentary } from './services/ai-commentary.js';
import { SocialMediaManager } from './services/social-media.js';
import { PatternDetector } from './services/pattern-detector.js';
import { HealthMonitor } from './services/health-monitor.js';
import { APIRoutes } from './api/routes.js';
import { WeatherService } from './services/weather-service.js';
import { logger, TARGET_TIMEZONE, setSummaryMode } from './utils/logging.js';
import { EventReader } from './ingest/event-reader.js';
import { RareWorkingShadowReader } from './ingest/rare-working-shadow-reader.js';

/**
 * Coordinates the bot services and their lifecycle.
 */
class BristolBusBot {
    private eventReader: EventReader | null = null;
    private rareWorkingShadowReader: RareWorkingShadowReader | null = null;
    private config: any;
    private appState: ApplicationState;
    private databaseManager!: DatabaseManager;
    private siriMonitor!: SIRIMonitor;
    private delayAnalyzer!: DelayAnalyzer;
    private aiCommentary!: AICommentary;
    private socialMediaManager!: SocialMediaManager;
    private patternDetector!: PatternDetector;
    private healthMonitor!: HealthMonitor;
    private weatherService!: WeatherService;
    private apiRoutes!: APIRoutes;
    private expressApp: express.Application;
    private server: any;
    
    constructor() {
        // Set summary mode if requested
        if (isSummaryMode) {
            setSummaryMode(true);
        }
        
        logger.info('🚀 Bristol Bus Bot starting up...');
        
        // Load configuration
        this.config = loadConfig();
        logger.info('Configuration loaded', { 
            testMode: this.config.testMode,
            nodeEnv: process.env.NODE_ENV 
        });
        
        // Initialize Express app
        this.expressApp = express();
        this.expressApp.use(express.json());

        // CORS for the dashboard. The API binds to localhost (see
        // startServer) and is reached from the laptop over an SSH local
        // forward, so the only legitimate browser origin is localhost.
        // Reflect localhost origins only; never '*' on a control API.
        this.expressApp.use((req, res, next) => {
            const origin = req.headers.origin;
            if (origin && /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(origin)) {
                res.header('Access-Control-Allow-Origin', origin);
            }
            res.header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
            res.header('Access-Control-Allow-Headers', 'Content-Type, Authorization');
            if (req.method === 'OPTIONS') {
                res.sendStatus(200);
            } else {
                next();
            }
        });
        
        // Initialize application state (singleton)
        this.appState = ApplicationState.getInstance();
        
        // Initialize all services with dependency injection
        this.initializeServices();
        
        logger.info('All services initialized, ready to start application');
    }
    
    /**
     * Initialize services and connect their dependencies.
     */
    private initializeServices(): void {
        logger.info('Initializing services...');
        
        // Core infrastructure services
        this.databaseManager = new DatabaseManager(this.config.database);
        this.healthMonitor = new HealthMonitor(this.appState);
        
        // Business logic services
        this.siriMonitor = new SIRIMonitor(this.config.siri, this.appState);
        this.delayAnalyzer = new DelayAnalyzer(this.config.processing, this.appState);
        this.weatherService = new WeatherService(this.config.weather);
        this.aiCommentary = new AICommentary(this.config.ai, this.appState, this.weatherService);
        this.socialMediaManager = new SocialMediaManager(this.config.social, this.appState);
        this.patternDetector = new PatternDetector(this.appState);
        
        // API layer
        this.apiRoutes = new APIRoutes(this.appState, this.healthMonitor);
        
        // Set up dependency injection - services need database access
        this.siriMonitor.setDatabaseManager(this.databaseManager);
        this.delayAnalyzer.setDatabaseManager(this.databaseManager);

        // Connect SIRI monitor to delay analyzer for schedule matching
        this.siriMonitor.setDelayAnalyzer(this.delayAnalyzer);
        this.socialMediaManager.setDatabaseManager(this.databaseManager);
        this.socialMediaManager.setAICommentary(this.aiCommentary);
        this.aiCommentary.setSocialMediaManager(this.socialMediaManager); // AI needs social media to fetch recent posts
        this.healthMonitor.setDatabaseManager(this.databaseManager);
        this.apiRoutes.setDatabaseManager(this.databaseManager);
        this.apiRoutes.setSocialMediaManager(this.socialMediaManager);
        this.apiRoutes.setAICommentary(this.aiCommentary);
        
        // Register API routes
        this.expressApp.use(this.apiRoutes.getRouter());
        
        logger.info('Services initialized successfully', {
            servicesCount: 8,
            dependencyInjectionComplete: true
        });
    }
    
    /**
     * Start the application - initialize all services and begin monitoring
     */
    async start(): Promise<void> {
        try {
            logger.info('🔧 Starting Bristol Bus Bot services...');
            
            // Initialize all services in proper order
            await this.databaseManager.initialize();
            await this.healthMonitor.initialize();
            await this.siriMonitor.initialize();
            await this.delayAnalyzer.initialize();
            await this.weatherService.initialize();
            await this.aiCommentary.initialize();
            await this.socialMediaManager.initialize();
            await this.patternDetector.initialize();
            
            // Load initial data
            await this.loadInitialData();
            
            // Start Express server
            await this.startServer();
            
            // Start main monitoring loop
            this.startMonitoringLoop();
            
            // Start periodic posting service (20-minute intervals)
            this.socialMediaManager.startPeriodicPosting();
            
            // Log startup success
            this.logStartupSuccess();
            
        } catch (error: any) {
            logger.error('Failed to start Bristol Bus Bot', {
                error: error.message,
                stack: error.stack 
            });
            throw error;
        }
    }
    
    /**
     * Load initial data (bus details, terminus stops, etc.)
     */
    private async loadInitialData(): Promise<void> {
        logger.info('Loading initial application data...');
        
        try {
            // Load bus details, route details, and terminus stops
            await this.databaseManager.loadBusDetailsLookup();
            await this.databaseManager.loadRouteDetails();
            await this.databaseManager.loadTerminusStops();

            logger.info('Initial data loaded successfully', {
                busDetailsLoaded: this.appState.busDetailsLookup?.results?.length || 0,
                routeDetailsLoaded: Object.keys(this.appState.routeDetails).length,
                terminusStopsLoaded: this.appState.terminusStopNames.size
            });
            
        } catch (error: any) {
            logger.error('Error loading initial data', { error: error.message });
            throw error;
        }
    }
    
    /**
     * Start Express server
     */
    private startServer(): Promise<void> {
        return new Promise((resolve, reject) => {
            try {
                // The control API is available only through a local SSH tunnel.
                this.server = this.expressApp.listen(this.config.server.port, this.config.server.host, () => {
                    logger.info(`🌐 Express server listening on ${this.config.server.host}:${this.config.server.port}`, {
                        testMode: this.config.testMode,
                        endpoints: this.apiRoutes.getEndpointsSummary().totalEndpoints
                    });
                    resolve();
                });
                
                this.server.on('error', (error: any) => {
                    logger.error('Express server error', { error: error.message });
                    reject(error);
                });
                
            } catch (error) {
                reject(error);
            }
        });
    }
    
    /**
     * Start main monitoring loop - the core SIRI monitoring cycle
     */
    private startMonitoringLoop(): void {
        logger.info('🔍 Starting main bus monitoring loop...');

        // Check if it's Christmas Day or Boxing Day (no bus service)
        const now = DateTime.now().setZone(TARGET_TIMEZONE);
        const isChristmasOrBoxingDay = now.month === 12 && (now.day === 25 || now.day === 26);

        if (isChristmasOrBoxingDay) {
            const dayName = now.day === 25 ? 'Christmas Day' : 'Boxing Day';
            logger.info(`🎄 ${dayName} detected - SIRI monitoring disabled (no bus service)`);

            // Schedule special post at 8am if we haven't passed it yet
            this.scheduleChristmasPost(now);
        } else {
            // Normal operation - start SIRI monitoring (this handles the main 2-minute cycle)
            // Event ingest is the production default. Direct SIRI ingest is an
            // explicit diagnostic mode so it cannot create a second poller by
            // accident.
            const ingestMode = (process.env.INGEST_MODE || 'events').toLowerCase();
            if (ingestMode === 'events') {
                const liveDbPath = process.env.LIVE_DB_PATH
                    || '/var/lib/bristolbusbot/collector/live.db';
                const operators = (process.env.INGEST_OPERATORS || 'FBRI')
                    .split(',').map(o => o.trim()).filter(Boolean);
                const maxAgeMin = parseInt(process.env.INGEST_MAX_AGE_MIN || '10', 10);
                this.eventReader = new EventReader(
                    liveDbPath, this.appState, this.delayAnalyzer, operators,
                    30_000, maxAgeMin);
                this.eventReader.start();
                logger.info('Ingest: collector events', { liveDbPath, operators });
                if ((process.env.RARE_WORKING_SHADOW || '').toLowerCase() === 'true') {
                    const snapshotPath = process.env.AUDIT_INTEGRATION_PATH
                        || '/var/lib/bristolbusbot/pipeline/audit_site/audit_integration.json';
                    const stateDbPath = process.env.APP_DATA_DB_PATH
                        || '/var/lib/bristolbusbot/bot/app_data.db';
                    this.rareWorkingShadowReader = new RareWorkingShadowReader(
                        snapshotPath, stateDbPath);
                    this.rareWorkingShadowReader.start();
                }
            } else {
                this.siriMonitor.startMonitoring();
            }
        }

        // Set up daily cleanup interval
        setInterval(async () => {
            try {
                await this.databaseManager.cleanupOldData();
            } catch (error: any) {
                logger.error('Error during daily cleanup', { error: error.message });
            }
        }, 24 * 60 * 60 * 1000); // 24 hours in milliseconds

        logger.info('Monitoring loop started successfully', {
            siriInterval: isChristmasOrBoxingDay ? 'disabled (holiday)' : this.config.monitoring.fetchInterval,
            cleanupInterval: '24 hours'
        });
    }
    
    /**
     * Schedule special Christmas/Boxing Day post at 8am
     */
    private scheduleChristmasPost(now: DateTime): void {
        const dayName = now.day === 25 ? 'Christmas Day' : 'Boxing Day';
        const target8am = now.set({ hour: 8, minute: 0, second: 0, millisecond: 0 });

        if (now < target8am) {
            // Haven't reached 8am yet - schedule the post
            const msUntil8am = target8am.toMillis() - now.toMillis();

            logger.info(`🎅 Scheduling ${dayName} post for 8:00 AM`, {
                currentTime: now.toFormat('HH:mm:ss'),
                scheduledTime: target8am.toFormat('HH:mm:ss'),
                delayMs: msUntil8am
            });

            setTimeout(async () => {
                try {
                    const message = now.day === 25
                        ? "Merry Christmas from Bristol! 🎄 No bus service today - even the buses are taking a well-earned break. Normal service resumes December 27th."
                        : "Happy Boxing Day! 🎁 No bus service today - the buses are still recovering from Christmas. Normal service resumes tomorrow.";

                    // Create dummy event for holiday post
                    const dummyEvent = {
                        timestamp: now.toISO()!,
                        vehicleRef: 'HOLIDAY',
                        datedJourneyRef: 'HOLIDAY',
                        line: 'ALL',
                        direction: 'N/A',
                        originAimedDepartureTimeStr: now.toISO()!,
                        delayMinutes: 0,
                        lastStopCode: 'N/A',
                        lastStopTime: now.toISO()!,
                        eventType: 'punctual' as const,
                        significance: 0
                    };

                    logger.info(`🎄 Posting ${dayName} message: "${message}"`);
                    await this.socialMediaManager.postUpdate(message, dummyEvent);
                    logger.info(`✅ ${dayName} post sent successfully`);
                } catch (error: any) {
                    logger.error(`Failed to post ${dayName} message`, { error: error.message });
                }
            }, msUntil8am);
        } else {
            // Already past 8am - post immediately
            logger.info(`🎄 Already past 8am on ${dayName}, posting immediately`);

            (async () => {
                try {
                    const message = now.day === 25
                        ? "Merry Christmas from Bristol! 🎄 No bus service today - even the buses are taking a well-earned break. Normal service resumes December 27th."
                        : "Happy Boxing Day! 🎁 No bus service today - the buses are still recovering from Christmas. Normal service resumes tomorrow.";

                    // Create dummy event for holiday post
                    const dummyEvent = {
                        timestamp: now.toISO()!,
                        vehicleRef: 'HOLIDAY',
                        datedJourneyRef: 'HOLIDAY',
                        line: 'ALL',
                        direction: 'N/A',
                        originAimedDepartureTimeStr: now.toISO()!,
                        delayMinutes: 0,
                        lastStopCode: 'N/A',
                        lastStopTime: now.toISO()!,
                        eventType: 'punctual' as const,
                        significance: 0
                    };

                    logger.info(`🎄 Posting ${dayName} message: "${message}"`);
                    await this.socialMediaManager.postUpdate(message, dummyEvent);
                    logger.info(`✅ ${dayName} post sent successfully`);
                } catch (error: any) {
                    logger.error(`Failed to post ${dayName} message`, { error: error.message });
                }
            })();
        }
    }

    /**
     * Log successful startup with system information
     */
    private logStartupSuccess(): void {
        const startupTime = DateTime.now().setZone(TARGET_TIMEZONE);
        
        logger.info('🎉 Bristol Bus Bot startup complete!', {
            startupTime: startupTime.toISO(),
            testMode: this.config.testMode,
            nodeEnv: process.env.NODE_ENV,
            services: {
                database: 'ready',
                siriMonitor: 'monitoring',
                aiCommentary: 'ready',
                socialMedia: this.config.testMode ? 'test-mode' : 'ready',
                patternDetector: 'ready',
                healthMonitor: 'monitoring',
                expressServer: 'listening'
            },
            monitoring: {
                siriPollInterval: `${this.config.monitoring.fetchInterval / 1000}s`,
                aiDailyLimit: this.config.ai.dailyLimit,
                socialDailyLimit: this.config.social.dailyLimit
            }
        });
        
        // Startup health logging
        logger.info(`[STARTUP_HEALTH] Current time: ${startupTime.toFormat('yyyy-MM-dd HH:mm:ss zzz')}`);
        logger.info(`[STARTUP_HEALTH] Daily counters: Posts ${this.appState.postsTodayCount}, AI calls ${this.appState.aiCallsToday}`);
        logger.info(`[STARTUP_HEALTH] Event collector: ${this.appState.busEventCollector.length} events`);
        
        if (this.config.testMode) {
            logger.info('🧪 TEST MODE ACTIVE - Social media posting disabled, enhanced logging enabled');
        }
    }
    
    /**
     * Graceful shutdown
     */
    async shutdown(): Promise<void> {
        logger.info('🛑 Bristol Bus Bot shutting down...');
        
        try {
            // Stop monitoring
            this.siriMonitor.stopMonitoring();
            if (this.eventReader) this.eventReader.stop();
            if (this.rareWorkingShadowReader) this.rareWorkingShadowReader.stop();
            
            // Close all services
            await Promise.all([
                this.databaseManager.close(),
                this.healthMonitor.close(),
                this.siriMonitor.close(),
                this.delayAnalyzer.close(),
                this.aiCommentary.close(),
                this.socialMediaManager.close(),
                this.patternDetector.close(),
                this.apiRoutes.close()
            ]);
            
            // Close Express server
            if (this.server) {
                this.server.close();
            }
            
            logger.info('✅ Bristol Bus Bot shutdown complete');
            
        } catch (error: any) {
            logger.error('Error during shutdown', { error: error.message });
        }
    }
}

// Handle uncaught exceptions and unhandled promise rejections
process.on('uncaughtException', (error) => {
    logger.error('Uncaught Exception', { error: error.message, stack: error.stack });
    process.exit(1);
});

process.on('unhandledRejection', (reason, promise) => {
    logger.error('Unhandled Rejection', { reason, promise });
    process.exit(1);
});

// Handle graceful shutdown signals
process.on('SIGTERM', async () => {
    logger.info('SIGTERM received, initiating graceful shutdown...');
    if (app) {
        await app.shutdown();
    }
    process.exit(0);
});

process.on('SIGINT', async () => {
    logger.info('SIGINT received, initiating graceful shutdown...');
    if (app) {
        await app.shutdown();
    }
    process.exit(0);
});

// Initialize and start the application
let app: BristolBusBot;

async function main() {
    try {
        app = new BristolBusBot();
        await app.start();
        
    } catch (error: any) {
        logger.error('Failed to start application', { 
            error: error.message,
            stack: error.stack 
        });
        process.exit(1);
    }
}

// Start the application
main().catch((error) => {
    logger.error('Fatal error in main', { error: error.message });
    process.exit(1);
});
