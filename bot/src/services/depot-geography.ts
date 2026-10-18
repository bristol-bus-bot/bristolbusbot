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
// Garage names as the fleet data spells them, mapped by hand to a reviewed yard
// above. Exact keys only; no fuzzy matching.
const yardAliases: Record<string, string> = {
  "Bath": "Bath (Weston Island)",
  "Weston": "Weston-super-Mare",
  "Keynsham": "Keynsham (Gypsy Ln)",
};
// Garages without a reviewed yard boundary: the centre of the garage's town or,
// for Marlborough Street, Bristol bus station beside it. Good to a kilometre or
// two, which is enough for distances of 8 km and more. Keyed by operator and
// fleet-data garage name. Garages with unclear locations are left out.
const garageTowns: Record<string, { place: string; latitude: number; longitude: number }> = {
  "FBRI:Marlborough Street": { place: "Bristol bus station, Marlborough Street", latitude: 51.4592, longitude: -2.5930 },
  "FBRI:Wells": { place: "Wells", latitude: 51.2093, longitude: -2.6474 },
  "SCGL:Gloucester": { place: "Gloucester", latitude: 51.8642, longitude: -2.2382 },
  "SCGL:Cheltenham": { place: "Cheltenham", latitude: 51.8994, longitude: -2.0783 },
  "SCGL:Stroud": { place: "Stroud", latitude: 51.7457, longitude: -2.2178 },
  "SCGL:Swindon": { place: "Swindon", latitude: 51.5558, longitude: -1.7797 },
  "TDTR:Swindon": { place: "Swindon", latitude: 51.5558, longitude: -1.7797 },
  "SSWL:Cwmbran": { place: "Cwmbran", latitude: 51.6537, longitude: -3.0237 },
  "SSWL:Merthyr": { place: "Merthyr Tydfil", latitude: 51.7487, longitude: -3.3781 },
  "SSWL:Caerphilly": { place: "Caerphilly", latitude: 51.5749, longitude: -3.2180 },
  "SSWL:Porth Depot SignON/OFF": { place: "Porth", latitude: 51.6136, longitude: -3.4076 },
  "NWPT:Newport Bus": { place: "Newport", latitude: 51.5842, longitude: -2.9977 },
};

export function depotGeography(event: BusEvent, stop?: {stop_code: string; lat: number; lon: number}): BusEvent['depotContext'] {
    const threshold = Number(process.env.DEPOT_STORY_MIN_KM || 8);
    if (event.lowConfidence || !stop || stop.stop_code !== event.lastStopCode
        || !Number.isFinite(stop.lat) || !Number.isFinite(stop.lon) || Math.abs(stop.lat)>90 || Math.abs(stop.lon)>180
        || !Number.isFinite(threshold) || threshold < 5 || threshold > 200) return undefined;
    const garage = event.busDetails?.garage?.name?.trim() || '';
    const yard = depots.find(d => d.name === (yardAliases[garage] || garage));
    const town = garageTowns[`${(event.operatorRef || 'FBRI').trim().toUpperCase()}:${garage}`];
    const centre = yard ? { latitude: yard.latitude, longitude: yard.longitude } : town;
    if (!centre) return undefined;
    const rad = (v:number) => v*Math.PI/180;
    const a = Math.sin(rad(stop.lat-centre.latitude)/2)**2 + Math.cos(rad(stop.lat))*Math.cos(rad(centre.latitude))*Math.sin(rad(stop.lon-centre.longitude)/2)**2;
    const distance = 6371*2*Math.atan2(Math.sqrt(a),Math.sqrt(Math.max(0,1-a)));
    if(distance < threshold) return undefined;
    // The name stays the fleet data's garage name so story-subject can confirm it.
    return { name: garage, distanceKm: Math.round(distance), sourceStopCode: stop.stop_code,
        scope: yard
            ? 'Approximate straight-line distance from the reported stop to the reviewed yard centre. Not distance travelled, a departure, or evidence of an unusual allocation.'
            : `Approximate straight-line distance from the reported stop to ${town!.place}, where the garage is. Not distance travelled, a departure, or evidence of an unusual allocation.` };
}
