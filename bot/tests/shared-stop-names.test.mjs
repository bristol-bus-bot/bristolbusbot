import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

import {
    STOP_NAME_ANY, STOP_NAME_EXACT, STOP_NAME_PREFIX, manualOverrides,
} from '../dist/utils/stop-name-cleaner.js';

// The site's tests (site/tests/test_shared_stop_names.py) run the same file,
// so a correction added to one copy fails the other until both match.
const shared = JSON.parse(readFileSync(
    join(process.cwd(), '..', 'shared', 'fixtures', 'stop_names.json'), 'utf8'));

test('the bot holds exactly the shared stop-name corrections', () => {
    assert.deepEqual(STOP_NAME_EXACT.map(row => [...row]).sort(), shared.tables.exact);
    assert.deepEqual(STOP_NAME_PREFIX.map(row => [...row]), shared.tables.prefix);
    assert.deepEqual(STOP_NAME_ANY.map(row => [...row]).sort(), shared.tables.any);
});

for (const { code, name, expected, why } of shared.cases) {
    test(`shared stop name ${code} "${name}"${why ? ` (${why})` : ''}`, () => {
        assert.equal(manualOverrides(name, code) ?? name, expected);
    });
}
