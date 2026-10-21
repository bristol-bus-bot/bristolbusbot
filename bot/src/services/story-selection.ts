import type { BusEvent } from '../types/bus-types.js';
import { isSmallHours } from './story-subject.js';

export type PublishedStory = Pick<BusEvent, 'vehicleRef' | 'operatorRef' | 'line' | 'eventType'>;

/** Editorial sampling, never a measurement of network-wide punctuality. */
export function selectStory(events: BusEvent[], recent: PublishedStory[], random = Math.random,
    now: number = Date.now()): BusEvent | null {
    if (!events.length) return null;
    // From 4am, the first buses of the day at their first scheduled stop are the night's small event.
    const ukHour = Number(new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', hour: 'numeric', hourCycle: 'h23' })
        .formatToParts(new Date(now)).find(part => part.type === 'hour')?.value);
    if (isSmallHours(now) && ukHour >= 4) {
        const early = events.filter(event => event.journeyContext?.timingPointNumber === 1);
        if (early.length) events = early;
    }
    const exceptions = events.filter(event => event.eventType !== 'punctual');
    const punctual = events.filter(event => event.eventType === 'punctual');
    const positiveSlot = recent.length >= 4 && recent.slice(-4).every(event => event.eventType !== 'punctual');
    let pool = positiveSlot && punctual.length ? punctual : exceptions.length ? exceptions : punctual;
    const differentVehicles = pool.filter(event => !recent.slice(-6).some(previous =>
        previous.vehicleRef === event.vehicleRef && previous.operatorRef === event.operatorRef));
    if (differentVehicles.length) pool = differentVehicles;
    const differentRoutes = pool.filter(event => !recent.slice(-2).some(previous =>
        previous.line === event.line && previous.operatorRef === event.operatorRef));
    if (differentRoutes.length) pool = differentRoutes;
    // Give meaningful delays more opportunity, without always picking the worst bus.
    const weight = (event: BusEvent) => 1 + Math.min(15, Math.abs(event.delayMinutes));
    let remaining = random() * pool.reduce((sum, event) => sum + weight(event), 0);
    return pool.find(event => (remaining -= weight(event)) < 0) || pool[pool.length - 1];
}
