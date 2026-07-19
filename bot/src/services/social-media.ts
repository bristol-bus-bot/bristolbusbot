// Posting, rate limiting and engagement tracking for social platforms.

import { BskyAgent } from '@atproto/api';
import { DateTime } from 'luxon';
import { logger, PerformanceTimer, TARGET_TIMEZONE, logSummary, logDetailed, logAlways } from '../utils/logging.js';
import { ApplicationState } from './application-state.js';
import { DatabaseManager } from './database-manager.js';
import { AICommentary } from './ai-commentary.js';
import type { BusEvent, SocialMediaPost } from '../types/bus-types.js';

/**
 * Handle Bluesky publishing and optional platform integrations.
 */
export class SocialMediaManager {
    private socialConfig: any;
    private appState: ApplicationState;
    private databaseManager: DatabaseManager | null = null;
    private aiCommentary: AICommentary | null = null;
    private bskyAgent: BskyAgent;
    private postingInterval: NodeJS.Timeout | null = null;
    private followerInterval: NodeJS.Timeout | null = null;

    constructor(socialConfig: any, appState: ApplicationState) {
        this.socialConfig = socialConfig;
        this.appState = appState;

        this.bskyAgent = new BskyAgent({ service: 'https://bsky.social' });

        logger.info('Social Media Manager initialized', {
            bluesky: {
                handle: this.socialConfig.handle ? `${this.socialConfig.handle.substring(0, 10)}...` : 'NOT_SET',
                testMode: this.socialConfig.testMode,
                dailyLimit: this.socialConfig.dailyLimit,
                postLimit: this.socialConfig.postLimit
            }
        });
    }
    
    /**
     * Get the Bluesky handle (for constructing post URLs)
     */
    getHandle(): string {
        return this.socialConfig.handle || 'bristolbusbot.live';
    }

    /**
     * Initialize social media service
     */
    async initialize(): Promise<void> {
        if (!this.socialConfig.handle || !this.socialConfig.appPassword) {
            logger.warn('Bluesky credentials not configured. Bluesky posting will be disabled.');
        } else {
            logger.info('Bluesky ready for posting', {
                testMode: this.socialConfig.testMode,
                postLimit: this.socialConfig.postLimit
            });
        }

        logger.info('Social Media Manager ready for posting', {
            bluesky: !!this.socialConfig.handle
        });
    }
    
    /**
     * Set the database manager used for delivery records.
     */
    setDatabaseManager(databaseManager: DatabaseManager): void {
        this.databaseManager = databaseManager;
    }
    
    /**
     * Set the commentary service used for posts.
     */
    setAICommentary(aiCommentary: AICommentary): void {
        this.aiCommentary = aiCommentary;
        logger.info('[SOCIAL_MEDIA] AI Commentary service injected', {
            hasAICommentary: !!this.aiCommentary
        });
    }
    
