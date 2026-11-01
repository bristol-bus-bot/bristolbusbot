// Bristol Bus Bot - Stop Name Cleaner
// Data-driven stop name enrichment from Bristol Open Data,
// with manual overrides for edge cases and creative local names.

import { readFileSync } from 'fs';
import { logger } from './logging.js';
import { BOT_DATA_PATHS } from '../config/data-paths.js';

/** Spoken label only: never use this as a stop identity or a matching key. */
export function spokenStopName(raw: string): string {
    return raw.trim()
        .replace(/\s+[-\u2013\u2014]\s+(?:(?:Stop|Stand|Bay)\s+[A-Za-z0-9]{1,4}|[A-Z]\d{0,2}|[A-Z][a-z])\s*$/, '')
        // NaPTAN position indicators such as "Down Lane - o/s" (outside) or "- nr".
        .replace(/\s+[-\u2013\u2014]\s+(?:o\/s|opp|nr|adj|o\/p)\.?\s*$/i, '')
        .replace(/\bStn\b/g, 'Station')
        .replace(/\bRd\b/g, 'Road')
        .replace(/\bUtd\b/g, 'United')
        .replace(/\bOpp\b/g, 'opposite')
        .replace(/\bNr\b/g, 'near');
}

// --- Data-driven enrichment loaded once at module init ---

interface StopEnrichment {
    name: string | null;
    locality: string | null;
    street: string | null;
    local_authority: string | null;
    atco_code: string;
}

let stopEnrichment: Record<string, StopEnrichment> = {};

try {
    const enrichmentPath = BOT_DATA_PATHS.stopEnrichment;
    const data = readFileSync(enrichmentPath, 'utf-8');
    stopEnrichment = JSON.parse(data);
    logger.info(`Loaded ${Object.keys(stopEnrichment).length} enriched stop names from Bristol Open Data`);
} catch {
    // Graceful degradation: manual entries still work
    logger.warn('stop_enrichment.json not found; using manual stop name mappings only');
}

// Suffixes to strip from enrichment names (bay numbers, platform numbers, etc.)
const BAY_SUFFIX_PATTERN = /\s*-\s*(?:Bay\s+\d+|PR\d+|\d+|Stop\s+[A-Z]\d?)\s*$/i;

// Generic stop names that benefit from a locality prefix
const GENERIC_NAMES = new Set([
    'Bus Station', 'Tesco', "Sainsbury's", 'Sainsburys', 'Morrisons',
    'Post Office', 'Leisure Centre', 'Shopping Centre', 'Retail Park',
    'Transport Hub', 'Skills Academy', 'Railway Station', 'Train Station',
    'Medical Centre', 'Health Centre', 'Community Centre', 'Library',
    'School', 'Church', 'Interchange', 'Park & Ride',
    'Public Transport Interchange', 'Airport Bus Station'
]);

/**
 * Clean up generic stop names by adding location context.
 *
 * 3-pass lookup:
 *   1. Manual overrides for edge cases (airport disambiguation, creative names)
 *   2. Data-driven enrichment from Bristol Open Data
 *   3. Locality prefix for remaining generic names
 */
export function cleanStopName(stopName: string, stopCode?: string): string {
    if (!stopCode) return stopName;

    // === PASS 1: Manual overrides (edge cases the data can't resolve) ===
    const manualResult = manualOverrides(stopName, stopCode);
    if (manualResult !== null) return manualResult;

    // === PASS 2: Data-driven enrichment ===
    const enriched = stopEnrichment[stopCode];
    if (enriched?.name) {
        // Strip bay/platform suffixes for a cleaner name
        const cleanedEnrichmentName = enriched.name.replace(BAY_SUFFIX_PATTERN, '').trim();

        // Use the enrichment name if it's more descriptive than the GTFS name
        // "Bus Station" -> "Bristol Bus Station" or "Dorchester Road" -> "Dorchester Road"
        if (cleanedEnrichmentName && cleanedEnrichmentName !== stopName) {
            return cleanedEnrichmentName;
        }
    }

    // === PASS 3: Locality prefix for generic names ===
    if (enriched?.locality && GENERIC_NAMES.has(stopName)) {
        return `${enriched.locality} ${stopName}`;
    }

    return stopName;
}

/**
 * Export the enrichment data for use by ai-commentary.ts
 */
export function getStopEnrichment(): Record<string, StopEnrichment> {
    return stopEnrichment;
}

// Hand-written corrections, in the same three tables as the site's
// site/app/services/stop_names.py. Both are checked against
// shared/fixtures/stop_names.json, so a correction added here fails the
// site's tests (and theirs fails ours) until both match.

