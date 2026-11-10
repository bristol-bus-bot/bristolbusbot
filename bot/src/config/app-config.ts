// Bristol Bus Bot - Application Configuration

import * as path from 'path';
import type { AppConfig } from '../types/bus-types.js';

/**
 * Load application configuration from environment variables.
 */
export function loadConfig(): AppConfig {
    const isTestMode = process.env.TEST_MODE?.toLowerCase() === 'true';
    const TARGET_TIMEZONE = 'Europe/London';

    // API credentials come from the environment only; there are no defaults.
    const bskyHandle = process.env.BSKY_HANDLE;
    const bskyAppPassword = process.env.BSKY_APP_PASSWORD;
    const AI_API_KEY = process.env.AI_API_KEY;
    const weatherApiKey = process.env.WEATHER_API_KEY;
    
    // Timing constants with environment overrides. Timeouts are generous
    // because the Pi's network path to BODS can be slow.
    const SUMMARY_INTERVAL_MS = 20 * 60 * 1000; // 20 minutes
    const DAILY_CLEANUP_INTERVAL_MS = parseInt(process.env.CLEANUP_INTERVAL || '86400000', 10);
    
    // File paths; environment overrides, working-directory defaults.
    const TIMETABLE_DB_FILE = process.env.TIMETABLE_DB_PATH
        || path.join(process.cwd(), 'timetable.db');
    const APP_DATA_DB_FILE = process.env.APP_DATA_DB_PATH
        || path.join(process.cwd(), 'app_data.db');
    const EDITORIAL_CONTEXT_FILE = process.env.EDITORIAL_CONTEXT_PATH
        || path.join(process.cwd(), 'editorial-context.json');
    const EDITORIAL_USAGE_FILE = process.env.EDITORIAL_USAGE_PATH
        || path.join(path.dirname(APP_DATA_DB_FILE), 'editorial-usage.json');
    const WITTY_COMMENTS_FILE = 'witty_comments.json';
    const FBRI_BUSES_FILE = 'fbribuses.json';
    const ZEN_COMMENTS_FILE = 'zen_comments.json';
    
    // Event-processing thresholds.
    const LATE_THRESHOLD_MINUTES = 4;
    const SIGNIFICANT_DELAY_THRESHOLD = 10;
    const TIME_WINDOW_MINUTES = 2;
    const MAX_DISTANCE_KM_SANITY_CHECK = 1.0;
    const MAX_JOURNEY_AGE_HOURS = 2;
    
    // AI constants with environment variable override for Pi optimization
    const AI_DAILY_CALL_LIMIT = parseInt(process.env.AI_DAILY_LIMIT || '2000', 10);
    
    // Social media constants with environment variable override for Pi optimization
    const BLUESKY_POST_LIMIT = parseInt(process.env.BLUESKY_DAILY_LIMIT || '300', 10);
    
    return {
        testMode: isTestMode,
        
        server: {
            port: parseInt(process.env.PORT || '3010', 10),
            host: '127.0.0.1'
        },
        
        database: {
            timetablePath: TIMETABLE_DB_FILE,
            appDataPath: APP_DATA_DB_FILE,
            maxConnections: 10
        },
        
        ai: {
            apiKey: AI_API_KEY || '',
            model: process.env.AI_MODEL || 'gemini-3.6-flash',
            // The checker only says pass or fail, so it can use a cheaper model.
            verifierModel: process.env.AI_VERIFIER_MODEL || process.env.AI_MODEL || 'gemini-3.6-flash',
            provider: process.env.AI_PROVIDER === 'agent-platform' ? 'agent-platform' : 'gemini',
            agentPlatformProject: process.env.AGENT_PLATFORM_PROJECT || '',
            agentPlatformLocation: process.env.AGENT_PLATFORM_LOCATION || 'global',
            dailyLimit: AI_DAILY_CALL_LIMIT,
            timeout: parseInt(process.env.AI_TIMEOUT || '75000', 10), // Increased from 30s to 75s for Pi network
            editorialContextPath: EDITORIAL_CONTEXT_FILE,
            editorialUsagePath: EDITORIAL_USAGE_FILE,
        },

        weather: {
            apiKey: weatherApiKey || '',
            baseUrl: 'https://api.openweathermap.org/data/2.5/weather',
            bristolLat: 51.4545,
            bristolLon: -2.5879
        },
        traffic: {
            apiKey: process.env.TOMTOM_API_KEY || '',
            enabled: process.env.TRAFFIC_ENABLED === 'true',
            usagePath: process.env.TRAFFIC_USAGE_PATH || path.join(path.dirname(APP_DATA_DB_FILE), 'traffic-usage.json'),
        },

        social: {
            handle: bskyHandle || '',
            appPassword: bskyAppPassword || '',
            testMode: isTestMode,
            dailyLimit: BLUESKY_POST_LIMIT,
            postLimit: 300
        },

        processing: {
            lateThreshold: LATE_THRESHOLD_MINUTES,
            earlyThreshold: -3, // From current calculateEventSignificance logic
            significantThreshold: 3, // From current significance logic  
            maxJourneyAge: MAX_JOURNEY_AGE_HOURS,
            timeWindow: TIME_WINDOW_MINUTES,
            maxDistance: MAX_DISTANCE_KM_SANITY_CHECK
        },
        
        patterns: {
            networkThreshold: 4, // From current detectDelayPatterns logic
            areaThreshold: 3,
            clusterThreshold: 2
        },
        
        monitoring: {
            summaryInterval: SUMMARY_INTERVAL_MS,
            metricsInterval: 60000, // 1 minute
            enableMetrics: true
        },
        
        health: {
            checkInterval: 30000, // 30 seconds
            alertThresholds: {
                errorRate: 0.05, // 5% error rate
                responseTime: 5000, // 5 second response time
                memoryUsage: 0.9 // 90% memory usage
            }
        },
        
        
    };
}

