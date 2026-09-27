import type { BusEvent } from '../types/bus-types.js';
// Centres of the reviewed site/app/data/depot_boundaries.geojson yards.
// Exact names only; unmatched garage names do not acquire guessed coordinates.
const depots = [
  {
    "name": "Lawrence Hill",
    "longitude": -2.5662822107142857,
    "latitude": 51.460810428571435,
    "reviewedOn": "2026-09-10"
  },
  {
    "name": "Hengrove",
    "longitude": -2.5870349732312294,
    "latitude": 51.42047119857991,
    "reviewedOn": "2026-09-10"
  },
  {
    "name": "Bath (Weston Island)",
    "longitude": -2.3946411636363636,
    "latitude": 51.38200866060606,
    "reviewedOn": "2026-09-10"
  },
  {
    "name": "Weston-super-Mare",
    "longitude": -2.957390416413546,
    "latitude": 51.34237702465217,
    "reviewedOn": "2026-09-10"
  },
  {
    "name": "Keynsham (Gypsy Ln)",
    "longitude": -2.4787048356873655,
    "latitude": 51.39290389461557,
    "reviewedOn": "2026-09-10"
  },
  {
    "name": "Eurocoaches Yard",
    "longitude": -2.5690620392560963,
    "latitude": 51.45599081216246,
    "reviewedOn": "2026-09-10"
  }
];
export function depotGeography(event: BusEvent, stop?: {stop_code: string; lat: number; lon: number}): BusEvent['depotContext'] {
    const threshold = Number(process.env.DEPOT_STORY_MIN_KM || 15);
    if (event.lowConfidence || !stop || stop.stop_code !== event.lastStopCode
        || !Number.isFinite(stop.lat) || !Number.isFinite(stop.lon) || Math.abs(stop.lat)>90 || Math.abs(stop.lon)>180
        || !Number.isFinite(threshold) || threshold < 15 || threshold > 200) return undefined;
    const depot = depots.find(d => d.name === event.busDetails?.garage?.name);
    if (!depot) return undefined;
    const rad = (v:number) => v*Math.PI/180;
    const a = Math.sin(rad(stop.lat-depot.latitude)/2)**2 + Math.cos(rad(stop.lat))*Math.cos(rad(depot.latitude))*Math.sin(rad(stop.lon-depot.longitude)/2)**2;
    const distance = 6371*2*Math.atan2(Math.sqrt(a),Math.sqrt(Math.max(0,1-a)));
    if(distance < threshold) return undefined;
    return { name: depot.name, distanceKm: Math.round(distance), sourceStopCode: stop.stop_code,
        scope: 'Approximate straight-line distance from the reported stop to the reviewed yard centre. Not distance travelled, a departure, or evidence of an unusual allocation.' };
}