// (stop code, timetable name, corrected name): reviewed NaPTAN identities.
export const STOP_NAME_EXACT: ReadonlyArray<readonly [string, string, string]> = [
    ["bstagpa", "The Roman Villa", "Kingsweston Roman Villa"],
    ["bstagpd", "The Roman Villa", "Kingsweston Roman Villa"],
    ["bstajdt", "Woodleaze", "Woodleaze in Sea Mills"],
    ["bstajmj", "Riverleaze", "Bristol Manor Farm Football Club"],
    ["bstdgjg", "Students Union", "UoB Students Union"],
    ["bstdgpt", "Students Union", "UoB Students Union"],
    ["bstdtmw", "Whiteleaze", "Whiteleaze - Southmead Road"],
    ["bstdwag", "Charlton Rd Jct", "Charlton Road Junction"],
    ["bstgpmt", "Hengrove Leisure Pk", "Hengrove Leisure Park"],
    ["bstgpwt", "Hengrove Leisure Pk", "Hengrove Leisure Park"],
    ["bstjdgp", "Alverstoke", "Alverstoke Green"],
    ["bstmdwp", "Stapleton Baptist Ch", "Stapleton Baptist Church"],
    ["bstmwga", "Quadrant West", "Hillfields Quadrant West"],
    ["bstpgdw", "Skills Academy", "South Bristol Skills Academy"],
    ["bstpgja", "Bridge Campus", "Bridge Learning Campus"],
    ["bstpgmj", "Transport Hub", "Bristol University North Village Transport Hub"],
    ["bstpgmp", "Transport Hub", "Bristol University North Village Transport Hub"],
    ["bstpgwa", "Cater Road Rbt", "Cater Road Roundabout"],
    ["bstpjmd", "Third Way", "Third Way Avonmouth"],
    ["bstpjtg", "Filwood Grn Business Pk", "Filwood Green Business Park"],
    ["bstpmwt", "Portway P&R", "Portway Park & Ride"],
    ["bstptdw", "Sainsbury's", "Arno's Vale Sainsbury's"],
    ["bthadgp", "Post Office", "Compton Martin Post Office"],
    ["bthadgt", "Post Office", "Compton Martin Post Office"],
    ["bthadtw", "Post Office", "Chew Magna Post Office"],
    ["bthadwa", "Post Office", "Chew Magna Post Office"],
    ["bthagat", "Post Office", "Bishop Sutton Post Office"],
    ["bthagaw", "Post Office", "Bishop Sutton Post Office"],
    ["bthajaj", "Hillcrest", "Hillcrest Pensford"],
    ["bthawjm", "Recreation Ground", "Timsbury Recreation Ground"],
    ["bthawmt", "Two Headed Man", "Keynsham Motors"],
    ["bthdjtp", "Newbridge P&R", "Newbridge Park & Ride"],
    ["bthjatw", "Post Office", "Odd Down Post Office"],
    ["bthjdwg", "Sainsbury's", "Odd Down Sainsbury's"],
    ["bthjdwg", "Sainsburys", "Odd Down Sainsbury's"],
    ["bthmwjt", "Sainsbury's", "Odd Down Sainsbury's"],
    ["bthmwjt", "Sainsburys", "Odd Down Sainsbury's"],
    ["bthpamj", "Post Office", "Keynsham Post Office"],
    ["bthpdap", "Tesco", "Old Mills Tesco"],
    ["bthpdat", "Tesco", "Old Mills Tesco"],
    ["sgladaj", "Post Office", "Severn Beach Post Office"],
    ["sgladam", "Post Office", "Severn Beach Post Office"],
    ["sglagdg", "Rugby Club", "Clifton Rugby Club"],
    ["sglagdj", "Rugby Club", "Clifton Rugby Club"],
    ["sglatjp", "Sainsbury's", "Stoke Gifford Sainsbury's"],
    ["sglatjp", "Sainsburys", "Stoke Gifford Sainsbury's"],
    ["sgldgap", "Leisure Centre", "Thornbury Leisure Centre"],
    ["sgldgat", "Leisure Centre", "Thornbury Leisure Centre"],
    ["sglgwgp", "Sainsbury's", "Emersons Green Sainsbury's"],
    ["sglmtap", "Morrisons", "Yate Morrisons"],
    ["sglmtma", "Shopping Centre", "Yate Shopping Centre"],
    ["sglmtmd", "Shopping Centre", "Yate Shopping Centre"],
    ["sglmtmg", "Shopping Centre", "Yate Shopping Centre"],
    ["sglmwma", "The Clock", "Chipping Sodbury Clock Tower"],
    ["sglmwtg", "The Boot", "The Boot Inn"],
    ["sglpata", "Post Office", "Horton Post Office"],
    ["sglpatd", "Post Office", "Horton Post Office"],
    ["sglpmpg", "Morrisons", "Yate Morrisons"],
    ["sglptwt", "Park & Ride (B)", "Yate Park & Ride (B)"],
    ["sglpwdj", "Shopping Centre", "Yate Shopping Centre"],
    ["sglpwdm", "Shopping Centre", "Yate Shopping Centre"],
    ["sglpwgt", "Park & Ride (A)", "Yate Park & Ride (A)"],
    ["sgltada", "Park & Ride (C)", "Yate Park & Ride (C)"],
    ["sgltadg", "Amazon", "Amazon Distribution Centre BRS1"],
    ["wsmdpmg", "Tesco", "Clevedon Tesco"],
    ["wsmdpmp", "Tesco", "Clevedon Tesco"],
    ["wsmgapj", "Tesco", "Congresbury Tesco"],
    ["wsmgapm", "Tesco", "Congresbury Tesco"],
    ["wsmgwjd", "Leisure Centre", "Backwell Leisure Centre"],
    ["wsmgwjg", "Leisure Centre", "Backwell Leisure Centre"],
    ["wsmjdgd", "Post Office", "Felton Post Office"],
    ["wsmjdgm", "Post Office", "Felton Post Office"],
    ["wsmjwgj", "Dental Practice", "Portishead Dental Practice"],
    ["wsmjwgp", "Marina Healthcare Centre", "Portishead Marina Healthcare Centre"],
    ["wsmpawp", "Sainsburys", "Locking Sainsburys"],
    ["wsmpdtw", "Airport Bus Station", "Bristol Airport Bus Station"],
    ["wsmpgwm", "Public Transport Interchange", "Bristol Airport Interchange"],
    ["wsmpgwp", "Public Transport Interchange", "Bristol Airport Interchange"],
    ["wsmpgwt", "Public Transport Interchange", "Bristol Airport Interchange"],
    ["wsmpjad", "Public Transport Interchange", "Bristol Airport Interchange"],
    ["wsmpjag", "Public Transport Interchange", "Bristol Airport Interchange"],
    ["wsmpjaj", "Public Transport Interchange", "Bristol Airport Interchange"],
    ["wsmpjam", "Public Transport Interchange", "Bristol Airport Interchange"],
    ["wsmpjap", "Public Transport Interchange", "Bristol Airport Interchange"],
    ["wsmpjat", "Public Transport Interchange", "Bristol Airport Interchange"],
    ["wsmpjaw", "Public Transport Interchange", "Bristol Airport Interchange"],
];

