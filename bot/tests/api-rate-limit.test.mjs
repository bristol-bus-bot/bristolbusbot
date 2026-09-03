import assert from 'node:assert/strict';
import test from 'node:test';

import express from 'express';

import {
  createSystemCommandRateLimiter,
  SYSTEM_COMMAND_RATE_LIMIT,
} from '../dist/api/routes.js';

test('system-command middleware bounds repeated authenticated work', async (t) => {
  const app = express();
  app.get('/command', createSystemCommandRateLimiter(), (_req, res) => {
    res.status(204).end();
  });

  const server = app.listen(0, '127.0.0.1');
  await new Promise((resolve) => server.once('listening', resolve));
  t.after(() => new Promise((resolve, reject) => {
    server.close((error) => error ? reject(error) : resolve());
  }));

  const address = server.address();
  assert.ok(address && typeof address === 'object');
  const url = `http://127.0.0.1:${address.port}/command`;

  for (let request = 0; request < SYSTEM_COMMAND_RATE_LIMIT; request += 1) {
    const response = await fetch(url);
    assert.equal(response.status, 204);
  }

  const blocked = await fetch(url);
  assert.equal(blocked.status, 429);
  assert.match(blocked.headers.get('retry-after') ?? '', /^\d+$/);
  assert.deepEqual(await blocked.json(), {
    success: false,
    error: 'Control command rate limit exceeded. Try again shortly.',
  });
});
