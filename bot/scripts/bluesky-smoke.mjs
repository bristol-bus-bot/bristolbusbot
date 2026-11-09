// Real Bluesky check for the session and posting code, against a TEST account.
//
//   BSKY_TEST_HANDLE=... BSKY_TEST_APP_PASSWORD=... npm run build && node scripts/bluesky-smoke.mjs
//
// It logs in once, resumes the saved session in a second client (as a restart
// would), posts one short text with a link, confirms the duplicate check finds
// it, deletes it, and prints how many password logins it used (expected: 1).
// It refuses to run against the live bot account.
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { RichText } from '@atproto/api';
import { BlueskyClient } from '../dist/services/bluesky-client.js';

const handle = process.env.BSKY_TEST_HANDLE || '';
const appPassword = process.env.BSKY_TEST_APP_PASSWORD || '';
if (!handle || !appPassword) {
    console.error('Set BSKY_TEST_HANDLE and BSKY_TEST_APP_PASSWORD for a test account.');
    process.exit(2);
}
if (/bristolbusbot\.live$/i.test(handle)) {
    console.error('Refusing to run against the live bot account.');
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
console.log('2. resumed saved session; password logins so far:', logins);

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
console.log(logins === 1 ? 'PASS: one password login' : `CHECK: ${logins} password logins`);
process.exit(found === uri && logins === 1 ? 0 : 1);