// (code prefix, timetable name, corrected name): longer prefixes first.
export const STOP_NAME_PREFIX: ReadonlyArray<readonly [string, string, string]> = [
    ["bstg", "Bus Station", "Bristol Bus Station"],
    ["bstj", "Temple Meads Stn", "Bristol Temple Meads Station"],
    ["bstgw", "Temple Meads Stn", "Bristol Temple Meads Station"],
    ["bthm", "Bus Station", "Bath Bus Station"],
    ["bthj", "Morrisons", "Bath Morrisons"],
    ["sglagt", "Bus Station", "Cribbs Causeway Bus Station"],
    ["sglag", "Retail Park", "Cribbs Causeway Retail Park"],
    ["sgldg", "Tesco", "Thornbury Tesco"],
];

// (timetable name, corrected name): abbreviations corrected wherever they appear.
export const STOP_NAME_ANY: ReadonlyArray<readonly [string, string]> = [
    ["Charlton Rd Jct", "Charlton Road Junction"],
    ["Filwood Grn Business Pk", "Filwood Green Business Park"],
    ["Hengrove Leisure Pk", "Hengrove Leisure Park"],
    ["Stapleton Baptist Ch", "Stapleton Baptist Church"],
    ["Temple Meads Stn", "Bristol Temple Meads Station"],
];

const EXACT = new Map(STOP_NAME_EXACT.map(([code, name, cleaned]) => [`${code} ${name}`, cleaned]));
const ANY = new Map(STOP_NAME_ANY);

/** The hand-written correction for this stop, or null if none applies. */
export function manualOverrides(stopName: string, stopCode: string): string | null {
    const code = stopCode.toLowerCase();
    const exact = EXACT.get(`${code} ${stopName}`);
    if (exact !== undefined) return exact;
    for (const [prefix, name, cleaned] of STOP_NAME_PREFIX) {
        if (code.startsWith(prefix) && stopName === name) return cleaned;
    }
    return ANY.get(stopName) ?? null;
}
