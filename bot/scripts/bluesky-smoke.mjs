// Real Bluesky check for the session and posting code, against a TEST account.
//
//   npm run build && node scripts/bluesky-smoke.mjs <test-account-handle>
//
// It asks for the app password without showing it, logs in once, resumes the
// saved session in a second client (as a restart would), posts one short text
// with a link, confirms the duplicate check finds it, deletes it, and prints
// how many password logins it used (expected: 1). It refuses the live account.
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { RichText } from '@atproto/api';
import { BlueskyClient } from '../dist/services/bluesky-client.js';

const ENTER = new Set(['\r', '\n']);
const CTRL_C = '\u0003';
const BACKSPACE = new Set(['\u007f', '\b']);

/** Read a line from the terminal without echoing it or keeping it in history. */
function askHidden(question) {
    if (!process.stdin.isTTY) return Promise.resolve('');
    process.stdout.write(question);
    process.stdin.setRawMode(true);
    process.stdin.resume();
    process.stdin.setEncoding('utf8');
    return new Promise((resolve) => {
        let value = '';
        const onData = (chunk) => {
            for (const ch of chunk) {
                if (ENTER.has(ch)) {
                    process.stdin.setRawMode(false);
                    process.stdin.pause();
                    process.stdin.off('data', onData);
                    process.stdout.write('\n');
                    resolve(value);
                    return;
                }
                if (ch === CTRL_C) process.exit(130);
                if (BACKSPACE.has(ch)) value = value.slice(0, -1);
                else value += ch;
            }
        };
        process.stdin.on('data', onData);
    });
}

const handle = (process.env.BSKY_TEST_HANDLE || process.argv[2] || '').replace(/^@/, '');
if (!handle) {
    console.error('Usage: node scripts/bluesky-smoke.mjs <test-account-handle>');
    process.exit(2);
}
if (/bristolbusbot\.live$/i.test(handle)) {
    console.error('Refusing to run against the live bot account.');
    process.exit(2);
}
const appPassword = process.env.BSKY_TEST_APP_PASSWORD || await askHidden(`App password for ${handle}: `);
if (!appPassword) {
    console.error('No app password given.');
    process.exit(2);
}

const sessionPath = join(mkdtempSync(join(tmpdir(), 'bbb-bsky-smoke-')), 'bluesky-session.json');
let logins = 0;
const make = () => new BlueskyClient({ handle, appPassword, sessionPath, onPasswordLogin: () => { logins += 1; } });

const first = make();
await first.ensureSession();
console.log('1. logged in as', first.agent.session?.handle);

const second = make();            // a "restart"
await second.ensureSession();
console.log('2. resumed the saved session; password logins so far:', logins);

const started = new Date();
const text = `Bristol Bus Bot session test ${started.toISOString()} https://bristolbuses.live`;
const rich = new RichText({ text });
await rich.detectFacets(second.agent);
const { uri } = await second.agent.post({ text: rich.text, facets: rich.facets });
console.log('3. posted', uri, 'with', rich.facets?.length || 0, 'link facet(s)');

const found = await second.findRecentOwnPost(text, started);
console.log('4. duplicate check found the post:', found === uri);

await second.agent.deletePost(uri);
console.log('5. deleted the test post');
const ok = found === uri && logins === 1;
console.log(ok ? 'PASS: one password login, post found and deleted' : `CHECK: ${logins} password logins, found=${found === uri}`);
process.exit(ok ? 0 : 1);
