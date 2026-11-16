// Bristol Bus Bot - Pattern Detector Service
// Classifies delay events into network-wide, area-specific and individual
// patterns for the commentary pipeline.

import { DateTime } from 'luxon';
import { logger, PerformanceTimer, TARGET_TIMEZONE } from '../utils/logging.js';
import { ApplicationState } from './application-state.js';
import type { BusEvent, DelayPattern } from '../types/bus-types.js';

interface PatternAnalysisResult {
    hasCritical: boolean;
    criticalCount: number;
    summary: string;
    significance: number;
    shouldReport: boolean;
    primaryPattern?: DelayPattern;
    affectedRoutes?: string[];
    averageDelay?: number;
    details: {
        patterns: DelayPattern[];
        impactLevel: 'low' | 'medium' | 'high' | 'critical';
        affectedRoutes: string[];
        totalDelays: number;
        averageDelay: number;
        maxDelay: number;
        patternType?: string;
    };
}

/**
 * Pattern Detector Service
 * Handles delay pattern analysis and classification.
 */
export class PatternDetector {
    private appState: ApplicationState;
    
    constructor(appState: ApplicationState) {
        this.appState = appState;
        
        logger.info('Pattern Detector service initialized', {
            areaKeywords: this.getAreaKeywordCount()
        });
    }
    
    /**
     * Initialize pattern detector service
     */
    async initialize(): Promise<void> {
        logger.info('Pattern Detector service ready', {
            areaKeywords: this.getAreaKeywordCount()
        });
    }
    
    /**
     * Detect delay patterns from bus events.
     */
    detectDelayPatterns(delays: BusEvent[]): DelayPattern[] {
        const timer = new PerformanceTimer('pattern_detection', logger);
        
        try {
            const patterns: DelayPattern[] = [];
            
            if (delays.length === 0) {
                timer.complete({ patternsFound: 0, inputDelays: 0 });
                return patterns;
            }
            
            // Network-wide pattern: 4+ routes affected
            if (delays.length >= 4) {
                patterns.push({
                    type: 'network',
                    routes: delays.map(d => d.line),
                    delays: delays.map(d => d.delayMinutes),
                    description: `Network-wide delays affecting ${delays.length} routes`,
                    timestamp: DateTime.now().setZone(TARGET_TIMEZONE).toISO() ?? '',
                    confidence: 0.8,
                    severity: 'high'
                });
                
                logger.info(`[PATTERN] Network-wide pattern detected: ${delays.length} routes affected`, {
                    routes: delays.map(d => d.line),
                    delays: delays.map(d => d.delayMinutes)
                });
                
                timer.complete({
                    patternsFound: 1,
                    patternType: 'network',
                    routesAffected: delays.length,
                    inputDelays: delays.length
                });
                
                return patterns; // Don't look for other patterns if network-wide
            }
            
            // Area cluster pattern: 3+ routes with stops in the same named area
            const areaGroups = new Map<string, BusEvent[]>();
            delays.forEach(delay => {
                const stopArea = this.extractAreaFromStopName(delay.lastStopName || '');
                if (stopArea) {
                    if (!areaGroups.has(stopArea)) areaGroups.set(stopArea, []);
                    areaGroups.get(stopArea)!.push(delay);
                }
            });
            
            areaGroups.forEach((areaDelays, area) => {
                if (areaDelays.length >= 3) {
                    patterns.push({
                        type: 'area',
                        routes: areaDelays.map(d => d.line),
                        delays: areaDelays.map(d => d.delayMinutes),
                        affectedArea: area,
                        description: `Area-specific delays around ${area}`,
                        timestamp: DateTime.now().setZone(TARGET_TIMEZONE).toISO() ?? '',
                        confidence: 0.7,
                        severity: 'medium'
                    });
                    
                    logger.info(`[PATTERN] Area-specific pattern detected: ${area}`, {
                        routes: areaDelays.map(d => d.line),
                        delays: areaDelays.map(d => d.delayMinutes)
                    });
                }
            });
            
            // If no area patterns, treat as individual delays
            if (patterns.length === 0) {
                delays.forEach(delay => {
                    patterns.push({
                        type: 'single',
                        routes: [delay.line],
                        delays: [delay.delayMinutes],
                        description: `Individual delay on route ${delay.line}`,
                        timestamp: DateTime.now().setZone(TARGET_TIMEZONE).toISO() ?? '',
                        confidence: 0.5,
                        severity: 'low'
                    });
                });
                
                logger.info(`[PATTERN] Individual delays detected`, {
                    count: delays.length,
                    routes: delays.map(d => d.line)
                });
            }
            
            timer.complete({
                patternsFound: patterns.length,
                patternTypes: patterns.map(p => p.type),
                inputDelays: delays.length,
                areaGroupsFound: areaGroups.size
            });
            
            return patterns;
            
        } catch (error: any) {
            timer.fail(error);
            logger.error('Error detecting delay patterns', {
                error: error.message,
                inputDelays: delays.length
            });
            return [];
        }
    }
    
