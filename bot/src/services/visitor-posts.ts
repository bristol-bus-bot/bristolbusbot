/**
 * Planning #67: "visitor" posts about a bus working a route far from its usual
 * one, from the nightly rare-working shadow. Switched off unless
 * BBB_RARE_WORKING_POSTS=true.
 *
 * Every fact comes from the pipeline's dated readings (the 25 Sep 2026 review
 * decision): the route the bus worked, the route it usually works and on how
 * many of the last 56 days. No livery, depot or allocation claims, no paid
 * model call, and the text is always about a past day, never a current
 * position. The pipeline decides what counts as a visitor (evidence.visitor).
 */
import { DateTime } from 'luxon';

export const VISITOR_POSTS_PER_DAY = 2;
/** Older than this and the story is stale. */
export const VISITOR_MAX_AGE_DAYS = 3;
const ZONE = 'Europe/London';

export interface VisitorEvidence {
    visitor?: boolean;
    usual_route?: string | null;
    usual_route_days?: number;
    shared_regulars_pct?: number | null;
    pair_days?: number;
}

export interface RareWorkingEvent {
    event_id: string;
    service_date: string;
    operator: string;
    vehicle_ref: string;
    route: string;
    evidence?: VisitorEvidence | unknown;
}

export function visitorPostsEnabled(env: NodeJS.ProcessEnv = process.env): boolean {
    return (env.BBB_RARE_WORKING_POSTS || '').trim().toLowerCase() === 'true';
}

function evidenceOf(event: RareWorkingEvent): VisitorEvidence {
    return event.evidence && typeof event.evidence === 'object'
        ? event.evidence as VisitorEvidence : {};
}

/** "FBRI-65172" -> "65172"; "EUTX-VF05_ETE" -> "VF05 ETE". */
export function busName(vehicleRef: string): string {
    const bare = vehicleRef.includes('-') ? vehicleRef.slice(vehicleRef.indexOf('-') + 1) : vehicleRef;
    return bare.replace(/_/g, ' ').trim();
}

function dayWords(serviceDate: string, today: DateTime): string | null {
    const day = DateTime.fromFormat(serviceDate, 'yyyyMMdd', { zone: ZONE });
    if (!day.isValid) return null;
    const age = Math.round(today.startOf('day').diff(day.startOf('day'), 'days').days);
    if (age < 1 || age > VISITOR_MAX_AGE_DAYS) return null;   // past days only
    return age === 1 ? 'Yesterday' : `On ${day.toFormat('cccc')}`;
}

/** The post, or null when the event is not a usable visitor today. */
export function visitorPost(event: RareWorkingEvent, now: DateTime = DateTime.now()): string | null {
    const evidence = evidenceOf(event);
    if (evidence.visitor !== true || evidence.pair_days !== 0) return null;
    const usual = (evidence.usual_route || '').trim();
    const days = Number(evidence.usual_route_days);
    if (!usual || !Number.isInteger(days) || days < 1 || usual === event.route) return null;
    const when = dayWords(event.service_date, now.setZone(ZONE));
    if (!when) return null;
    const bus = busName(event.vehicle_ref);
    if (!bus) return null;
    return `${when}, bus ${bus} turned up on the ${event.route}, a route it hadn't worked once in the previous eight weeks. `
        + `Its usual haunt is the ${usual}, which it worked on ${days} of those 56 days.`;
}

/**
 * Which visitors to post now: at most VISITOR_POSTS_PER_DAY a day (counting
 * `postedToday`), one per bus, those furthest from their usual route first.
 */
export function chooseVisitorPosts(events: RareWorkingEvent[], postedToday: number,
                                   alreadyPosted: Set<string>, now: DateTime = DateTime.now()):
        Array<{ event: RareWorkingEvent; text: string }> {
    const room = Math.max(0, VISITOR_POSTS_PER_DAY - postedToday);
    if (!room) return [];
    const usable = events
        .filter(event => !alreadyPosted.has(event.event_id))
        .map(event => ({ event, text: visitorPost(event, now) }))
        .filter((item): item is { event: RareWorkingEvent; text: string } => item.text !== null)
        .sort((a, b) => {
            const ea = evidenceOf(a.event), eb = evidenceOf(b.event);
            return (Number(ea.shared_regulars_pct ?? 100) - Number(eb.shared_regulars_pct ?? 100))
                || (Number(eb.usual_route_days ?? 0) - Number(ea.usual_route_days ?? 0))
                || a.event.event_id.localeCompare(b.event.event_id);
        });
    const chosen: Array<{ event: RareWorkingEvent; text: string }> = [];
    const buses = new Set<string>();
    for (const item of usable) {
        const bus = `${item.event.service_date}|${item.event.vehicle_ref}`;
        if (buses.has(bus)) continue;
        buses.add(bus);
        chosen.push(item);
        if (chosen.length >= room) break;
    }
    return chosen;
}
