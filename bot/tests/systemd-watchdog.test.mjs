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
