import assert from 'node:assert/strict';
import test from 'node:test';

import { DEPOT_CARDS_FOR_REVIEW, DEPOT_FACTS, depotFact } from '../dist/services/depot-facts.js';
import { ROUTE_CARDS } from '../dist/services/route-cards.js';

test('depot cards have a claim, qualification and source', () => {
  for (const [key, cards] of Object.entries({ ...DEPOT_FACTS, ...DEPOT_CARDS_FOR_REVIEW })) {
    assert.ok(cards.length > 0, key);
    for (const card of cards) {
      assert.ok(card.claim.trim(), key);
      assert.ok(card.qualification.trim(), key);
      assert.equal(new URL(card.source).protocol, 'https:', key);
      assert.doesNotMatch(JSON.stringify(card), /\u2014/, key);
    }
  }
});

test('the two depot cards under review are unavailable to the writer', () => {
  assert.deepEqual(Object.keys(DEPOT_CARDS_FOR_REVIEW).sort(), ['FBRI:Lawrence Hill', 'FBRI:Wells']);
  assert.equal(depotFact('FBRI', 'Lawrence Hill'), undefined);
  assert.equal(depotFact('FBRI', 'Wells'), undefined);
});

test('all ten route cards record timetable provenance and both directions', () => {
  assert.equal(Object.keys(ROUTE_CARDS).length, 10);
  for (const [routeId, card] of Object.entries(ROUTE_CARDS)) {
    assert.match(routeId, /^\d+$/);
    assert.ok(card.operatorRef.trim());
    assert.ok(card.line.trim());
    assert.equal(card.source, 'derived');
    assert.equal(card.timetableBuildDate, '2026-11-13');
    assert.equal(card.serviceDate, '2026-11-18');
    assert.ok(Number.isInteger(card.scheduledJourneys) && card.scheduledJourneys > 0);
    assert.deepEqual(card.directions.map(direction => direction.directionId), [0, 1]);
    assert.doesNotMatch(JSON.stringify(card), /\u2014/);
    for (const direction of card.directions) {
      for (const stop of [direction.fromStop, direction.toStop]) {
        assert.ok(stop.id.trim());
        assert.ok(stop.name.trim());
        assert.ok(stop.locality.trim());
      }
      assert.ok(Number.isInteger(direction.stopCount) && direction.stopCount >= 2);
      assert.ok(Number.isInteger(direction.patternJourneys) && direction.patternJourneys > 0);
      assert.ok(direction.patternJourneys <= card.scheduledJourneys);
      assert.ok(Number.isInteger(direction.otherPatterns) && direction.otherPatterns >= 0);
      assert.ok(Number.isInteger(direction.shapeVariant) && direction.shapeVariant >= 0);
      if (routeId === '8396' && direction.directionId === 1) {
        assert.equal(direction.approximateLengthKm, null);
        assert.match(direction.shapeNote, /Hengrove Park.*Hengrove Depot/);
      } else {
        assert.ok(Number.isFinite(direction.approximateLengthKm) && direction.approximateLengthKm > 0);
      }
    }
  }
});
