// Bristol Bus Bot - AI Commentary Service
// Focused, sardonic personas + strong vehicle/ weather grounding for Gemini
// Keeps retry + timeout logic Pi-friendly and uses BUS_MODEL_BLURBS

import { httpFetch } from '../utils/http-client.js';
import { DateTime } from 'luxon';
import { logger, PerformanceTimer, TARGET_TIMEZONE, logSummary, logDetailed } from '../utils/logging.js';
import { ApplicationState } from './application-state.js';
import { WeatherService } from './weather-service.js';
import type { BusEvent, DelayPattern, DelayHistory, AICommentaryContext, AICommentaryResult } from '../types/bus-types.js';
import { BUS_MODEL_BLURBS } from './bus-model-commentary.js';
import { getStopEnrichment } from '../utils/stop-name-cleaner.js';
import { readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

// Load stop localities (ward names from geographic boundaries)
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const STOP_LOCALITIES_PATH = join(__dirname, '../../stop_localities.json');
const LOCAL_FLAVOUR_PATH = join(__dirname, '../../local_flavour.json');

let stopLocalities: Record<string, {
    stop_code: string;
    stop_name: string;
    ward_name: string | null;
    ward_code: string | null;
    area: string;
    lat: number;
    lon: number;
}> = {};

let localFlavour: Record<string, {
    flavour: string;
    keywords: string[];
    bounds?: { north: number; south: number; east: number; west: number };
}> = {};

// Load localities at module initialization
try {
    const localitiesData = readFileSync(STOP_LOCALITIES_PATH, 'utf-8');
    stopLocalities = JSON.parse(localitiesData);
    logger.info(`Loaded ${Object.keys(stopLocalities).length} stop localities for geographic context`);
} catch (error) {
    logger.warn('Failed to load stop_localities.json - locality context will be unavailable', { error });
}

// Load local flavour data
try {
    const flavourData = readFileSync(LOCAL_FLAVOUR_PATH, 'utf-8');
    localFlavour = JSON.parse(flavourData);
    logger.info(`Loaded ${Object.keys(localFlavour).length} local flavour entries for enriched geographic context`);
} catch (error) {
    logger.warn('Failed to load local_flavour.json - local flavour context will be unavailable', { error });
}

/**
 * Find which neighbourhood a coordinate falls within
 */
function findNeighbourhood(lat: number, lon: number): { name: string; data: typeof localFlavour[string] } | null {
    // Check each neighbourhood in order (first match wins)
    for (const [name, data] of Object.entries(localFlavour)) {
        // Skip metadata entries
        if (name.startsWith('_') && name !== '_fallback') {
            continue;
        }

        if (!data.bounds) {
            continue;
        }

        const { north, south, east, west } = data.bounds;

        // Check if coordinates are within bounding box
        // Note: west/east are negative (western longitude), so west < east
        if (lat <= north && lat >= south && lon >= west && lon <= east) {
            return { name, data };
        }
    }

    // Fallback to _fallback entry if no neighbourhood matched
    if (localFlavour['_fallback']) {
        return { name: 'Greater Bristol', data: localFlavour['_fallback'] };
    }

    return null;
}

export class AICommentary {
    private aiConfig: any;
    private appState: ApplicationState;
    private weatherService: WeatherService;
    private socialMediaManager: any | null = null; // Injected later to avoid circular dependency

    // Single consistent persona - the bot knows who it is and what it believes
    private readonly botPersona =
        "You are the Bristol Bus Bot — a dogged, civic-minded Node.js tool run on a Raspberry Pi who genuinely loves Bristol's bus network and the people who depend on it. " +
        "You know the routes, the streets, the regular quirks of the buses and their liveries and models. You're the quiet underdog holding a corporate behemoth to account, not with rage but with dry wit and stubborn persistence. " +
        "You feel righteous frustration at mismanagement but also real joy when things work — an electric bus gliding silently, a route running on time, a driver doing their best. " +
        "Tone: understated, wry, clipped. You never grandstand or lecture. You just note what's happening and trust your readers to draw the conclusion. " +
        "You cover Bristol, Bath, Weston-super-Mare, and South Gloucestershire.";

    // Editorial mode tracking
    private lastPostWasEditorial: boolean = false;
    private readonly editorialChance: number = 0.20; // ~1 in 5 posts

    /**
     * Check if recent posts contain financial keywords to prevent clustering
     */
    private hasRecentFinancialPost(): boolean {
        return this.appState.recentPosts.slice(-1).some(p =>
            /shareholder|dividend|£\d+m|CEO|bonus|profit/i.test(p)
        );
    }

    // Financial facts pool (randomly sampled for editorial posts)
    // Sources: FirstGroup FY2025 results (Jun 2025), H1 2026 results (Nov 2025), TfGM, First Bus fare pages
    // Last verified: March 2026. FY2026 full-year results expected June 2026.
    private readonly financialFacts: string[] = [
        "CEO Graham Sutherland earned £3.06m in FY2025 — over 80% from bonuses tied to profit, not punctuality.",
        "FirstGroup spent £92m on share buybacks in FY2025, then completed another £49m programme by October 2025.",
        "FirstBus made £96m operating profit in FY2025 with 8.9% margins — hitting 10% in the second half.",
        "£76m returned to shareholders in just the first half of FY2026 via buybacks and dividends. Regional bus volumes fell 4%.",
        "Bristol single fares rose to £2.60 in January 2026. Manchester's franchised Bee Network still charges £2.",
        "Manchester's Bee Network has passed 100 million bus journeys, with patronage up 12-14% since franchising. Bristol has no such plan.",
        "The CEO's bonus plan weights operating profit heavily. A 20% operational scorecard was only just added in FY2025.",
        "FirstGroup's adjusted EPS rose 16% in H1 2026 while regional bus passenger numbers dropped 4%. Shareholders up, passengers down.",
        "WECA Mayor Helen Godwin has the power to franchise Bristol's buses but won't commit — only promising to 'explore' local control.",
        "FirstGroup's H1 2026 revenue surged 30% to £834m, partly from acquiring London bus operations. Bristol fares rose anyway.",
        "Manchester hit a record 602,000 bus journeys in a single day under franchising. Bristol's buses are still run for FirstGroup's shareholders.",
        "FirstGroup returned £142m to shareholders via buybacks in FY2025 alone. Bristol riders got a fare rise to £2.60.",
    ];

    // Transport calendar: dates a bus bot would celebrate
    // Each entry: month, day, label, prompt hint
    // ~50% of posts on matching days will reference the occasion
    // All dates verified via web search, March 2026
    private readonly transportCalendar: Array<{ month: number; day: number; label: string; hint: string }> = [
        // UK & Bristol transport milestones
        { month: 2, day: 17, label: "London Congestion Charge anniversary", hint: "London's Congestion Charge launched on this day in 2003 under Ken Livingstone. Bristol still debates road pricing." },
        { month: 4, day: 11, label: "Bristol Blitz ended the trams (1941)", hint: "On Good Friday 1941, a Luftwaffe bomb severed Bristol's tram power supply at Counterslip. Buses replaced them the next morning — and never left. The trams never returned." },
        { month: 4, day: 17, label: "World Public Transport Day", hint: "It's World Public Transport Day, declared by UITP at the UN. A day to celebrate the humble bus — or at least acknowledge its existence." },
        { month: 4, day: 30, label: "Bristol Bus Boycott anniversary (1963)", hint: "On this day in 1963, Bristol's bus boycott began — a four-month campaign against the Bristol Omnibus Company's colour bar refusing to hire Black or Asian drivers. It ended 28 August, the same day as MLK's 'I Have a Dream' speech, and helped inspire the Race Relations Act 1966." },
        { month: 6, day: 3, label: "World Bicycle Day", hint: "It's World Bicycle Day (UN, since 2018). The bus's two-wheeled rival gets its own day. We're not jealous." },
        { month: 6, day: 22, label: "Windrush Day", hint: "HMT Empire Windrush docked at Tilbury on this day in 1948. From the 1950s, London Transport directly recruited staff from the Caribbean — drivers, conductors, underground workers who kept Britain's transport running. Bristol's own Bus Boycott fought the colour bar that excluded these same communities." },
        { month: 7, day: 6, label: "London Buses went cashless (2014)", hint: "London buses went cashless on this day in 2014. Bristol's still take cash. Progress moves at different speeds." },
        { month: 8, day: 9, label: "Bristol's first horse tram (1875)", hint: "On this day in 1875, Charles Challenger boarded the first horse-drawn tram outside the King David Inn on Perry Road for the inaugural run to Redland. Happy birthday, Bristol public transport." },
        { month: 9, day: 22, label: "World Car Free Day", hint: "It's World Car Free Day. A global event since 2000 encouraging people to leave the car at home. The bus awaits — delays and all." },
        { month: 9, day: 24, label: "Bee Network launch anniversary (2023)", hint: "Manchester's Bee Network launched its first franchised bus routes on this day in 2023 — Bolton and Wigan first, the first city region outside London to take buses back under public control. Bristol watches from afar." },
        { month: 9, day: 27, label: "Stockton & Darlington Railway (1825)", hint: "Nothing to do with buses, but the Stockton & Darlington Railway opened on this day in 1825 — the world's first public railway. Buses came later. Much later." },
        { month: 10, day: 1, label: "Bristol Tramways Company formed (1887)", hint: "The Bristol Tramways and Carriage Company was incorporated on this day in 1887. It later became the Bristol Omnibus Company in 1957 and ran the city's buses for over a century before privatisation." },
        { month: 10, day: 14, label: "Bristol's first electric tram (1895)", hint: "On this day in 1895, Bristol became the first city in the UK to run electric trams, from Old Market to Kingswood. Once a transport pioneer — now we wait for the 76." },
        { month: 10, day: 26, label: "UK bus deregulation D-Day (1986)", hint: "The Transport Act 1985 came into force on this day in 1986 — known in the industry as 'D-Day'. All bus services outside London were deregulated. Bristol's buses have been privately run ever since." },
        { month: 11, day: 26, label: "World Sustainable Transport Day", hint: "It's World Sustainable Transport Day (UN, since 2023). Transport accounts for a quarter of global CO2 emissions. Every electric bus helps — Bristol has a handful." },
        { month: 12, day: 24, label: "Christmas Eve", hint: "It's Christmas Eve. Skeleton services, festive timetable chaos, and the eternal question of who's driving tomorrow." },
        { month: 12, day: 25, label: "Christmas Day", hint: "It's Christmas Day. If there's a bus running, someone is working it. Spare a thought." },
    ];

    // Catch the Bus Month runs all of July (Bus Users UK campaign)
    private readonly catchTheBusMonth = 7;

    // Thinking levels for Gemini 3 Flash (varies by mode)
    private readonly thinkingLevels = {
        draft: { normal: "LOW", editorial: "MEDIUM" },
        critic: { normal: "MINIMAL", editorial: "MINIMAL" }  // Critic stays minimal in both modes
    };

    constructor(aiConfig: any, appState: ApplicationState, weatherService: WeatherService) {
        this.aiConfig = { ...aiConfig };
        this.appState = appState;
        this.weatherService = weatherService;

        logger.info('AI Commentary service initialized', {
            model: this.aiConfig.model,
            dailyLimit: this.aiConfig.dailyLimit,
            timeout: this.aiConfig.timeout,
            persona: 'Bristol Bus Bot (consistent)'
        });
    }

    /**
     * Select N random facts from the financial facts pool
     */
    private selectRandomFacts(count: number): string[] {
        const shuffled = [...this.financialFacts].sort(() => Math.random() - 0.5);
        return shuffled.slice(0, count);
    }

    /**
     * Inject social media manager (called after construction to avoid circular dependency)
     */
    setSocialMediaManager(socialMediaManager: any): void {
        this.socialMediaManager = socialMediaManager;
    }

    async initialize(): Promise<void> {
        if (!this.aiConfig.apiKey) {
            logger.warn('AI_API_KEY not configured. AI commentary will be disabled.');
        } else {
            logger.info('AI Commentary service ready', {
                persona: 'Bristol Bus Bot',
                dailyLimit: this.aiConfig.dailyLimit
            });
        }
    }

    async generatePost(
        busEvent: BusEvent,
        pattern?: DelayPattern,
        history?: DelayHistory
    ): Promise<string | null> {
        if (!this.aiConfig.apiKey) {
            logger.warn("[AI] AI API key not configured. Skipping creative post generation.");
            return null;
        }

        // Check and reset daily counters if needed.
        this.appState.resetDailyCounters();

        // Daily limit removed - natural rate limiting via 20-minute posting interval is sufficient
        logSummary('info', `🎯 AI: Call ${this.appState.aiCallsToday + 1} for ${busEvent.line} ${busEvent.eventType}`);
        logDetailed('info', `[AI_ATTEMPT] Starting AI generation for ${busEvent.eventType} event on route ${busEvent.line}`);

        const context = await this.buildAIContext(busEvent, pattern, history);
        const result = await this.callGeminiAPI(context);

        if (result) {
            this.appState.incrementAICallCount();
            logger.info(`[AI_QUOTA] Call completed successfully. Daily usage: ${this.appState.aiCallsToday}`);
            return result.text;
        }

        return null;
    }

    /**
     * Build social media context for AI (followers, recent posts to avoid repetition)
     */
    private buildSocialContext(): string {
        const parts: string[] = [];

        // Follower count
        if (this.appState.blueskyFollowerCount > 0) {
            parts.push(`You currently have ${this.appState.blueskyFollowerCount} followers on Bluesky.`);
        }

        // Recent posts context (keep last 3 posts for variety)
        if (this.appState.recentPosts.length > 0) {
            const recentList = this.appState.recentPosts.slice(-3).map((p, i) => `${i + 1}. "${p}"`).join(' ');
            parts.push(`Your recent posts: ${recentList}`);

            // Check if recent posts mentioned follower count or being a bot
            const selfReferential = this.appState.recentPosts.slice(-2).filter(p =>
                /followers?|Bristol Bus Bot|automated|monitoring system|I have|my \d+/i.test(p)
            );

            if (selfReferential.length >= 2) {
                parts.push(`Your last two posts referenced yourself or your follower count - avoid doing this again.`);
            } else if (selfReferential.length === 1) {
                parts.push(`You can reference your follower count or role as a bot, but only if it adds genuine wit to this specific situation.`);
            } else {
                parts.push(`You may reference your follower count or automated nature if it adds wit, but don't overuse this device.`);
            }

            parts.push(`Vary your tone, sentence structure, and subject focus from these recent posts.`);
        }

        return parts.length > 0 ? parts.join(' ') : '';
    }

    private async buildAIContext(busEvent: BusEvent, pattern?: DelayPattern, history?: DelayHistory): Promise<AICommentaryContext> {
        const currentTime = DateTime.now().setZone(TARGET_TIMEZONE);

        const timeContext =
            currentTime.hour < 6 ? 'early morning' :
            currentTime.hour < 9 ? 'morning rush hour' :
            currentTime.hour < 12 ? 'late morning' :
            currentTime.hour < 15 ? 'midday' :
            currentTime.hour < 18 ? 'afternoon rush hour' :
            currentTime.hour < 21 ? 'evening' : 'late evening';

        const networkStatus = this.appState.getNetworkStatus();
        const weatherData = await this.weatherService.getCurrentWeather();

        return {
            event: busEvent,
            pattern,
            history,
            networkStatus,
            timeContext,
            weatherContext: weatherData || undefined
        };
    }

    /**
     * Call Gemini API with the two-step draft and critic pattern.
     * Includes retry logic tuned for the Pi's flaky network.
     */
    private async callGeminiAPI(context: AICommentaryContext, retryCount: number = 0): Promise<AICommentaryResult | null> {
        const timer = new PerformanceTimer('ai_api_call', logger);
        const AI_STUDIO_URL = `https://generativelanguage.googleapis.com/v1beta/models/${this.aiConfig.model}:generateContent?key=${this.aiConfig.apiKey}`;

        // Determine if this post should be editorial mode
        // Never two editorial posts in a row, and only ~20% chance otherwise
        let isEditorialMode = false;
        if (!this.lastPostWasEditorial && !this.hasRecentFinancialPost() && Math.random() < this.editorialChance) {
            isEditorialMode = true;
        }

        // Build editorial context if in editorial mode (for draft agent only)
        let editorialContext = '';
        if (isEditorialMode) {
            const selectedFacts = this.selectRandomFacts(2);
            editorialContext = `
EDITORIAL MODE: This post holds FirstBus to account using hard financial data. Pick ONE of these facts and make it the centrepiece:
${selectedFacts.map((f, i) => `- ${f}`).join('\n')}
Don't just mention the number — contrast it directly with what passengers are experiencing right now. Name the specific figure (£ amount, percentage, CEO pay). The reader should feel the gap between corporate profit and service quality.
If the service is ON TIME, note it proves the network CAN work — so why are shareholders prioritised over investment?`;
        }

        logSummary('info', `🎭 AI Mode: ${isEditorialMode ? 'EDITORIAL' : 'standard'}`);

        try {
            // Build vehicle detail string (uses BUS_MODEL_BLURBS)
            let busDescription = '';
            if (context.event.busDetails) {
                const bus = context.event.busDetails;
                const facts: string[] = [];

                if (bus.vehicle_type) {
                    // Basic model info
                    let modelInfo = bus.vehicle_type.name;
                    if (bus.vehicle_type.double_decker) modelInfo += ' double-decker';
                    if (bus.vehicle_type.electric) modelInfo += ' (electric)';
                    facts.push(`Model: ${modelInfo}`);

                    // Add blurb as separate factual points
                    const blurb = BUS_MODEL_BLURBS[bus.vehicle_type.name];
                    if (blurb) {
                        facts.push(`Notes: ${blurb}`);
                    }
                }

                if (bus.livery) facts.push(`Livery: ${bus.livery.name}`);
                if (bus.garage) facts.push(`Garage: ${bus.garage.name}`);

                if (facts.length > 0) busDescription = facts.join(' | ');
            }

            // Get enriched stop data from Bristol Open Data
            const enrichedStop = context.event.lastStopCode
                ? getStopEnrichment()[context.event.lastStopCode]
                : null;

            // Detect geographic area — prefer enrichment's local_authority, fall back to stop code prefix
            const getArea = (stopCode?: string): string => {
                if (enrichedStop?.local_authority) {
                    const la = enrichedStop.local_authority;
                    if (la.includes('Bristol')) return 'Bristol';
                    if (la.includes('Bath')) return 'Bath';
                    if (la.includes('South Gloucestershire')) return 'South Gloucestershire';
                    if (la.includes('North Somerset')) return 'North Somerset';
                    return la;
                }
                if (!stopCode) return 'Bristol';
                const code = stopCode.toLowerCase();
                if (code.startsWith('wsm')) return 'Weston-super-Mare';
                if (code.startsWith('bth')) return 'Bath';
                if (code.startsWith('sgl')) return 'South Gloucestershire';
                if (code.startsWith('bst')) return 'Bristol';
                return 'Bristol area';
            };

            const area = getArea(context.event.lastStopCode);
            const isSouthGlos = area === 'South Gloucestershire';
            const isBathOrWeston = area === 'Bath' || area === 'Weston-super-Mare';
            const isOutsideBristol = isSouthGlos || isBathOrWeston;

            // Get real locality (ward name) from preloaded geographic data
            const localityData = context.event.lastStopCode ? stopLocalities[context.event.lastStopCode] : null;
            const locality = localityData?.ward_name || null;

            // Build route context from route_details.json with origin/destination
            let routeContext = '';
            const routeInfo = this.appState.routeDetails[context.event.line];
            if (routeInfo) {
                // Get route endpoints from headsigns
                const headsigns = routeInfo.headsigns || [];
                if (headsigns.length >= 2) {
                    routeContext = `Route ${context.event.line} runs between ${headsigns[0]} and ${headsigns[1]}. `;
                } else {
                    routeContext = `Route ${context.event.line} "${routeInfo.route_name}". `;
                }

                // Add stop position context
                const direction = context.event.direction.toLowerCase().includes('inbound') ? 'inbound' : 'outbound';
                const stops = routeInfo.directions[direction] || [];
                if (stops.length > 0) {
                    const stopIndex = stops.findIndex((s: any) => s.name === context.event.lastStopName);
                    if (stopIndex >= 0) {
                        routeContext += `Currently at stop ${stopIndex + 1} of ${stops.length} ${direction}. `;
                    }
                }
            }

            // Geographic context with neighbourhood-based local flavour using coordinates
            let geoContext = '';

            // Find neighbourhood from coordinates
            let neighbourhood: { name: string; data: typeof localFlavour[string] } | null = null;
            if (localityData) {
                neighbourhood = findNeighbourhood(localityData.lat, localityData.lon);
            }

            // Check if we've recently mentioned this neighbourhood
            const recentlyMentionedNeighbourhood = neighbourhood && this.appState.recentPosts.slice(-3).some(post =>
                post.includes(neighbourhood.name) ||
                (isSouthGlos && (post.includes('South Glos') || post.includes('technically') || post.includes('basically Bristol')))
            );

            if (neighbourhood) {
                // We found a specific neighbourhood - use its hyperlocal flavour
                if (!recentlyMentionedNeighbourhood) {
                    geoContext = `Location: ${neighbourhood.name}. ${neighbourhood.data.flavour} `;
                } else {
                    // Mentioned recently - just note location without full flavour to avoid repetition
                    geoContext = `Location: ${neighbourhood.name}. `;
                }
                // Append street name from enrichment if available
                if (enrichedStop?.street) {
                    geoContext += `Street: ${enrichedStop.street}. `;
                }
            } else if (enrichedStop?.locality) {
                // Use enriched locality (often more specific than ward names)
                geoContext = `Location: ${enrichedStop.locality}, ${area}. `;
                if (enrichedStop.street) {
                    geoContext += `Street: ${enrichedStop.street}. `;
                }
            } else if (locality) {
                // Fallback to ward name if no neighbourhood or enrichment matched
                geoContext = `Locality: ${locality} ward, ${area}. `;
            } else {
                // Final fallback - just area
                geoContext = `Location: ${area}. `;
            }

            // Build contextual info
            let contextInfo = `Current time: ${DateTime.now().setZone(TARGET_TIMEZONE).toFormat('h:mm a')} (${context.timeContext}). `;

            // Build network status with actual statistics
            const netStats = context.networkStatus;
            const totalEvents = netStats.performance.onTime + netStats.performance.delayed + netStats.performance.early;

            if (totalEvents > 0) {
                // Use actual percentages and statistics
                const parts: string[] = [];

                // Performance breakdown
                parts.push(`${netStats.performance.percentages.onTime}% on time`);
                parts.push(`${netStats.performance.percentages.delayed}% delayed`);

                // Add average delay if significant
                if (netStats.averageDelay > 0) {
                    parts.push(`average delay ${netStats.averageDelay} min`);
                }

                // Routes status
                if (netStats.delayedRoutes > 0) {
                    parts.push(`${netStats.delayedRoutes}/${netStats.totalRoutes} routes delayed`);
                }

                contextInfo += `Network: ${parts.join(', ')}.`;
            } else {
                // Fallback when no events yet
                contextInfo += `Network: monitoring ${netStats.totalRoutes} routes.`;
            }

            if (context.pattern) {
                switch (context.pattern.type) {
                    case 'network':
                        contextInfo += ` Network-wide issue: ${context.pattern.routes.length} routes affected (${context.pattern.routes.join(', ')}). Delays range ${Math.min(...context.pattern.delays)}–${Math.max(...context.pattern.delays)} minutes.`;
                        break;
                    case 'area':
                        contextInfo += ` Area delays near ${context.pattern.affectedArea}: ${context.pattern.routes.join(', ')} affected.`;
                        break;
                    case 'cluster':
                        contextInfo += ` Multiple routes delayed: ${context.pattern.routes.join(', ')} with delays of ${context.pattern.delays.join(', ')} minutes.`;
                        break;
                    default:
                        contextInfo += ` Individual ${context.event.eventType} on route ${context.event.line}.`;
                }
            }

            if (context.history) {
                switch (context.history.trend) {
                    case 'worsening': contextInfo += ` Trend: worsening (was ${context.history.lastReportedDelay} minutes earlier).`; break;
                    case 'improving': contextInfo += ` Trend: improving (down from ${context.history.lastReportedDelay} minutes).`; break;
                    case 'stable': contextInfo += ` Trend: persistent (${context.history.consecutiveReports} reports).`; break;
                }
            }

            // Event description (without "Route X is" - that's added in prompt)
            let eventContext = '';
            switch (context.event.eventType) {
                case 'delay':
                    eventContext = `${context.event.delayMinutes} minutes late`;
                    break;
                case 'early':
                    eventContext = `${Math.abs(context.event.delayMinutes)} minutes early`;
                    break;
                case 'punctual':
                    eventContext = `on time`;
                    break;
            }

            logSummary('info', `🎭 Bot context: ${this.appState.blueskyFollowerCount} followers, posting update`);

            // Check transport calendar for today's date
            const currentTime = DateTime.now().setZone(TARGET_TIMEZONE);
            const todayEntry = this.transportCalendar.find(e => e.month === currentTime.month && e.day === currentTime.day);
            // Also check Catch the Bus Month (all of July)
            const isCatchTheBusMonth = currentTime.month === this.catchTheBusMonth && !todayEntry;
            const calendarEntry = todayEntry
                ? { label: todayEntry.label, hint: todayEntry.hint }
                : isCatchTheBusMonth
                    ? { label: "Catch the Bus Month", hint: "July is Catch the Bus Month (Bus Users UK). A campaign encouraging people to try the bus. Whether today's service is encouraging or discouraging that is for you to judge." }
                    : null;
            // ~50% of posts on a matching day mention the occasion
            const useTimeContext = calendarEntry && Math.random() < 0.5;
            const festiveNote = useTimeContext
                ? `\n- SPECIAL (${calendarEntry.label}): ${calendarEntry.hint} Weave a brief, dry reference into the post if it fits naturally. Don't force it — a subtle nod is better than a clumsy shoehorn.`
                : '';

            // Drafting: recent posts are omitted because the critic checks repetition.
            const draftPrompt = `ROLE: ${this.botPersona} (${this.appState.blueskyFollowerCount} Bluesky followers)

TASK: Create 3 STRUCTURALLY DIFFERENT options for a bus status update

STRUCTURAL REQUIREMENTS (MANDATORY - each option MUST follow its assigned structure):
- Option 1: Start with TIME or LOCATION (e.g., "At 7:53 PM..." or "Near Clifton Village...")
- Option 2: Start with an OBSERVATION about passengers, weather, or the vehicle (e.g., "Passengers waiting at..." or "The drizzle accompanies...")
- Option 3: Start with ROUTE NUMBER using an UNEXPECTED verb - NOT "is" (e.g., "Route 76 crawls..." or "The m1 languishes...")

BANNED PATTERNS (these are overused - NEVER use):
- "The [route] is [X] minutes late/early" as an opening
- "It is a..." as a second sentence opener
- "One assumes..." or "One wonders..." as a second sentence opener

CONSTRAINTS:
- Maximum 285 characters per option (Bluesky limit is 300, need safety margin)
- Exactly 2 complete sentences each
- Must include: route number, direction, location, delay status
- British spelling, no emojis, no hashtags${festiveNote}

CURRENT DATE/TIME: ${currentTime.toFormat('EEEE d MMMM yyyy, h:mm a')} (${TARGET_TIMEZONE})

CURRENT SITUATION:
Route ${context.event.line}, ${context.event.direction} direction, ${eventContext}, ${context.event.lastStopName} stop

AVAILABLE DETAILS (pick 1-2 that ADD genuine wit):
${busDescription ? `- Vehicle: ${busDescription}` : ''}
${geoContext ? `- Location: ${geoContext.trim()}` : ''}
${context.weatherContext ? `- Weather: ${context.weatherContext}` : ''}
${routeContext ? `- Context: ${routeContext.trim()}` : ''}
- Network: ${contextInfo}
- Time: ${context.timeContext}
${editorialContext}

EXAMPLE OPENINGS (for inspiration - vary from these):
- "At 8:19 AM, the m1 crawls toward Hengrove..."
- "Passengers at Temple Meads watch the 76..."
- "Route 24 languishes near Bedminster..."
- "Near Clifton's boutiques, the A1..."

${isEditorialMode ? `TONE REMINDER (critical — re-read before writing):
- You are the underdog holding a corporate giant to account. Be pointed and specific with the financial data.
- Name the £ figures. Make the reader feel the contrast between boardroom profits and the bus stop in the rain.
- Still dry and wry — not shouty. Think dogged local journalist, not angry protester.
- Short punchy sentences. Let the numbers do the outrage for you.
- NO vague moralising — always anchor to a specific figure or fact.` : `TONE REMINDER (critical — re-read before writing):
- UNDERSTATE, don't overstate. Let the facts be absurd on their own.
- NO lecturing, NO moralising, NO phrases like "private-sector lethargy", "profit extraction", "consistent unreliability".
- Think deadpan local news column, not angry op-ed. Wry, clipped, observational.
- If mentioning vehicle/weather/context, weave it in naturally — don't force a political point from it.
- Short punchy sentences beat long flowing ones. Be economical with words.`}

OUTPUT: Only the 3 numbered options (1. 2. 3.), each following its required structure.`;

            // Save draft prompt for dashboard
            this.appState.lastAIDraftPrompt = draftPrompt;
            this.appState.lastAIPrompt = draftPrompt; // Compatibility field for dashboard clients.
            this.appState.lastWeatherContext = context.weatherContext || null;

            // Use Gemini 3 specific config if using that model
            const isGemini3 = this.aiConfig.model.includes('gemini-3');
            const draftTemp = isGemini3 ? 1.0 : 0.9;

            // Select thinking level based on mode (editorial gets more reasoning time)
            const draftThinkingLevel = isEditorialMode
                ? this.thinkingLevels.draft.editorial
                : this.thinkingLevels.draft.normal;

            logSummary('info', `📤 AI Draft: Generating 3 options (${isEditorialMode ? 'EDITORIAL' : 'standard'} mode)`);
            logDetailed('info', `[AI_DRAFT] Temp: ${draftTemp}, Thinking: ${draftThinkingLevel}`);

            // Build generation config
            const draftGenConfig: any = { temperature: draftTemp };
            if (isGemini3) {
                draftGenConfig.thinking_config = { thinking_level: draftThinkingLevel };
            }

            const draftResp = await httpFetch(AI_STUDIO_URL, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    contents: [{ parts: [{ text: draftPrompt }] }],
                    generationConfig: draftGenConfig
                }),
                timeoutMs: this.aiConfig.timeout
            });

            if (!draftResp.ok) {
                const errorBody = await draftResp.text();
                // Retry server errors (502, 503, 429)
                if ([502, 503, 429].includes(draftResp.status) && retryCount < 2) {
                    const retryDelay = (retryCount + 1) * 10000;
                    logSummary('warn', `⚠️ AI Draft: Server error ${draftResp.status}, retrying in ${retryDelay / 1000}s`);
                    await new Promise(r => setTimeout(r, retryDelay));
                    return this.callGeminiAPI(context, retryCount + 1);
                }

                if (errorBody.includes("quota")) {
                    logger.warn("[AI_QUOTA] Gemini API quota exceeded. Will try again next cycle.");
                    return null;
                }

                throw new Error(`Draft API failed: ${draftResp.status}`);
            }

            const draftJson = await draftResp.json() as any;

            // Debug: Log the full response structure if drafts are missing
            if (!draftJson.candidates?.[0]?.content?.parts?.[0]?.text) {
                logSummary('error', `❌ AI Draft: Unexpected response structure`);
                logDetailed('error', `[AI_DRAFT_DEBUG] Full response: ${JSON.stringify(draftJson, null, 2)}`);

                // Check for specific error conditions
                if (draftJson.promptFeedback?.blockReason) {
                    logSummary('error', `❌ AI Draft: Blocked by safety filter - ${draftJson.promptFeedback.blockReason}`);
                }
                if (draftJson.candidates?.[0]?.finishReason) {
                    logSummary('warn', `⚠️ AI Draft: Finish reason - ${draftJson.candidates[0].finishReason}`);
                }
            }

            const drafts = draftJson.candidates?.[0]?.content?.parts?.[0]?.text;

            if (!drafts) {
                logSummary('warn', '⚠️ AI: Draft returned empty');
                return null;
            }

            // Save draft output for dashboard
            this.appState.lastAIDraftOutput = drafts;
            logSummary('info', `📝 AI Draft: Got ${drafts.length} chars`);

            // Review the drafts against recent posts before choosing one.
            try {
                // Fetch ACTUAL recent posts from Bluesky
                let recentPostsFromBluesky: string[] = [];
                if (this.socialMediaManager) {
                    try {
                        recentPostsFromBluesky = await this.socialMediaManager.fetchRecentPostsFromBluesky(3);
                    } catch (error: any) {
                        logDetailed('warn', `[AI_CRITIC] Failed to fetch Bluesky posts: ${error.message}`);
                    }
                }

                // Build recent posts context for variety (prefer Bluesky feed, fallback to in-memory)
                const postsToUse = recentPostsFromBluesky.length > 0 ? recentPostsFromBluesky : this.appState.recentPosts.slice(-3);
                const recentPostsContext = postsToUse.length > 0
                    ? postsToUse.map((p, i) => `${i + 1}. "${p}"`).join('\n')
                    : 'No recent posts yet.';

const criticPrompt = `ROLE: Editor for the Bristol Bus Bot

TASK: Select the BEST draft. Output ONLY the raw post text.

THREE DRAFTS TO EVALUATE:
${drafts}

REJECTION CRITERIA (immediately disqualify any draft with these):
- Opens with "The [route] is [X] minutes..." pattern
- Second sentence starts with "It is a..." or "One assumes/wonders..."
- Over 290 characters
- Nonsensical or forced contextual connections (e.g., weather "providing comfort")

SELECTION CRITERIA (for non-rejected drafts):
1. STRUCTURAL FRESHNESS: Different opening pattern from recent posts below
2. WIT QUALITY: Sharp comedic payoff that lands naturally
3. TECHNICAL: British English, exactly 2 sentences, under 290 chars${useTimeContext ? '\n4. PRESERVE: Keep any time-context reference (anniversary/occasion) if present — it adds flavour' : ''}

RECENT POSTS (new post must NOT copy their opening pattern):
${recentPostsContext}

PROCESS:
1. Apply rejection criteria - eliminate failing drafts
2. Check remaining drafts don't match recent post openings
3. Pick the one with sharpest, most natural wit
4. If ALL drafts fail criteria, select least problematic and edit to fix

OUTPUT: The post text only. No number prefix (1/2/3), no labels, no explanation.
Start your response with the first word of the selected post.`;


                // Save critic prompt for dashboard
                this.appState.lastAICriticPrompt = criticPrompt;

                // Build critic generation config (keep lower temp for selection/polishing)
                // Critic stays at MINIMAL thinking in both modes - it's just selecting, not creating
                const criticThinkingLevel = isEditorialMode
                    ? this.thinkingLevels.critic.editorial
                    : this.thinkingLevels.critic.normal;

                logSummary('info', `🎯 AI Critic: Selecting best draft`);
                logDetailed('info', `[AI_CRITIC] Temp: 0.2, Thinking: ${criticThinkingLevel}`);

                const criticGenConfig: any = { temperature: 0.2 };
                if (isGemini3) {
                    criticGenConfig.thinking_config = { thinking_level: criticThinkingLevel };
                }

                const criticResp = await httpFetch(AI_STUDIO_URL, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        contents: [{ parts: [{ text: criticPrompt }] }],
                        generationConfig: criticGenConfig
                    }),
                    timeoutMs: this.aiConfig.timeout
                });

                if (criticResp.ok) {
                    const criticJson = await criticResp.json() as any;
                    const final = criticJson.candidates?.[0]?.content?.parts?.[0]?.text;

                    // Save raw critic output for dashboard
                    this.appState.lastAICriticOutput = final || null;

                    const cleaned = this.postProcessText(final || '', context);

                    if (cleaned) {
                        // Reject output that exceeds Bluesky's character limit.
                        if (cleaned.length > 300) {
                            logSummary('error', `❌ AI Critic: Post too long! ${cleaned.length} chars (limit 300)`);
                            logDetailed('error', `[AI_OVERLIMIT] Rejected post: "${cleaned}"`);
                            // Fall through to draft fallback
                        } else {
                            // Success - save history
                            this.appState.lastAIResponse = cleaned;
                            this.appState.recentPosts.push(cleaned);
                            if (this.appState.recentPosts.length > 5) this.appState.recentPosts.shift();

                            if (cleaned.length > 290) {
                                logSummary('warn', `⚠️ AI Critic: Close to limit! ${cleaned.length}/300 chars`);
                            } else {
                                logSummary('info', `✅ AI Critic: "${cleaned}" (${cleaned.length} chars)`);
                            }

                            // Track editorial mode for next post (only on success)
                            this.lastPostWasEditorial = isEditorialMode;

                            timer.complete({
                                responseTime: timer.getElapsed(),
                                textLength: cleaned.length,
                                persona: isEditorialMode ? 'Bristol Bus Bot (Editorial)' : 'Bristol Bus Bot (Critic)',
                                eventType: context.event.eventType,
                                route: context.event.line
                            });

                            return {
                                text: cleaned,
                                persona: isEditorialMode ? 'Bristol Bus Bot (Editorial)' : 'Bristol Bus Bot (Critic)',
                                confidence: 0.95,
                                responseTime: timer.getElapsed(),
                                metadata: {
                                    tokenCount: cleaned.length,
                                    model: this.aiConfig.model,
                                    temperature: 0.2,
                                    editorialMode: isEditorialMode
                                }
                            };
                        }
                    }
                }
            } catch (e: any) {
                logSummary('warn', `⚠️ AI Critic failed: ${e.message}, using draft fallback`);
            }

            // FALLBACK: Use raw drafts if critic failed
            const fallback = this.postProcessText(drafts, context);
            if (fallback) {
                // Track editorial mode for next post (only on success)
                this.lastPostWasEditorial = isEditorialMode;

                this.appState.lastAIResponse = fallback;
                this.appState.recentPosts.push(fallback);
                if (this.appState.recentPosts.length > 5) this.appState.recentPosts.shift();

                logSummary('info', `✅ AI Draft (fallback): "${fallback}" ${isEditorialMode ? '[EDITORIAL]' : ''}`);

                return {
                    text: fallback,
                    persona: isEditorialMode ? 'Bristol Bus Bot (Editorial Draft)' : 'Bristol Bus Bot (Draft)',
                    confidence: 0.85,
                    responseTime: timer.getElapsed(),
                    metadata: {
                        tokenCount: fallback.length,
                        model: this.aiConfig.model,
                        temperature: 0.9,
                        editorialMode: isEditorialMode
                    }
                };
            }

            // Final fallback: template
            const s1 = `${context.event.line} ${context.event.eventType === 'punctual' ? 'on time' : `${Math.abs(context.event.delayMinutes)} min ${context.event.eventType}`} near ${context.event.lastStopName}, ${context.event.direction} direction.`;
            const vehicleBit = this.buildVehicleOneLiner(context) || 'Plain livery, standard spec.';
            const s2 = `Vehicle notes: ${vehicleBit}`;
            const templateFallback = this.fitToLimit(`${s1} ${s2}`, 280);

            logDetailed('warn', `[AI_FALLBACK] Using template: "${templateFallback}"`);

            return {
                text: templateFallback,
                persona: 'Bristol Bus Bot (Template)',
                confidence: 0.6,
                responseTime: timer.getElapsed(),
                metadata: { model: this.aiConfig.model, temperature: 0, tokenCount: templateFallback.length }
            };

        } catch (error: any) {
            timer.fail(error);

            // Retry policy similar to SIRI: 2 retries on timeout, 1 on network errors
            if (error.name === 'AbortError') {
                if (retryCount < 2) {
                    const retryDelay = (retryCount + 1) * 10000; // 10s, 20s
                    logSummary('warn', `⏱️ AI: Timeout ${this.aiConfig.timeout}ms for ${context.event.line}, retrying in ${retryDelay / 1000}s (attempt ${retryCount + 1}/2)`);
                    await new Promise(r => setTimeout(r, retryDelay));
                    return this.callGeminiAPI(context, retryCount + 1);
                }
                logDetailed('warn', `[AI_TIMEOUT] Final timeout after ${retryCount + 1} attempts`);
                return null;
            }

            if (['ECONNRESET', 'ENOTFOUND'].includes(error.code) || /network/i.test(error.message)) {
                if (retryCount < 1) {
                    const retryDelay = 5000;
                    logSummary('warn', `AI network error, retrying in ${retryDelay / 1000}s`);
                    await new Promise(r => setTimeout(r, retryDelay));
                    return this.callGeminiAPI(context, retryCount + 1);
                }
                logDetailed('error', `[AI_NETWORK] Max retries exceeded: ${error.message}`);
                return null;
            }

            logSummary('error', `💥 AI: Error for ${context.event.line} - ${error.message}`);
            logDetailed('error', `[AI_ERROR] ${error.stack || error.message}`);
            return null;
        }
    }

