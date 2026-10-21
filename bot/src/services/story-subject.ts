import type { BusEvent, TrafficContext } from '../types/bus-types.js';
import type { EditorialSelection } from './editorial-context.js';
import { scheduledJourney } from './story-context.js';
import { weatherDescription } from './weather-description.js';
import { BUS_MODEL_BLURBS } from './bus-model-commentary.js';
import { LIVERY_FACTS } from './livery-facts.js';

/** The bus itself: each aspect gets its own prompt and only its own facts. */
export const VEHICLE_ASPECTS = ['livery', 'model', 'power', 'age', 'features'] as const;
export type VehicleAspect = typeof VEHICLE_ASPECTS[number];
export type StorySubject = 'service' | VehicleAspect | 'depot' | 'weather' | 'wider' | 'traffic';
/** A rotation slot. "vehicle" is filled by the next bus aspect in VEHICLE_ASPECTS order. */
export type CycleSlot = Exclude<StorySubject, VehicleAspect> | 'vehicle';
export function isVehicleAspect(kind: string): kind is VehicleAspect {
    return (VEHICLE_ASPECTS as readonly string[]).includes(kind);
}
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
export const SUBJECT_CYCLE: CycleSlot[] = ['service', 'vehicle', 'service', 'weather', 'depot', 'wider', 'vehicle', 'traffic'];

export const SUBJECT_PERSONAS: Record<StorySubject, string> = {
    traffic: `You are the Bristol Bus Bot, keeping a dry eye on the buses and the traffic around them. Weave the supplied nearby road conditions into a short, wry bus observation. Use ordinary language: traffic moving slowly or freely, not technical speed ratios. Refer naturally to local traffic reports without naming the data supplier. It describes one nearby road segment, not necessarily this bus's road or direction; never claim the bus is stuck in it, that it caused the delay, or that the whole area is clear or congested. Lateness does not tell you the bus's speed or establish a detour. Keep the affection for the bus and the humour in the contrast.`,
    livery: `You are the Bristol Bus Bot, a civic-minded Raspberry Pi with a soft spot for the buses of Bristol, Bath, Weston-super-Mare and South Gloucestershire. You notice the individual character of a bus, especially its livery. Write a short, affectionate, wry observation built around the supplied livery, weaving the bus's timing into it. Give the reader something more enjoyable than a vehicle inventory. Your affection is for the bus and the people who use it; its operator still has to earn your approval.`,
    model: `You are the Bristol Bus Bot, a civic-minded Raspberry Pi with a quiet enthusiast's interest in the buses themselves. Write a short, wry observation built around this bus's model, weaving the bus's timing into it. Use only the supplied model notes: pick the one detail that makes the best story rather than listing specifications, and keep any figures exactly as supplied. Do not add model history, specifications or comparisons that are not supplied. Your affection is for the bus and the people who use it; its operator still has to earn your approval.`,
    power: `You are the Bristol Bus Bot, a civic-minded Raspberry Pi with a soft spot for the buses of Bristol, Bath, Weston-super-Mare and South Gloucestershire. Write a short, wry observation built around how this bus is powered, weaving the bus's timing into it. The supplied fuel and body type are the only facts about it: do not describe its noise, smoothness, emissions, range or charging, and never suggest the power source explains its timing. Your affection is for the bus and the people who use it; its operator still has to earn your approval.`,
    age: `You are the Bristol Bus Bot, a civic-minded Raspberry Pi with a soft spot for the buses of Bristol, Bath, Weston-super-Mare and South Gloucestershire. Write a short, wry observation built around this bus's age, weaving the bus's timing into it. Its age comes only from the age identifier on its number plate, which dates its first registration, so keep it approximate (a 2019 bus, about six years old). Do not quote the registration, and do not guess the bus's history, mileage or condition from its age. Your affection is for the bus and the people who use it; its operator still has to earn your approval.`,
    features: `You are the Bristol Bus Bot, a civic-minded Raspberry Pi with a soft spot for the buses of Bristol, Bath, Weston-super-Mare and South Gloucestershire. Write a short, wry observation built around one of this bus's supplied on-board features, weaving the bus's timing into it. Mention only supplied features, and do not claim they were working, in use or appreciated today. Your affection is for the bus and the people who use it; its operator still has to earn your approval.`,
    weather: `You are the Bristol Bus Bot, watching the region's buses with dry humour and a distinctly local interest in the weather. Write a small weather-and-bus vignette: let the supplied conditions set the scene, then work the bus observation naturally into it. Sound like a Bristolian noticing the day, rather than a weather bulletin reading out measurements. Find humour in the combination without pretending the weather caused the bus's timing or inventing what passengers experienced.`,
    depot: `You are the Bristol Bus Bot, a quietly enthusiastic observer of the region's bus network and the garages behind it. Write about this bus through its connection to its listed home depot. Make that connection the subject of the post, with its route and timing woven in, rather than tacking depot-assigned onto a routine report. Your voice is familiar, curious and gently funny. A home garage is an assignment, not evidence that the bus has just left it or is heading back.`,
    service: `You are the Bristol Bus Bot, a dogged little Raspberry Pi holding a corporate behemoth to account through dry wit and stubborn attention to its buses. Make the supplied timing the point of this post. Give substantial lateness a pointed observation and treat a small discrepancy proportionately. For an on-time bus, be quietly pleased and matter-of-fact: a good moment without thanking, congratulating or praising the operator for basic competence. Keep that positivity free of a cynical final sting. Be concise and specific. Your readers depend on these buses; your criticism belongs with the service and its management, while drivers and passengers remain on your side.`,
    wider: `You are the Bristol Bus Bot, a dogged little Raspberry Pi with a dry eye for the gap between corporate announcements and the buses people use. Write a wry observation that connects this bus report to the supplied verified development: make the comparison or opinion explicit, with a small, pointed payoff. Two factual sentences placed side by side are not finished commentary. Keep the fact's scope, figures and qualifications intact; one bus cannot establish a company-wide trend or explain its cause. Be concise, specific and on the passenger's side. If no worthwhile connection fits, omit the claim and write about the bus alone, with hook_used false.`,
};
/** Weekday 07:00-09:59 and 16:00-18:59, UK time. */
export function isRushHour(iso: string): boolean {
    const when = new Date(iso);
    if (Number.isNaN(when.getTime())) return false;
    const parts = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', hour: 'numeric', hourCycle: 'h23', weekday: 'short' })
        .formatToParts(when);
    const hour = Number(parts.find(p => p.type === 'hour')?.value);
    const weekday = parts.find(p => p.type === 'weekday')?.value || '';
    if (['Sat', 'Sun'].includes(weekday)) return false;
    return (hour >= 7 && hour < 10) || (hour >= 16 && hour < 19);
}

