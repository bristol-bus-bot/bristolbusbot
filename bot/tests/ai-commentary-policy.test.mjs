import assert from 'node:assert/strict';
import test from 'node:test';

import {
  NEWS_EDITORIAL_VETO,
  containsSourceReference,
  isNewsEditorialVeto,
} from '../dist/services/ai-commentary.js';


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
