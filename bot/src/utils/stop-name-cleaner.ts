// Bristol Bus Bot - stop names
// Stop names are corrected once, when the timetable is built
// (pipeline/prepare_stop_names.py with pipeline/stop_names.json), so the bot,
// the site, the live map and the audit all use the same name. This module only
// turns that name into how it's said in a post, and loads the stop enrichment
// file for the locality and street details used in commentary.

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
    logger.info(`Loaded ${Object.keys(stopEnrichment).length} stop enrichment records`);
} catch {
    // Graceful degradation: commentary just has less local detail.
    logger.warn('stop_enrichment.json not found; commentary will have less local detail');
}

/**
 * Export the enrichment data for use by ai-commentary.ts
 */
export function getStopEnrichment(): Record<string, StopEnrichment> {
    return stopEnrichment;
}
