// Bristol Bus Bot - Stop Name Cleaner
// Data-driven stop name enrichment from Bristol Open Data,
// with manual overrides for edge cases and creative local names.

import { readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { logger } from './logging.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

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
    const enrichmentPath = join(__dirname, '../../stop_enrichment.json');
    const data = readFileSync(enrichmentPath, 'utf-8');
    stopEnrichment = JSON.parse(data);
    logger.info(`Loaded ${Object.keys(stopEnrichment).length} enriched stop names from Bristol Open Data`);
} catch {
    // Graceful degradation — manual entries still work
    logger.warn('stop_enrichment.json not found — using manual stop name mappings only');
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

// ─────────────────────────────────────────────────────
// Manual overrides — returns null if no override applies
// ─────────────────────────────────────────────────────

function manualOverrides(stopName: string, stopCode: string): string | null {
    const code = stopCode.toLowerCase();

    // === BRISTOL AIRPORT ===
    // These wsmp* codes are at Bristol Airport, not Weston-super-Mare
    if (code === 'wsmpgwp' || code === 'wsmpgwt' || code === 'wsmpjad' || code === 'wsmpjaj' || code === 'wsmpjam' || code === 'wsmpjap') {
        if (stopName === 'Public Transport Interchange') {
            return 'Bristol Airport Interchange';
        }
    }
    if (code === 'wsmpdtw') {
        if (stopName === 'Airport Bus Station') {
            return 'Bristol Airport Bus Station';
        }
    }

    // === WESTON-SUPER-MARE ===
    if (code.startsWith('wsmp')) {
        if (stopName === 'Public Transport Interchange') {
            return 'Weston-super-Mare Bus Station';
        }
    }

    // === CREATIVE/LOCAL-KNOWLEDGE NAMES ===
    // These are bespoke names that the data doesn't have

    // Bristol Manor Farm Football Club (enrichment just says "Riverleaze")
    if (code === 'bstajmj' && stopName === 'Riverleaze') {
        return 'Bristol Manor Farm Football Club';
    }

    // UoB Students Union (enrichment just says "Students Union")
    if ((code === 'bstdgjg' || code === 'bstdgpt') && stopName === 'Students Union') {
        return 'UoB Students Union';
    }

    // Chipping Sodbury Clock Tower (enrichment just says "The Clock")
    if (code === 'sglmwma' && stopName === 'The Clock') {
        return 'Chipping Sodbury Clock Tower';
    }

    // Amazon Distribution Centre BRS1 (enrichment just says "Amazon")
    if (code === 'sgltadg' && stopName === 'Amazon') {
        return 'Amazon Distribution Centre BRS1';
    }

    // The Boot Inn (enrichment just says "The Boot")
    if (code === 'sglmwtg' && stopName === 'The Boot') {
        return 'The Boot Inn';
    }

    // Keynsham Motors (enrichment just says "Two Headed Man")
    if (code === 'bthawmt' && stopName === 'Two Headed Man') {
        return 'Keynsham Motors';
    }

    // === ABBREVIATION EXPANSIONS ===
    // P&R -> Park & Ride, Stn -> Station, etc.

    if (stopName === 'Temple Meads Stn') {
        return 'Bristol Temple Meads Station';
    }

    if (stopName === 'Hengrove Leisure Pk') {
        return 'Hengrove Leisure Park';
    }

    if (stopName === 'Charlton Rd Jct') {
        return 'Charlton Road Junction';
    }

    if (stopName === 'Stapleton Baptist Ch') {
        return 'Stapleton Baptist Church';
    }

    if (stopName === 'Filwood Grn Business Pk') {
        return 'Filwood Green Business Park';
    }

    // Bridge Learning Campus
    if (code === 'bstpgja' && stopName === 'Bridge Campus') {
        return 'Bridge Learning Campus';
    }

    // Cater Road Roundabout
    if (code === 'bstpgwa' && stopName === 'Cater Road Rbt') {
        return 'Cater Road Roundabout';
    }

    // Kingsweston Roman Villa
    if ((code === 'bstagpa' || code === 'bstagpd') && stopName === 'The Roman Villa') {
        return 'Kingsweston Roman Villa';
    }

    // === SPECIFIC LOCALITY ADDITIONS ===
    // Where the enrichment locality doesn't match the known local name

    // Woodleaze in Sea Mills (enrichment says locality="Sea Mills" but name="Woodleaze" which is same as GTFS)
    if (code === 'bstajdt' && stopName === 'Woodleaze') {
        return 'Woodleaze in Sea Mills';
    }

    // Hillfields Quadrant West
    if (code === 'bstmwga' && stopName === 'Quadrant West') {
        return 'Hillfields Quadrant West';
    }

    // Third Way Avonmouth
    if (code === 'bstpjmd' && stopName === 'Third Way') {
        return 'Third Way Avonmouth';
    }

    // Alverstoke Green
    if (code === 'bstjdgp' && stopName === 'Alverstoke') {
        return 'Alverstoke Green';
    }

    // Whiteleaze - Southmead Road
    if (code === 'bstdtmw' && stopName === 'Whiteleaze') {
        return 'Whiteleaze - Southmead Road';
    }

    // Bath Green Park Sainsbury's (enrichment says locality="Odd Down" which is wrong area context)
    if ((code === 'bthjdwg' || code === 'bthmwjt') && (stopName === "Sainsbury's" || stopName === 'Sainsburys')) {
        return "Bath Green Park Sainsbury's";
    }

    // Gloucester Road Sainsbury's (enrichment would prefix with wrong locality)
    if (code === 'sglatjp' && (stopName === "Sainsbury's" || stopName === 'Sainsburys')) {
        return "Gloucester Road Sainsbury's";
    }

    // Clifton Rugby Club
    if ((code === 'sglagdg' || code === 'sglagdj') && stopName === 'Rugby Club') {
        return 'Clifton Rugby Club';
    }

    // No manual override applies
    return null;
}