    /**
     * Map a stop name to a named Bristol area, or null if unrecognised.
     */
    extractAreaFromStopName(stopName: string): string | null {
        const areaKeywords = {
            'Temple Meads': ['Temple Meads', 'Redcliff'],
            'City Centre': ['The Centre', 'College Green', 'Park Street', 'Queen Square'],
            'Cabot Circus': ['Cabot Circus', 'The Horsefair'],
            'Clifton': ['Clifton', 'Whiteladies'],
            'Harbourside': ['Harbourside', 'Millennium Square'],
            'Stokes Croft': ['Stokes Croft', 'Gloucester Road'],
            'Bus Station': ['Bus Station']
        };

        for (const [area, keywords] of Object.entries(areaKeywords)) {
            if (keywords.some(keyword => stopName.includes(keyword))) {
                return area;
            }
        }
        
        return null;
    }
    
    /**
     * Analyze pattern significance for reporting.
     */
    analyzePatternSignificance(patterns: DelayPattern[]): PatternAnalysisResult {
        const timer = new PerformanceTimer('pattern_analysis', logger);
        
        try {
            let maxSignificance = 0;
            let primaryPattern: DelayPattern | undefined = undefined;
            let totalRoutesAffected = 0;
            let averageDelay = 0;
            
            if (patterns.length === 0) {
                timer.complete({ significance: 0, patterns: 0 });
                return {
                    hasCritical: false,
                    criticalCount: 0,
                    summary: 'No delay patterns detected',
                    significance: 0,
                    shouldReport: false,
                    details: {
                        patterns: [],
                        impactLevel: 'low',
                        affectedRoutes: [],
                        totalDelays: 0,
                        averageDelay: 0,
                        maxDelay: 0
                    }
                };
            }
            
            // Calculate significance based on pattern type and scope
            patterns.forEach(pattern => {
                let significance = 0;
                const routeCount = pattern.routes.length;
                const avgDelay = pattern.delays.reduce((sum, delay) => sum + delay, 0) / pattern.delays.length;
                
                switch (pattern.type) {
                    case 'network':
                        significance = 8 + Math.min(routeCount * 0.5, 4); // 8-12 points for network-wide
                        break;
                    case 'area':
                        significance = 5 + Math.min(routeCount * 0.3, 3); // 5-8 points for area-specific
                        break;
                    case 'single':
                        significance = Math.min(avgDelay * 0.2, 3); // 0-3 points for individual
                        break;
                }
                
                // Bonus for severe delays
                if (avgDelay > 15) significance += 2;
                if (avgDelay > 25) significance += 2;
                
                if (significance > maxSignificance) {
                    maxSignificance = significance;
                    primaryPattern = pattern;
                }
                
                totalRoutesAffected += routeCount;
                averageDelay += avgDelay;
            });
            
            averageDelay = averageDelay / patterns.length;
            const shouldReport = maxSignificance >= 4; // Report threshold
            
            let summary = '';
            if (primaryPattern) {
                const pattern = primaryPattern as DelayPattern;
                switch (pattern.type) {
                    case 'network':
                        summary = `Network-wide disruption: ${totalRoutesAffected} routes delayed by ${averageDelay.toFixed(1)} minutes on average`;
                        break;
                    case 'area':
                        summary = `Area disruption around ${(pattern as any).affectedArea}: ${pattern.routes.length} routes affected`;
                        break;
                    case 'single':
                        summary = `Individual delays: ${patterns.length} route${patterns.length > 1 ? 's' : ''} experiencing issues`;
                        break;
                }
            }
            
            const affectedRoutesArray = Array.from(new Set(patterns.flatMap(p => p.routes)));
            const totalDelays = patterns.reduce((sum, p) => sum + p.delays.reduce((s, d) => s + d, 0), 0);
            const maxDelay = Math.max(...patterns.flatMap(p => p.delays));
            const hasCritical = maxSignificance >= 8;
            const criticalPatterns = patterns.filter(p => p.severity === 'high');
            const patternType = primaryPattern ? (primaryPattern as DelayPattern).type : undefined;

            
            const result: PatternAnalysisResult = {
                hasCritical,
                criticalCount: criticalPatterns.length,
                summary: summary || 'No significant patterns detected',
                significance: maxSignificance,
                shouldReport,
                primaryPattern: primaryPattern,
                affectedRoutes: affectedRoutesArray,
                averageDelay: Math.round(averageDelay),
                details: {
                    patterns: patterns,
                    impactLevel: hasCritical ? 'critical' : maxSignificance >= 8 ? 'high' : maxSignificance >= 5 ? 'medium' : 'low',
                    affectedRoutes: affectedRoutesArray,
                    totalDelays: totalDelays,
                    averageDelay: Math.round(averageDelay),
                    maxDelay: maxDelay,
                    patternType: patternType,

                }
            };
            
            logger.info('[PATTERN_ANALYSIS] Pattern significance calculated', {
                significance: maxSignificance,
                shouldReport,
                patternType: patternType,
                routesAffected: totalRoutesAffected,
                averageDelay: averageDelay.toFixed(1)
            });
            
            timer.complete({
                significance: maxSignificance,
                patterns: patterns.length,
                shouldReport,
                routesAffected: totalRoutesAffected
            });
            
            return result;
            
        } catch (error: any) {
            timer.fail(error);
            logger.error('Error analyzing pattern significance', {
                error: error.message,
                patterns: patterns.length
            });
            
            return {
                hasCritical: false,
                criticalCount: 0,
                summary: 'Error analyzing patterns',
                significance: 0,
                shouldReport: false,
                details: {
                    patterns: [],
                    impactLevel: 'low',
                    affectedRoutes: [],
                    totalDelays: 0,
                    averageDelay: 0,
                    maxDelay: 0
                }
            };
        }
    }
    
