import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { DateTime } from 'luxon';
import { ApplicationState } from '../dist/services/application-state.js';
import { TARGET_TIMEZONE } from '../dist/utils/logging.js';

const today = () => DateTime.now().setZone(TARGET_TIMEZONE).toISODate();
const tempFile = t => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'bbb-counters-'));
    t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
    return path.join(dir, 'nested', 'daily-counters.json');
};

test('today\'s counters survive a restart', t => {
    const file = tempFile(t);
    const before = new ApplicationState();
    before.attachCounterStore(file);
    before.incrementPostCount();
    before.incrementPostCount();
    before.incrementAICallCount();
    before.incrementSummaryCount();

    const after = new ApplicationState();
    after.attachCounterStore(file);
    assert.equal(after.postsTodayCount, 2);
    assert.equal(after.aiCallsToday, 1);
    assert.equal(after.summariesPosted, 1);
    after.incrementPostCount();
    assert.equal(JSON.parse(fs.readFileSync(file, 'utf8')).postsToday, 3);
});

test('counters from an earlier day are not restored, and a rollover is saved', t => {
    const file = tempFile(t);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, JSON.stringify({ version: 1, date: '2000-01-01', postsToday: 50, aiCallsToday: 70, summariesPosted: 3 }));
    const state = new ApplicationState();
    state.attachCounterStore(file);
    assert.equal(state.postsTodayCount, 0);
    assert.equal(state.aiCallsToday, 0);

    state.postsTodayCount = 9;
    state.lastResetDate = '2000-01-01';
    state.resetDailyCounters();
    const saved = JSON.parse(fs.readFileSync(file, 'utf8'));
    assert.equal(saved.date, today());
    assert.equal(saved.postsToday, 0);
});

test('an unreadable or invalid file starts from zero instead of crashing', t => {
    const file = tempFile(t);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, '{not json');
    const broken = new ApplicationState();
    broken.attachCounterStore(file);
    assert.equal(broken.postsTodayCount, 0);

    fs.writeFileSync(file, JSON.stringify({ version: 1, date: today(), postsToday: -4, aiCallsToday: 'lots' }));
    const invalid = new ApplicationState();
    invalid.attachCounterStore(file);
    assert.equal(invalid.postsTodayCount, 0);
    assert.equal(invalid.aiCallsToday, 0);
});

test('without a file the counters still work in memory', () => {
    const state = new ApplicationState();
    state.attachCounterStore(undefined);
    state.incrementPostCount();
    assert.equal(state.postsTodayCount, 1);
});
