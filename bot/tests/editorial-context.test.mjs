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


test('a month-long occasion can be used at most once per day', () => {
  const occasions = [{
    id: 'september-campaign',
    label: 'September campaign',
    prompt_hint: 'A restrained reference.',
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
