import assert from "node:assert/strict";
import test from "node:test";

import {
    fleetResultLimit,
    getFleetMatches,
    getRouteMatches,
    isBusOnRoute,
    routeFallbackCandidates,
    routeFitOptions,
    routeSelectionSheetState,
} from "../../static/js/search_logic.js";

const fleet = [
    { id: "exact", fleet_code: "42", fleet_number: 42, reg: "WX12 ABC",
      previous_reg: "", name: "Mabel", model: "Enviro 400" },
    { id: "plate", fleet_code: "500", fleet_number: 500, reg: "YX42 BUS",
      previous_reg: "SN12 OLD", name: "Rosie", model: "StreetDeck" },
    { id: "name", fleet_code: "700", fleet_number: 700, reg: "YX70 NEW",
      previous_reg: "", name: "Bluebird", model: "Volvo B9TL" },
];

test("fleet matching applies exact and minimum-length gates", () => {
    assert.deepEqual(
        getFleetMatches(fleet, [], "42").matches.map(v => v.id),
        ["exact"],
    );
    assert.equal(getFleetMatches(fleet, [], "x4").total, 0);
    assert.deepEqual(
        getFleetMatches(fleet, [], "x42").matches.map(v => v.id),
        ["plate"],
    );
    assert.equal(getFleetMatches(fleet, [], "blu").total, 0);
    assert.deepEqual(
        getFleetMatches(fleet, [], "blue").matches.map(v => v.id),
        ["name"],
    );
    assert.equal(getFleetMatches(fleet, [], "vol").total, 0);
    assert.deepEqual(
        getFleetMatches(fleet, [], "volv").matches.map(v => v.id),
        ["name"],
    );
});

test("fleet results are capped and live vehicles sort first", () => {
    const many = Array.from({ length: 10 }, (_, index) => ({
        id: String(index),
        fleet_code: String(100 + index),
        fleet_number: 100 + index,
        reg: `AB1${index} CDE`,
        name: `Example ${index}`,
        model: "StreetDeck",
    }));
    const result = getFleetMatches(
        many,
        [{ fleetNumber: "109", reg: "AB19 CDE" }],
        "street",
        8,
    );
    assert.equal(result.total, 10);
    assert.equal(result.matches.length, 8);
    assert.equal(result.matches[0].id, "9");
    assert.equal(fleetResultLimit([]), 8);
    assert.equal(fleetResultLimit([{ routeKey: "FBRI_42" }]), 3);
});

test("duplicate public route numbers remain operator-specific", () => {
    const routeIndex = {
        FBRI_42: [{ direction: "outbound" }, { direction: "inbound" }],
        CTCO_42: [{ direction: "outbound" }],
        FBRI_43: [{ direction: "outbound" }],
    };
    const buses = [
        { operatorRef: "FBRI", line: "42", eventType: "service",
          destination: "City Centre" },
        { operatorRef: "CTCO", line: "42", eventType: "service",
          destination: "Keynsham" },
        { operatorRef: "CTCO", line: "42", eventType: "depot",
          destination: "Depot" },
    ];
    const matches = getRouteMatches(
        routeIndex, buses, "42", { FBRI: "First", CTCO: "CT Coaches" });
    assert.equal(matches.length, 2);
    assert.deepEqual(
        new Set(matches.map(match => match.routeKey)),
        new Set(["FBRI_42", "CTCO_42"]),
    );
    assert.equal(matches.find(match => match.routeKey === "CTCO_42").activeBuses, 1);
});

test("a live TNDS route is searchable without stored shape geometry", () => {
    const matches = getRouteMatches({}, [
        { operatorRef: "FBRI", line: "45", eventType: "punctual",
          destination: "Cherry Garden Road" },
        { operatorRef: "FBRI", line: "45", eventType: "delayed",
          destination: "Hengrove Depot" },
        { operatorRef: "FBRI", line: "45", eventType: "depot",
          destination: "Depot" },
    ], "45", { FBRI: "First Bus" });
    assert.equal(matches.length, 1);
    assert.equal(matches[0].routeKey, "FBRI_45");
    assert.equal(matches[0].variants, 0);
    assert.equal(matches[0].activeBuses, 2);
});

test("fallback paths choose one scheduled live journey per direction", () => {
    const buses = [
        { operatorRef: "FBRI", line: "45", eventType: "punctual",
          directionId: 0, tripId: null, hasSchedule: false },
        { operatorRef: "FBRI", line: "45", eventType: "punctual",
          directionId: 0, tripId: "OUT", hasSchedule: true },
        { operatorRef: "FBRI", line: "45", eventType: "delayed",
          directionId: 1, tripId: "IN", hasSchedule: true },
        { operatorRef: "FBRI", line: "44", eventType: "punctual",
          directionId: 0, tripId: "OTHER", hasSchedule: true },
    ];
    const selected = routeFallbackCandidates(buses, "FBRI_45");
    assert.deepEqual(selected.map(bus => bus.tripId), ["OUT", "IN"]);
});

test("route membership can be recalculated on every live refresh", () => {
    const before = { operatorRef: "FBRI", line: "42", eventType: "service" };
    const after = { operatorRef: "FBRI", line: "43", eventType: "service" };
    assert.equal(isBusOnRoute(before, "FBRI_42"), true);
    assert.equal(isBusOnRoute(after, "FBRI_42"), false);
});

test("mobile route selection peeks and reserves map space", () => {
    assert.equal(routeSelectionSheetState(true), "peek");
    assert.equal(routeSelectionSheetState(false), "collapsed");
    assert.deepEqual(routeFitOptions(false, 900), { padding: [40, 40] });
    const mobile = routeFitOptions(true, 900);
    assert.deepEqual(mobile.paddingTopLeft, [32, 32]);
    assert.ok(mobile.paddingBottomRight[1] >= 324);
});
