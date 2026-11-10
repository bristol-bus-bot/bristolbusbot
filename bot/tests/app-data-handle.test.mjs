// The bot uses one SQLite library and exactly one app_data.db handle (#104).
// Before, DatabaseManager (sqlite3) and the rare-working shadow reader
// (better-sqlite3) held separate handles, and a write through one while the
// other wrote failed at once and lost the engagement record.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import Database from 'better-sqlite3';
import { DatabaseManager } from '../dist/services/database-manager.js';
import { RareWorkingShadowReader } from '../dist/ingest/rare-working-shadow-reader.js';

function files() {
    const dir = mkdtempSync(join(tmpdir(), 'bbb-appdata-'));
    const timetablePath = join(dir, 'timetable.db');
    const timetable = new Database(timetablePath);
    timetable.exec(`CREATE TABLE stops(stop_id TEXT, stop_code TEXT, stop_name TEXT);
                    INSERT INTO stops VALUES ('1','A','Bristol Bus Station'),('2','B','Elm Road');`);
    timetable.close();
    return { dir, timetablePath, appDataPath: join(dir, 'app_data.db') };
}

test('engagement records round-trip through the one app_data.db handle', async () => {
    const { timetablePath, appDataPath } = files();
    const manager = new DatabaseManager({ timetablePath, appDataPath, maxConnections: 1 });
    await manager.initialize();
    try {
        await manager.storeEngagementRecord({
            postContent: 'The 48 is running late', postType: 'delay', significance: 3,
            postUri: 'at://did:plc:x/app.bsky.feed.post/abc',
            event: { vehicleRef: 'FBRI-1', line: '48', delayMinutes: 6, timestamp: '2026-11-10T09:00:00Z' },
        });
        const posts = await manager.getRecentPosts(5);
        assert.equal(posts.length, 1);
        assert.equal(posts[0].line, '48');
        assert.equal(posts[0].delaySeconds, 360);
        // The old direct-SIRI tables are gone.
        const tables = manager.getAppDataDb().prepare(
            "SELECT name FROM sqlite_master WHERE type='table'").all().map(r => r.name);
        assert.ok(tables.includes('engagement_analytics'));
        for (const gone of ['historical_delays', 'kalman_state', 'delay_reports']) {
            assert.equal(tables.includes(gone), false, gone);
        }
    } finally {
        await manager.close();
    }
});

test('the shadow reader shares the handle and leaves it open when it stops', async () => {
    const { dir, timetablePath, appDataPath } = files();
    const manager = new DatabaseManager({ timetablePath, appDataPath, maxConnections: 1 });
    await manager.initialize();
    try {
        const shared = manager.getAppDataDb();
        const reader = new RareWorkingShadowReader(join(dir, 'missing.json'), shared, 60_000);
        reader.stop();                    // e.g. ingest paused for Christmas
        assert.equal(shared.open, true);  // DatabaseManager can still write
        await manager.storeEngagementRecord({ postContent: 'x', postType: 'editorial', significance: 0 });
        assert.equal(shared.prepare('SELECT COUNT(*) AS n FROM engagement_analytics').get().n, 1);
        assert.equal(shared.prepare(
            "SELECT COUNT(*) AS n FROM sqlite_master WHERE name='rare_working_shadow_seen'").get().n, 1);
    } finally {
        await manager.close();
    }
});

test('the bot depends on one SQLite library', () => {
    const pkg = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
    assert.equal('sqlite3' in (pkg.dependencies || {}), false);
    assert.equal('@types/sqlite3' in (pkg.devDependencies || {}), false);
    assert.ok(pkg.dependencies['better-sqlite3']);
});

test('stop names are loaded once from the timetable, one per stop code (#114)', async () => {
    const { timetablePath, appDataPath } = files();
    const extra = new Database(timetablePath);
    extra.exec("INSERT INTO stops VALUES ('3','A','Bristol Bus Station Bay 2')");  // a second name for code A
    extra.close();
    const manager = new DatabaseManager({ timetablePath, appDataPath, maxConnections: 1 });
    await manager.initialize();
    try {
        await manager.loadStopNames();
        const { ApplicationState } = await import('../dist/services/application-state.js');
        const names = ApplicationState.getInstance().stopNamesByCode;
        assert.equal(names.get('B'), 'Elm Road');
        assert.equal(names.get('A'), 'Bristol Bus Station');   // lowest stop_id wins, every time
        assert.equal(names.size, 2);
    } finally {
        await manager.close();
    }
});
