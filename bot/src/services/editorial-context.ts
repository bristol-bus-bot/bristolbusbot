import { createHash } from 'crypto';
import {
    existsSync,
    mkdirSync,
    readFileSync,
    renameSync,
    unlinkSync,
    writeFileSync,
} from 'fs';
import { dirname } from 'path';
import { DateTime } from 'luxon';
import { logger } from '../utils/logging.js';

const ID_RE = /^[a-z0-9][a-z0-9-]{1,79}$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const TIMESTAMP_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/;
const MAX_CONTEXT_BYTES = 256 * 1024;
const ALLOWED_SOURCE_HOSTS = [
    'bristolmuseums.org.uk',
    'bususers.org',
    'firstbus.co.uk',
    'firstgroupplc.com',
    'gov.uk',
    'legislation.gov.uk',
    'mobilityweek.eu',
    'tfl.gov.uk',
    'un.org',
];

export interface EditorialSource {
    publisher: string;
    title: string;
    url: string;
    published_on?: string;
    verified_on: string;
}

export interface EditorialFact {
    id: string;
    claim: string;
    prompt_hint: string;
    active_from: string;
    active_until: string;
    source: EditorialSource;
}

export interface EditorialOccasion {
    id: string;
    label: string;
    prompt_hint: string;
    schedule:
        | { kind: 'annual_date'; month: number; day: number }
        | { kind: 'date_range'; start: string; end: string };
    probability: number;
    max_uses_per_day: number;
    source: EditorialSource;
}

export interface EditorialNews {
    id: string;
    label: string;
    claim: string;
    prompt_hint: string;
    published_at: string;
    active_from: string;
    expires_at: string;
    probability: number;
    max_uses_total: number;
    cooldown_hours: number;
    append_source_link: boolean;
    source: EditorialSource;
}

export interface EditorialDocument {
    schema_version: 1;
    updated_at: string;
    facts: EditorialFact[];
    occasions: EditorialOccasion[];
    news: EditorialNews[];
}

export interface EditorialSelection {
    kind: 'fact' | 'occasion' | 'news';
    id: string;
    label: string;
    claim?: string;
    promptHint: string;
    sourceUrl: string;
    appendSourceLink: boolean;
}

interface UsageRecord {
    uses: number;
    last_used_at: string;
    last_used_on: string;
}

interface UsageState {
    schema_version: 1;
    last_post_was_special: boolean;
    items: Record<string, UsageRecord>;
}

export interface EditorialStatus {
    loaded: boolean;
    path: string;
    sha256: string | null;
    updated_at: string | null;
    counts: { facts: number; occasions: number; news: number };
    error?: string;
}

function requireObject(value: unknown, name: string): Record<string, unknown> {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        throw new Error(`${name} must be an object`);
    }
    return value as Record<string, unknown>;
}

function requireString(value: unknown, name: string, maximum = 1000): string {
    if (typeof value !== 'string' || !value.trim() || value.length > maximum) {
        throw new Error(`${name} must be a non-empty string no longer than ${maximum} characters`);
    }
    if (/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(value)) {
        throw new Error(`${name} contains control characters`);
    }
    return value;
}

function requireDate(value: unknown, name: string): string {
    const date = requireString(value, name, 10);
    if (!DATE_RE.test(date) || !DateTime.fromISO(date, { zone: 'utc' }).isValid) {
        throw new Error(`${name} must be an ISO date`);
    }
    return date;
}

function requireTimestamp(value: unknown, name: string): string {
    const timestamp = requireString(value, name, 40);
    if (!TIMESTAMP_RE.test(timestamp) || !DateTime.fromISO(timestamp, { zone: 'utc' }).isValid) {
        throw new Error(`${name} must be a UTC ISO timestamp`);
    }
    return timestamp;
}

function requireNumber(value: unknown, name: string, minimum: number, maximum: number): number {
    if (typeof value !== 'number' || !Number.isFinite(value) || value < minimum || value > maximum) {
        throw new Error(`${name} must be between ${minimum} and ${maximum}`);
    }
    return value;
}

function validateSource(value: unknown, name: string): EditorialSource {
    const source = requireObject(value, name);
    const url = requireString(source.url, `${name}.url`, 500);
    let parsed: URL;
    try {
        parsed = new URL(url);
    } catch {
        throw new Error(`${name}.url is invalid`);
    }
    const host = parsed.hostname.toLowerCase();
    if (parsed.protocol !== 'https:' || !ALLOWED_SOURCE_HOSTS.some(
        allowed => host === allowed || host.endsWith(`.${allowed}`)
    )) {
        throw new Error(`${name}.url is not an allowlisted HTTPS source`);
    }
    const result: EditorialSource = {
        publisher: requireString(source.publisher, `${name}.publisher`, 100),
        title: requireString(source.title, `${name}.title`, 200),
        url,
        verified_on: requireDate(source.verified_on, `${name}.verified_on`),
    };
    if (source.published_on !== undefined) {
        result.published_on = requireDate(source.published_on, `${name}.published_on`);
    }
    return result;
}

