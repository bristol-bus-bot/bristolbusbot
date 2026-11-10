import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { AtpAgent, type AtpSessionData, type AtpSessionEvent } from '@atproto/api';
import { logger } from '../utils/logging.js';

/**
 * One Bluesky session, kept across restarts.
 *
 * Bluesky allows 30 password logins (createSession) per 5 minutes and 300 a
 * day. The bot used to log in with its password before every post attempt.
 * Now it logs in once, saves the session, resumes it after a restart and lets
 * the library refresh the access token. A password login happens only when
 * there is no usable saved session, and each one is logged and counted.
 */
export interface BlueskyClientOptions {
    handle: string;
    appPassword: string;
    /** Where the session is saved. Unset: kept in memory only. */
    sessionPath?: string;
    service?: string;
    /** Called before every password login (for the daily counter). */
    onPasswordLogin?: () => void;
    /** Tests inject a stand-in agent; it receives the persist handler. */
    agentFactory?: (persist: (evt: AtpSessionEvent, session: AtpSessionData | undefined) => void) => AtpAgent;
}

const NETWORK_ERROR = /fetch failed|ECONNRESET|ETIMEDOUT|ENOTFOUND|EAI_AGAIN|socket hang up|network|timed? ?out/i;

export function isNetworkError(error: any): boolean {
    return NETWORK_ERROR.test(String(error?.message ?? ''))
        || ['ECONNRESET', 'ETIMEDOUT', 'ENOTFOUND', 'EAI_AGAIN'].includes(error?.code)
        || NETWORK_ERROR.test(String(error?.cause?.message ?? ''));
}

export class BlueskyClient {
    readonly agent: AtpAgent;
    private readonly options: BlueskyClientOptions;
    private pending: Promise<void> | null = null;

    constructor(options: BlueskyClientOptions) {
        this.options = options;
        const persist = (evt: AtpSessionEvent, session: AtpSessionData | undefined) =>
            this.persist(evt, session);
        this.agent = options.agentFactory
            ? options.agentFactory(persist)
            : new AtpAgent({ service: options.service || 'https://bsky.social', persistSession: persist });
    }

    get configured(): boolean {
        return Boolean(this.options.handle && this.options.appPassword);
    }

    /** Make sure the agent has a session: resume a saved one, else log in once. */
    async ensureSession(): Promise<void> {
        if (this.agent.session) return;
        // Concurrent callers (posting, metrics, feed) share one attempt.
        if (!this.pending) {
            this.pending = this.establish().finally(() => { this.pending = null; });
        }
        return this.pending;
    }

    private async establish(): Promise<void> {
        const saved = this.readSaved();
        if (saved) {
            try {
                await this.agent.resumeSession(saved);
                logger.info('[BLUESKY_SESSION] Resumed the saved session');
                return;
            } catch (error: any) {
                // A network failure says nothing about the saved session. Keep
                // it and let the caller retry rather than spend a login.
                if (isNetworkError(error)) throw error;
                logger.warn('[BLUESKY_SESSION] Saved session not accepted; logging in', { error: error?.message });
                this.removeSaved();
            }
        }
        this.options.onPasswordLogin?.();
        logger.info('[BLUESKY_LOGIN] Password login (createSession)');
        await this.agent.login({ identifier: this.options.handle, password: this.options.appPassword });
    }

    /**
     * A recent post of ours with exactly this text, created at or after
     * `notBefore`. Used before retrying a post whose reply was lost, so a post
     * that did reach Bluesky is not published twice.
     */
    async findRecentOwnPost(text: string, notBefore: Date): Promise<string | null> {
        await this.ensureSession();
        const actor = this.agent.session?.did || this.options.handle;
        const feed = await this.agent.getAuthorFeed({ actor, limit: 5 });
        for (const item of feed.data.feed || []) {
            const record: any = item.post?.record;
            if (!record || item.post.author?.did !== this.agent.session?.did) continue;
            const created = Date.parse(record.createdAt || '');
            if (record.text === text && Number.isFinite(created) && created >= notBefore.getTime() - 60_000) {
                return item.post.uri;
            }
        }
        return null;
    }

    private persist(evt: AtpSessionEvent, session: AtpSessionData | undefined): void {
        if ((evt === 'create' || evt === 'update') && session) {
            this.writeSaved(session);
        } else if (evt === 'expired' || evt === 'create-failed') {
            this.removeSaved();
        }
        // 'network-error' keeps the saved session for the next attempt.
    }

    private readSaved(): AtpSessionData | null {
        const file = this.options.sessionPath;
        if (!file || !existsSync(file)) return null;
        try {
            const data = JSON.parse(readFileSync(file, 'utf8'));
            if (typeof data?.did === 'string' && typeof data?.refreshJwt === 'string'
                && typeof data?.accessJwt === 'string') {
                return data as AtpSessionData;
            }
        } catch { /* unreadable: treated as absent */ }
        logger.warn('[BLUESKY_SESSION] Saved session file unreadable; ignoring it');
        return null;
    }

    /** Refresh tokens rotate and the old one stops working, so never half-write. */
    private writeSaved(session: AtpSessionData): void {
        const file = this.options.sessionPath;
        if (!file) return;
        try {
            mkdirSync(dirname(file), { recursive: true });
            const temporary = `${file}.new`;
            writeFileSync(temporary, JSON.stringify(session), { mode: 0o600 });
            renameSync(temporary, file);
        } catch (error: any) {
            logger.warn('[BLUESKY_SESSION] Could not save the session; it is kept in memory only', { error: error?.message });
        }
    }

    private removeSaved(): void {
        const file = this.options.sessionPath;
        if (!file) return;
        try { rmSync(file, { force: true }); } catch { /* best effort */ }
    }
}