    /**
     * Publish an update with bounded retries.
     */
    async postUpdate(postText: string, busEvent: BusEvent): Promise<{ bluesky: boolean }> {
        const timer = new PerformanceTimer('social_media_post', logger);

        let blueskySuccess = false;

        try {
            // Test mode: count and log the would-be post, publish nothing.
            if (this.socialConfig.testMode) {
                const previousCount = this.appState.postsTodayCount;
                this.appState.incrementPostCount();
                logger.info({ message: `[TEST MODE] Post (Len: ${postText.length})`, postText });
                logger.info(`[STATE_CHANGE] Test post counted. Daily posts: ${previousCount} → ${this.appState.postsTodayCount}`);

                timer.complete({
                    testMode: true,
                    postLength: postText.length,
                    eventType: busEvent.eventType,
                    route: busEvent.line,
                    platforms: { bluesky: true }
                });
                return { bluesky: true };
            }

            // Validate post text
            if (!postText) {
                logger.error("Empty post text");
                timer.fail(new Error('Empty post'));
                return { bluesky: false };
            }

            // Truncate the post if it exceeds the platform limit.
            const finalPostText = postText.length > this.socialConfig.postLimit
                ? postText.substring(0, this.socialConfig.postLimit - 3) + "..."
                : postText;

            logger.info(`[DUAL_POST] About to post: "${finalPostText}" (${finalPostText.length} chars, type: ${busEvent.eventType}, significance: ${busEvent.significance})`);

            // POST TO BLUESKY
            if (this.socialConfig.handle && this.socialConfig.appPassword) {
                const MAX_RETRIES = 3;
                const RETRY_DELAYS = [5000, 10000, 20000]; // 5s, 10s, 20s
                let lastError: any = null;

                for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
                    try {
                        // Login to Bluesky with timeout handling
                        await this.bskyAgent.login({
                            identifier: this.socialConfig.handle,
                            password: this.socialConfig.appPassword
                        });

                        // Make the actual post and capture the response URI
                        const postResponse = await this.bskyAgent.post({ text: finalPostText });

                        // Extract the post rkey from the AT Protocol URI (at://did:plc:xxx/app.bsky.feed.post/rkey)
                        const postUri = postResponse?.uri || '';
                        const postRkey = postUri.split('/').pop() || '';
                        const postUrl = postRkey ? `https://bsky.app/profile/${this.socialConfig.handle}/post/${postRkey}` : '';

                        logger.info(`--- Successfully posted to BlueSky! --- ${postUrl ? `URL: ${postUrl}` : ''}`);
                        blueskySuccess = true;

                        // Store engagement analytics with vehicle ref and post URI
                        if (this.databaseManager) {
                            await this.databaseManager.storeEngagementRecord(postText, busEvent.eventType, busEvent.significance, busEvent.vehicleRef, postUri);
                        }

                        const previousCount = this.appState.postsTodayCount;
                        this.appState.incrementPostCount();
                        logger.info(`[STATE_CHANGE] Successful BlueSky post. Daily posts: ${previousCount} → ${this.appState.postsTodayCount}`);

                        break; // Success, exit retry loop

                    } catch (error: any) {
                        lastError = error;
                        const isNetworkError = error.message?.includes('fetch failed') ||
                                              error.message?.includes('ECONNRESET') ||
                                              error.message?.includes('ETIMEDOUT') ||
                                              error.message?.includes('socket hang up') ||
                                              error.message?.includes('network') ||
                                              error.code === 'ECONNRESET' ||
                                              error.code === 'ETIMEDOUT';

                        // If it's not a network error (e.g., authentication issue), don't retry
                        if (!isNetworkError) {
                            logger.error("--- Non-network error posting to Bluesky (not retrying): ---", {
                                err: {
                                    status: 1,
                                    error: error.message,
                                    success: false
                                }
                            });
                            break; // Exit retry loop
                        }

                        // Network error - retry if attempts remaining
                        if (attempt < MAX_RETRIES) {
                            const retryDelay = RETRY_DELAYS[attempt - 1];
                            logger.warn(`🔄 Bluesky network error, retrying in ${retryDelay/1000}s (attempt ${attempt}/${MAX_RETRIES})`, {
                                route: busEvent.line,
                                error: error.message,
                                retryCount: attempt
                            });

                            // Wait before retrying
                            await new Promise(resolve => setTimeout(resolve, retryDelay));
                        } else {
                            // All retries exhausted
                            logger.error("--- Bluesky final network timeout after all attempts ---", {
                                route: busEvent.line,
                                totalAttempts: MAX_RETRIES,
                                lastError: error.message
                            });
                        }
                    }
                }

                if (!blueskySuccess && lastError) {
                    logger.error("--- Error posting to Bluesky: ---", {
                        err: {
                            status: 1,
                            error: lastError?.message || 'Unknown error',
                            success: false
                        }
                    });
                }
            }

            // Complete timer with results
            timer.complete({
                testMode: false,
                postLength: finalPostText.length,
                eventType: busEvent.eventType,
                route: busEvent.line,
                significance: busEvent.significance,
                platforms: { bluesky: blueskySuccess }
            });

            return { bluesky: blueskySuccess };

        } catch (error: any) {
            timer.fail(error);
            logger.error("--- Error posting to Bluesky: ---", { err: error });
            return { bluesky: blueskySuccess };
        }
    }
    
    /**
     * Post a prepared network summary.
     */
    async postSummary(summaryText: string): Promise<boolean> {
        const timer = new PerformanceTimer('social_media_summary', logger);

        try {
            // Create a synthetic bus event for summary posts
            const summaryEvent: BusEvent = {
                timestamp: DateTime.now().setZone(TARGET_TIMEZONE).toISO() ?? "",
                vehicleRef: 'NETWORK',
                datedJourneyRef: 'SUMMARY',
                line: 'NETWORK',
                direction: 'summary',
                originAimedDepartureTimeStr: DateTime.now().setZone(TARGET_TIMEZONE).toISO() || '',
                delayMinutes: 0,
                lastStopCode: 'NETWORK',
                lastStopTime: DateTime.now().setZone(TARGET_TIMEZONE).toFormat('HH:mm:ss'),
                lastStopName: 'Network Summary',
                eventType: 'punctual',
                significance: 5 // Medium significance for summaries
            };

            const results = await this.postUpdate(summaryText, summaryEvent);

            const posted = results.bluesky;

            if (posted) {
                this.appState.incrementSummaryCount();
                logger.info('Network summary posted successfully', {
                    summaryLength: summaryText.length,
                    bluesky: results.bluesky
                });
            }

            timer.complete({
                posted,
                summaryLength: summaryText.length,
                testMode: this.socialConfig.testMode,
                platforms: results
            });

            return posted;

        } catch (error: any) {
            timer.fail(error);
            logger.error('Error posting network summary', { error: error.message });
            return false;
        }
    }
    
    /**
     * Check if daily posting limit has been reached
     */
    hasReachedDailyLimit(): boolean {
        if (this.socialConfig.dailyLimit <= 0) {
            return false; // No limit set
        }
        
        const hasReached = this.appState.postsTodayCount >= this.socialConfig.dailyLimit;
        
        if (hasReached) {
            logger.warn(`Daily posting limit reached: ${this.appState.postsTodayCount}/${this.socialConfig.dailyLimit}`);
        }
        
        return hasReached;
    }
    
    /**
     * Get posting statistics for the day
     */
    getDailyStats(): any {
        if (!this.appState) {
            return {
                postsToday: 0,
                summariesToday: 0,
                dailyLimit: this.socialConfig.dailyLimit,
                remainingPosts: this.socialConfig.dailyLimit,
                lastResetDate: 'N/A',
                testMode: this.socialConfig.testMode
            };
        }

        return {
            postsToday: this.appState.postsTodayCount,
            summariesToday: this.appState.summariesPosted,
            dailyLimit: this.socialConfig.dailyLimit,
            remainingPosts: Math.max(0, this.socialConfig.dailyLimit - this.appState.postsTodayCount),
            lastResetDate: this.appState.lastResetDate,
            testMode: this.socialConfig.testMode
        };
    }
    
    /**
     * Create social media post record for tracking
     */
    private createPostRecord(text: string, event: BusEvent, posted: boolean): SocialMediaPost {
        return {
            id: `${Date.now()}_${event.line}_${event.eventType}`,
            text,
            timestamp: DateTime.now().setZone(TARGET_TIMEZONE).toISO() ?? '',
            platform: 'bluesky',
            engagement: {
                likes: 0,
                reposts: 0,
                replies: 0
            },
            metadata: {
                event,
                persona: 'ai-generated',
                postType: event.eventType === 'punctual' && event.line === 'NETWORK' ? 'summary' : 'event'
            }
        };
    }
    
    /**
     * Validate post content before sending
     */
    private validatePostContent(text: string): { valid: boolean; reason?: string } {
        if (!text || text.trim().length === 0) {
            return { valid: false, reason: 'Empty post text' };
        }
        
        if (text.length > this.socialConfig.postLimit) {
            return { valid: false, reason: `Post too long: ${text.length} > ${this.socialConfig.postLimit}` };
        }
        
        // Reject malformed content.
        if (text.includes('undefined') || text.includes('null')) {
            return { valid: false, reason: 'Post contains undefined/null values' };
        }
        
        return { valid: true };
    }
    
    /**
     * Validate and publish with bounded retries.
     */
    async postWithValidation(postText: string, busEvent: BusEvent, retries: number = 1): Promise<boolean> {
        const timer = new PerformanceTimer('social_media_validated_post', logger);
        
        try {
            // Validate content first
            const validation = this.validatePostContent(postText);
            if (!validation.valid) {
                logger.warn('Post validation failed', {
                    reason: validation.reason,
                    postText: postText.substring(0, 100)
                });
                timer.fail(new Error(`Validation failed: ${validation.reason}`));
                return false;
            }
            
            // Check daily limit
            if (this.hasReachedDailyLimit()) {
                logger.warn('Daily posting limit reached, skipping post');
                timer.fail(new Error('Daily limit reached'));
                return false;
            }
            
            // Attempt posting with retries
            let lastError: Error | null = null;
            for (let attempt = 1; attempt <= retries; attempt++) {
                try {
                    logger.info(`Attempting post (${attempt}/${retries})`, {
                        route: busEvent.line,
                        eventType: busEvent.eventType,
                        textLength: postText.length
                    });
                    
                    const result = await this.postUpdate(postText, busEvent);
                    
                    if (result) {
                        timer.complete({
                            posted: true,
                            attempts: attempt,
                            route: busEvent.line,
                            eventType: busEvent.eventType
                        });
                        return true;
                    }
                    
                } catch (error: any) {
                    lastError = error;
                    logger.warn(`Post attempt ${attempt} failed`, {
                        error: error.message,
                        attemptsRemaining: retries - attempt
                    });
                    
                    // Wait before retry (exponential backoff)
                    if (attempt < retries) {
                        const waitTime = Math.pow(2, attempt) * 1000; // 2s, 4s, 8s...
                        await new Promise(resolve => setTimeout(resolve, waitTime));
                    }
                }
            }
            
            // All retries failed
            timer.fail(lastError || new Error('All retry attempts failed'));
            logger.error('All posting attempts failed', {
                route: busEvent.line,
                eventType: busEvent.eventType,
                attempts: retries,
                lastError: lastError?.message
            });
            
            return false;
            
        } catch (error: any) {
            timer.fail(error);
            logger.error('Error in validated posting', { error: error.message });
            return false;
        }
    }
    
    /**
     * Get service status
     */
    getStatus(): any {
        return {
            name: 'Social Media Manager',
            status: this.socialConfig.handle && this.socialConfig.appPassword ? 'ready' : 'disabled',
            config: {
                platform: 'bluesky',
                handle: this.socialConfig.handle ? `${this.socialConfig.handle.substring(0, 10)}...` : 'NOT_SET',
                testMode: this.socialConfig.testMode,
                dailyLimit: this.socialConfig.dailyLimit,
                postLimit: this.socialConfig.postLimit
            },
            dailyStats: this.getDailyStats(),
            agent: {
                connected: !!this.bskyAgent,
                service: 'https://bsky.social'
            }
        };
    }
    
    /**
     * Start the periodic posting service — processes the event collector
     * every 20 minutes.
     */
    startPeriodicPosting(): void {
        // Prevent multiple posting intervals.
        if (this.postingInterval || this.followerInterval) {
            logger.warn('Periodic posting already started, clearing old intervals first');
            this.clearIntervals();
        }

        logger.info('Starting periodic posting service (20-minute intervals)');

        // Initial posting check (fire and forget with error handling)
        this.processEventCollector().catch(error => {
            logger.error('Error in initial processEventCollector call', { error: error.message });
        });

        // Set up 20-minute interval and STORE the ID
        this.postingInterval = setInterval(() => {
            this.processEventCollector().catch(error => {
                logger.error('Error in periodic processEventCollector call', { error: error.message });
            });
        }, 20 * 60 * 1000); // 20 minutes

        // Fetch follower counts on startup and then hourly
        this.updateFollowerCounts().catch(error => {
            logger.error('Error in initial follower count fetch', { error: error.message });
        });

        // Set up hourly interval and STORE the ID
        this.followerInterval = setInterval(() => {
            this.updateFollowerCounts().catch(error => {
                logger.error('Error in periodic follower count fetch', { error: error.message });
            });
        }, 60 * 60 * 1000); // 1 hour

        logger.info('Periodic posting service started successfully', {
            postingIntervalActive: !!this.postingInterval,
            followerIntervalActive: !!this.followerInterval
        });
    }

    /**
     * Clear all intervals
     */
    private clearIntervals(): void {
        if (this.postingInterval) {
            clearInterval(this.postingInterval);
            this.postingInterval = null;
            logger.info('Cleared posting interval');
        }
        if (this.followerInterval) {
            clearInterval(this.followerInterval);
            this.followerInterval = null;
            logger.info('Cleared follower interval');
        }
    }
    
    /**
     * Process the event collector: filter, select one event, generate
     * commentary and post it.
     */
