// The bot reads live.db read-only and keeps its own position (#101).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import Database from 'better-sqlite3';
import { EventReader } from '../dist/ingest/event-reader.js';

const EVENTS = `CREATE TABLE events (
    id INTEGER PRIMARY KEY, created_at TEXT NOT NULL,
    vehicle_ref TEXT, operator_ref TEXT, line TEXT, direction TEXT,
    journey_ref TEXT, origin_aimed_departure TEXT, stop_code TEXT, stop_name TEXT,
    delay_seconds INTEGER NOT NULL, event_type TEXT NOT NULL, source TEXT NOT NULL,
    corroboration INTEGER NOT NULL, lat REAL, lon REAL, block_ref TEXT,
    low_confidence INTEGER, consumed_by_bot_at TEXT)`;

function setup() {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'bbb-cursor-'));
    const liveDb = path.join(dir, 'live.db');
    const writer = new Database(liveDb);
    writer.pragma('journal_mode = WAL');
    writer.exec(EVENTS);
    return { dir, liveDb, writer, cursorPath: path.join(dir, 'event-cursor.json') };
}

function addEvent(writer, minutesAgo = 0, line = '48') {
    const created = new Date(Date.now() - minutesAgo * 60_000).toISOString();
    return writer.prepare(`INSERT INTO events (created_at, vehicle_ref, operator_ref, line, direction,
        journey_ref, stop_code, stop_name, delay_seconds, event_type, source, corroboration)
        VALUES (?, 'FBRI-1', 'FBRI', ?, 'outbound', 'J1', 'S1', 'Elm Road', 0, 'punctual', 'timing_point', 2)`)
        .run(created, line).lastInsertRowid;
}

function reader(ctx, collected) {
    const appState = { addBusEvents: events => collected.push(...events), busEventCollector: collected };
    const analyzer = {
        calculateEventSignificance: () => ({ type: 'punctual', score: 1 }),
        extractBusDetails: () => null,
        updateDelayHistory: () => ({}),
        shouldReportDelay: () => true,
    };
    return new EventReader(ctx.liveDb, appState, analyzer, ['FBRI'], 60_000, 10, () => {}, ctx.cursorPath);
}

const cursor = ctx => JSON.parse(fs.readFileSync(ctx.cursorPath, 'utf8'));

test('the reader never writes to live.db', () => {
    const ctx = setup();
    addEvent(ctx.writer);
    const r = reader(ctx, []);
    assert.throws(() => r['db'].prepare("UPDATE events SET consumed_by_bot_at = 'x'").run(), /readonly/i);
    r.stop();
    assert.equal(ctx.writer.prepare('SELECT COUNT(*) AS n FROM events WHERE consumed_by_bot_at IS NOT NULL').get().n, 0);
});

test('first start begins after the newest event, then reads only new ones', () => {
    const ctx = setup();
    const old = addEvent(ctx.writer);
    const collected = [];
    const r = reader(ctx, collected);
    r.start();                         // first cycle: position = newest, nothing replayed
    assert.equal(collected.length, 0);
    assert.equal(cursor(ctx).lastEventId, Number(old));
    const fresh = addEvent(ctx.writer);
    r['cycle']();
    assert.equal(collected.length, 1);
    assert.equal(cursor(ctx).lastEventId, Number(fresh));
    r.stop();
});

test('a restart carries on from the saved position without repeating', () => {
    const ctx = setup();
    addEvent(ctx.writer);
    const first = [];
    const r1 = reader(ctx, first);
    r1.start();
    addEvent(ctx.writer);
    r1['cycle']();
    r1.stop();
    assert.equal(first.length, 1);

    addEvent(ctx.writer);              // arrives while the bot restarts
    const second = [];
    const r2 = reader(ctx, second);
    r2.start();
    assert.equal(second.length, 1);    // only the new one
    r2.stop();
});

test('stale events are skipped but still move the position on', () => {
    const ctx = setup();
    const r = reader(ctx, []);
    r.start();
    const stale = addEvent(ctx.writer, 30);
    const collected = [];
    r['appState'] = { addBusEvents: e => collected.push(...e), busEventCollector: collected };
    r['cycle']();
    assert.equal(collected.length, 0);
    assert.equal(cursor(ctx).lastEventId, Number(stale));
    r.stop();
});

test('when the events table is emptied and ids restart, the next event is still picked up', () => {
    const ctx = setup();
    for (let i = 0; i < 5; i += 1) addEvent(ctx.writer);
    const collected = [];
    const r = reader(ctx, collected);
    r.start();                          // position = event 5
    assert.equal(cursor(ctx).lastEventId, 5);

    // A live.db rebuild or a week-long outage: the table is emptied and
    // SQLite hands out ids from 1 again.
    ctx.writer.exec('DROP TABLE events');
    ctx.writer.exec(EVENTS);
    const reborn = addEvent(ctx.writer);
    assert.equal(Number(reborn), 1);

    r['cycle']();
    assert.equal(collected.length, 1, 'the new event 1 must not be hidden behind the old position 5');
    assert.equal(cursor(ctx).lastEventId, 1);
    r.stop();
});
