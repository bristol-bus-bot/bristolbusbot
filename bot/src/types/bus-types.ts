// Shared application types.

import { DateTime } from 'luxon';

// Core bus event types
export interface BusEvent {
    timestamp: string;
    vehicleRef: string;
    datedJourneyRef: string;
    line: string;
    direction: string;
    originAimedDepartureTimeStr: string;
    delayMinutes: number;
    lastStopCode: string;
    lastStopTime: string;
    lastStopName?: string;
    weight?: number;
    eventType: 'delay' | 'early' | 'punctual';
    significance: number;
    busDetails?: BusVehicleDetails;
    location?: {
        latitude: number;
        longitude: number;
    };
}

// Vehicle details structure
export interface BusVehicleDetails {
    fleet_number: number;
    registration: string;
    vehicle_type: {
        name: string;
        double_decker: boolean;
        electric: boolean;
        low_floor: boolean;
        wheelchair_accessible: boolean;
    };
    livery: {
        name: string;
        colors: string[];
        special_features?: string[];
    };
    garage: {
        name: string;
        location: string;
    };
    special_features: string[];
    in_service: boolean;
    last_seen: string;
}

// Delay pattern analysis
export interface DelayPattern {
    type: 'single' | 'cluster' | 'network' | 'area';
    routes: string[];
    delays: number[];
    affectedArea?: string;
    description: string;
    timestamp: string;
    confidence: number;
    severity: 'low' | 'medium' | 'high';
}

// Delay history tracking
export interface DelayHistory {
    routeId: string;
    lastReportedDelay: number;
    lastReportTime: DateTime;
    trend: 'worsening' | 'improving' | 'stable' | 'new';
    consecutiveReports: number;
    significantChange: boolean;
    averageDelay: number;
    peakDelay: number;
    reports: DelayReport[];
}

export interface DelayReport {
    timestamp: DateTime;
    delay: number;
    location: string;
    significance: number;
}

// SIRI-VM data structures
export interface SIRIVehicleActivity {
    vehicleRef: string;
    lineRef: string;
    directionRef: string;
    datedJourneyRef: string;
    operatorRef: string;
    originAimedDepartureTime: string;
    recordedAtTime: string;
    validUntilTime: string;
    vehicleLocation?: {
        longitude: number;
        latitude: number;
    };
    monitored: boolean;
    monitoredCall?: SIRIMonitoredCall;
    onwardCalls?: SIRIOnwardCall[];
}

export interface SIRIMonitoredCall {
    stopPointRef: string;
    stopPointName: string;
    vehicleAtStop: boolean;
    aimedArrivalTime?: string;
    expectedArrivalTime?: string;
    aimedDepartureTime?: string;
    expectedDepartureTime?: string;
    extensions?: any;
}

export interface SIRIOnwardCall {
    stopPointRef: string;
    stopPointName: string;
    aimedArrivalTime?: string;
    expectedArrivalTime?: string;
    aimedDepartureTime?: string;
    expectedDepartureTime?: string;
}

// Database entities
export interface DatabaseStop {
    stop_id: number;
    stop_code: string;
    common_name: string;
    latitude: number;
    longitude: number;
    location_type: number;
    parent_station?: string;
}

export interface DatabaseRoute {
    line_id: number;
    line_name: string;
    route_type: number;
    route_color?: string;
    route_text_color?: string;
}

export interface DatabaseJourney {
    journey_id: number;
    trip_id: string;
    operator_code: string;
    line_id: number;
    direction_id: number;
    service_id: string;
    departure_time: string;
    journey_code: string;
    trip_headsign: string;
    operating_days: string;
}

export interface DatabaseStopTime {
    stop_time_id: number;
    journey_id: number;
    stop_id: number;
    arrival_time: string;
    departure_time: string;
    stop_sequence: number;
    pickup_type: number;
    drop_off_type: number;
}

// Network status and metrics
export interface NetworkStatus {
    totalRoutes: number;
    operatingRoutes: number;
    delayedRoutes: number;
    punctualRoutes: number;
    averageDelay: number;
    totalEvents: number;
    lastUpdate: string;
    coverage: {
        monitored: number;
        total: number;
        percentage: number;
    };
    performance: {
        onTime: number;
        delayed: number;
        early: number;
        percentages: {
            onTime: number;
            delayed: number;
            early: number;
        };
    };
}

