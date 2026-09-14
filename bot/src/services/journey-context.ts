import type { BusEvent } from '../types/bus-types.js';

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
