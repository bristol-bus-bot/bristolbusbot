import assert from 'node:assert/strict';
import test from 'node:test';

import {
    buildFleetIdentityIndex,
    resolveFleetVehicle,
} from '../dist/services/vehicle-identity.js';

function bus(operator, fleet, registration, model, extra = {}) {
    return {
        operator: { id: operator },
        fleet_number: Number(fleet),
        fleet_code: String(fleet),
        reg: registration,
        vehicle_type: { name: model },
        ...extra,
    };
}

test('operator-scoped fleet lookup prevents shared-code cross-wiring', () => {
    const a = bus('OPAA', 101, 'AA11 AAA', 'Operator A model');
    const b = bus('OPBB', 101, 'BB11 BBB', 'Operator B model');
    const index = buildFleetIdentityIndex([a, b]);

    assert.equal(resolveFleetVehicle(index, 'OPAA-101', 'OPAA'), a);
    assert.equal(resolveFleetVehicle(index, 'OPBB-101', 'OPBB'), b);
    assert.equal(resolveFleetVehicle(index, 'OPCC-101', 'OPCC'), null);
});

test('registration is canonical and scoped to the observed operator', () => {
    const a = bus('OPAA', 101, 'AA11 AAA', 'Operator A model');
    const b = bus('OPBB', 101, 'BB11 BBB', 'Operator B model');
    const index = buildFleetIdentityIndex([a, b]);

    assert.equal(resolveFleetVehicle(index, 'AA11_AAA', 'OPAA'), a);
    assert.equal(resolveFleetVehicle(index, 'BB11-BBB', 'OPBB'), b);
    assert.equal(resolveFleetVehicle(index, 'BB11-BBB', 'OPAA'), null);
});

test('operator-prefixed registration resolves only within that operator', () => {
    const vehicle = {
        operator: { id: 'EUTX' },
        fleet_code: '',
        reg: 'YW68 PDO',
        vehicle_type: { name: 'Eurotaxis vehicle' },
    };
    const index = buildFleetIdentityIndex([vehicle]);

    assert.equal(resolveFleetVehicle(
        index, 'EUTX-YW68_PDO', 'EUTX'), vehicle);
    assert.equal(resolveFleetVehicle(
        index, 'ABUS-YW68_PDO', 'ABUS'), null);
});

test('same-operator reused code fails closed without a registration', () => {
    const first = bus('OPAA', 303, 'AA30 AAA', 'First vehicle');
    const second = bus('OPAA', 303, 'AA30 BBB', 'Second vehicle');
    const index = buildFleetIdentityIndex([first, second]);

    assert.equal(resolveFleetVehicle(index, 'OPAA-303', 'OPAA'), null);
    assert.equal(resolveFleetVehicle(index, 'AA30AAA', 'OPAA'), first);
    assert.equal(resolveFleetVehicle(index, 'AA30BBB', 'OPAA'), second);
});

test('unambiguous legacy data remains usable when operator metadata is absent', () => {
    const legacy = { fleet_number: 404, registration: 'AA40 AAA' };
    const index = buildFleetIdentityIndex([legacy]);

    assert.equal(resolveFleetVehicle(index, 'UNKNOWN-404'), legacy);
});

test('a registration-like reference is never guessed from its year digits', () => {
    const fleet = bus('OPAA', 21, 'AA21 AAA', 'Fleet 21');
    const index = buildFleetIdentityIndex([fleet]);

    assert.equal(resolveFleetVehicle(index, 'BR21OCV', 'OPAA'), null);
});
