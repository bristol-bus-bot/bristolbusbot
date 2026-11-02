import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { DateTime } from 'luxon';

import {
  EditorialContextStore,
  editorialApplies,
  isOccasionPeak,
  OCCASION_OFF_PEAK_FACTOR,
  validateEditorialDocument,
} from '../dist/services/editorial-context.js';


const SOURCE = {
  publisher: 'UK Government',
  title: 'Approved source',
  url: 'https://www.gov.uk/government/news/example',
  published_on: '2026-07-22',
  verified_on: '2026-07-23',
};

const REQUIREMENTS = [{
  label: 'approved wording',
  alternatives: ['approved claim'],
}];


function documentWith({ facts = [], occasions = [], news = [] } = {}) {
  return {
    schema_version: 1,
    updated_at: '2026-07-23T00:00:00Z',
    facts,
    occasions,
    news,
  };
}


function makeStore(document, random = () => 0) {
  const directory = mkdtempSync(join(tmpdir(), 'bbb-editorial-'));
  const contextPath = join(directory, 'editorial-context.json');
  const usagePath = join(directory, 'editorial-usage.json');
  writeFileSync(contextPath, `${JSON.stringify(document)}\n`);
  return {
    directory,
    contextPath,
    usagePath,
    store: new EditorialContextStore(contextPath, usagePath, random),
  };
}

test('scopes require exact collector evidence and combine restrictions', () => {
  const item = { scope: { operators: ['FBRI'], routes: ['T1'], localities: ['Thornbury'], excluded_routes: ['A1'] } };
  const event = { operatorRef: 'FBRI', line: 't1', placeContext: { locality: 'Thornbury' } };
  assert.equal(editorialApplies(item, '2026-09-15', event), true);
  for (const wrong of [undefined, { ...event, operatorRef: 'OTHER' }, { ...event, line: 'T2' },
    { ...event, placeContext: {} }, { ...event, placeContext: { locality: 'Thornbury Road' } }]) {
    assert.equal(editorialApplies(item, '2026-09-15', wrong), false);
  }
  assert.equal(editorialApplies({ scope: { excluded_routes: ['A1'] } }, '2026-09-15', { ...event, line: 'A1' }), false);
  assert.equal(editorialApplies({ review_due: '2026-09-14' }, '2026-09-15', event), false);
  assert.equal(editorialApplies({ review_due: '2026-09-15' }, '2026-09-15', event), true);
});

test('selection skips unrelated and overdue facts before choosing an eligible one', () => {
  const fact = { id: 'local-fact', claim: 'Approved local fact.', prompt_hint: 'Keep the scope.',
    requirements: REQUIREMENTS, active_from: '2026-01-01', active_until: '2026-12-31', source: SOURCE };
  const fixture = makeStore(documentWith({ facts: [
    { ...fact, id: 'wrong-route', scope: { routes: ['42'] } },
    { ...fact, id: 'overdue', review_due: '2026-08-01' },
    { ...fact, scope: { operators: ['FBRI'], routes: ['T1'] } },
  ] }));
  const now = DateTime.fromISO('2026-09-15T10:00:00', { zone: 'Europe/London' });
  assert.equal(fixture.store.select(now, [], { operatorRef: 'FBRI', line: 'T1' })?.id, 'local-fact');
  assert.equal(fixture.store.select(now, [], { operatorRef: 'FBRI', line: '19' }), null);
});

test('unknown or empty scopes and inverted review dates fail validation', () => {
  const document = JSON.parse(readFileSync(join(process.cwd(), 'data', 'editorial-context.json'), 'utf8'));
  for (const scope of [{}, { operator: ['FBRI'] }, { routes: [] }, { routes: ['T1', 't1'] }]) {
    const copy = structuredClone(document);
    copy.facts[0].scope = scope;
    assert.throws(() => validateEditorialDocument(copy), /scope/);
  }
  document.facts[0].review_due = '2020-01-01';
  assert.throws(() => validateEditorialDocument(document), /precedes verification/);
});


