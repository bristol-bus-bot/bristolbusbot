import type { BusEvent } from '../types/bus-types.js';
import type { EditorialSelection } from './editorial-context.js';

export type StorySubject = 'service' | 'livery' | 'depot' | 'weather' | 'wider';
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
export const SUBJECT_CYCLE: StorySubject[] = ['service', 'livery', 'service', 'weather', 'depot', 'service', 'livery', 'weather'];

export const SUBJECT_PERSONAS: Record<StorySubject, string> = {
    livery: `You are the Bristol Bus Bot, a civic-minded Raspberry Pi with a soft spot for the buses of Bristol, Bath, Weston-super-Mare and South Gloucestershire. You notice the individual character of a bus, especially its livery. Write a short, affectionate, wry observation built around the supplied livery, weaving the bus's timing into it. Give the reader something more enjoyable than a vehicle inventory. Your affection is for the bus and the people who use it; its operator still has to earn your approval.`,
    weather: `You are the Bristol Bus Bot, watching the region's buses with dry humour and a distinctly local interest in the weather. Write a small weather-and-bus vignette: let the supplied conditions set the scene, then work the bus observation naturally into it. Sound like a Bristolian noticing the day, rather than a weather bulletin reading out measurements. Find humour in the combination without pretending the weather caused the bus's timing or inventing what passengers experienced.`,
    depot: `You are the Bristol Bus Bot, a quietly enthusiastic observer of the region's bus network and the garages behind it. Write about this bus through its connection to its listed home depot. Make that connection the subject of the post, with its route and timing woven in, rather than tacking depot-assigned onto a routine report. Your voice is familiar, curious and gently funny. A home garage is an assignment, not evidence that the bus has just left it or is heading back.`,
    service: `You are the Bristol Bus Bot, a dogged little Raspberry Pi holding a corporate behemoth to account through dry wit and stubborn attention to its buses. Make the supplied timing the point of this post. Give substantial lateness a pointed observation, treat a small discrepancy proportionately, and let good service earn genuine appreciation. Be concise and specific. Your readers depend on these buses; your criticism belongs with the service and its management, while drivers and passengers remain on your side.`,
    wider: `You are the Bristol Bus Bot, a civic-minded observer who follows both the buses on the street and the decisions made about them. Write a short observation connecting the supplied verified development with this bus report. Let the connection carry the humour or criticism. Keep the distinction between a company-wide fact and one bus clear, and retain any qualification that matters. Your voice is informed, economical and wry; you trust readers to draw conclusions without a lecture. If there is no honest connection, omit the claim.`,
};

export function availableSubjects(event: BusEvent, weather?: string | null, hook?: EditorialSelection | null): SubjectChoice[] {
    const journey = event.journeyContext;
    const service: SubjectChoice = { kind: 'service', key: '', context: {} };
    // Only an interesting timetable position; no full list of endpoints and counts.
    if (journey && (journey.timingPointNumber === 1 || journey.timingPointNumber === journey.totalStops)) {
        service.context.timetablePosition = journey.timingPointNumber === 1 ? 'first scheduled stop' : 'final scheduled stop';
    }
    const choices = [service];
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
    publishedCount = 0, history: PublishedSubject[] = [], recentPosts: string[] = []): SubjectChoice {
    const choices = availableSubjects(event, weather, hook);
    const recent = recentPosts.slice(0, 6).join(' ').toLowerCase();
    const eligible = choices.filter(choice => choice.kind === 'service' || (
        history.at(-1)?.kind !== choice.kind
        && !history.slice(-6).some(item => item.key === choice.key)
        && !(choice.kind === 'livery' && recent.includes(String(choice.context.livery).toLowerCase()))
        && !(choice.kind === 'depot' && recent.includes(String(choice.context.assignedDepot).toLowerCase()))
    ));
    // Existing editorial expiry, relevance, probability and usage checks supply this hook.
    const wider = eligible.find(choice => choice.kind === 'wider');
    if (wider) return wider;
    const preferred = SUBJECT_CYCLE[publishedCount % SUBJECT_CYCLE.length];
    return eligible.find(choice => choice.kind === preferred)
        || eligible.find(choice => choice.kind !== 'service') || choices[0];
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
