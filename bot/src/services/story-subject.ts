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
    traffic: `Use ordinary language and local traffic reports without naming the supplier. They describe one nearby road segment, not necessarily this bus's road or direction. Do not imply this bus was delayed, unaffected, trapped or moving at that segment's speed.`,
    livery: `Use the supplied livery as a specific visual detail. Its name is not evidence of geography, a rare working or where the bus is heading. Avoid a generic clothing simile.`,
    weather: `Use the supplied area weather in plain words, keeping its area scope. Do not turn an area reading into weather measured at the stop or imply it caused the timing.`,
    depot: `Use a supported detail about the listed depot only if it adds information. A home garage is an assignment, not evidence of a departure, a return or an unusual working. Do not invent pride or a homecoming story.`,
    service: `Make the observed timing the point. Do not soften lateness with 'only', 'minor wobble' or 'hardly a crisis', or restate the same number as the joke. An on-time observation can stand alone.`,
    wider: `Connect the supplied verified fact to this observation only when a useful comparison fits. Preserve its scope, figures and qualifications. One bus proves no wider trend or cause. Otherwise omit the claim with hook_used false.`,
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
    const notableTraffic = traffic && ['moving slowly', 'moving very slowly', 'much slower than a clear road'].includes(traffic.condition);
    if (traffic && notableTraffic && trafficAge >= -30_000 && trafficAge <= 2 * 60_000) {
        choices.push({ kind: 'traffic', key: `traffic:${traffic.key}`, context: {
            nearbyTraffic: { source: 'local traffic reports', checkedAt: traffic.checkedAt, condition: traffic.condition, scope: traffic.scope },
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