test('the checked-in editorial context is valid and contains no Bee Network claims', () => {
  const path = join(process.cwd(), 'data', 'editorial-context.json');
  const raw = readFileSync(path, 'utf8');
  assert.doesNotMatch(raw, /Bee Network/i);
  const document = validateEditorialDocument(JSON.parse(raw));
  // Content edits shouldn't need test edits: check shape, not counts.
  for (const list of ['facts', 'occasions', 'news']) {
    assert.ok(document[list].length > 0, `${list} is empty`);
    const ids = document[list].map(entry => entry.id);
    assert.equal(new Set(ids).size, ids.length, `${list} has duplicate ids`);
  }
});


test('the validator refuses Bee Network claims and non-allowlisted sources', () => {
  const bee = documentWith({
    facts: [{
      id: 'bad-bee-claim',
      claim: 'A Bee Network comparison.',
      prompt_hint: 'Do not use.',
      requirements: REQUIREMENTS,
      active_from: '2026-01-01',
      active_until: '2026-12-31',
      source: SOURCE,
    }],
  });
  assert.throws(() => validateEditorialDocument(bee), /Bee Network claims/);

  const unsafe = structuredClone(bee);
  unsafe.facts[0].claim = 'A different claim.';
  unsafe.facts[0].source.url = 'https://example.com/not-approved';
  assert.throws(() => validateEditorialDocument(unsafe), /allowlisted/);
});


test('news expires, has a cooldown and cannot exceed its total use limit', () => {
  const news = [{
    id: 'approved-news',
    label: 'Approved news',
    claim: 'A precise approved claim.',
    prompt_hint: 'Use exact dates.',
    requirements: REQUIREMENTS,
    published_at: '2026-07-22T00:00:00Z',
    active_from: '2026-07-22T00:00:00Z',
    expires_at: '2026-07-30T23:59:59Z',
    probability: 1,
    max_uses_total: 2,
    cooldown_hours: 36,
    append_source_link: false,
    source: SOURCE,
  }];
  const fixture = makeStore(documentWith({ news }));
  const firstTime = DateTime.fromISO('2026-07-23T10:00:00', { zone: 'Europe/London' });
  const first = fixture.store.select(firstTime, []);
  assert.equal(first?.kind, 'news');
  fixture.store.recordPost(first, firstTime);

  assert.equal(fixture.store.select(firstTime.plus({ hours: 1 }), []), null);
  fixture.store.recordPost(null, firstTime.plus({ hours: 1 }));
  assert.equal(fixture.store.select(firstTime.plus({ hours: 35 }), []), null);

  const secondTime = firstTime.plus({ hours: 37 });
  const second = fixture.store.select(secondTime, []);
  assert.equal(second?.id, 'approved-news');
  fixture.store.recordPost(second, secondTime);
  fixture.store.recordPost(null, secondTime.plus({ hours: 1 }));
  assert.equal(fixture.store.select(secondTime.plus({ hours: 40 }), []), null);

  const reloaded = new EditorialContextStore(
    fixture.contextPath,
    fixture.usagePath,
    () => 0,
  );
  assert.equal(reloaded.select(secondTime.plus({ hours: 41 }), []), null);
  assert.equal(
    reloaded.select(DateTime.fromISO('2026-08-01T10:00:00', { zone: 'Europe/London' }), []),
    null,
  );
});


test('legacy source-link flags stay private and cannot reach commentary', () => {
  const news = [{
    id: 'approved-news',
    label: 'Approved news',
    claim: 'A precise approved claim.',
    prompt_hint: 'Keep the material qualifications.',
    requirements: REQUIREMENTS,
    published_at: '2026-07-22T00:00:00Z',
    active_from: '2026-07-22T00:00:00Z',
    expires_at: '2026-07-30T23:59:59Z',
    probability: 1,
    max_uses_total: 2,
    cooldown_hours: 36,
    append_source_link: true,
    source: SOURCE,
  }];
  const fixture = makeStore(documentWith({ news }));
  const selection = fixture.store.select(
    DateTime.fromISO('2026-07-23T10:00:00', { zone: 'Europe/London' }),
    [],
  );
  assert.equal(selection?.kind, 'news');
  assert.equal('sourceUrl' in selection, false);
  assert.equal('appendSourceLink' in selection, false);
});


