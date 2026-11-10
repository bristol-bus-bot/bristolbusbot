import assert from 'node:assert/strict';
import test from 'node:test';

import {
  SystemdWatchdog,
  watchdogEnabled,
} from '../dist/services/systemd-watchdog.js';


test('watchdog is disabled outside a systemd watchdog service', () => {
  assert.equal(watchdogEnabled({}), false);
  let calls = 0;
  const watchdog = new SystemdWatchdog({}, () => { calls += 1; });
  assert.equal(watchdog.progress(), false);
  assert.equal(calls, 0);
});


test('progress notifies systemd only for the main process', () => {
  const environment = {
    NOTIFY_SOCKET: '/run/systemd/notify',
    WATCHDOG_USEC: '120000000',
    WATCHDOG_PID: String(process.pid),
  };
  let calls = 0;
  const watchdog = new SystemdWatchdog(environment, () => { calls += 1; });
  assert.equal(watchdog.progress(), true);
  assert.equal(calls, 1);

  assert.equal(watchdogEnabled({
    ...environment,
    WATCHDOG_PID: String(process.pid + 1),
  }), false);
  assert.equal(watchdogEnabled({
    ...environment,
    WATCHDOG_PID: `${process.pid}-invalid`,
  }), false);
});


test('a paused holiday ingest still tells systemd the bot is alive', async () => {
  const { pausedIngestKeepalive } = await import('../dist/services/systemd-watchdog.js');
  const environment = {
    NOTIFY_SOCKET: '/run/systemd/notify',
    WATCHDOG_USEC: '120000000',
    WATCHDOG_PID: String(process.pid),
  };
  let calls = 0;
  const watchdog = new SystemdWatchdog(environment, () => { calls += 1; });
  let running = true;
  const tick = pausedIngestKeepalive(watchdog, () => running);
  assert.equal(tick(), false);      // ingest running: only its cycles ping
  assert.equal(calls, 0);
  running = false;                  // 25-26 December: ingest stopped
  assert.equal(tick(), true);
  assert.equal(calls, 1);
});
