import assert from 'node:assert/strict';
import test from 'node:test';

import { emDashesToCommas } from '../dist/services/ai-commentary.js';
import { BOT_VOICE } from '../dist/services/story-brief.js';

test('posts never keep an em-dash', () => {
  assert.equal(emDashesToCommas('The 72 is late \u2014 again.'), 'The 72 is late, again.');
  assert.equal(emDashesToCommas('Late\u2014again\u2014still.'), 'Late, again, still.');
  assert.equal(emDashesToCommas('On time \u2014.'), 'On time.');
  assert.equal(emDashesToCommas('No dashes here.'), 'No dashes here.');
  assert.equal(emDashesToCommas('Range 7\u201310am stays.'), 'Range 7\u201310am stays.');
});

test('the writer is told not to use em-dashes, and its own voice has none', () => {
  assert.match(BOT_VOICE, /Never use em-dashes/);
  assert.doesNotMatch(BOT_VOICE, /\u2014/);
});
