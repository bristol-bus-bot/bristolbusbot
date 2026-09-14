// Posting, rate limiting and engagement tracking for social platforms.

import { BskyAgent, RichText } from '@atproto/api';
import { DateTime } from 'luxon';
import { logger, PerformanceTimer, TARGET_TIMEZONE, logSummary, logDetailed, logAlways } from '../utils/logging.js';
import { ApplicationState } from './application-state.js';
import { DatabaseManager } from './database-manager.js';
import { AICommentary } from './ai-commentary.js';
import type { BusEvent, SocialMediaPost } from '../types/bus-types.js';
import { latestStoryEvents, observationIssue } from './story-brief.js';
import { observationPost, reservePost } from './posting-fallback.js';
import { selectStory, type PublishedStory } from './story-selection.js';

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
    private processingStory = false;
    private recentStories: PublishedStory[] = [];
    private storyProvider: (() => BusEvent[]) | null = null;

    setStoryProvider(provider: () => BusEvent[]): void {
        this.storyProvider = provider;
    }

    private observationValidator: ((event: BusEvent) => boolean) | null = null;
    private publicationValidator: ((event: BusEvent) => boolean) | null = null;

    setObservationValidator(validate: (event: BusEvent) => boolean): void {
        this.observationValidator = validate;
    }

    setPublicationValidator(validate: (event: BusEvent) => boolean): void {
        this.publicationValidator = validate;
    }

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
    async postUpdate(postText: string, busEvent: BusEvent | null): Promise<{ bluesky: boolean; stale?: boolean }> {
        const timer = new PerformanceTimer('social_media_post', logger);

        this.appState.resetDailyCounters();
        if (this.hasReachedDailyLimit()) return { bluesky: false };
        if ((busEvent?.collectorEventId !== undefined || busEvent?.source === 'live_snapshot')
            && (observationIssue(busEvent)
                || !(this.publicationValidator || this.observationValidator)?.(busEvent))) {
            logSummary('info', '[POST_SKIP] Observation could not be confirmed before publication');
            return { bluesky: false, stale: true };
        }

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
                    eventType: (busEvent?.eventType || 'editorial'),
                    route: busEvent?.line,
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

            logger.info(`[DUAL_POST] About to post: "${finalPostText}" (${finalPostText.length} chars, type: ${(busEvent?.eventType || 'editorial')}, significance: ${(busEvent?.significance || 0)})`);

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

                        // Detect URL/mention facets so an approved source URL is
                        // a real clickable link rather than decorative text.
                        const richText = new RichText({ text: finalPostText });
                        await richText.detectFacets(this.bskyAgent);

                        // Make the actual post and capture the response URI
                        const postResponse = await this.bskyAgent.post({
                            text: richText.text,
                            facets: richText.facets,
                        });

                        // Extract the post rkey from the AT Protocol URI (at://did:plc:xxx/app.bsky.feed.post/rkey)
                        const postUri = postResponse?.uri || '';
                        const postRkey = postUri.split('/').pop() || '';
                        const postUrl = postRkey ? `https://bsky.app/profile/${this.socialConfig.handle}/post/${postRkey}` : '';

                        logger.info(`--- Successfully posted to BlueSky! --- ${postUrl ? `URL: ${postUrl}` : ''}`);
                        blueskySuccess = true;
                        try {
                            this.aiCommentary?.recordPublished(finalPostText);
                        } catch (error: any) {
                            // A ledger failure must not resubmit an already published post.
                            logger.error('Published post could not update editorial memory', { error: error.message });
                        }

                        // Store engagement analytics with vehicle ref and post URI
                        if (this.databaseManager) {
                            await this.databaseManager.storeEngagementRecord({
                                postContent: finalPostText,
                                postType: (busEvent?.eventType || 'editorial'),
                                significance: (busEvent?.significance || 0),
                                postUri,
                                event: busEvent || undefined,
                            });
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
                                route: busEvent?.line,
                                error: error.message,
                                retryCount: attempt
                            });

                            // Wait before retrying
                            await new Promise(resolve => setTimeout(resolve, retryDelay));
                        } else {
                            // All retries exhausted
                            logger.error("--- Bluesky final network timeout after all attempts ---", {
                                route: busEvent?.line,
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
                eventType: (busEvent?.eventType || 'editorial'),
                route: busEvent?.line,
                significance: (busEvent?.significance || 0),
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
    if (this.processingStory) return;
    this.processingStory = true;
    let publishing = false;
    try {
        this.appState.resetDailyCounters();
        if (this.hasReachedDailyLimit()) return;
        const collected = this.appState.getAndClearBusEvents();
        let snapshots: BusEvent[] = [];
        try { snapshots = this.storyProvider?.() || []; }
        catch (error: any) { logger.warn('Current story lookup failed', { error: error.message }); }
        const candidates = latestStoryEvents([...collected, ...snapshots])
            .filter(event => !this.observationValidator || this.observationValidator(event));
        logAlways('info', `[POSTING] ${collected.length} queued, ${snapshots.length} current, ${candidates.length} eligible`);
        let event = selectStory(candidates, this.recentStories);
        if (event) {
            logAlways('info', `[STORY_SELECTION] ${event.eventType} route ${event.line}; pool ${candidates.filter(item => item.eventType === 'punctual').length} on-time, ${candidates.filter(item => item.eventType !== 'punctual').length} late/early`);
            try { event = await this.databaseManager?.enrichStoryJourney(event) || event; }
            catch (error: any) { logger.warn('Journey context unavailable', { error: error.message }); }
        }
        let text: string | null = null;
        let fallbackReason = event ? 'writer_unavailable' : 'no_eligible_observation';
        if (event && this.aiCommentary) {
            try { text = await this.aiCommentary.generatePost(event); }
            catch (error: any) { logger.warn('Writer failed; using factual fallback', { error: error.message }); }
            fallbackReason = 'writer_rejected_or_failed';
        }
        const current = (item: BusEvent) => !observationIssue(item)
            && (!this.observationValidator || this.observationValidator(item));
        const validatePublication = this.publicationValidator || this.observationValidator;
        const publishable = (item: BusEvent) => !observationIssue(item)
            && (!validatePublication || validatePublication(item));
        if (event && !publishable(event)) {
            // Re-select only if the observation expired or its run/quality changed.
            let refreshed: BusEvent[] = [];
            try { refreshed = this.storyProvider?.() || []; } catch { /* reserve below */ }
            event = selectStory(latestStoryEvents(refreshed).filter(current), this.recentStories);
            text = null;
            fallbackReason = 'observation_no_longer_publishable';
        }
        const usedFallback = !text;
        if (!text && event) {
            text = observationPost(event);
            // Do not truncate away qualifying evidence on unusually long names.
            if (text.length > Math.min(300, this.socialConfig.postLimit || 300)) {
                event = null;
                text = null;
                fallbackReason = 'observation_exceeds_post_limit';
            }
        }
        if (usedFallback) {
            logAlways('info', `[POSTING_FALLBACK] ${event ? 'observation' : 'reserve'}: ${fallbackReason}`);
        }
        text ||= reservePost();
        publishing = true;
        let result = await this.postUpdate(text, event);
        // A last-instant freshness rejection has made no network request. Reserve
        // prose is safe to send; an uncertain network result must not send a second post.
        if (result.stale) {
            logAlways('info', '[POSTING_FALLBACK] reserve: publication_recheck_failed');
            result = await this.postUpdate(reservePost(), null);
            event = null;
        }
        if (result.bluesky && event) this.recentStories = [...this.recentStories, event].slice(-6);
        if (!result.bluesky) logger.error('[POSTING] Scheduled post could not be delivered');
    } catch (error: any) {
        logger.error('Posting cycle failed', { error: error.message });
        if (!publishing) {
            try { await this.postUpdate(reservePost(), null); }
            catch (fallbackError: any) { logger.error('Reserve post failed', { error: fallbackError.message }); }
        }
    } finally {
        this.processingStory = false;
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
