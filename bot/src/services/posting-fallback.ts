import { DateTime } from 'luxon';
import type { BusEvent } from '../types/bus-types.js';
import { storyStatus } from './story-brief.js';

// Timeless editorial observations: no invented live service, passenger or delay claims.
const EDITORIAL_RESERVE = [
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
];

export function reservePost(now = Date.now()): string {
    return EDITORIAL_RESERVE[Math.floor(now / (20 * 60_000)) % EDITORIAL_RESERVE.length];
}

export function observationPost(event: BusEvent): string {
    const time = DateTime.fromISO(event.timestamp).setZone('Europe/London').toFormat('HH:mm');
    return `At ${time}, the ${event.line} was recorded ${storyStatus(event)} at ${event.lastStopName}.`;
}