/** Validate required configuration values. */
export function validateConfig(config: AppConfig): void {
    // The bot only reads the collector's events. Any other ingest mode is a
    // leftover setting and must not be silently ignored.
    // The single writer is the only commentary pipeline. "single" (the old
    // explicit value) is still accepted; anything else is a leftover setting.
    const pipeline = (process.env.AI_COMMENTARY_PIPELINE || 'single').toLowerCase();
    if (pipeline !== 'single') {
        throw new Error(`AI_COMMENTARY_PIPELINE=${process.env.AI_COMMENTARY_PIPELINE} is not supported; the legacy writer has been removed`);
    }
    const ingestMode = (process.env.INGEST_MODE || 'events').toLowerCase();
    if (ingestMode !== 'events') {
        throw new Error(`INGEST_MODE=${process.env.INGEST_MODE} is not supported; the bot reads collector events only (INGEST_MODE=events)`);
    }
    const requiredFields = [
        { field: 'social.handle', value: config.social.handle, name: 'BSKY_HANDLE' },
        { field: 'social.appPassword', value: config.social.appPassword, name: 'BSKY_APP_PASSWORD' },
        { field: 'weather.apiKey', value: config.weather.apiKey, name: 'WEATHER_API_KEY' }
    ];

    const missingFields = requiredFields
        .filter(req => !req.value)
        .map(req => req.name);

    if (missingFields.length > 0) {
        // The weather key is deliberately optional: without it the bot runs
        // normally but weather context is disabled.
        const requiredMissing = missingFields.filter(name => name !== 'WEATHER_API_KEY');
        if (requiredMissing.length > 0) {
            throw new Error(`Missing required environment variables: ${requiredMissing.join(', ')}`);
        }
        if (missingFields.includes('WEATHER_API_KEY')) {
            console.warn('WEATHER_API_KEY not configured - weather context will be disabled.');
        }
    }

    // AI API key is optional but log if missing
    if (!config.ai.apiKey) {
        console.warn('AI_API_KEY not configured - AI commentary will be disabled');
    }
    if (config.ai.provider === 'agent-platform' && !config.ai.agentPlatformProject) {
        throw new Error('AI_PROVIDER=agent-platform needs AGENT_PLATFORM_PROJECT');
    }
}

/**
 * Get environment-specific configuration overrides
 */
export function getEnvironmentConfig(): Partial<AppConfig> {
    const env = process.env.NODE_ENV || 'development';
    
    switch (env) {
        case 'production':
            return {
                monitoring: {
                    enableMetrics: true,
                    summaryInterval: 1200000, // 20 minutes
                    metricsInterval: 60000 // 1 minute
                }
            };
            
        case 'development':
            return {
                testMode: true,
                monitoring: {
                    enableMetrics: true,
                    summaryInterval: 600000, // 10 minutes for dev
                    metricsInterval: 60000 // 1 minute
                }
            };
            
        case 'test':
            return {
                testMode: true,
                monitoring: {
                    enableMetrics: false,
                    summaryInterval: 120000, // 2 minutes for testing
                    metricsInterval: 60000 // 1 minute
                }
            };
            
        default:
            return {};
    }
}

// Constants that may be needed by other services
export const CONSTANTS = {
    TARGET_TIMEZONE: 'Europe/London',
    DEFAULT_PORT: 3010,
    MAX_RETRY_ATTEMPTS: 3,
    DEFAULT_TIMEOUT: 30000
} as const;

// Export individual configuration sections for service-specific use

export function getDatabaseConfig(config: AppConfig) {
    return config.database;
}

export function getAIConfig(config: AppConfig) {
    return config.ai;
}

export function getSocialConfig(config: AppConfig) {
    return config.social;
}

export function getProcessingConfig(config: AppConfig) {
    return config.processing;
}

export function getMonitoringConfig(config: AppConfig) {
    return config.monitoring;
}