// System monitoring metrics
export interface SystemMetrics {
    uptime: number;
    totalEvents: number;
    postsToday: number;
    aiCallsToday: number;
    summariesPosted: number;
    averageResponseTime: number;
    errorRate: number;
    lastSiriUpdate: string;
    databaseHealth: {
        timetableConnected: boolean;
        appDataConnected: boolean;
        lastHealthCheck: string;
    };
    networkStatus: NetworkStatus;
    performance: {
        avgSiriResponseTime: number;
        avgProcessingTime: number;
        avgPostingTime: number;
        memoryUsage: NodeJS.MemoryUsage;
        cpuUsage: NodeJS.CpuUsage;
    };
}

// AI Commentary system
export interface AICommentaryContext {
    event: BusEvent;
    pattern?: DelayPattern;
    history?: DelayHistory;
    networkStatus: NetworkStatus;
    timeContext: string;
    weatherContext?: string | null | undefined;
}

export interface AICommentaryResult {
    text: string;
    persona: string;
    confidence: number;
    responseTime: number;
    metadata: {
        tokenCount: number;
        model: string;
        temperature: number;
        editorialMode?: boolean;
        editorialKind?: 'fact' | 'occasion' | 'news';
    };
}

// Social media integration
export interface SocialMediaPost {
    id: string;
    text: string;
    timestamp: string;
    platform: string;
    engagement: {
        likes: number;
        reposts: number;
        replies: number;
    };
    metadata: {
        event: BusEvent;
        persona: string;
        postType: 'event' | 'summary' | 'update';
    };
}

// Configuration interfaces
export interface AppConfig {
    testMode: boolean;
    server: {
        port: number;
        host: string;
    };
    database: {
        timetablePath: string;
        appDataPath: string;
        maxConnections: number;
    };
    siri: {
        apiKey: string;
        operatorRef: string;
        boundingBox: string;
        timeout: number;
        requestUrl?: string;
        targetOperator?: string;
    };
    ai: {
        apiKey: string;
        model: string;
        dailyLimit: number;
        timeout: number;
        editorialContextPath: string;
        editorialUsagePath: string;
    };
    weather: {
        apiKey: string;
        baseUrl: string;
        bristolLat: number;
        bristolLon: number;
    };
    social: {
        handle: string;
        appPassword: string;
        testMode: boolean;
        dailyLimit: number;
        postLimit?: number;
    };
    processing: {
        lateThreshold: number;
        earlyThreshold: number;
        significantThreshold: number;
        maxJourneyAge: number;
        timeWindow: number;
        maxDistance: number;
    };
    patterns: {
        networkThreshold: number;
        areaThreshold: number;
        clusterThreshold: number;
    };
    monitoring: {
        fetchInterval: number;
        summaryInterval: number;
        metricsInterval: number;
        enableMetrics: boolean;
    };
    health: {
        checkInterval: number;
        alertThresholds: {
            errorRate: number;
            responseTime: number;
            memoryUsage: number;
        };
    };
}

// Event handler types
export type EventHandler<T = any> = (event: T) => void | Promise<void>;

// Service interface
export interface Service {
    name: string;
    initialize(): Promise<void>;
    start(): Promise<void>;
    stop(): Promise<void>;
    getStatus(): ServiceStatus;
}

export interface ServiceStatus {
    name: string;
    status: 'starting' | 'running' | 'stopped' | 'error';
    uptime: number;
    lastError?: Error;
    metrics: Record<string, any>;
}

// Error types
export class BusMonitoringError extends Error {
    constructor(
        message: string,
        public readonly code: string,
        public readonly context?: any
    ) {
        super(message);
        this.name = 'BusMonitoringError';
    }
}

export class SIRIError extends BusMonitoringError {
    constructor(message: string, context?: any) {
        super(message, 'SIRI_ERROR', context);
        this.name = 'SIRIError';
    }
}

export class DatabaseError extends BusMonitoringError {
    constructor(message: string, context?: any) {
        super(message, 'DATABASE_ERROR', context);
        this.name = 'DatabaseError';
    }
}

export class AIError extends BusMonitoringError {
    constructor(message: string, context?: any) {
        super(message, 'AI_ERROR', context);
        this.name = 'AIError';
    }
}

export class SocialMediaError extends BusMonitoringError {
    constructor(message: string, context?: any) {
        super(message, 'SOCIAL_MEDIA_ERROR', context);
        this.name = 'SocialMediaError';
    }
}