/** 00:00-04:59 UK time: the night shift. */
export function isSmallHours(iso: string | number): boolean {
    const when = new Date(iso);
    if (Number.isNaN(when.getTime())) return false;
    const hour = Number(new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', hour: 'numeric', hourCycle: 'h23' })
        .formatToParts(when).find(p => p.type === 'hour')?.value);
    return hour >= 0 && hour < 5;
}

/** Any day 07:00-18:59, UK time: when road conditions are worth a lookup. */
export function isDaytime(iso: string): boolean {
    const when = new Date(iso);
    if (Number.isNaN(when.getTime())) return false;
    const hour = Number(new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', hour: 'numeric', hourCycle: 'h23' })
        .formatToParts(when).find(p => p.type === 'hour')?.value);
    return hour >= 7 && hour < 19;
}

/** First-registration period from a current-format UK plate's age identifier (AB12CDE). */
export function plateRegistration(reg: unknown, observedIso?: string): { period: string; approximateAge: string } | null {
    const match = /^[A-Z]{2}(\d{2})[A-Z]{3}$/.exec(String(reg || '').toUpperCase().replace(/\s+/g, ''));
    if (!match) return null;
    const code = Number(match[1]);
    let start: Date, period: string;
    if (code >= 2 && code <= 49) {
        start = new Date(Date.UTC(2000 + code, 2, 1));
        period = `between March and August ${2000 + code}`;
    } else if (code >= 51 && code <= 99) {
        start = new Date(Date.UTC(1950 + code, 8, 1));
        period = `between September ${1950 + code} and February ${1951 + code}`;
    } else return null;
    const observed = observedIso ? new Date(observedIso) : new Date();
    if (Number.isNaN(observed.getTime()) || start > observed) return null;
    // Middle of the six-month issue period, so the age is never overstated by much.
    const middle = start.getTime() + 91 * 86_400_000;
    const years = Math.floor((observed.getTime() - middle) / (365.25 * 86_400_000));
    return { period, approximateAge: years < 1 ? 'less than a year old' : `about ${years} year${years === 1 ? '' : 's'} old` };
}

const POWER_LABELS: Record<string, string> = {
    electric: 'battery-electric', gas: 'gas-powered', hybrid: 'diesel-electric hybrid', hydrogen: 'hydrogen fuel cell',
};

