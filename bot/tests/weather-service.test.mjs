import assert from 'node:assert/strict';
import test from 'node:test';
import { WeatherService } from '../dist/services/weather-service.js';

const config = { apiKey: 'test-key', baseUrl: 'https://weather.example/current', bristolLat: 51.45, bristolLon: -2.59 };
const observation = () => ({ dt: Math.floor(Date.now() / 1000), name: 'Bath',
  main: { temp: 12, feels_like: 10, humidity: 80 }, weather: [{ description: 'light rain' }], wind: { speed: 2, deg: 180 } });

test('weather uses the requested area, caches by area and makes only one bounded request', async () => {
  const requests = [];
  const weather = new WeatherService(config, async (url, options) => {
    requests.push({ url: new URL(url), options });
    return { ok: true, json: async () => observation() };
  });
  const bath = { latitude: 51.381, longitude: -2.361 };
  const first = await weather.getCurrentWeather(bath);
  assert.match(first, /near Bath at .*(?:BST|GMT): 12°C \(feels like 10°C\), with light rain/);
  assert.equal(await weather.getCurrentWeather(bath), first);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url.searchParams.get('lat'), '51.38');
  assert.equal(requests[0].url.searchParams.get('lon'), '-2.36');
  assert.deepEqual(requests[0].options, { timeoutMs: 5000, retries: 0 });
  await weather.getCurrentWeather({ latitude: 51.45, longitude: -2.59 });
  assert.equal(requests.length, 2, 'Bath observations must not be reused for Bristol');
});

test('stale, future, malformed and failed weather is omitted without preventing a post', async () => {
  for (const patch of [{ dt: 1 }, { dt: Date.now() / 1000 + 3600 }, { dt: undefined }, { main: {} }]) {
    const weather = new WeatherService(config, async () => ({ ok: true, json: async () => ({ ...observation(), ...patch }) }));
    assert.equal(await weather.getCurrentWeather(), null);
  }
  const failing = new WeatherService(config, async () => { throw new Error('offline'); });
  assert.equal(await failing.getCurrentWeather(), null);
  const unconfigured = new WeatherService({ ...config, apiKey: '' }, async () => { assert.fail('must not request without a key'); });
  assert.equal(await unconfigured.getCurrentWeather(), null);
});

test('a stalled weather response cannot hold up posting past the overall deadline', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const weather = new WeatherService(config, async () => ({ ok: true, json: () => new Promise(() => {}) }));
  const result = weather.getCurrentWeather();
  t.mock.timers.tick(6000);
  assert.equal(await result, null);
});
