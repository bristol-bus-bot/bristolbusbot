import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { buildFleetLookups } from "../../static/js/vehicle_identity.js";

// The map looks buses up by operator and fleet code; the bot, the site and
// the audit check give the same answers from the same file.
const shared = JSON.parse(readFileSync(
    new URL("../../../shared/fixtures/vehicle_identity.json", import.meta.url), "utf8"));
// /api/fleet hands the map operator_id rather than operator.id.
const fleet = shared.fleet.map((record) => ({ ...record, operator_id: record.operator?.id ?? "" }));
const { byOperatorCode } = buildFleetLookups(fleet);

for (const [key, expected] of Object.entries(shared.map_operator_codes)) {
    test(`shared map lookup ${key}`, () => {
        assert.equal(byOperatorCode[key] ?? null, expected === null ? null : fleet[expected]);
    });
}