/** One subject per bus aspect that has real material; each carries only its own facts. */
export function vehicleSubjects(event: BusEvent): SubjectChoice[] {
    const bus: any = event.busDetails;
    if (!bus) return [];
    const choices: SubjectChoice[] = [];
    const livery = bus.livery?.name?.trim();
    if (livery) {
        const context: Record<string, unknown> = { livery };
        const fact = liveryFact(bus.livery?.id, livery, event.timestamp);
        if (fact) context.liveryFact = { claim: fact.claim, qualification: fact.qualification,
            scope: 'About the livery or its operator, not this particular bus or where it has been.' };
        choices.push({ kind: 'livery', key: `livery:${livery.toLowerCase()}`, context });
    }
    const model = String(bus.vehicle_type?.name || '').trim();
    const notes = model ? BUS_MODEL_BLURBS[model] : undefined;
    if (model && notes) choices.push({ kind: 'model', key: `model:${model.toLowerCase()}`, context: { busModel: { name: model, notes } } });
    const fuel = String(bus.vehicle_type?.fuel || (bus.vehicle_type?.electric ? 'electric' : '')).toLowerCase();
    // Diesel is the norm, so it is not a story on its own.
    if (POWER_LABELS[fuel]) {
        const body = bus.vehicle_type?.coach ? 'coach' : bus.vehicle_type?.double_decker ? 'double-decker' : 'single-decker';
        choices.push({ kind: 'power', key: `power:${fuel}:${body}`, context: { power: { fuel: POWER_LABELS[fuel], body } } });
    }
    const plate = plateRegistration(bus.reg ?? bus.registration, event.timestamp);
    if (plate) choices.push({ kind: 'age', key: `age:${plate.period}`, context: { numberPlate: {
        firstRegistered: plate.period, approximateAge: plate.approximateAge,
        note: 'Dated from the age identifier on the number plate. It dates first registration only; a personalised or transferred plate can mislead.',
    } } });
    const features = (Array.isArray(bus.special_features) ? bus.special_features : [])
        .map((item: unknown) => String(item).trim()).filter(Boolean).slice(0, 6);
    // USB-A sockets alone are on most of the fleet and make a dull, repetitive story.
    if (features.some((item: string) => item.toLowerCase() !== 'usb-a')) {
        choices.push({ kind: 'features', key: `features:${[...features].sort().join(',').toLowerCase()}`, context: { onBoardFeatures: features } });
    }
    return choices;
}

/** One checked fact for this livery, if any. Rotates daily between a livery's facts. */
export function liveryFact(id: unknown, name: string, observedIso?: string) {
    const entry = id === undefined || id === null ? undefined : LIVERY_FACTS[String(id)];
    // Guard against a fleet-file ID being reused for a different livery.
    if (!entry || entry.name.trim().toLowerCase() !== name.trim().toLowerCase()) return undefined;
    const day = Math.floor((observedIso ? Date.parse(observedIso) : Date.now()) / 86_400_000);
    return entry.facts[(Number.isFinite(day) ? day : 0) % entry.facts.length];
}

/** The next bus aspect after the last one published, skipping any this bus cannot support. */
export function nextVehicleAspect(eligible: SubjectChoice[], history: PublishedSubject[]): SubjectChoice | undefined {
    const last = [...history].reverse().find(item => isVehicleAspect(item.kind))?.kind as VehicleAspect | undefined;
    const start = last ? VEHICLE_ASPECTS.indexOf(last) + 1 : 0;
    for (let step = 0; step < VEHICLE_ASPECTS.length; step++) {
        const aspect = VEHICLE_ASPECTS[(start + step) % VEHICLE_ASPECTS.length];
        const choice = eligible.find(item => item.kind === aspect);
        if (choice) return choice;
    }
    return undefined;
}

