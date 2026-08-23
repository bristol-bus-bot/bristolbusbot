import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import Database from 'better-sqlite3';

import { EventReader } from '../dist/ingest/event-reader.js';


test('event reader reports progress after an empty database cycle', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'bbb-event-watchdog-'));
  const liveDb = path.join(dir, 'live.db');
  const db = new Database(liveDb);
  db.exec(`CREATE TABLE events (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    consumed_by_bot_at TEXT
  )`);
  db.close();

  let reports = 0;
  const reader = new EventReader(
    liveDb, {}, {}, ['FBRI'], 60_000, 10, () => { reports += 1; });
  reader.start();
  reader.stop();

  assert.equal(reports, 1);
  fs.rmSync(dir, { recursive: true, force: true });
});


test('event reader does not report progress after a failed database cycle', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'bbb-event-watchdog-fail-'));
  const liveDb = path.join(dir, 'live.db');
  new Database(liveDb).close();

  let reports = 0;
  const reader = new EventReader(
    liveDb, {}, {}, ['FBRI'], 60_000, 10, () => { reports += 1; });
  reader.start();
  reader.stop();

  assert.equal(reports, 0);
  fs.rmSync(dir, { recursive: true, force: true });
});
