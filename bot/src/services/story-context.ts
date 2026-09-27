import type { BusEvent } from '../types/bus-types.js';
import { spokenStopName } from '../utils/stop-name-cleaner.js';

const label = (value: unknown): string | undefined => typeof value === 'string'
    && value.trim().length > 0 && value.length <= 160 && !/[\r\n\u0000-\u001f]/.test(value)
    ? value.trim() : undefined;

/** Project only stop-linked labels, never local-flavour prose or bounding-box guesses. */
export function stopGeography(event: BusEvent): Record<string, unknown> | undefined {
    const place = event.placeContext;
    if (event.lowConfidence || !event.lastStopCode || place?.sourceStopCode !== event.lastStopCode) return undefined;
    const locality = label(place.locality), street = label(place.street);
    const localAuthority = label(place.localAuthority), ward = label(place.ward);
    if (!locality && !street && !localAuthority && !ward) return undefined;
    return {
        locality, street, localAuthority, ward,
        source: 'stop enrichment and geographic ward lookup for the reported stop code',
        scope: 'Geography of the reported stop, not proof of the bus position now. A ward is an administrative area, not necessarily a neighbourhood.',
    };
}

/** Exact matched timetable context, without exposing identifiers or claiming progress. */
export function scheduledJourney(event: BusEvent): Record<string, unknown> | undefined {
    const journey = event.journeyContext;
    if (event.lowConfidence || !journey || !event.collectorTripId || journey.tripId !== event.collectorTripId
        || !Number.isInteger(event.collectorStopSequence) || !Number.isInteger(journey.timingPointNumber)
        || !Number.isInteger(journey.totalStops) || journey.totalStops < 1 || journey.totalStops > 1000
        || journey.timingPointNumber < 1 || journey.timingPointNumber > journey.totalStops
        || !label(journey.origin) || !label(journey.destination)) return undefined;
    return {
        origin: spokenStopName(journey.origin), destination: spokenStopName(journey.destination),
        reportedStopNumber: journey.timingPointNumber, totalScheduledStops: journey.totalStops,
        source: 'collector-matched trip and exact stop sequence in the timetable',
        scope: 'Scheduled journey, including short workings and loops. This does not establish departure, arrival, stops actually served or timing elsewhere on the journey.',
    };
}
