import type { BusEvent } from '../types/bus-types.js';
import { spokenStopName } from '../utils/stop-name-cleaner.js';

/** Only an exact, confident matched trip supplies a public destination. */
export function spokenDestination(event: BusEvent): string | null {
    const journey = event.journeyContext;
    if (event.lowConfidence || !journey || !event.collectorTripId
        || journey.tripId !== event.collectorTripId || !Number.isInteger(event.collectorStopSequence)
        || !Number.isInteger(journey.timingPointNumber) || journey.timingPointNumber < 1
        || journey.timingPointNumber >= journey.totalStops) return null;
    const destination = spokenStopName(journey.destination || '');
    const origin = spokenStopName(journey.origin || '');
    // A loop or an unqualified generic terminus does not distinguish direction.
    if (!destination || destination.toLowerCase() === origin.toLowerCase()
        || /^(?:the centre|bus station|transport hub|interchange|railway station)$/i.test(destination)) return null;
    return destination;
}

export function destinationMentioned(post: string, destination: string): boolean {
    const escaped = destination.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return new RegExp(`\\btowards\\s+${escaped}(?=$|[^\\p{L}\\p{N}])`, 'iu').test(post);
}

export interface JourneyStop { stop_sequence: number; stop_code: string; stop_name: string; }

/** Use the collector's matched trip and exact timing point, not a route-wide name search. */
export function matchedJourneyContext(event: BusEvent, stops: JourneyStop[]): BusEvent['journeyContext'] {
    if (!event.collectorTripId || !Number.isInteger(event.collectorStopSequence) || event.lowConfidence || !stops.length) return undefined;
    const matches = stops.map((stop, index) => ({ stop, index })).filter(({ stop }) =>
        stop.stop_sequence === event.collectorStopSequence && stop.stop_code === event.lastStopCode);
    if (matches.length !== 1 || stops.some(stop => !stop.stop_name)) return undefined;
    return { tripId: event.collectorTripId, timingPointNumber: matches[0].index + 1,
        totalStops: stops.length, origin: stops[0].stop_name, destination: stops[stops.length - 1].stop_name };
}
