import assert from 'node:assert/strict';
import test from 'node:test';

import { botSaidSvg, busWeekSvg, manifest, quoteLayout, validatePack, weeklyDaysSvg, weeklyDistributionSvg, weeklyOperatorsSvg, weeklyPowertrainSvg, weeklyTargetSvg, wrapWords, xml } from '../generate-pack.mjs';

const pack = {
  botSaid: { postText: 'A bus & its timetable <disagree>.', postUrl: 'https://bsky.app/x', route: '75', stop: 'Centre', observedAt: '2026-07-28T12:00:00Z', delayMinutes: 5, operatorRef: 'FBRI', operatorName: 'First Bristol' },
  busWeek: {
    operatorCode: 'FBRI', operatorName: 'First Bristol',
    operatorComparison: [
      { operatorCode: 'FBRI', operatorName: 'First Bristol', readings: 1200, onTime: 809, onTimePct: 67.4 },
      { operatorCode: 'SCGL', operatorName: 'Stagecoach West', readings: 600, onTime: 420, onTimePct: 70.0 },
    ],
    startDate: '2026-07-20', endDate: '2026-07-26',
    onTimePct: 67.4, onTimeReadings: 809, readings: 1200,
    targetPct: 82, targetLabel: 'latest WECA area target', targetGapPoints: 14.6,
    longTermTargetPct: 95, longTermTargetLabel: 'WECA 2030 goal',
    longTermTargetGapPoints: 27.6,
    powertrain: {
      identifiedReadings: 1175, unidentifiedReadings: 25,
      electric: { readings: 470, onTime: 300, sharePct: 40, onTimePct: 63.8 },
      dieselOther: { readings: 705, onTime: 490, sharePct: 60, onTimePct: 69.5 },
      onTimeDifferencePoints: -5.7,
    },
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
  assert.match(svg, /FIRST BRISTOL · LIVE DATA/);
  assert.match(svg, /CURRENT OBSERVATION/);
});

test('both cards use the required Instagram portrait dimensions', () => {
  assert.match(botSaidSvg(pack.botSaid), /width="1080" height="1350"/);
  assert.match(busWeekSvg(pack.busWeek), /width="1080" height="1350"/);
  assert.match(weeklyTargetSvg(pack.busWeek), /width="1080" height="1350"/);
  assert.match(weeklyDaysSvg(pack.busWeek), /width="1080" height="1350"/);
  assert.match(weeklyDistributionSvg(pack.busWeek), /width="1080" height="1350"/);
  assert.match(weeklyPowertrainSvg(pack.busWeek), /width="1080" height="1350"/);
  assert.match(weeklyOperatorsSvg(pack.busWeek), /width="1080" height="1350"/);
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
    weeklyTarget: 'three.jpg', weeklyDays: 'four.jpg',
    weeklyDistribution: 'five.jpg', weeklyPowertrain: 'six.jpg',
    weeklyOperators: 'seven.jpg',
  });
  assert.equal(output.drafts[1].sources.dailyOnTimePct.length, 7);
  assert.match(output.drafts[0].caption, /A bus & its timetable/);
  assert.match(busWeekSvg(pack.busWeek), /33 in every 100/);
  assert.doesNotMatch(busWeekSvg(pack.busWeek), /TARGET/);
  assert.match(weeklyTargetSvg(pack.busWeek), /14.6 points short/);
  assert.match(weeklyTargetSvg(pack.busWeek), /82% TARGET/);
  assert.equal(output.drafts[1].slides.length, 6);
  assert.match(output.drafts[1].slides[0].altText, /100 squares/);
  assert.match(weeklyDaysSvg(pack.busWeek), /Best day: Sunday/);
  assert.match(weeklyDaysSvg(pack.busWeek), /Worst: Monday/);
  assert.match(weeklyDistributionSvg(pack.busWeek), /1 in 10 was over/);
  assert.match(weeklyDistributionSvg(pack.busWeek), /8.0 min late/);
  assert.match(weeklyDistributionSvg(pack.busWeek), /8 in 10 readings/);
  assert.doesNotMatch(weeklyDistributionSvg(pack.busWeek), /WECA TARGET/);
  assert.match(weeklyPowertrainSvg(pack.busWeek), /40.0% of readings/);
  assert.match(weeklyPowertrainSvg(pack.busWeek), /DIESEL \/ OTHER/);
  assert.match(weeklyPowertrainSvg(pack.busWeek), /5.7 percentage points apart/);
  assert.match(weeklyOperatorsSvg(pack.busWeek), /Same week/);
  assert.match(weeklyOperatorsSvg(pack.busWeek), /Stagecoach West/);
  assert.match(weeklyOperatorsSvg(pack.busWeek), /THIS ROUNDUP/);
  assert.match(botSaidSvg(pack.botSaid), /FIRST BRISTOL · LIVE DATA/);
  assert.match(output.drafts[1].caption, /14.6 points below/);
  assert.match(output.drafts[1].caption, /40.0% of identified readings/);
});

test('single-card mode needs no weekly data and emits one draft', () => {
  const single = { generatedAt: '2026-08-02T12:00:00Z', botSaid: pack.botSaid };
  validatePack(single, 'bot-said');
  assert.throws(() => validatePack(single), /busWeek/);
  const output = manifest(single, { botSaid: 'one.jpg' }, 'bot-said');
  assert.equal(output.drafts.length, 1);
  assert.equal(output.drafts[0].kind, 'bot-said');
  assert.equal(output.drafts[0].file, 'one.jpg');
});

test('quote autofit never truncates and refuses illegible text', () => {
  const long = Array.from({ length: 36 }, (_, index) => `word${index}`).join(' ');
  const layout = quoteLayout(long);
  assert.ok(layout.fontSize >= 42);
  assert.ok(layout.lines.length <= 7);
  assert.equal(layout.lines.join(' '), long);
  assert.throws(() => quoteLayout('word '.repeat(500)), /minimum legible/);
  assert.doesNotMatch(botSaidSvg({ ...pack.botSaid, postText: long }), /…/);
});
