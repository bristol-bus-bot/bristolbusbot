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


test('shared event ingest does not require a duplicate BODS credential', () => {
  withEnv({
    INGEST_MODE: 'events',
    BODS_API_KEY: undefined,
    BSKY_HANDLE: 'example.test',
    BSKY_APP_PASSWORD: 'test-only-value', // scan-secrets: allow
  }, () => assert.doesNotThrow(() => validateConfig(loadConfig())));
});


test('explicit direct SIRI ingest still requires BODS credentials', () => {
  withEnv({
    INGEST_MODE: 'siri',
    BODS_API_KEY: undefined,
    BSKY_HANDLE: 'example.test',
    BSKY_APP_PASSWORD: 'test-only-value', // scan-secrets: allow
  }, () => assert.throws(
    () => validateConfig(loadConfig()),
    /BODS_API_KEY/,
  ));
});
