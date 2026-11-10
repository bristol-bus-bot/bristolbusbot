// A release copies dist/ as built. tsc never deletes outputs for removed
// sources, so every build must start from an empty dist/ or deleted code
// (for example the old siri-monitor.js) keeps shipping.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';

const pkg = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));

test('the build clears dist first, on any platform', () => {
    assert.equal(pkg.scripts.prebuild, 'npm run clean');
    assert.match(pkg.scripts.clean, /rmSync\('dist'/);
});

test('no compiled output survives for a deleted source file', () => {
    assert.equal(existsSync(new URL('../dist/services/siri-monitor.js', import.meta.url)), false);
});
