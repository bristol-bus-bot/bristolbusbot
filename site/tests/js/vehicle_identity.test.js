import assert from "node:assert/strict";
import test from "node:test";

import {
    ambiguousFleetCodes,
    buildFleetLookups,
    vehicleDescription,
} from "../../static/js/vehicle_identity.js";

test("shared fleet codes are ambiguous across operators and reused vehicles", () => {
    const ambiguous = ambiguousFleetCodes([
        { operator_id: "OPAA", fleet_code: "101", reg: "AA11 AAA" },
        { operator_id: "OPBB", fleet_code: "101", reg: "BB11 BBB" },
        { operator_id: "OPCC", fleet_code: "202", reg: "CC22 CCC" },
        { operator_id: "OPCC", fleet_code: "303", reg: "CC33 AAA" },
        { operator_id: "OPCC", fleet_code: "303", reg: "CC33 BBB" },
        { operator_id: "OLD", fleet_code: "202", reg: "OL22 OLD", withdrawn: true },
    ]);

    assert.deepEqual([...ambiguous].sort(), ["101", "303"]);
});

test("fleet lookups omit ambiguous same-operator codes and global registrations", () => {
    const fleet = [
        { operator_id: "OPAA", fleet_code: "101", reg: "AA11 AAA" },
        { operator_id: "OPBB", fleet_code: "101", reg: "BB11 BBB" },
        { operator_id: "OPAA", fleet_code: "303", reg: "AA30 AAA" },
        { operator_id: "OPAA", fleet_code: "303", reg: "AA30 BBB" },
        { operator_id: "OPCC", fleet_code: "404", reg: "ZZ40 ZZZ" },
        { operator_id: "OPDD", fleet_code: "405", reg: "ZZ40 ZZZ" },
    ];

    const lookups = buildFleetLookups(fleet);

    assert.equal(lookups.byOperatorCode["OPAA:101"], fleet[0]);
    assert.equal(lookups.byOperatorCode["OPBB:101"], fleet[1]);
    assert.equal(lookups.byOperatorCode["OPAA:303"], undefined);
    assert.equal(lookups.scopedRegistrations["OPAA:AA30AAA"], fleet[2]);
    assert.equal(lookups.byUniqueRegistration.ZZ40ZZZ, undefined);
});

test("scoped descriptions win and ambiguous legacy descriptions fail closed", () => {
    const pool = {
        "101": "ambiguous legacy text",
        "OPAA:101": "operator A text",
        "202": "safe legacy text",
    };
    const ambiguous = new Set(["101"]);

    assert.equal(vehicleDescription(pool, "OPAA", "101", ambiguous),
        "operator A text");
    assert.equal(vehicleDescription(pool, "OPBB", "101", ambiguous), null);
    assert.equal(vehicleDescription(pool, "OPCC", "202", ambiguous),
        "safe legacy text");
});
