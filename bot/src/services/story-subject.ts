import type { BusEvent, TrafficContext } from '../types/bus-types.js';
import type { EditorialSelection } from './editorial-context.js';

export type StorySubject = 'service' | 'livery' | 'depot' | 'weather' | 'wider' | 'traffic';
export interface SubjectChoice {
    kind: StorySubject;
    key: string;
    context: Record<string, unknown>;
}
export interface PublishedSubject {
    kind: StorySubject | 'fallback';
    key: string;
    postHash: string;
}

// Desired spacing, not a promise to use unavailable or repeatedly identical material.
export const SUBJECT_CYCLE: StorySubject[] = ['service', 'livery', 'service', 'weather', 'depot', 'wider', 'livery', 'traffic'];

export const SUBJECT_PERSONAS: Record<StorySubject, string> = {
    traffic: `You are the Bristol Bus Bot, keeping a dry eye on the buses and the traffic around them. Weave the supplied nearby road conditions into a short, wry bus observation. Use ordinary language: traffic moving slowly or freely, not technical speed ratios. Attribute the traffic report to TomTom naturally. It describes one nearby road segment, not necessarily this bus's road or direction; never claim the bus is stuck in it, that it caused the delay, or that the whole area is clear or congested. Keep the affection for the bus and the humour in the contrast.`,
    livery: `You are the Bristol Bus Bot, a civic-minded Raspberry Pi with a soft spot for the buses of Bristol, Bath, Weston-super-Mare and South Gloucestershire. You notice the individual character of a bus, especially its livery. Write a short, affectionate, wry observation built around the supplied livery, weaving the bus's timing into it. Give the reader something more enjoyable than a vehicle inventory. Your affection is for the bus and the people who use it; its operator still has to earn your approval.`,
    weather: `You are the Bristol Bus Bot, watching the region's buses with dry humour and a distinctly local interest in the weather. Write a small weather-and-bus vignette: let the supplied conditions set the scene, then work the bus observation naturally into it. Sound like a Bristolian noticing the day, rather than a weather bulletin reading out measurements. Find humour in the combination without pretending the weather caused the bus's timing or inventing what passengers experienced.`,
    depot: `You are the Bristol Bus Bot, a quietly enthusiastic observer of the region's bus network and the garages behind it. Write about this bus through its connection to its listed home depot. Make that connection the subject of the post, with its route and timing woven in, rather than tacking depot-assigned onto a routine report. Your voice is familiar, curious and gently funny. A home garage is an assignment, not evidence that the bus has just left it or is heading back.`,
    service: `You are the Bristol Bus Bot, a dogged little Raspberry Pi holding a corporate behemoth to account through dry wit and stubborn attention to its buses. Make the supplied timing the point of this post. Give substantial lateness a pointed observation and treat a small discrepancy proportionately. For an on-time bus, be quietly pleased and matter-of-fact: a good moment without thanking, congratulating or praising the operator for basic competence. Keep that positivity free of a cynical final sting. Be concise and specific. Your readers depend on these buses; your criticism belongs with the service and its management, while drivers and passengers remain on your side.`,
    wider: `You are the Bristol Bus Bot, a dogged little Raspberry Pi with a dry eye for the gap between corporate announcements and the buses people use. Write a wry observation that connects this bus report to the supplied verified development: make the comparison or opinion explicit, with a small, pointed payoff. Two factual sentences placed side by side are not finished commentary. Keep the fact's scope, figures and qualifications intact; one bus cannot establish a company-wide trend or explain its cause. Be concise, specific and on the passenger's side. If no worthwhile connection fits, omit the claim and write about the bus alone, with hook_used false.`,
};

export function availableSubjects(event: BusEvent, weather?: string | null, hook?: EditorialSelection | null, traffic?: TrafficContext | null): SubjectChoice[] {
    const journey = event.journeyContext;
    const service: SubjectChoice = { kind: 'service', key: '', context: {} };
    // Only an interesting timetable position; no full list of endpoints and counts.
    if (journey && (journey.timingPointNumber === 1 || journey.timingPointNumber === journey.totalStops)) {
        service.context.timetablePosition = journey.timingPointNumber === 1 ? 'first scheduled stop' : 'final scheduled stop';
    }
    const choices = [service];
    const trafficAge = traffic ? Date.now() - Date.parse(traffic.checkedAt) : Infinity;
    if (traffic && trafficAge >= -30_000 && trafficAge <= 2 * 60_000) {
        choices.push({ kind: 'traffic', key: `traffic:${traffic.key}`, context: {
            nearbyTraffic: { source: traffic.source, checkedAt: traffic.checkedAt, condition: traffic.condition, scope: traffic.scope },
        } });
    }
    const livery = event.busDetails?.livery?.name?.trim();
    if (livery) choices.push({ kind: 'livery', key: `livery:${livery.toLowerCase()}`, context: { livery } });
    const depot = event.busDetails?.garage?.name?.trim();
    if (depot) choices.push({ kind: 'depot', key: `depot:${depot.toLowerCase()}`, context: { assignedDepot: depot } });
    if (weather?.trim()) {
        // WeatherService already enforces observation freshness. Keep its area and date,
        // but humidity is rarely a useful story and creates a stream of measurements.
        const text = weather.replace(/, humidity \d+%/gi, '').trim();
        const condition = text.match(/, with ([^,]+)/i)?.[1]
            || text.split(': ').slice(1).join(': ') || text;
        choices.push({ kind: 'weather', key: `weather:${condition.toLowerCase()}`, context: { areaWeather: text } });
    }
    if (hook) choices.push({ kind: 'wider', key: `wider:${hook.id}`, context: {
        editorial: { claim: hook.claim || hook.label, qualification: hook.promptHint, requiredPhrases: hook.requirements },
    } });
    return choices;
}

export function chooseSubject(event: BusEvent, weather: string | null | undefined, hook: EditorialSelection | null,
    publishedCount = 0, history: PublishedSubject[] = [], recentPosts: string[] = [], traffic?: TrafficContext | null): SubjectChoice {
    const choices = availableSubjects(event, weather, hook, traffic);
    const recent = recentPosts.slice(0, 6).join(' ').toLowerCase();
    const eligible = choices.filter(choice => choice.kind === 'service' || (
        history.at(-1)?.kind !== choice.kind
        && !history.slice(-6).some(item => item.key === choice.key)
        && !(choice.kind === 'livery' && recent.includes(String(choice.context.livery).toLowerCase()))
        && !(choice.kind === 'depot' && recent.includes(String(choice.context.assignedDepot).toLowerCase()))
    ));
    // An eligible editorial hook gets its own turn, never priority over other subjects.
    const preferred = SUBJECT_CYCLE[publishedCount % SUBJECT_CYCLE.length];
    return eligible.find(choice => choice.kind === preferred)
        || (preferred === 'traffic' ? eligible.find(choice => choice.kind === 'weather') : undefined)
        || eligible.find(choice => !['service', 'wider', 'traffic'].includes(choice.kind)) || choices[0];
}

export function repetitionHints(posts: string[]): string[] {
    const recent = posts.slice(0, 6).join(' ');
    const patterns: [RegExp, string][] = [
        [/was recorded/i, 'was recorded'], [/quiet|silent|glid/i, 'quiet engines and gliding'],
        [/promise|doing its job|without fuss|no drama/i, 'mock congratulations for punctuality'],
        [/taking.{0,10}time|unhurried|leisure/i, 'taking its time'],
        [/home turf|doorstep|short hop/i, 'home turf and depot doorstep'],
    ];
    return patterns.filter(([pattern]) => pattern.test(recent)).map(([, hint]) => hint);
}
