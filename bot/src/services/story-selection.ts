import type { BusEvent } from '../types/bus-types.js';

export type PublishedStory = Pick<BusEvent, 'vehicleRef' | 'operatorRef' | 'line' | 'eventType'>;

/** Editorial sampling, never a measurement of network-wide punctuality. */
export function selectStory(events: BusEvent[], recent: PublishedStory[], random = Math.random): BusEvent | null {
    if (!events.length) return null;
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
