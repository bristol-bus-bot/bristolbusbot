// The shared HTTP client keeps its Pi limits after moving from node-fetch and
// agentkeepalive to undici (#105). Local server only; no internet.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { readFileSync } from 'node:fs';

process.env.HTTP_MAX_SOCKETS = '2';
process.env.HTTP_MAX_CONCURRENT = '4';
const { httpFetch, getHttpClientStats } = await import('../dist/utils/http-client.js');

function server(handler) {
    return new Promise(resolve => {
        const s = http.createServer(handler);
        s.listen(0, '127.0.0.1', () => resolve(s));
    });
}

test('a request works and returns a standard Response', async () => {
    const s = await server((req, res) => { res.setHeader('content-type', 'application/json'); res.end('{"ok":true}'); });
    try {
        const res = await httpFetch(`http://127.0.0.1:${s.address().port}/`, { retries: 0 });
        assert.equal(res.status, 200);
        assert.deepEqual(await res.json(), { ok: true });
    } finally { s.close(); }
});

test('a slow reply becomes an ETIMEDOUT error', async () => {
    const s = await server(() => { /* never answers */ });
    try {
        await assert.rejects(
            httpFetch(`http://127.0.0.1:${s.address().port}/`, { timeoutMs: 200, retries: 0 }),
            err => err.code === 'ETIMEDOUT');
    } finally { s.closeAllConnections?.(); s.close(); }
});

test('no more than HTTP_MAX_SOCKETS connections are open to one host', async () => {
    const sockets = new Set();
    let open = 0, peak = 0;
    const s = await server((req, res) => setTimeout(() => res.end('x'), 100));
    s.on('connection', socket => {
        sockets.add(socket); open += 1; peak = Math.max(peak, open);
        socket.on('close', () => { open -= 1; });
    });
    try {
        const url = `http://127.0.0.1:${s.address().port}/`;
        await Promise.all(Array.from({ length: 4 }, () => httpFetch(url, { retries: 0 }).then(r => r.text())));
        assert.ok(peak <= 2, `peak connections ${peak}`);
        assert.equal(getHttpClientStats().maxSockets, 2);
    } finally { s.closeAllConnections?.(); s.close(); }
});

test('the replaced packages are gone', () => {
    const pkg = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
    for (const name of ['node-fetch', 'agentkeepalive', 'dotenv']) {
        assert.equal(name in pkg.dependencies, false, name);
    }
    assert.equal('@types/better-sqlite3' in pkg.dependencies, false);
    assert.match(pkg.devDependencies['@types/node'], /^\^24/);
});