function validateId(value: unknown, name: string, ids: Set<string>): string {
    const id = requireString(value, name, 80);
    if (!ID_RE.test(id)) throw new Error(`${name} is not a safe identifier`);
    if (ids.has(id)) throw new Error(`duplicate editorial id: ${id}`);
    ids.add(id);
    return id;
}

function validateProbability(value: unknown, name: string): number {
    return requireNumber(value, name, 0, 1);
}

export function validateEditorialDocument(value: unknown): EditorialDocument {
    const root = requireObject(value, 'editorial context');
    if (root.schema_version !== 1) throw new Error('unsupported editorial schema_version');
    const updatedAt = requireTimestamp(root.updated_at, 'updated_at');
    const factsRaw = root.facts;
    const occasionsRaw = root.occasions;
    const newsRaw = root.news;
    if (!Array.isArray(factsRaw) || factsRaw.length > 100) throw new Error('facts must be an array of at most 100 items');
    if (!Array.isArray(occasionsRaw) || occasionsRaw.length > 100) throw new Error('occasions must be an array of at most 100 items');
    if (!Array.isArray(newsRaw) || newsRaw.length > 50) throw new Error('news must be an array of at most 50 items');

    const serialized = JSON.stringify(value);
    if (/bee network/i.test(serialized)) {
        throw new Error('Bee Network claims are intentionally prohibited');
    }
    const ids = new Set<string>();
    const facts = factsRaw.map((item, index): EditorialFact => {
        const fact = requireObject(item, `facts[${index}]`);
        const activeFrom = requireDate(fact.active_from, `facts[${index}].active_from`);
        const activeUntil = requireDate(fact.active_until, `facts[${index}].active_until`);
        if (activeUntil < activeFrom) throw new Error(`facts[${index}] has an inverted active window`);
        return {
            id: validateId(fact.id, `facts[${index}].id`, ids),
            claim: requireString(fact.claim, `facts[${index}].claim`, 600),
            prompt_hint: requireString(fact.prompt_hint, `facts[${index}].prompt_hint`, 700),
            active_from: activeFrom,
            active_until: activeUntil,
            source: validateSource(fact.source, `facts[${index}].source`),
        };
    });

    const occasions = occasionsRaw.map((item, index): EditorialOccasion => {
        const occasion = requireObject(item, `occasions[${index}]`);
        const schedule = requireObject(occasion.schedule, `occasions[${index}].schedule`);
        let parsedSchedule: EditorialOccasion['schedule'];
        if (schedule.kind === 'annual_date') {
            const month = requireNumber(schedule.month, `occasions[${index}].schedule.month`, 1, 12);
            const day = requireNumber(schedule.day, `occasions[${index}].schedule.day`, 1, 31);
            if (!Number.isInteger(month) || !Number.isInteger(day)
                || !DateTime.fromObject({ year: 2024, month, day }, { zone: 'utc' }).isValid) {
                throw new Error(`occasions[${index}] has an invalid annual date`);
            }
            parsedSchedule = { kind: 'annual_date', month, day };
        } else if (schedule.kind === 'date_range') {
            const start = requireDate(schedule.start, `occasions[${index}].schedule.start`);
            const end = requireDate(schedule.end, `occasions[${index}].schedule.end`);
            if (end < start) throw new Error(`occasions[${index}] has an inverted date range`);
            parsedSchedule = { kind: 'date_range', start, end };
        } else {
            throw new Error(`occasions[${index}].schedule.kind is unsupported`);
        }
        const maxUses = requireNumber(
            occasion.max_uses_per_day, `occasions[${index}].max_uses_per_day`, 1, 5
        );
        if (!Number.isInteger(maxUses)) throw new Error(`occasions[${index}].max_uses_per_day must be an integer`);
        return {
            id: validateId(occasion.id, `occasions[${index}].id`, ids),
            label: requireString(occasion.label, `occasions[${index}].label`, 120),
            prompt_hint: requireString(occasion.prompt_hint, `occasions[${index}].prompt_hint`, 700),
            schedule: parsedSchedule,
            probability: validateProbability(occasion.probability, `occasions[${index}].probability`),
            max_uses_per_day: maxUses,
            source: validateSource(occasion.source, `occasions[${index}].source`),
        };
    });

    const news = newsRaw.map((item, index): EditorialNews => {
        const story = requireObject(item, `news[${index}]`);
        const publishedAt = requireTimestamp(story.published_at, `news[${index}].published_at`);
        const activeFrom = requireTimestamp(story.active_from, `news[${index}].active_from`);
        const expiresAt = requireTimestamp(story.expires_at, `news[${index}].expires_at`);
        if (expiresAt <= activeFrom || publishedAt > expiresAt) {
            throw new Error(`news[${index}] has an invalid active window`);
        }
        const maxUses = requireNumber(story.max_uses_total, `news[${index}].max_uses_total`, 1, 10);
        if (!Number.isInteger(maxUses)) throw new Error(`news[${index}].max_uses_total must be an integer`);
        if (typeof story.append_source_link !== 'boolean') {
            throw new Error(`news[${index}].append_source_link must be boolean`);
        }
        const source = validateSource(story.source, `news[${index}].source`);
        if (story.append_source_link && source.url.length > 160) {
            throw new Error(`news[${index}].source.url is too long to append safely`);
        }
        return {
            id: validateId(story.id, `news[${index}].id`, ids),
            label: requireString(story.label, `news[${index}].label`, 120),
            claim: requireString(story.claim, `news[${index}].claim`, 800),
            prompt_hint: requireString(story.prompt_hint, `news[${index}].prompt_hint`, 800),
            published_at: publishedAt,
            active_from: activeFrom,
            expires_at: expiresAt,
            probability: validateProbability(story.probability, `news[${index}].probability`),
            max_uses_total: maxUses,
            cooldown_hours: requireNumber(story.cooldown_hours, `news[${index}].cooldown_hours`, 1, 720),
            append_source_link: story.append_source_link,
            source,
        };
    });
    return { schema_version: 1, updated_at: updatedAt, facts, occasions, news };
}

