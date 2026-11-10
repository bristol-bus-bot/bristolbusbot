import assert from 'node:assert/strict';
import test from 'node:test';

import { loadConfig, validateConfig } from '../dist/config/app-config.js';


function withEnv(values, callback) {
  const previous = new Map();
  for (const [key, value] of Object.entries(values)) {
    previous.set(key, process.env[key]);
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
  try {
    callback();
  } finally {
    for (const [key, value] of previous) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
}


test('the bot needs no BODS credential: the collector is the only BODS client', () => {
  withEnv({
    INGEST_MODE: 'events',
    BODS_API_KEY: undefined,
    BSKY_HANDLE: 'example.test',
    BSKY_APP_PASSWORD: 'test-only-value', // scan-secrets: allow
  }, () => {
    const config = loadConfig();
    assert.doesNotThrow(() => validateConfig(config));
    assert.equal('siri' in config, false);
  });
});


test('a leftover direct-SIRI ingest setting stops the bot at start-up', () => {
  withEnv({
    INGEST_MODE: 'siri',
    BSKY_HANDLE: 'example.test',
    BSKY_APP_PASSWORD: 'test-only-value', // scan-secrets: allow
  }, () => assert.throws(
    () => validateConfig(loadConfig()),
    /INGEST_MODE=siri is not supported/,
  ));
});


test('single-writer Gemini 3.6 is the only commentary pipeline', () => {
  withEnv({
    AI_MODEL: undefined,
    AI_COMMENTARY_PIPELINE: undefined,
  }, () => {
    const config = loadConfig();
    assert.equal(config.ai.model, 'gemini-3.6-flash');
    assert.equal('pipeline' in config.ai, false);
  });
});


test('the old explicit single setting still starts; a leftover legacy setting stops the bot', () => {
  const base = { INGEST_MODE: 'events', BSKY_HANDLE: 'example.test',
                 BSKY_APP_PASSWORD: 'test-only-value' }; // scan-secrets: allow
  withEnv({ ...base, AI_COMMENTARY_PIPELINE: 'single' },
    () => assert.doesNotThrow(() => validateConfig(loadConfig())));
  withEnv({ ...base, AI_COMMENTARY_PIPELINE: 'legacy' },
    () => assert.throws(() => validateConfig(loadConfig()), /legacy writer has been removed/));
});


test('a malformed number setting stops the bot with a clear message (#106)', () => {
  for (const [name, value] of [['AI_DAILY_LIMIT', 'abc'], ['BLUESKY_DAILY_LIMIT', '-5'],
                               ['AI_TIMEOUT', '7.5'], ['PORT', '30 10']]) {
    withEnv({ [name]: value }, () => assert.throws(
      () => loadConfig(), new RegExp(`${name}=.* is not valid`), name));
  }
  withEnv({ AI_DAILY_LIMIT: '1500', BLUESKY_DAILY_LIMIT: undefined }, () => {
    const config = loadConfig();
    assert.equal(config.ai.dailyLimit, 1500);
    assert.equal(config.social.dailyLimit, 300);
  });
});