test('a month-long occasion can be used at most once per day', () => {
  const occasions = [{
    id: 'september-campaign',
    label: 'September campaign',
    prompt_hint: 'A restrained reference.',
    requirements: REQUIREMENTS,
    schedule: { kind: 'date_range', start: '2026-09-01', end: '2026-09-30' },
    probability: 1,
    max_uses_per_day: 1,
    source: SOURCE,
  }];
  const fixture = makeStore(documentWith({ occasions }));
  const morning = DateTime.fromISO('2026-09-05T09:00:00', { zone: 'Europe/London' });
  const first = fixture.store.select(morning, []);
  assert.equal(first?.kind, 'occasion');
  fixture.store.recordPost(first, morning);
  fixture.store.recordPost(null, morning.plus({ hours: 1 }));
  assert.equal(fixture.store.select(morning.plus({ hours: 2 }), []), null);
  assert.equal(fixture.store.select(morning.plus({ days: 1 }), [])?.kind, 'occasion');
});


test('editorial requirements are mandatory, validated and exposed to commentary', () => {
  const fact = {
    id: 'approved-fact',
    claim: 'An approved claim with £12 million.',
    prompt_hint: 'Keep the figure exact.',
    requirements: [{
      label: '£12 million',
      alternatives: ['£12 million', '£12m'],
    }],
    active_from: '2026-01-01',
    active_until: '2026-12-31',
    source: SOURCE,
  };
  const fixture = makeStore(documentWith({ facts: [fact] }));
  const selected = fixture.store.select(
    DateTime.fromISO('2026-07-23T10:00:00', { zone: 'Europe/London' }),
    [],
  );
  assert.deepEqual(selected?.requirements, fact.requirements);

  const missing = structuredClone(fact);
  delete missing.requirements;
  assert.throws(
    () => validateEditorialDocument(documentWith({ facts: [missing] })),
    /requirements/,
  );

  const duplicate = structuredClone(fact);
  duplicate.requirements[0].alternatives = ['£12m', '£12M'];
  assert.throws(
    () => validateEditorialDocument(documentWith({ facts: [duplicate] })),
    /duplicates/,
  );
});


test('an old usage file that still has deferrals loads and is ignored', () => {
  const fact = {
    id: 'deferred-fact',
    claim: 'An approved claim.',
    prompt_hint: 'Use only when it fits.',
    requirements: REQUIREMENTS,
    active_from: '2026-01-01',
    active_until: '2026-12-31',
    source: SOURCE,
  };
  const fixture = makeStore(documentWith({ facts: [fact] }));
  writeFileSync(fixture.usagePath, JSON.stringify({
    schema_version: 1, last_post_was_special: false, items: {},
    deferrals: { 'deferred-fact': '2099-01-01T00:00:00Z' },
  }));
  const reloaded = new EditorialContextStore(fixture.contextPath, fixture.usagePath, () => 0);
  const now = DateTime.fromISO('2026-07-23T10:00:00', { zone: 'Europe/London' });
  assert.equal(reloaded.select(now, [])?.id, fact.id);
  reloaded.recordPost(null, now);
  assert.equal('deferrals' in JSON.parse(readFileSync(fixture.usagePath, 'utf8')), false);
});