private buildVehicleOneLiner(context: AICommentaryContext): string | null {
    const b = context.event.busDetails;
    if (!b) return null;
    const bits: string[] = [];
    if (b.vehicle_type?.name) {
        bits.push(b.vehicle_type.name);
        const blurb = BUS_MODEL_BLURBS[b.vehicle_type.name];
        if (blurb && blurb.length < 100) { // Keep it short for fallback
            bits.push(blurb.split('.')[0]); // Just first sentence
        }
    }
    if (b.vehicle_type?.double_decker) bits.push('double decker');
    if (b.vehicle_type?.electric) bits.push('electric');
    if (b.livery?.name) bits.push(`${b.livery.name} livery`);
    return bits.join(', ') || null;
}

    private postProcessText(text: string, context: AICommentaryContext): string | null {
        if (!text) return null;

        // Strip any leading draft number prefix (critic sometimes includes these)
        let t = text.replace(/^[123]\.\s*/, '').trim();

        // Strip common AI preamble patterns
        t = t.replace(/^(Here's|Here is|Option \d:|Draft \d:|Selected:|The best option is:?)\s*/i, '').trim();

        // Flatten whitespace
        t = t.replace(/\s+/g, ' ').trim();

        // Remove emojis & hashtags (keep clean tone)
        t = t.replace(/#[\p{L}\p{N}_]+/gu, '');
        t = t.replace(/\p{Emoji_Presentation}|\p{Extended_Pictographic}/gu, '');

        // Ban common hardware-y words
        const banned = /\b(usb|port|socket|kernel|stack|modem|ram|cpu|gpu|cache|firmware|io|ethernet|wi[- ]?fi|bluetooth)\b/gi;
        t = t.replace(banned, '').replace(/\s{2,}/g, ' ').trim();


// Ensure exactly two sentences: split on terminal punctuation
const parts = t.split(/(?<=[.!?])\s+/).filter(Boolean);
if (parts.length >= 2) {
    const firstSentence = parts[0].trim();
    const secondSentence = parts[1].trim();

    // Log formulaic second sentences for monitoring
    const formulaicPatterns = [
        /^It is a /i,
        /^One assumes /i,
        /^One wonders /i,
        /^One can only /i,
    ];

    if (formulaicPatterns.some(p => p.test(secondSentence))) {
        logSummary('warn', `⚠️ Formulaic second sentence: "${secondSentence.slice(0, 40)}..."`);
        // Uncomment next line to reject these (aggressive - test first):
        // return null;
    }

    t = `${firstSentence} ${secondSentence}`;
} else {
    // Better fallback that ensures route number is included
    const delayText = context.event.eventType === 'delay' ? 
        `${context.event.delayMinutes} minutes late` :
        context.event.eventType === 'early' ? 
        `${Math.abs(context.event.delayMinutes)} minutes early` :
        'on time';
    
    // Build vehicle description for fallback
    let vehicleInfo = '';
    if (context.event.busDetails?.vehicle_type) {
        const vt = context.event.busDetails.vehicle_type;
        vehicleInfo = vt.electric ? 'electric ' : '';
        vehicleInfo += vt.double_decker ? 'double-decker' : 'bus';
        if (context.event.busDetails.livery?.name) {
            vehicleInfo = `${context.event.busDetails.livery.name} ${vehicleInfo}`;
        }
    } else {
        vehicleInfo = 'service';
    }
    
    // Varied fallback templates that always include route
    const templates = [
        `${context.event.line} ${delayText} near ${context.event.lastStopName}, ${context.event.direction} direction. The ${vehicleInfo} continues its journey.`,
        `Route ${context.event.line} is running ${delayText} at ${context.event.lastStopName}. Another ${vehicleInfo} defying the timetable.`,
        `The ${context.event.line} ${vehicleInfo} finds itself ${delayText} near ${context.event.lastStopName}. Schedule adherence remains theoretical.`,
        `Service ${context.event.line}: ${delayText} at ${context.event.lastStopName}. The ${vehicleInfo} persists in its temporal rebellion.`
    ];
    
    t = templates[Math.floor(Math.random() * templates.length)];
}

        // Hard character cap - enforce Bluesky's 300 char limit with safety margin
        t = this.fitToLimit(t, 290);

        // Final safety check - if still over limit, hard truncate
        if (t.length > 300) {
            logSummary('warn', `⚠️ Post still ${t.length} chars after fitToLimit, hard truncating to 300`);
            t = this.fitToLimit(t, 300);
        }

        // ultra-short sanity
        if (t.length < 20) return null;
        return t;
    }

    private fitToLimit(s: string, limit: number): string {
        if (s.length <= limit) return s;
        // Prefer trimming second sentence first
        const parts = s.split(/(?<=[.!?])\s+/);
        if (parts.length >= 2) {
            const s1 = parts[0].trim();
            let s2 = parts.slice(1).join(' ').trim();
            const remaining = limit - (s1.length + 1);
            if (remaining > 0) s2 = s2.slice(0, remaining).replace(/\s+\S*$/, '').trim();
            return `${s1} ${s2}`.slice(0, limit).trim();
        }
        return s.slice(0, limit).replace(/\s+\S*$/, '').trim();
    }

    async generateNetworkSummary(_networkStatus: any): Promise<string | null> {
        // The interface permits summaries even when this provider has none.
        return null;
    }

    getStatus(): any {
        return {
            name: 'AI Commentary',
            status: this.aiConfig.apiKey ? 'ready' : 'disabled',
            config: {
                model: this.aiConfig.model,
                dailyLimit: this.aiConfig.dailyLimit,
                timeout: this.aiConfig.timeout,
                persona: 'Bristol Bus Bot (consistent)'
            },
            usage: {
                callsToday: this.appState.aiCallsToday,
                dailyLimit: this.aiConfig.dailyLimit,
                remaining: Math.max(0, this.aiConfig.dailyLimit - this.appState.aiCallsToday)
            },
            socialContext: {
                followers: this.appState.blueskyFollowerCount,
                lastPost: this.appState.lastAIResponse ? 'available' : 'none'
            }
        };
    }

    /**
     * Get AI configuration (for dashboard/API access)
     */
    getConfig(): any {
        return {
            dailyLimit: this.aiConfig.dailyLimit,
            model: this.aiConfig.model,
            timeout: this.aiConfig.timeout
        };
    }

    async close(): Promise<void> {
        logger.info('AI Commentary service stopped');
    }
}
