import test from 'node:test';
import assert from 'node:assert/strict';

import { loadConfig } from '../dist/config/app-config.js';
import { ApplicationState } from '../dist/services/application-state.js';
import { DelayAnalyzer } from '../dist/services/delay-analyzer.js';

function event(line, delayMinutes) {
  return {
    line,
    direction: 'outbound',
    delayMinutes,
    eventType: 'delay',
    significance: 5,
    vehicleRef: `vehicle-${line}`,
    lastStopName: 'Test stop',
  };
}

test('production process and API defaults use the canonical deployment', () => {
  const oldPort = process.env.PORT;
  delete process.env.PORT;
  try {
    const config = loadConfig();
    assert.equal(config.server.host, '127.0.0.1');
    assert.equal(config.server.port, 3010);
  } finally {
    if (oldPort === undefined) delete process.env.PORT;
    else process.env.PORT = oldPort;
  }
});

test('delay freshness compares with the previous accepted report', () => {
  const state = ApplicationState.getInstance();
  const analyzer = new DelayAnalyzer({
    lateThreshold: 4,
    earlyThreshold: -3,
    significantThreshold: 3,
    maxJourneyAge: 2,
    timeWindow: 2,
    maxDistance: 1,
  }, state);
  const line = `TEST-${Date.now()}`;

  const first = event(line, 5);
  assert.equal(analyzer.shouldReportDelay(first, analyzer.updateDelayHistory(first)), true);

  const smallChange = event(line, 6);
  const secondHistory = analyzer.updateDelayHistory(smallChange);
  assert.equal(secondHistory.lastReportedDelay, 5);
  assert.equal(analyzer.shouldReportDelay(smallChange, secondHistory), false);

  const worsening = event(line, 13);
  const thirdHistory = analyzer.updateDelayHistory(worsening);
  assert.equal(thirdHistory.lastReportedDelay, 5);
  assert.equal(thirdHistory.significantChange, true);
  assert.equal(analyzer.shouldReportDelay(worsening, thirdHistory), true);
});