function emptyUsage(): UsageState {
    return { schema_version: 1, last_post_was_special: false, items: {} };
}

function loadUsage(path: string): UsageState {
    try {
        const value = JSON.parse(readFileSync(path, 'utf8')) as Partial<UsageState>;
        if (value.schema_version !== 1 || typeof value.last_post_was_special !== 'boolean'
            || !value.items || typeof value.items !== 'object' || Array.isArray(value.items)) {
            throw new Error('unsupported usage state');
        }
        return value as UsageState;
    } catch (error: any) {
        if (existsSync(path)) logger.warn('Editorial usage state was ignored', { error: error.message });
        return emptyUsage();
    }
}

function atomicWrite(path: string, payload: object): void {
    mkdirSync(dirname(path), { recursive: true });
    const temporary = `${path}.new-${process.pid}`;
    try {
        writeFileSync(temporary, `${JSON.stringify(payload, null, 2)}\n`, {
            encoding: 'utf8',
            mode: 0o600,
            flag: 'wx',
        });
        renameSync(temporary, path);
    } finally {
        try {
            unlinkSync(temporary);
        } catch {
            // The rename normally removed it.
        }
    }
}

function activeOnDate(start: string, end: string, today: string): boolean {
    return start <= today && today <= end;
}

function chooseOne<T>(items: T[], random: () => number): T | null {
    if (items.length === 0) return null;
    return items[Math.min(items.length - 1, Math.floor(random() * items.length))];
}

export class EditorialContextStore {
    private document: EditorialDocument = {
        schema_version: 1,
        updated_at: '1970-01-01T00:00:00Z',
        facts: [],
        occasions: [],
        news: [],
    };
    private usage: UsageState;
    private status: EditorialStatus;

    constructor(
        private readonly contextPath: string,
        private readonly usagePath: string,
        private readonly random: () => number = Math.random,
    ) {
        this.usage = loadUsage(usagePath);
        this.status = {
            loaded: false,
            path: contextPath,
            sha256: null,
            updated_at: null,
            counts: { facts: 0, occasions: 0, news: 0 },
        };
        this.reload();
    }

