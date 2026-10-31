import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

import { validateEditorialDocument } from '../dist/services/editorial-context.js';

// The same examples run through the Pi's Python validator
// (deploy/tests/test_editorial_fixtures.py); both must agree on each one.
const FIXTURES = join(process.cwd(), '..', 'shared', 'fixtures', 'editorial_context');
const cases = JSON.parse(readFileSync(join(FIXTURES, 'cases.json'), 'utf8'));

for (const { file, valid, why } of cases) {
  test(`shared example ${file}: ${valid ? 'accepted' : 'rejected'} (${why})`, () => {
    const document = JSON.parse(readFileSync(join(FIXTURES, file), 'utf8'));
    if (valid) validateEditorialDocument(document);
    else assert.throws(() => validateEditorialDocument(document));
  });
}
