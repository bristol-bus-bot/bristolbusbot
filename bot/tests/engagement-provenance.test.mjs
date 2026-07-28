import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import Database from 'better-sqlite3';
import { DatabaseManager } from '../dist/services/database-manager.js';

test('old engagement database migrates and stores exact journey provenance', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'bbb-provenance-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const timetablePath = path.join(directory, 'timetable.db');
  const appDataPath = path.join(directory, 'app_data.db');

  new Database(timetablePath).close();
  const legacy = new Database(appDataPath);
  legacy.exec(`CREATE TABLE engagement_analytics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_content TEXT, post_type TEXT, significance_score INTEGER,
    timestamp TEXT, vehicle_ref TEXT, post_uri TEXT
  )`);
  legacy.close();

  const manager = new DatabaseManager({
    timetablePath, appDataPath, maxConnections: 1,
  });
  await manager.initialize();
  await manager.storeEngagementRecord({
    postContent: 'Exactly what appeared on Bluesky.',
    postType: 'delay',
    significance: 8,
    postUri: 'at://did:plc:test/app.bsky.feed.post/abc123',
    event: {
      collectorEventId: 77,
      operatorRef: 'FBRI',
      timestamp: '2026-07-28T12:00:00Z',
      vehicleRef: 'FBRI-39441',
      datedJourneyRef: 'journey-42',
      line: 'X1',
      direction: 'outbound',
      originAimedDepartureTimeStr: '2026-07-28T11:45:00Z',
      delayMinutes: 14,
      delaySeconds: 842,
      lastStopCode: '0100A',
      lastStopTime: '',
      eventType: 'delay',
      significance: 8,
      source: 'siri_vm',
      corroboration: 4,
      lowConfidence: false,
    },
  });

  const [post] = await manager.getRecentPosts(1);
  assert.equal(post.postContent, 'Exactly what appeared on Bluesky.');
  assert.equal(post.collectorEventId, 77);
  assert.equal(post.operatorRef, 'FBRI');
  assert.equal(post.vehicleRef, 'FBRI-39441');
  assert.equal(post.line, 'X1');
  assert.equal(post.journeyRef, 'journey-42');
  assert.equal(post.originAimedDeparture, '2026-07-28T11:45:00Z');
  assert.equal(post.eventTimestamp, '2026-07-28T12:00:00Z');
  assert.equal(post.delaySeconds, 842);
  assert.equal(post.direction, 'outbound');
  assert.equal(post.stopCode, '0100A');
  assert.equal(post.source, 'siri_vm');
  assert.equal(post.corroboration, 4);
  assert.equal(post.lowConfidence, false);

  await manager.close();
  const check = new Database(appDataPath, { readonly: true });
  const columns = check.prepare('PRAGMA table_info(engagement_analytics)').all()
    .map(row => row.name);
  check.close();
  assert.ok(columns.includes('operator_ref'));
  assert.ok(columns.includes('journey_ref'));
  assert.ok(columns.includes('collector_event_id'));
});
