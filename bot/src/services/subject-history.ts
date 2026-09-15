import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { logger } from '../utils/logging.js';
import type { PublishedSubject, SubjectChoice } from './story-subject.js';

export class SubjectHistory {
    publishedCount = 0;
    history: PublishedSubject[] = [];

    constructor(private readonly file?: string) {
        if (!file || !existsSync(file)) return;
        try {
            const raw = readFileSync(file, 'utf8');
            if (raw.length > 32_768) throw new Error('oversized subject history');
            const data = JSON.parse(raw);
            if (data.version !== 1 || !Number.isSafeInteger(data.publishedCount) || data.publishedCount < 0
                || !Array.isArray(data.history) || data.history.length > 20
                || data.history.some((item: any) => !item || !['service', 'livery', 'depot', 'weather', 'wider', 'fallback'].includes(item.kind)
                    || typeof item.key !== 'string' || item.key.length > 1000
                    || typeof item.postHash !== 'string' || !/^[a-f0-9]{64}$/.test(item.postHash))) throw new Error('invalid subject history');
            this.publishedCount = data.publishedCount;
            this.history = data.history;
        } catch {
            logger.warn('Subject history could not be read; using an empty rotation');
        }
    }

    record(post: string, choice?: SubjectChoice, publicationId?: string): void {
        const postHash = createHash('sha256').update(publicationId || post).digest('hex');
        if (this.history.some(item => item.postHash === postHash)) return;
        this.publishedCount++;
        const entry: PublishedSubject = { kind: choice?.kind || 'fallback', key: choice?.key || '', postHash };
        this.history = [...this.history, entry].slice(-20);
        if (!this.file) return;
        const temporary = `${this.file}.new-${process.pid}`;
        try {
            mkdirSync(dirname(this.file), { recursive: true });
            writeFileSync(temporary, JSON.stringify({ version: 1, publishedCount: this.publishedCount, history: this.history }), { mode: 0o600 });
            renameSync(temporary, this.file);
        } catch {
            // The post has already succeeded. A ledger failure must never resubmit it.
            logger.warn('Could not save subject history; keeping rotation in memory');
        } finally {
            try { unlinkSync(temporary); } catch { /* Renamed, or could not be created. */ }
        }
    }
}
