import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { logger } from '../utils/logging.js';
import type { SubjectChoice } from './story-subject.js';

export const PROMPT_VERSION = '2026-09-context-2';
export interface Publication {
    uri: string; publishedAt: string; text: string; subject: string; key: string;
    factIds: string[]; fallback: boolean; fallbackReason?: string; promptVersion: string;
    metrics?: { checkedAt: string; ageHours: number; likes: number; reposts: number };
}

/** Confirmed publications only. Bounded, private, and never a reason to resend a post. */
export class PublicationLedger {
    records: Publication[] = [];
    constructor(private readonly file?: string) {
        if (!file || !existsSync(file)) return;
        try {
            const raw = readFileSync(file, 'utf8');
            if (raw.length > 8_000_000) throw new Error('oversized ledger');
            const data = JSON.parse(raw);
            if (data.version !== 1 || !Array.isArray(data.records) || data.records.length > 10_000
                || data.records.some((r: Publication) => !r || typeof r.uri !== 'string'
                    || !Number.isFinite(Date.parse(r.publishedAt)) || typeof r.text !== 'string'
                    || r.text.length > 1000 || !Array.isArray(r.factIds)
                    || typeof r.fallback !== 'boolean' || typeof r.subject !== 'string')) throw new Error('invalid ledger');
            this.records = data.records;
        } catch { logger.warn('Publication ledger unreadable; starting an empty ledger'); }
    }
    record(text: string, uri: string | undefined, subject?: SubjectChoice, factIds: string[] = [], fallbackReason?: string, now = Date.now()): void {
        if (!uri || this.records.some(r => r.uri === uri)) return;
        this.records = this.records.filter(r => now - Date.parse(r.publishedAt) <= 90 * 86400_000);
        this.records.push({ uri, text, publishedAt: new Date(now).toISOString(), subject: subject?.kind || 'fallback',
            key: subject?.key || '', factIds, fallback: !subject, fallbackReason, promptVersion: PROMPT_VERSION });
        this.records = this.records.slice(-10_000);
        this.save();
    }
    recentTexts(limit = 60): string[] { return this.records.slice(-limit).reverse().map(r => r.text); }
    /** Freeze one comparable 24–26-hour sample, never compare a week-old post with a new one. */
    async collectMetrics(fetchPosts: (uris: string[]) => Promise<Array<{ uri: string; likeCount?: number; repostCount?: number }>>, now = Date.now()): Promise<void> {
        const due = this.records.filter(r => !r.metrics && now - Date.parse(r.publishedAt) >= 24 * 3600_000
            && now - Date.parse(r.publishedAt) <= 26 * 3600_000);
        for (let i = 0; i < due.length; i += 25) {
            const batch = due.slice(i, i + 25);
            const posts = await fetchPosts(batch.map(r => r.uri));
            for (const r of batch) {
                const p = posts.find(p => p.uri === r.uri);
                if (!p || !Number.isSafeInteger(p.likeCount) || p.likeCount! < 0
                    || !Number.isSafeInteger(p.repostCount) || p.repostCount! < 0) continue;
                r.metrics = { checkedAt: new Date(now).toISOString(), ageHours: (now - Date.parse(r.publishedAt)) / 3600_000,
                    likes: p.likeCount!, reposts: p.repostCount! };
            }
            this.save();
        }
        this.saveReport(now);
    }
    report(now = Date.now()): object {
        const week = this.records.filter(r => Date.parse(r.publishedAt) >= now - 7 * 86400_000 && Date.parse(r.publishedAt) <= now);
        const groups: Record<string, { posts: number; sampled: number; likes: number; reposts: number }> = {};
        for (const r of week) {
            const key = `${r.promptVersion}/${r.subject}`;
            const g = groups[key] ||= { posts: 0, sampled: 0, likes: 0, reposts: 0 };
            g.posts++;
            if (r.metrics && r.metrics.ageHours >= 24 && r.metrics.ageHours <= 26) {
                g.sampled++; g.likes += r.metrics.likes; g.reposts += r.metrics.reposts;
            }
        }
        return { generatedAt: new Date(now).toISOString(), windowDays: 7, posts: week.length,
            fallbacks: week.filter(r => r.fallback).length, fallbackShare: week.length ? week.filter(r => r.fallback).length / week.length : null,
            sampleWindow: '24–26 hours after publication; missing samples excluded, never treated as zero', groups,
            caveat: 'Descriptive counts, not proof that wording caused engagement. No liker identities stored.' };
    }
    private saveReport(now: number): void { if (this.file) this.write(`${this.file}.report.json`, this.report(now)); }
    private save(): void { if (this.file) this.write(this.file, { version: 1, records: this.records }); }
    private write(file: string, data: unknown): void {
        try { mkdirSync(dirname(file), { recursive: true }); writeFileSync(`${file}.new`, JSON.stringify(data), { mode: 0o600 }); renameSync(`${file}.new`, file); }
        catch { logger.warn('Publication ledger write failed; publication will not be retried'); }
    }
}
