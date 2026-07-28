import assert from 'node:assert/strict';
import test from 'node:test';

import {
  NEWS_EDITORIAL_VETO,
  buildGeminiStructuredGenerationConfig,
  containsSourceReference,
  isNewsEditorialVeto,
} from '../dist/services/ai-commentary.js';
import {
  cleanEditorialPost,
  missingEditorialRequirements,
  operatorDisplayName,
  parseEditorialVerifierOutput,
  parseEditorialWriterOutput,
  validateCommentaryCandidate,
} from '../dist/services/editorial-commentary-policy.js';


const EVENT = {
  operatorRef: 'FBRI',
  timestamp: '2026-07-23T20:00:00Z',
  vehicleRef: 'FBRI-39461',
  datedJourneyRef: 'journey-1',
  line: '21',
  direction: 'outbound',
  originAimedDepartureTimeStr: '2026-07-23T19:00:00Z',
  delayMinutes: -5,
  lastStopCode: 'bth-newbridge',
  lastStopTime: '2026-07-23T20:00:00Z',
  lastStopName: 'Newbridge Park and Ride',
  eventType: 'early',
  significance: 5,
};

const SHAREHOLDER_HOOK = {
  kind: 'fact',
  id: 'shareholder-returns',
  label: 'sourced fact',
  claim: 'FirstGroup returned £89 million and announced a £100 million buyback.',
  promptHint: 'Keep completed and announced actions separate.',
  requirements: [
    {
      label: '£89 million returned',
      alternatives: ['returned £89 million', 'returned £89m'],
    },
    {
      label: '£50 million buyback',
      alternatives: ['£50 million buyback', '£50m buyback'],
    },
    {
      label: '£100 million announced',
      alternatives: ['announced a £100 million', 'announced a £100m'],
    },
  ],
};


test('the news critic veto is strict but formatting-tolerant', () => {
  assert.equal(isNewsEditorialVeto(NEWS_EDITORIAL_VETO), true);
  assert.equal(isNewsEditorialVeto('`skip_news`'), true);
  assert.equal(isNewsEditorialVeto('SKIP_NEWS.'), true);
  assert.equal(isNewsEditorialVeto('Skip this news story'), false);
  assert.equal(isNewsEditorialVeto(null), false);
});


test('public commentary rejects source labels and web links', () => {
  assert.equal(containsSourceReference('Source: https://www.gov.uk/example'), true);
  assert.equal(containsSourceReference('Details at www.gov.uk/example'), true);
  assert.equal(containsSourceReference('A source of delay near Temple Meads.'), false);
  assert.equal(containsSourceReference('The X1 arrives five minutes early.'), false);
});


test('structured writer and verifier responses are parsed strictly', () => {
  assert.deepEqual(
    parseEditorialWriterOutput('{"post":"Route 21 behaves.","hook_used":false}'),
    { post: 'Route 21 behaves.', hookUsed: false },
  );
  assert.throws(
    () => parseEditorialWriterOutput('{"post":"Route 21 behaves.","hook_used":"no"}'),
    /hook_used/,
  );
  assert.deepEqual(
    parseEditorialVerifierOutput('{"verdict":"PASS","reasons":[]}'),
    { verdict: 'PASS', reasons: [] },
  );
  assert.throws(
    () => parseEditorialVerifierOutput('{"verdict":"MAYBE","reasons":[]}'),
    /PASS or FAIL/,
  );
});

test('Gemini structured output uses the REST enum expected by responseFormat', () => {
  const schema = { type: 'object' };
  assert.deepEqual(
    buildGeminiStructuredGenerationConfig(schema, 1, 'LOW'),
    {
      temperature: 1,
      thinkingConfig: { thinkingLevel: 'LOW' },
      responseFormat: {
        text: {
          mimeType: 'APPLICATION_JSON',
          schema,
        },
      },
    },
  );
});


test('the deterministic gate preserves editorial figures and action states', () => {
  const valid = (
    'First Bristol’s outbound 21 reaches Newbridge five minutes early. '
    + 'FirstGroup returned £89m including a £50m buyback, then announced a £100m buyback.'
  );
  assert.deepEqual(
    validateCommentaryCandidate(valid, EVENT, SHAREHOLDER_HOOK, true),
    [],
  );

  const missingCompletedBuyback = (
    'First Bristol’s outbound 21 reaches Newbridge five minutes early. '
    + 'FirstGroup returned £89m, then announced a £100m buyback.'
  );
  assert.deepEqual(
    missingEditorialRequirements(
      missingCompletedBuyback,
      SHAREHOLDER_HOOK.requirements,
    ),
    ['£50 million buyback'],
  );
  assert.match(
    validateCommentaryCandidate(
      missingCompletedBuyback,
      EVENT,
      SHAREHOLDER_HOOK,
      true,
    ).join(' '),
    /£50 million buyback/,
  );
});


test('ordinary candidates still require the observed route, place, direction and timing', () => {
  const valid = (
    'First Bristol’s outbound 21 reaches Newbridge five minutes early. '
    + 'An admirable burst of enthusiasm, unless you were still walking to the stop.'
  );
  assert.deepEqual(validateCommentaryCandidate(valid, EVENT, null, false), []);

  const wrongStatus = valid.replace('five minutes early', 'six minutes late');
  assert.match(
    validateCommentaryCandidate(wrongStatus, EVENT, null, false).join(' '),
    /exact observed status/,
  );
  const missingDirection = valid.replace('outbound ', '');
  assert.match(
    validateCommentaryCandidate(missingDirection, EVENT, null, false).join(' '),
    /outbound direction/,
  );
  const missingOperator = valid.replace('First Bristol’s ', 'The ');
  assert.match(
    validateCommentaryCandidate(missingOperator, EVENT, null, false).join(' '),
    /missing operator First Bristol/,
  );
  assert.deepEqual(
    validateCommentaryCandidate(
      valid.replace('First Bristol’s', 'First Bus’s'), EVENT, null, false,
    ),
    [],
  );
});

test('operator identities distinguish First Bristol from Stagecoach West', () => {
  assert.equal(operatorDisplayName('FBRI'), 'First Bristol');
  assert.equal(operatorDisplayName('SCGL'), 'Stagecoach West');
  assert.equal(operatorDisplayName('unknown'), null);
  const stagecoach = { ...EVENT, operatorRef: 'SCGL' };
  const valid = 'Stagecoach West’s outbound 21 reaches Newbridge five minutes early.';
  assert.deepEqual(validateCommentaryCandidate(valid, stagecoach, null, false), []);
  assert.match(
    validateCommentaryCandidate(
      valid.replace('Stagecoach West’s', 'First Bristol’s'),
      stagecoach,
      null,
      false,
    ).join(' '),
    /missing operator Stagecoach West/,
  );
});


test('cleaning never permits public sources and keeps compliant prose intact', () => {
  const post = 'The outbound 21 reaches Newbridge five minutes early.';
  assert.equal(cleanEditorialPost(post), post);
  assert.equal(
    cleanEditorialPost(`${post} Source: https://example.com`),
    null,
  );
});