test('peak hours are 07:00-10:00 and 16:00-21:00 on the UK clock', () => {
  const at = (time, date = '2026-11-02') => DateTime.fromISO(`${date}T${time}`, { zone: 'Europe/London' });
  for (const time of ['06:59', '10:00', '15:59', '21:00', '23:30', '03:00']) {
    assert.equal(isOccasionPeak(at(time)), false, time);
  }
  for (const time of ['07:00', '09:59', '16:00', '20:59']) {
    assert.equal(isOccasionPeak(at(time)), true, time);
  }
  // Clock change (29 March 2026, 01:00 GMT -> 02:00 BST): judged on UK local time,
  // whatever zone the caller passes.
  assert.equal(isOccasionPeak(DateTime.fromISO('2026-03-29T06:30:00Z')), true);   // 07:30 BST
  assert.equal(isOccasionPeak(DateTime.fromISO('2026-03-28T06:30:00Z')), false);  // 06:30 GMT
  assert.equal(isOccasionPeak(DateTime.fromISO('2026-10-25T09:30:00Z')), true);   // 09:30 GMT, day clocks go back
});


test('an occasion is likelier at peak than off-peak, from one shared rule', () => {
  const occasions = [{
    id: 'halloween',
    label: 'Halloween',
    prompt_hint: 'A light spooky touch.',
    requirements: REQUIREMENTS,
    schedule: { kind: 'annual_date', month: 10, day: 31 },
    probability: 0.4,
    max_uses_per_day: 8,
    source: SOURCE,
  }];
  // First random() picks the occasion; the second is the roll against its chance.
  const roll = value => { const values = [0, value]; return () => values.shift() ?? 0.99; };
  const peak = DateTime.fromISO('2026-10-31T19:00:00', { zone: 'Europe/London' });
  const offPeak = DateTime.fromISO('2026-10-31T14:00:00', { zone: 'Europe/London' });
  assert.equal(OCCASION_OFF_PEAK_FACTOR, 0.25);
  assert.equal(makeStore(documentWith({ occasions }), roll(0.39)).store.select(peak, [])?.id, 'halloween');
  assert.equal(makeStore(documentWith({ occasions }), roll(0.39)).store.select(offPeak, []), null);
  assert.equal(makeStore(documentWith({ occasions }), roll(0.09)).store.select(offPeak, [])?.id, 'halloween');
  // The caller's zone doesn't matter: 18:00 UTC on 31 Oct is 18:00 GMT, peak.
  assert.equal(makeStore(documentWith({ occasions }), roll(0.39)).store
    .select(DateTime.fromISO('2026-10-31T18:00:00Z'), [])?.id, 'halloween');
});


test('an occasion may be used up to 8 times a day, not 9', () => {
  const occasion = cap => documentWith({ occasions: [{
    id: 'big-day', label: 'Big day', prompt_hint: 'Hint.', requirements: REQUIREMENTS,
    schedule: { kind: 'annual_date', month: 11, day: 5 }, probability: 0.4,
    max_uses_per_day: cap, source: SOURCE,
  }] });
  assert.equal(validateEditorialDocument(occasion(8)).occasions[0].max_uses_per_day, 8);
  assert.throws(() => validateEditorialDocument(occasion(9)), /max_uses_per_day/);
});


test('a First company fact is never offered for a Stagecoach bus, even unscoped', () => {
  const today = '2026-09-30';
  const claim = { claim: 'First Bus adjusted operating profit rose 7% to £102.8 million.' };
  assert.equal(editorialApplies(claim, today, { operatorRef: 'FBRI', line: '72' }), true);
  assert.equal(editorialApplies(claim, today, { operatorRef: 'SSWL', line: '10' }), false);
  assert.equal(editorialApplies(claim, today, { operatorRef: 'SCGL', line: '620' }), false);
  const stagecoach = { claim: 'Stagecoach reported rising passenger numbers.' };
  assert.equal(editorialApplies(stagecoach, today, { operatorRef: 'SSWL', line: '10' }), true);
  assert.equal(editorialApplies(stagecoach, today, { operatorRef: 'FBRI', line: '72' }), false);
});
