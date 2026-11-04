import { appendFileSync, mkdirSync, renameSync, statSync } from 'node:fs';
import { dirname } from 'node:path';
import { logger } from '../utils/logging.js';

/**
 * Every writer draft with the exact brief it was given and what the checks
 * said, for a possible later tuning run. Bus data only, nothing personal.
 * Two files of at most maxBytes each (the current one and ".1"), so disk use
 * stays bounded; a write failure never affects posting.
 */
export interface TrainingExample {
    at: string;
    model: string;
    verifierModel: string;
    subject: string;
    attempt: number;
    brief: string;
    draft: string;
    hookUsed: boolean;
    mechanicalIssues: string[];
    verifier: { verdict: string; reasons: string[] } | null;
    outcome: 'passed' | 'mechanical_fail' | 'verifier_fail';
}

export class TrainingLog {
    constructor(private readonly file?: string, private readonly maxBytes = 25 * 1024 * 1024) {}

    record(example: TrainingExample): void {
        if (!this.file) return;
        try {
            mkdirSync(dirname(this.file), { recursive: true });
            try {
                if (statSync(this.file).size >= this.maxBytes) renameSync(this.file, `${this.file}.1`);
            } catch { /* No file yet. */ }
            appendFileSync(this.file, JSON.stringify(example) + '\n', { mode: 0o600 });
        } catch {
            logger.warn('Training example could not be saved; posting is unaffected');
        }
    }
}
