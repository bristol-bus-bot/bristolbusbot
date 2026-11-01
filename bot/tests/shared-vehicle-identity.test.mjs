import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

import { buildFleetIdentityIndex, resolveFleetVehicle } from '../dist/services/vehicle-identity.js';

// The site, the live map and the audit check run the same file.
const shared = JSON.parse(readFileSync(
    join(process.cwd(), '..', 'shared', 'fixtures', 'vehicle_identity.json'), 'utf8'));
const index = buildFleetIdentityIndex(shared.fleet);

for (const { vehicle_ref, operator, expected, why } of shared.cases) {
    test(`shared identity "${vehicle_ref}" for "${operator}": ${why}`, () => {
        const found = resolveFleetVehicle(index, vehicle_ref, operator);
        assert.equal(found, expected === null ? null : shared.fleet[expected]);
    });
}
