import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { DateTime } from 'luxon';

import {
  EditorialContextStore,
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


test('the checked-in editorial context is valid and contains no Bee Network claims', () => {
  const path = join(process.cwd(), 'data', 'editorial-context.json');
  const raw = readFileSync(path, 'utf8');
  assert.doesNotMatch(raw, /Bee Network/i);
  const document = validateEditorialDocument(JSON.parse(raw));
  assert.equal(document.facts.length, 9);
  assert.equal(document.occasions.length, 8);
  assert.equal(document.news.length, 1);
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


test('a deferred hook is not consumed and becomes eligible after its short sleep', () => {
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
  const now = DateTime.fromISO('2026-07-23T10:00:00', { zone: 'Europe/London' });
  const selected = fixture.store.select(now, []);
  assert.equal(selected?.id, fact.id);
  fixture.store.recordDeferredPost(selected, now, 6);

  assert.equal(fixture.store.select(now.plus({ hours: 5 }), []), null);
  assert.equal(fixture.store.select(now.plus({ hours: 7 }), [])?.id, fact.id);

  const usage = JSON.parse(readFileSync(fixture.usagePath, 'utf8'));
  assert.equal(usage.items[fact.id], undefined);
  assert.match(usage.deferrals[fact.id], /Z$/);

  const reloaded = new EditorialContextStore(
    fixture.contextPath,
    fixture.usagePath,
    () => 0,
  );
  assert.equal(reloaded.select(now.plus({ hours: 5 }), []), null);
  assert.equal(reloaded.select(now.plus({ hours: 7 }), [])?.id, fact.id);
});