public async processEventCollector(): Promise<void> {
    logAlways('info', '[POSTING] ▶️ processEventCollector() called');

    try {
        // Safety check - ensure appState is initialized
        logAlways('info', `[POSTING] AppState check: exists=${!!this.appState}, hasMethod=${this.appState ? typeof this.appState.getAndClearBusEvents === 'function' : 'N/A'}`);

        if (!this.appState || typeof this.appState.getAndClearBusEvents !== 'function') {
            logAlways('warn', '[POSTING] ❌ AppState not ready yet, skipping this cycle');
            return;
        }

        const now = Date.now();
        const events = this.appState.getAndClearBusEvents();

        logAlways('info', `[POSTING] Retrieved ${events.length} events from collector`);
        
        if (events.length === 0) {
            logSummary('info', `💤 No events collected - skipping posting cycle`);
            logDetailed('info', "Event collector empty - no posts to generate this cycle");
            return;
        }
        
        // Log the posting decision.
        logSummary('info', `🚀 POSTING: Processing ${events.length} events for potential posts`);
        logDetailed('info', `--- Processing ${events.length} events for potential posting ---`);
        
        // Determine if we're in rush hour
        const currentTime = DateTime.now().setZone(TARGET_TIMEZONE);
        const hour = currentTime.hour;
        const isWeekday = currentTime.weekday <= 5;
        const isRushHour = isWeekday && ([7, 8, 9, 17, 18, 19].includes(hour));
        
        // Filter out buses that are too late based on time of day
        const maxDelay = isRushHour ? 24 : 15; // 24 mins max during rush hour, 15 otherwise
        const postableEvents = events.filter(e => {
            if (e.eventType === 'delay' && e.delayMinutes > maxDelay) {
                logDetailed('info', `[FILTER] ${e.line} excluded: ${e.delayMinutes}min delay exceeds ${maxDelay}min limit (${isRushHour ? 'rush hour' : 'off-peak'})`);
                return false;
            }
            if (e.eventType === 'early' && Math.abs(e.delayMinutes) > 10) {
                logDetailed('info', `[FILTER] ${e.line} excluded: ${Math.abs(e.delayMinutes)}min early exceeds 10min limit`);
                return false;
            }
            return true;
        });
        
        if (postableEvents.length === 0) {
            logSummary('info', `💤 No suitable events (all exceed delay limits) - skipping posting`);
            return;
        }

        // Prefer Bristol and South Gloucestershire events over Bath and Weston.
        const getArea = (stopCode?: string): string => {
            if (!stopCode) return 'Bristol';
            const code = stopCode.toLowerCase();
            if (code.startsWith('wsm')) return 'Weston-super-Mare';
            if (code.startsWith('bth')) return 'Bath';
            if (code.startsWith('sgl')) return 'South Gloucestershire';
            if (code.startsWith('bst')) return 'Bristol';
            return 'Bristol';
        };

        const bristolAndSouthGlosEvents = postableEvents.filter(e => {
            const area = getArea(e.lastStopCode);
            return area === 'Bristol' || area === 'South Gloucestershire';
        });

        const bathAndWestonEvents = postableEvents.filter(e => {
            const area = getArea(e.lastStopCode);
            return area === 'Bath' || area === 'Weston-super-Mare';
        });

        // Only use Bath/Weston events if there are NO Bristol/South Glos events
        const eventsToSelect = bristolAndSouthGlosEvents.length > 0 ? bristolAndSouthGlosEvents : bathAndWestonEvents;

        if (eventsToSelect.length === 0) {
            logSummary('info', `💤 No events in coverage area - skipping posting`);
            return;
        }

        logDetailed('info', `[GEO_FILTER] ${bristolAndSouthGlosEvents.length} Bristol/South Glos, ${bathAndWestonEvents.length} Bath/Weston → Using ${eventsToSelect.length} events`);

        // Mix of strategies for variety
        const strategy = Math.random();
        let topEvent;
        
        if (strategy < 0.3) {
            // 30% chance: Pick highest significance (but filtered)
            const sorted = eventsToSelect.sort((a, b) => b.significance - a.significance);
            topEvent = sorted[0];
            logDetailed('info', `[STRATEGY] Picking highest significance: ${topEvent.line} (sig: ${topEvent.significance})`);

        } else if (strategy < 0.6) {
            // 30% chance: Pick a minor delay (4-10 mins) or small early (3-5 mins)
            const minorEvents = eventsToSelect.filter(e =>
                (e.eventType === 'delay' && e.delayMinutes >= 4 && e.delayMinutes <= 10) ||
                (e.eventType === 'early' && Math.abs(e.delayMinutes) >= 3 && Math.abs(e.delayMinutes) <= 5) ||
                (e.eventType === 'punctual')
            );

            if (minorEvents.length > 0) {
                topEvent = minorEvents[Math.floor(Math.random() * minorEvents.length)];
                logDetailed('info', `[STRATEGY] Picking random minor event: ${topEvent.line} (${topEvent.delayMinutes}min ${topEvent.eventType})`);
            } else {
                // Fallback to any random event
                topEvent = eventsToSelect[Math.floor(Math.random() * eventsToSelect.length)];
                logDetailed('info', `[STRATEGY] No minor events, picking random: ${topEvent.line}`);
            }

        } else if (strategy < 0.85) {
            // 25% chance: Pick moderate delays (11-20 mins during rush, 11-15 off-peak)
            const moderateMax = isRushHour ? 20 : 15;
            const moderateEvents = eventsToSelect.filter(e =>
                e.eventType === 'delay' && e.delayMinutes >= 11 && e.delayMinutes <= moderateMax
            );

            if (moderateEvents.length > 0) {
                topEvent = moderateEvents[Math.floor(Math.random() * moderateEvents.length)];
                logDetailed('info', `[STRATEGY] Picking moderate delay: ${topEvent.line} (${topEvent.delayMinutes}min)`);
            } else {
                // Fallback to highest significance
                const sorted = eventsToSelect.sort((a, b) => b.significance - a.significance);
                topEvent = sorted[0];
                logDetailed('info', `[STRATEGY] No moderate delays, picking highest sig: ${topEvent.line}`);
            }

        } else {
            // 15% chance: Completely random from available events
            topEvent = eventsToSelect[Math.floor(Math.random() * eventsToSelect.length)];
            logDetailed('info', `[STRATEGY] Picking completely random: ${topEvent.line} (${topEvent.delayMinutes}min ${topEvent.eventType})`);
        }
        
        // Summary: Show what will be posted
        logSummary('info', `📱 TOP EVENT: ${topEvent.line} (${Math.abs(topEvent.delayMinutes)}min ${topEvent.eventType} at ${topEvent.lastStopName}, sig:${topEvent.significance})`);
        
        // Generate AI commentary for the event
        let postText = null;
        logAlways('info', `[POSTING_DEBUG] Checking aiCommentary: ${!!this.aiCommentary}`);
        if (this.aiCommentary) {
            logAlways('info', `[POSTING_DEBUG] Calling AI generatePost for ${topEvent.line}`);
            try {
                postText = await this.aiCommentary.generatePost(topEvent);
                if (postText) {
                    logSummary('info', `✅ AI: Generated post for ${topEvent.line} - "${postText}"`);
                } else {
                    logSummary('info', `❌ AI: Failed to generate post for ${topEvent.line}, using fallback`);
                }
            } catch (error: any) {
                logSummary('warn', `⚠️ AI: Error generating post for ${topEvent.line} - ${error.message}`);
            }
        }
        
// Fallback templates used when commentary generation is unavailable.
if (!postText) {
    const delayText = topEvent.eventType === 'delay' ? 
        `running ${topEvent.delayMinutes} minutes late` :
        topEvent.eventType === 'early' ? 
        `${Math.abs(topEvent.delayMinutes)} minutes early` :
        'on time';
    
    // Add the vehicle type when it is available.
    let vehicleType = '';
    if (topEvent.busDetails?.vehicle_type) {
        const vt = topEvent.busDetails.vehicle_type;
        if (vt.electric && vt.double_decker) {
            vehicleType = 'electric double-decker ';
        } else if (vt.double_decker) {
            vehicleType = 'double-decker ';
        } else if (vt.electric) {
            vehicleType = 'electric bus ';
        }
    }
    
    const templates = [
        `Route ${topEvent.line} is ${delayText} near ${topEvent.lastStopName}`,
        `The ${topEvent.line} ${vehicleType}finds itself ${delayText} at ${topEvent.lastStopName}`,
        `Service ${topEvent.line}: ${delayText} near ${topEvent.lastStopName}`,
        `${topEvent.line} ${vehicleType}currently ${delayText} passing ${topEvent.lastStopName}`,
        `Near ${topEvent.lastStopName}, the ${topEvent.line} is ${delayText}`,
        `${topEvent.direction === 'inbound' ? 'Inbound' : 'Outbound'} ${topEvent.line} ${vehicleType}${delayText} at ${topEvent.lastStopName}`
    ];
    
    postText = templates[Math.floor(Math.random() * templates.length)];
    logSummary('info', `📝 Using fallback template for ${topEvent.line}`);
}
        logDetailed('info', `[POSTING_READY] Selected: ${topEvent.line} (${topEvent.vehicleRef}): ${topEvent.delayMinutes}min ${topEvent.eventType} at ${topEvent.lastStopName} (significance: ${topEvent.significance})`);
        
        if (this.socialConfig.testMode) {
            logSummary('info', `🧪 TEST MODE: Would post about ${topEvent.line} ${topEvent.eventType}`);
            logSummary('info', `📄 POST TEXT: "${postText}"`);
            logDetailed('info', `[TEST_MODE] Would post about ${topEvent.line} ${topEvent.eventType}: "${postText}"`);
        } else {
            // Publish to Bluesky in production.
            const results = await this.postUpdate(postText, topEvent);

            if (results.bluesky) {
                logSummary('info', `✅ Posted to Bluesky: ${topEvent.line} ${topEvent.eventType}`);
            } else {
                logSummary('error', `❌ Failed to post to Bluesky: ${topEvent.line} ${topEvent.eventType}`);
            }

        }
        
    } catch (error: any) {
        logAlways('error', 'Error processing event collector', { error: error.message });
    }
}
    
    /**
     * Fetch and update follower counts from Bluesky
     * Called periodically to keep AI context aware of audience size
     */
    async updateFollowerCounts(): Promise<void> {
        const timer = new PerformanceTimer('social_media_follower_update', logger);

        try {
            // Only fetch if we have credentials and it's been at least 1 hour
            const now = DateTime.now().setZone(TARGET_TIMEZONE);
            if (this.appState.lastFollowerUpdate) {
                const hoursSinceUpdate = now.diff(this.appState.lastFollowerUpdate, 'hours').hours;
                if (hoursSinceUpdate < 1) {
                    logDetailed('info', `[FOLLOWER_UPDATE] Skipping - last update was ${Math.round(hoursSinceUpdate * 60)} minutes ago`);
                    return;
                }
            }

            if (!this.socialConfig.handle || !this.socialConfig.appPassword) {
                logDetailed('warn', '[FOLLOWER_UPDATE] No credentials configured, skipping');
                return;
            }

            // Ensure we're logged in
            if (!this.bskyAgent.session) {
                await this.bskyAgent.login({
                    identifier: this.socialConfig.handle,
                    password: this.socialConfig.appPassword
                });
            }

            // Fetch profile to get follower count
            const profile = await this.bskyAgent.getProfile({ actor: this.socialConfig.handle });

            if (profile.success && profile.data.followersCount !== undefined) {
                const previousCount = this.appState.blueskyFollowerCount;
                this.appState.blueskyFollowerCount = profile.data.followersCount;
                this.appState.lastFollowerUpdate = now;

                if (previousCount !== profile.data.followersCount) {
                    logSummary('info', `👥 Bluesky followers: ${previousCount} → ${profile.data.followersCount}`);
                }

                timer.complete({
                    blueskyFollowers: profile.data.followersCount,
                    change: profile.data.followersCount - previousCount
                });
            } else {
                throw new Error('Failed to fetch profile data');
            }

        } catch (error: any) {
            timer.fail(error);
            logDetailed('warn', `[FOLLOWER_UPDATE] Error: ${error.message}`);
        }
    }

    /**
     * Close service and cleanup resources
     */
    async close(): Promise<void> {
        try {
            // Clear all service intervals.
            this.clearIntervals();

            logger.info('Social Media Manager service stopped', {
                finalStats: this.getDailyStats(),
                intervalsCleared: true
            });

        } catch (error: any) {
            logger.warn('Error during social media service shutdown', {
                error: error.message
            });
        }
    }
    
    /**
     * Emergency disable (for rate limiting or API issues)
     */
    emergencyDisable(reason: string): void {
        logger.warn('Social Media Manager emergency disabled', {
            reason,
            timestamp: DateTime.now().setZone(TARGET_TIMEZONE).toISO() ?? ''
        });
        
        // Callers treat the warning as the disable signal; no state is mutated.
    }
    
    /**
     * Fetch recent posts from Bluesky (for AI context)
     */
    async fetchRecentPostsFromBluesky(limit: number = 5): Promise<string[]> {
        try {
            if (!this.bskyAgent) {
                return [];
            }

            // Ensure we're authenticated
            if (!this.bskyAgent.session) {
                await this.bskyAgent.login({
                    identifier: this.socialConfig.handle,
                    password: this.socialConfig.appPassword
                });
            }

            // Fetch author feed
            const feed = await this.bskyAgent.getAuthorFeed({
                actor: this.socialConfig.handle,
                limit
            });

            if (feed.success && feed.data.feed) {
                // Extract post text from feed
                const posts = feed.data.feed
                    .map((item: any) => item.post?.record?.text)
                    .filter((text: string) => text && text.length > 0)
                    .slice(0, limit);

                return posts;
            }

            return [];
        } catch (error: any) {
            logger.warn('[BLUESKY] Failed to fetch recent posts', { error: error.message });
            return [];
        }
    }

    /**
     * Get recent posts for debugging
     */
    getRecentPostsDebugInfo(): any {
        return {
            postsToday: this.appState.postsTodayCount,
            summariesToday: this.appState.summariesPosted,
            lastResetDate: this.appState.lastResetDate,
            testMode: this.socialConfig.testMode,
            dailyLimit: this.socialConfig.dailyLimit,
            postLimit: this.socialConfig.postLimit,
            hasCredentials: !!(this.socialConfig.handle && this.socialConfig.appPassword)
        };
    }
}