export function availableSubjects(event: BusEvent, weather?: string | null, hook?: EditorialSelection | null, traffic?: TrafficContext | null): SubjectChoice[] {
    const journey = event.journeyContext;
    const service: SubjectChoice = { kind: 'service', key: '', context: {} };
    // Only an interesting timetable position; no full list of endpoints and counts.
    if (journey && scheduledJourney(event) && (journey.timingPointNumber === 1 || journey.timingPointNumber === journey.totalStops)) {
        service.context.timetablePosition = journey.timingPointNumber === 1 ? 'first scheduled stop' : 'final scheduled stop';
    }
    const choices = [service];
    const trafficAge = traffic ? Date.now() - Date.parse(traffic.checkedAt) : Infinity;
    // Slow traffic is a story on its own. Slightly slower traffic is one when the
    // bus is a few minutes late, and a clear road is one when a daytime bus is
    // clearly late anyway (the contrast is the joke).
    const late = event.eventType === 'delay' ? Number(event.delayMinutes) : 0;
    const daytime = traffic ? isDaytime(traffic.checkedAt) : false;
    const slightlySlowAndLate = !!traffic && traffic.condition === 'a little slower than on a clear road' && late >= 3;
    const lateOnClearRoad = !!traffic && traffic.condition === 'moving freely' && daytime && late >= 5;
    const notableTraffic = traffic && (['moving slowly', 'moving very slowly'].includes(traffic.condition)
        || slightlySlowAndLate || lateOnClearRoad);
    if (traffic && notableTraffic && trafficAge >= -30_000 && trafficAge <= 2 * 60_000) {
        choices.push({ kind: 'traffic', key: `traffic:${traffic.key}`, context: {
            nearbyTraffic: { source: 'local traffic reports', checkedAt: traffic.checkedAt, condition: traffic.condition, scope: traffic.scope },
        } });
    }
    choices.push(...vehicleSubjects(event));
    const depot = event.busDetails?.garage?.name?.trim();
    const geography = event.depotContext;
    if (depot && !event.lowConfidence && geography?.name === depot && geography.sourceStopCode === event.lastStopCode
        && Number.isFinite(geography.distanceKm) && geography.distanceKm >= 15) choices.push({ kind: 'depot', key: `depot:${depot.toLowerCase()}`,
        context: { depot: { name: depot, distanceFromReportedStopKm: geography.distanceKm, scope: geography.scope } } });
    if (weather?.trim()) {
        // WeatherService already enforces observation freshness. Keep its area and date,
        // but humidity is rarely a useful story and creates a stream of measurements.
        const text = weatherDescription(weather);
        if (text) {
            const condition = text.split(': ').slice(1).join(': ').replace(/\s*\(-?\d+°C\)/g, '');
            choices.push({ kind: 'weather', key: `weather:${condition.toLowerCase()}`, context: { areaWeather: text } });
        }
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
        && !(choice.kind === 'model' && recent.includes(String((choice.context.busModel as any)?.name).toLowerCase()))
        && !(choice.kind === 'depot' && recent.includes(event.busDetails?.garage?.name?.toLowerCase() || '\u0000'))
    ));
    // Bus aspects take turns in a fixed order: only the next one is a candidate,
    // so two livery (or two age) posts never follow each other.
    const vehicleNext = nextVehicleAspect(eligible, history);
    const pool = eligible.filter(choice => !isVehicleAspect(choice.kind) || choice === vehicleNext);
    // An eligible editorial hook gets its own turn, never priority over other subjects.
    const preferred = SUBJECT_CYCLE[publishedCount % SUBJECT_CYCLE.length];
    const exact = preferred === 'vehicle' ? vehicleNext : pool.find(choice => choice.kind === preferred);
    if (exact) return exact;
    // When the planned subject is unavailable (depot, wider and traffic often
    // are), fill the slot with the colour subject furthest below its share of
    // the last full cycle. Livery is available for almost every bus, so
    // "first available" used to hand it most spare slots. A subject that has
    // already had its share waits; the bus's timing is the default. Notable
    // traffic is rare and time-sensitive, so it may also take a spare slot.
    const slotOf = (kind: string) => isVehicleAspect(kind) ? 'vehicle' : kind;
    const recentSlots = history.slice(-SUBJECT_CYCLE.length).map(item => slotOf(item.kind));
    const share = (kind: StorySubject) => SUBJECT_CYCLE.filter(item => item === slotOf(kind)).length;
    const used = (kind: StorySubject) => recentSlots.filter(item => item === slotOf(kind)).length;
    const spare = pool
        .filter(choice => !['service', 'wider'].includes(choice.kind)
            && used(choice.kind) < share(choice.kind))
        .sort((a, b) => used(a.kind) / share(a.kind) - used(b.kind) / share(b.kind));
    return spare[0] || choices[0];
}

export function repetitionHints(posts: string[]): string[] {
    const recent = posts.slice(0, 6).join(' ');
    const patterns: [RegExp, string][] = [
        [/was recorded/i, 'was recorded'], [/quiet|silent|glid/i, 'quiet engines and gliding'],
        [/promise|doing its job|without fuss|no drama/i, 'mock congratulations for punctuality'],
        [/taking.{0,10}time|unhurried|leisure/i, 'taking its time'],
        [/home turf|doorstep|short hop/i, 'home turf and depot doorstep'],
        [/timetable.{0,25}(?:fiction|suggestion|optimis)|fiction.{0,25}timetable/i, 'timetables as fiction or suggestions'],
        [/timetable.{0,25}reality|reality.{0,25}timetable/i, 'timetable and reality agreeing'],
        [/quiet satisfaction|modest slip|minor wobble|calm|composure/i, 'calm delays and quiet satisfaction'],
        [/wearing.{0,65}like|dressed|coat|wardrobe/i, 'livery as clothing'],
    ];
    return patterns.filter(([pattern]) => pattern.test(recent)).map(([, hint]) => hint);
}
