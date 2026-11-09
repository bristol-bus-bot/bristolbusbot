// The bot keeps one Bluesky session and logs in with its password only when it
// has no usable saved session (planning tickets 99 and 100). No network: a
// stand-in agent records what the client asks of it.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, existsSync, writeFileSync, readdirSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { BlueskyClient, isNetworkError } from '../dist/services/bluesky-client.js';

const SESSION = { did: 'did:plc:test', handle: 'test.example', accessJwt: 'a1', refreshJwt: 'r1', active: true };

function fakeAgent(persist, behaviour = {}) {
    const calls = [];
    const agent = {
        session: undefined,
        calls,
        async login(opts) {
            calls.push(['login', opts.identifier]);
            if (behaviour.loginError) throw behaviour.loginError;
            agent.session = { ...SESSION };
            persist('create', agent.session);
        },
        async resumeSession(saved) {
            calls.push(['resume', saved.refreshJwt]);
            if (behaviour.resumeError) {
                if (!isNetworkError(behaviour.resumeError)) persist('expired', undefined);
                throw behaviour.resumeError;
            }
            agent.session = { ...saved, refreshJwt: 'r2' };
            persist('update', agent.session);
        },
        async getAuthorFeed() {
            calls.push(['feed']);
            return { data: { feed: behaviour.feed || [] } };
        },
    };
    return agent;
}

function client(dir, behaviour, counter = { logins: 0 }) {
    let agent;
    const made = new BlueskyClient({
        handle: 'test.example', appPassword: 'pw',
        sessionPath: join(dir, 'bluesky-session.json'),
        onPasswordLogin: () => { counter.logins += 1; },
        agentFactory: (persist) => (agent = fakeAgent(persist, behaviour)),
    });
    return { made, agent, counter };
}

const tempDir = () => mkdtempSync(join(tmpdir(), 'bbb-bsky-'));

test('first start logs in once and saves the session; later calls reuse it', async () => {
    const dir = tempDir();
    const { made, agent, counter } = client(dir);
    await Promise.all([made.ensureSession(), made.ensureSession(), made.ensureSession()]);
    await made.ensureSession();
    assert.deepEqual(agent.calls, [['login', 'test.example']]);
    assert.equal(counter.logins, 1);
    const saved = JSON.parse(readFileSync(join(dir, 'bluesky-session.json'), 'utf8'));
    assert.equal(saved.refreshJwt, 'r1');
    // Atomic write: no temporary file left behind.
    assert.deepEqual(readdirSync(dir), ['bluesky-session.json']);
});

test('a restart resumes the saved session without a password login', async () => {
    const dir = tempDir();
    writeFileSync(join(dir, 'bluesky-session.json'), JSON.stringify(SESSION));
    const { made, agent, counter } = client(dir);
    await made.ensureSession();
    assert.deepEqual(agent.calls, [['resume', 'r1']]);
    assert.equal(counter.logins, 0);
    // The rotated refresh token was saved for the next restart.
    assert.equal(JSON.parse(readFileSync(join(dir, 'bluesky-session.json'), 'utf8')).refreshJwt, 'r2');
});

test('a rejected saved session falls back to one password login', async () => {
    const dir = tempDir();
    writeFileSync(join(dir, 'bluesky-session.json'), JSON.stringify(SESSION));
    const { made, agent, counter } = client(dir, { resumeError: new Error('ExpiredToken') });
    await made.ensureSession();
    assert.deepEqual(agent.calls, [['resume', 'r1'], ['login', 'test.example']]);
    assert.equal(counter.logins, 1);
});

test('a network failure while resuming keeps the saved session and spends no login', async () => {
    const dir = tempDir();
    writeFileSync(join(dir, 'bluesky-session.json'), JSON.stringify(SESSION));
    const { made, agent, counter } = client(dir, { resumeError: new Error('fetch failed') });
    await assert.rejects(made.ensureSession(), /fetch failed/);
    assert.deepEqual(agent.calls, [['resume', 'r1']]);
    assert.equal(counter.logins, 0);
    assert.ok(existsSync(join(dir, 'bluesky-session.json')));
});

test('an unreadable session file is ignored, not trusted', async () => {
    const dir = tempDir();
    writeFileSync(join(dir, 'bluesky-session.json'), '{half a file');
    const { made, agent } = client(dir);
    await made.ensureSession();
    assert.deepEqual(agent.calls, [['login', 'test.example']]);
});

test('a post that reached Bluesky before its reply was lost is found, not repeated', async () => {
    const dir = tempDir();
    const startedAt = new Date('2026-11-10T09:00:00Z');
    const mine = (text, createdAt, uri) => ({ post: { uri, author: { did: SESSION.did }, record: { text, createdAt } } });
    const feed = [
        mine('Other post', '2026-11-10T09:00:05Z', 'at://x/other'),
        mine('The 48 is late', '2026-11-10T09:00:03Z', 'at://x/match'),
        mine('The 48 is late', '2026-11-09T09:00:00Z', 'at://x/yesterday'),
    ];
    const { made } = client(dir, { feed });
    assert.equal(await made.findRecentOwnPost('The 48 is late', startedAt), 'at://x/match');
    assert.equal(await made.findRecentOwnPost('Never posted', startedAt), null);
});

test('the posting code never calls login itself', () => {
    const source = readFileSync(new URL('../src/services/social-media.ts', import.meta.url), 'utf8');
    assert.equal(/\.login\(/.test(source), false);
});

test('network errors are told apart from authentication errors', () => {
    assert.equal(isNetworkError(new Error('fetch failed')), true);
    assert.equal(isNetworkError({ message: 'x', code: 'ECONNRESET' }), true);
    assert.equal(isNetworkError(new Error('Invalid identifier or password')), false);
});
