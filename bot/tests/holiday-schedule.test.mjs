import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { DateTime } from 'luxon';

import { HolidaySchedule, holidayMessage, noServiceDay } from '../dist/services/holiday-schedule.js';

const uk = iso => DateTime.fromISO(iso, { zone: 'Europe/London' });

function harness({ sends = true, statePath } = {}) {
  const path = statePath || join(mkdtempSync(join(tmpdir(), 'bbb-holiday-')), 'holiday-posts.json');
  const log = [];
  const schedule = new HolidaySchedule(path, {
    startMonitoring: () => log.push('start'),
    stopMonitoring: () => log.push('stop'),
    post: async message => { log.push(`post:${message.split(' ').slice(0, 2).join(' ')}`); return typeof sends === 'function' ? sends() : sends; },
  });
  return { schedule, log, path };
}

test('only Christmas Day and Boxing Day are no-service days, on the UK clock', () => {
  assert.equal(noServiceDay(uk('2026-12-24T23:59')), null);
  assert.equal(noServiceDay(uk('2026-12-25T00:00')), 'Christmas Day');
  assert.equal(noServiceDay(uk('2026-12-26T23:59')), 'Boxing Day');
  assert.equal(noServiceDay(uk('2026-12-27T00:00')), null);
});

test('running through midnight on Christmas Eve pauses monitoring and posts at 8am', async () => {
  const { schedule, log } = harness();
  await schedule.tick(uk('2026-12-24T23:58'));
  await schedule.tick(uk('2026-12-25T00:01'));
  await schedule.tick(uk('2026-12-25T07:59'));
  assert.deepEqual(log, ['start', 'stop']);
  await schedule.tick(uk('2026-12-25T08:00'));
  await schedule.tick(uk('2026-12-25T08:01'));
  assert.deepEqual(log, ['start', 'stop', 'post:Merry Christmas']);
});

test('a restart on Boxing Day keeps monitoring off, then resumes at midnight', async () => {
  const { schedule, log } = harness();
  await schedule.tick(uk('2026-12-26T06:00'));
  assert.deepEqual(log, ['stop']);
  await schedule.tick(uk('2026-12-26T23:59'));
  await schedule.tick(uk('2026-12-27T00:00'));
  assert.deepEqual(log, ['stop', 'post:Happy Boxing', 'start']);
});

test('a restart after 8am on Christmas Day does not post the greeting again', async () => {
  const first = harness();
  await first.schedule.tick(uk('2026-12-25T09:00'));
  assert.ok(first.log.includes('post:Merry Christmas'));
  assert.equal(JSON.parse(readFileSync(first.path, 'utf8'))['2026-12-25'], 'Christmas Day');
  const restarted = harness({ statePath: first.path });
  await restarted.schedule.tick(uk('2026-12-25T11:00'));
  assert.deepEqual(restarted.log, ['stop']);
});

test('a greeting held back by the daily cap is not recorded, so it is tried again', async () => {
  let allowed = false;
  const { schedule, log } = harness({ sends: () => allowed });
  await schedule.tick(uk('2026-12-25T08:00'));
  allowed = true;
  await schedule.tick(uk('2026-12-25T08:01'));
  await schedule.tick(uk('2026-12-25T08:02'));
  assert.deepEqual(log, ['stop', 'post:Merry Christmas', 'post:Merry Christmas']);
});

test('the greetings no longer promise when normal service resumes', () => {
  for (const day of ['Christmas Day', 'Boxing Day']) {
    assert.doesNotMatch(holidayMessage(day), /resumes|27th|tomorrow/i);
  }
});
