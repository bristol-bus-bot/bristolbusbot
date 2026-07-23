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
    const apiKey = process.env.BODS_API_KEY;
    const bskyHandle = process.env.BSKY_HANDLE;
    const bskyAppPassword = process.env.BSKY_APP_PASSWORD;
    const AI_API_KEY = process.env.AI_API_KEY;
    const weatherApiKey = process.env.WEATHER_API_KEY;
    
    // Operator and area configuration 
    const operatorRefForApiQuery = 'FBRI';
    const target_operator = 'O1';
    const boundingBox = '-2.8319664502,51.3170328857,-2.3362655622,51.6102366333';
    
    // Timing constants with environment overrides. Timeouts are generous
    // because the Pi's network path to BODS can be slow.
    const FETCH_INTERVAL_MS = parseInt(process.env.SIRI_VM_POLL_INTERVAL || '120000', 10);
    const FETCH_TIMEOUT_MS = parseInt(process.env.QUERY_TIMEOUT || '60000', 10);
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
    const AI_COMMENTARY_PIPELINE = process.env.AI_COMMENTARY_PIPELINE === 'legacy'
        ? 'legacy'
        : 'single';
    
    // Social media constants with environment variable override for Pi optimization
    const BLUESKY_POST_LIMIT = parseInt(process.env.BLUESKY_DAILY_LIMIT || '300', 10);
    
    const requestUrl = `https://data.bus-data.dft.gov.uk/api/v1/datafeed/?boundingBox=${encodeURIComponent(boundingBox)}&operatorRef=${encodeURIComponent(operatorRefForApiQuery)}&api_key=${apiKey}`;
    
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
        
        siri: {
            apiKey: apiKey || '',
            operatorRef: operatorRefForApiQuery,
            boundingBox: boundingBox,
            timeout: FETCH_TIMEOUT_MS,
            requestUrl: requestUrl,
            targetOperator: target_operator
        },
        
        ai: {
            apiKey: AI_API_KEY || '',
            model: process.env.AI_MODEL || 'gemini-3.6-flash',
            pipeline: AI_COMMENTARY_PIPELINE,
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
            fetchInterval: FETCH_INTERVAL_MS,
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
    const directSiriIngest = (process.env.INGEST_MODE || 'events').toLowerCase() === 'siri';
    const requiredFields = [
        { field: 'social.handle', value: config.social.handle, name: 'BSKY_HANDLE' },
        { field: 'social.appPassword', value: config.social.appPassword, name: 'BSKY_APP_PASSWORD' },
        { field: 'weather.apiKey', value: config.weather.apiKey, name: 'WEATHER_API_KEY' }
    ];
    if (directSiriIngest) {
        requiredFields.push({
            field: 'siri.apiKey', value: config.siri.apiKey, name: 'BODS_API_KEY'
        });
    }

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
                    fetchInterval: 120000, // 2 minutes
                    summaryInterval: 1200000, // 20 minutes
                    metricsInterval: 60000 // 1 minute
                }
            };
            
        case 'development':
            return {
                testMode: true,
                monitoring: {
                    enableMetrics: true,
                    fetchInterval: 300000, // 5 minutes for dev
                    summaryInterval: 600000, // 10 minutes for dev
                    metricsInterval: 60000 // 1 minute
                }
            };
            
        case 'test':
            return {
                testMode: true,
                monitoring: {
                    enableMetrics: false,
                    fetchInterval: 60000, // 1 minute for testing
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
export function getSIRIConfig(config: AppConfig) {
    return config.siri;
}

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