    /**
     * Get current network pattern status
     * Provides overview of all active patterns
     */
    getNetworkPatternStatus(): any {
        const recentDelays = this.appState.getRecentDelayEvents(30); // Last 30 minutes
        const patterns = this.detectDelayPatterns(recentDelays);
        const analysis = this.analyzePatternSignificance(patterns);
        
        return {
            timestamp: new Date().toISOString(),
            totalPatterns: patterns.length,
            analysis: analysis,
            networkHealth: this.calculateNetworkHealth(patterns, recentDelays.length)
        };
    }
    
    /**
     * Calculate overall network health score
     */
    private calculateNetworkHealth(patterns: DelayPattern[], totalEvents: number): 'good' | 'fair' | 'poor' | 'disrupted' {
        if (totalEvents === 0) return 'good';
        
        const networkPatterns = patterns.filter(p => p.type === 'network');
        const areaPatterns = patterns.filter(p => p.type === 'area');
        
        if (networkPatterns.length > 0) return 'disrupted';
        if (areaPatterns.length >= 2) return 'poor';
        if (areaPatterns.length === 1 || patterns.length >= 5) return 'fair';
        
        return 'good';
    }
    
    /**
     * Get area keyword count for diagnostics
     */
    private getAreaKeywordCount(): number {
        const areaKeywords = {
            'Temple Meads': ['Temple Meads', 'Redcliff'],
            'City Centre': ['The Centre', 'College Green', 'Park Street', 'Queen Square'],
            'Cabot Circus': ['Cabot Circus', 'The Horsefair'],
            'Clifton': ['Clifton', 'Whiteladies'],
            'Harbourside': ['Harbourside', 'Millennium Square'],
            'Stokes Croft': ['Stokes Croft', 'Gloucester Road'],
            'Bus Station': ['Bus Station']
        };
        
        return Object.keys(areaKeywords).length;
    }
    
    /**
     * Get service status
     */
    getStatus(): any {
        const recentDelays = this.appState.getRecentDelayEvents(30);
        const currentPatterns = this.detectDelayPatterns(recentDelays);
        
        return {
            name: 'Pattern Detector',
            status: 'running',
            config: {
                areaKeywords: this.getAreaKeywordCount(),
                networkThreshold: 4, // Routes needed for network pattern
                areaThreshold: 3     // Routes needed for area pattern
            },
            currentAnalysis: {
                recentEvents: recentDelays.length,
                patternsDetected: currentPatterns.length,
                patternTypes: currentPatterns.map(p => p.type),
                networkHealth: this.calculateNetworkHealth(currentPatterns, recentDelays.length)
            }
        };
    }
    
    /**
     * Close service and cleanup resources
     */
    async close(): Promise<void> {
        logger.info('Pattern Detector service stopped');
    }
}