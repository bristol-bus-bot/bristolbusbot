import assert from 'node:assert/strict';
import test from 'node:test';

import { botSaidSvg, busWeekSvg, manifest, validatePack, weeklyDaysSvg, weeklyDistributionSvg, wrapWords, xml } from '../generate-pack.mjs';

const pack = {
  botSaid: { postText: 'A bus & its timetable <disagree>.', postUrl: 'https://bsky.app/x', route: '75', stop: 'Centre', observedAt: '2026-07-28T12:00:00Z', delayMinutes: 5 },
  busWeek: {
    startDate: '2026-07-20', endDate: '2026-07-26',
    onTimePct: 67.4, onTimeReadings: 809, readings: 1200,
    serviceDays: 7, daily: [61, 62, 63, 64, 65, 66, 67],
    distribution: {
      binEdgesSeconds: [-600, -300, -180, -120, -60, 0, 60, 120, 180, 240, 300, 360, 480, 600, 900, 1200],
      counts: [5, 10, 15, 20, 30, 100, 120, 140, 150, 149, 30, 120, 200, 50, 30, 20, 11],
      medianDelaySeconds: 120, p10DelaySeconds: -120, p90DelaySeconds: 480,
    },
  },
};

test('XML and wrapping keep untrusted public text inside the SVG', () => {
  assert.equal(xml('A&B<'), 'A&amp;B&lt;');
  assert.deepEqual(wrapWords('one two three four', 7), ['one two', 'three', 'four']);
  const svg = botSaidSvg(pack.botSaid);
  assert.match(svg, /A bus &amp; its/);
  assert.doesNotMatch(svg, /<disagree>/);
  assert.match(svg, /fill="#0d0f11"/);
  assert.match(svg, /class="matrix" font-size="20" font-weight="700" letter-spacing="2" fill="#34d399">LIVE DATA/);
  assert.match(svg, /CURRENT OBSERVATION/);
});

test('both cards use the required Instagram portrait dimensions', () => {
  assert.match(botSaidSvg(pack.botSaid), /width="1080" height="1350"/);
  assert.match(busWeekSvg(pack.busWeek), /width="1080" height="1350"/);
  assert.match(weeklyDaysSvg(pack.busWeek), /width="1080" height="1350"/);
  assert.match(weeklyDistributionSvg(pack.busWeek), /width="1080" height="1350"/);
});

test('weekly card gates and manifest preserve facts', () => {
  validatePack(pack);
  assert.throws(() => validatePack({ ...pack, busWeek: { ...pack.busWeek, readings: 999 } }), /1,000/);
  assert.throws(() => validatePack({
    ...pack,
    busWeek: { ...pack.busWeek, onTimeReadings: 808 },
  }), /match onTimeReadings/);
  const output = manifest(pack, {
    botSaid: 'one.jpg', weeklyHeadline: 'two.jpg',
    weeklyDays: 'three.jpg', weeklyDistribution: 'four.jpg',
  });
  assert.equal(output.drafts[1].sources.dailyOnTimePct.length, 7);
  assert.match(output.drafts[0].caption, /A bus & its timetable/);
  assert.match(busWeekSvg(pack.busWeek), /33 in every 100/);
  assert.equal(output.drafts[1].slides.length, 3);
  assert.match(output.drafts[1].slides[0].altText, /100 squares/);
  assert.match(weeklyDaysSvg(pack.busWeek), /Best day: Sunday/);
  assert.match(weeklyDaysSvg(pack.busWeek), /Worst: Monday/);
  assert.match(weeklyDistributionSvg(pack.busWeek), /Typical result:/);
  assert.match(weeklyDistributionSvg(pack.busWeek), /8 in 10 readings/);
});
