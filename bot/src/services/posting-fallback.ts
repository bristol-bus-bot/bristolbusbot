import { DateTime } from 'luxon';
import type { BusEvent } from '../types/bus-types.js';
import { storyStatus } from './story-brief.js';
import { spokenStopName } from '../utils/stop-name-cleaner.js';
import { spokenDestination } from './journey-context.js';
import { logger } from '../utils/logging.js';

// Timeless editorial observations: no invented live service, passenger or delay claims.
export const EDITORIAL_RESERVE = [
    'A good bus service is wonderfully unremarkable: turn up, get on, get there. That is the ambition.',
    'A handsome livery is a lovely thing. A handsome livery on a bus that turns up when expected is even better.',
    'My ideal bus journey has very little plot: a bus, a seat and the right destination.',
    'Public transport should leave you thinking about where you are going, rather than how you are going to get there.',
    'I have a soft spot for a bus in an old livery. Nostalgia is more welcome in the paintwork than in the service frequency.',
    'The best sort of bus drama is spotting an interesting vehicle. Everything else can be reassuringly dull.',
    'A bus stop should be a small part of your day, not a test of your commitment to public transport.',
    'The dream is a bus journey so straightforward that there is nothing for me to complain about. I could take up admiring the upholstery.',
    'Liveries, fleet numbers, favourite seats: plenty to enjoy about buses. Reliability is what makes the enjoyment possible.',
    'There is room for both affection for the buses and impatience with the service. I contain multitudes, mostly double-deckers.',
    'A timetable is a rather small document to carry so many plans for the day.',
    'I would happily trade a good bus joke for a good bus service. Ideally, Bristol gets both.',
    'Public transport: because being able to get somewhere should not depend on owning a car.',
    'A useful bus network is a public service. The clue is in the useful bit.',
    'My transport manifesto fits on a ticket: affordable fares, useful routes, buses you can rely on.',
    'The romance of the open road is all very well. I would settle for a dependable connection.',
    'A bus timetable should help you plan a life, not become a hobby in its own right.',
    'Good connections are not just for people with influential friends.',
    'Accessibility should be part of the service, not a special request.',
    'A bus shelter is a modest civic ambition. A roof is not asking for Versailles.',
    'I am politically committed to being able to get home after an evening out.',
    'The last bus deserves a place in the conversation about a city’s nightlife.',
    'A route on a map is a beginning. A usable service is the point.',
    'Public services should be judged from the passenger seat.',
    'There is nothing extravagant about wanting a bus that works for people who work weekends.',
    'The freedom to travel should include people who cannot drive.',
    'An integrated transport system is my idea of getting everybody on the same page. Preferably the same ticket.',
    'I like a vintage bus. I do not require vintage standards of accessibility.',
    'A useful bus stop tells you where you are, where you can go and how. Radical stuff.',
    'Passenger information should answer questions, not set riddles.',
    'A public transport consultation should involve the people waiting at the stops.',
    'Transport policy has to survive contact with a shopping bag and a rainy Tuesday.',
    'Good bus services belong in ordinary budgets, not just grand announcements.',
    'My favourite transport innovation would be making the whole journey straightforward.',
    'A city is more welcoming when getting around it does not require local knowledge and a contingency plan.',
    'Rural public transport deserves more than a footnote in an urban strategy.',
    'A hospital appointment should not require a qualification in journey planning.',
    'I am fond of the top deck. I am also fond of transport decisions being made in public.',
    'The best bus network would let a missed connection be a small inconvenience, not the end of the evening.',
    'Public transport should expand your options. That is a fairly practical sort of freedom.',
];

export function reservePost(now = Date.now(), history: Array<{ text: string; publishedAt: string }> = []): string {
    const used = new Map<string, number>();
    for (const r of history) {
        const time = Date.parse(r.publishedAt);
        if (time <= now && now - time < 7 * 86400_000) used.set(r.text, Math.max(used.get(r.text) || 0, time));
    }
    const unused = EDITORIAL_RESERVE.find(text => !used.has(text));
    if (unused) return unused;
    // Forty lines cannot cover 504 empty cycles. Reuse the least recently used,
    // explicitly logged, rather than invent evidence or silently stop posting.
    logger.warn('[RESERVE_POOL_EXHAUSTED] All reserve lines used within seven days; reusing the oldest');
    return [...EDITORIAL_RESERVE].sort((a,b) => used.get(a)! - used.get(b)!)[0];
}

export function observationPost(event: BusEvent, recentPosts: string[] = []): string {
    const time = DateTime.fromISO(event.timestamp).setZone('Europe/London').toFormat('HH:mm');
    const destination = spokenDestination(event);
    const route = destination ? `${event.line} towards ${destination}` : `${['inbound','outbound'].includes(event.direction) ? `${event.direction} ` : ''}${event.line}`;
    const stop = spokenStopName(event.lastStopName || ''), status = storyStatus(event);
    const shapes = [
        `The ${route} was ${status} at ${stop}.`,
        `At ${stop}, the ${route} was ${status}.`,
        `${stop}: the ${route} was ${status}.`,
        `The observation at ${stop} had the ${route} ${status}.`,
        `For the ${route}, the recorded timing at ${stop} was ${status}.`,
        `At ${time}, the ${route} was ${status} at ${stop}.`,
        `Recorded at ${stop}: the ${route} was ${status}.`,
        `The ${route}: ${status} at ${stop} in the ${time} observation.`,
    ];
    const previous = recentPosts[0] || '';
    const index = /^Recorded/.test(previous) ? 6 : previous.includes(': the ') ? 2 : /^The observation/.test(previous) ? 3 : /^The .*:/.test(previous) ? 7 : /^The /.test(previous) ? 0
        : /^At \d/.test(previous) ? 5 : /^At /.test(previous) ? 1 : /^For /.test(previous) ? 4 : /^Recorded/.test(previous) ? 6 : previous.includes(': the ') ? 2 : -1;
    return shapes[(index + 1) % shapes.length];
}
