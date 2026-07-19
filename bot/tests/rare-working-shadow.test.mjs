import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import Database from 'better-sqlite3';

import { RareWorkingShadowReader } from '../dist/ingest/rare-working-shadow-reader.js';


test('rare-working shadow handoff records each materialised event once', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'bbb-rare-shadow-'));
  const snapshot = path.join(dir, 'integration.json');
  const stateDb = path.join(dir, 'state.db');
  fs.writeFileSync(snapshot, JSON.stringify({
    schema: 1,
    published_at: '2026-07-17T08:00:00+00:00',
    rare_workings: {
      mode: 'shadow',
      events: [{
        event_id: 'event-1', service_date: '20260716', operator: 'FBRI',
        vehicle_ref: 'FBRI-123', route: 'X1', profile_slug: 'fbri-deadbeef',
      }],
    },
  }));

  const reader = new RareWorkingShadowReader(snapshot, stateDb);
  assert.equal(reader.pollOnce(), 1);
  assert.equal(reader.pollOnce(), 0);
  reader.stop();

  const db = new Database(stateDb, { readonly: true });
  assert.equal(db.prepare('SELECT COUNT(*) AS n FROM rare_working_shadow_seen').get().n, 1);
  db.close();
  fs.rmSync(dir, { recursive: true, force: true });
});