    reload(): void {
        try {
            const raw = readFileSync(this.contextPath);
            if (raw.length === 0 || raw.length > MAX_CONTEXT_BYTES) {
                throw new Error(`file size must be between 1 and ${MAX_CONTEXT_BYTES} bytes`);
            }
            const parsed = validateEditorialDocument(JSON.parse(raw.toString('utf8')));
            this.document = parsed;
            this.status = {
                loaded: true,
                path: this.contextPath,
                sha256: createHash('sha256').update(raw).digest('hex'),
                updated_at: parsed.updated_at,
                counts: {
                    facts: parsed.facts.length,
                    occasions: parsed.occasions.length,
                    news: parsed.news.length,
                },
            };
            logger.info('Loaded approved editorial context', this.status);
        } catch (error: any) {
            this.document = {
                schema_version: 1,
                updated_at: '1970-01-01T00:00:00Z',
                facts: [],
                occasions: [],
                news: [],
            };
            this.status = {
                loaded: false,
                path: this.contextPath,
                sha256: null,
                updated_at: null,
                counts: { facts: 0, occasions: 0, news: 0 },
                error: error.message,
            };
            logger.warn('Approved editorial context is unavailable; special posts are disabled', this.status);
        }
    }

    getStatus(): EditorialStatus {
        return { ...this.status, counts: { ...this.status.counts } };
    }

    select(now: DateTime, recentPosts: string[]): EditorialSelection | null {
        if (!this.status.loaded || this.usage.last_post_was_special) return null;
        const today = now.toISODate();
        const nowIso = now.toUTC().toISO();
        if (!today || !nowIso) return null;

        const exactOccasions = this.document.occasions.filter(item =>
            item.schedule.kind === 'annual_date'
            && item.schedule.month === now.month
            && item.schedule.day === now.day
            && this.canUseOccasion(item, today)
        );
        const rangedOccasions = this.document.occasions.filter(item =>
            item.schedule.kind === 'date_range'
            && activeOnDate(item.schedule.start, item.schedule.end, today)
            && this.canUseOccasion(item, today)
        );
        const occasion = chooseOne(exactOccasions, this.random)
            || chooseOne(rangedOccasions, this.random);
        if (occasion && this.random() < occasion.probability) {
            return {
                kind: 'occasion',
                id: occasion.id,
                label: occasion.label,
                promptHint: occasion.prompt_hint,
                sourceUrl: occasion.source.url,
                appendSourceLink: false,
            };
        }

        const eligibleNews = this.document.news.filter(item => {
            const usage = this.usage.items[item.id];
            const active = item.active_from <= nowIso && nowIso <= item.expires_at;
            const belowLimit = !usage || usage.uses < item.max_uses_total;
            const cooledDown = !usage || now.toMillis() - DateTime.fromISO(usage.last_used_at).toMillis()
                >= item.cooldown_hours * 60 * 60 * 1000;
            return active && belowLimit && cooledDown;
        });
        const story = chooseOne(eligibleNews, this.random);
        if (story && this.random() < story.probability) {
            return {
                kind: 'news',
                id: story.id,
                label: story.label,
                claim: story.claim,
                promptHint: story.prompt_hint,
                sourceUrl: story.source.url,
                appendSourceLink: story.append_source_link,
            };
        }

        const recentFinancial = recentPosts.slice(-1).some(post =>
            /shareholder|dividend|£\d+(?:\.\d+)?\s*(?:m|million|bn|billion)|CEO|bonus|profit/i.test(post)
        );
        if (recentFinancial || this.random() >= 0.20) return null;
        const facts = this.document.facts.filter(item => {
            if (!activeOnDate(item.active_from, item.active_until, today)) return false;
            const usage = this.usage.items[item.id];
            return !usage || now.toMillis() - DateTime.fromISO(usage.last_used_at).toMillis()
                >= 72 * 60 * 60 * 1000;
        });
        const fact = chooseOne(facts, this.random);
        if (!fact) return null;
        return {
            kind: 'fact',
            id: fact.id,
            label: 'sourced fact',
            claim: fact.claim,
            promptHint: fact.prompt_hint,
            sourceUrl: fact.source.url,
            appendSourceLink: false,
        };
    }

    private canUseOccasion(item: EditorialOccasion, today: string): boolean {
        const usage = this.usage.items[item.id];
        return !usage || usage.last_used_on !== today || usage.uses < item.max_uses_per_day;
    }

    recordPost(selection: EditorialSelection | null, now: DateTime): void {
        this.usage.last_post_was_special = selection !== null;
        if (selection) {
            const usedAt = now.toUTC().toISO();
            const usedOn = now.toISODate();
            if (usedAt && usedOn) {
                const previous = this.usage.items[selection.id];
                this.usage.items[selection.id] = {
                    uses: (previous?.uses || 0) + 1,
                    last_used_at: usedAt,
                    last_used_on: usedOn,
                };
            }
        }
        try {
            atomicWrite(this.usagePath, this.usage);
        } catch (error: any) {
            logger.warn('Could not persist editorial usage state', { error: error.message });
        }
    }
}
