#!/usr/bin/env node
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Resvg } from '@resvg/resvg-js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WIDTH = 1080;
const HEIGHT = 1350;
const DELAY_BIN_EDGES_S = [
  -600, -300, -180, -120, -60, 0, 60, 120, 180,
  240, 300, 360, 480, 600, 900, 1200,
];
const COLORS = {
  bg: '#eef0f2', ink: '#14181d', muted: '#4a545f', line: '#c5ccd4',
  surface: '#ffffff', board: '#101112', amber: '#f59e0b',
  green: '#00703c', blue: '#1d70b8', red: '#d4351c', yellow: '#ffdd00',
  tintGreen: '#e6f0ea', tintBlue: '#e6eef6', cream: '#f7f6f3',
  boardInk: '#f4efe4', boardMuted: '#9b9588', liveGreen: '#34d399',
  earlyBlue: '#5b93c7', lateRed: '#ff6b57', paleLate: '#f7dcd7',
};

export function xml(value) {
  return String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;',
  })[character]);
}

export function wrapWords(value, maxCharacters, maxLines = 10) {
  const words = String(value ?? '').trim().split(/\s+/).filter(Boolean);
  const lines = [];
  let current = '';
  for (const word of words) {
    const candidate = current ? `${current} ${word}` : word;
    if (candidate.length <= maxCharacters || !current) {
      current = candidate;
    } else {
      lines.push(current);
      current = word;
    }
  }
  if (current) lines.push(current);
  if (lines.length <= maxLines) return lines;
  const clipped = lines.slice(0, maxLines);
  clipped[maxLines - 1] = clipped[maxLines - 1].replace(/[.,;:!?]?$/, '…');
  return clipped;
}

function textLines(lines, x, y, lineHeight, attributes = '') {
  return `<text x="${x}" y="${y}" ${attributes}>${lines.map((line, index) =>
    `<tspan x="${x}" dy="${index ? lineHeight : 0}">${xml(line)}</tspan>`).join('')}</text>`;
}

function fontCss() {
  return `
    text{font-family:"Google Sans Flex"}
    .mono{font-family:"Google Sans Code"}.matrix{font-family:"Bitcount Grid Double"}
  `;
}

function frame(title, kicker, body, css, titleAttributes = '') {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${WIDTH}" height="${HEIGHT}" viewBox="0 0 ${WIDTH} ${HEIGHT}">
    <style>${css}</style>
    <rect width="1080" height="1350" fill="${COLORS.bg}"/>
    <rect x="52" y="52" width="976" height="1246" rx="28" fill="${COLORS.surface}" stroke="${COLORS.line}" stroke-width="2"/>
    <text x="92" y="116" class="mono" font-size="24" font-weight="750" letter-spacing="4" fill="${COLORS.muted}">${xml(kicker)}</text>
    <text x="92" y="174" font-size="42" font-weight="800" ${titleAttributes}>${xml(title)}</text>
    ${body}
    <text x="92" y="1250" class="mono" font-size="21" font-weight="650" fill="${COLORS.muted}">bristolbuses.live</text>
    <circle cx="980" cy="1242" r="12" fill="${COLORS.green}"/>
  </svg>`;
}

function quoteLines(lines, x, y, lineHeight, fontSize) {
  const highlightFrom = Math.max(1, lines.length - 2);
  return `<text x="${x}" y="${y}" font-size="${fontSize}" font-weight="750" letter-spacing="-1.5">${lines.map((line, index) =>
    `<tspan x="${x}" dy="${index ? lineHeight : 0}" fill="${index >= highlightFrom ? COLORS.amber : COLORS.boardInk}">${xml(line)}</tspan>`).join('')}</text>`;
}

function delayX(seconds) {
  const minutes = Math.max(-5, Math.min(15, Number(seconds) / 60));
  return 82 + ((minutes + 5) / 20) * 916;
}

function recentObservationStrip(data) {
  const supplied = Array.isArray(data.recentDepartures) ? data.recentDepartures : [];
  const observations = supplied.length ? supplied.slice(-20) : [{
    delaySeconds: Number(data.delayMinutes || 0) * 60,
    isCurrent: true,
  }];
  const stacks = new Map();
  const points = observations.map((observation, index) => {
    const seconds = Number(observation.delaySeconds);
    const bucket = Math.round(seconds / 60);
    const stack = stacks.get(bucket) || 0;
    stacks.set(bucket, stack + 1);
    const x = delayX(seconds);
    const y = 1078 - Math.min(stack, 3) * 20;
    const colour = seconds < -60 ? COLORS.earlyBlue
      : seconds <= 359 ? COLORS.liveGreen : COLORS.lateRed;
    return { x, y, colour, current: Boolean(observation.isCurrent), index };
  });
  const ordinary = points.filter(point => !point.current).map(point =>
    `<circle cx="${point.x.toFixed(1)}" cy="${point.y}" r="7" fill="${point.colour}"/>`).join('');
  const current = points.find(point => point.current) || points.at(-1);
  const currentPoint = current ? `
    <circle cx="${current.x.toFixed(1)}" cy="${current.y}" r="13" fill="${COLORS.boardInk}" stroke="${COLORS.amber}" stroke-width="5"/>
    <text x="${Math.max(150, Math.min(930, current.x)).toFixed(1)}" y="${current.y - 23}" text-anchor="middle" class="matrix" font-size="20" font-weight="700" fill="${COLORS.boardInk}">THIS ONE ▸</text>` : '';
  const label = observations.length > 1
    ? `LAST ${observations.length} READINGS · THIS STOP`
    : 'CURRENT OBSERVATION';
  return `
    <text x="82" y="962" class="matrix" font-size="22" font-weight="700" letter-spacing="2.4" fill="${COLORS.boardMuted}">${label}</text>
    <rect x="265.2" y="986" width="320.6" height="104" fill="rgba(52,211,153,.13)"/>
    <line x1="265.2" y1="986" x2="265.2" y2="1090" stroke="rgba(52,211,153,.65)" stroke-width="2"/>
    <line x1="82" y1="1090" x2="998" y2="1090" stroke="rgba(255,255,255,.18)" stroke-width="2"/>
    ${ordinary}${currentPoint}
    <text x="82" y="1132" class="mono" font-size="22" font-weight="700" fill="${COLORS.boardMuted}">5 min early</text>
    <text x="402" y="1132" class="mono" text-anchor="middle" font-size="22" font-weight="700" fill="${COLORS.liveGreen}">on time</text>
    <text x="998" y="1132" class="mono" text-anchor="end" font-size="22" font-weight="700" fill="${COLORS.boardMuted}">15 min late</text>`;
}

export function botSaidSvg(data, css = '') {
  const text = String(data.postText || '');
  const operator = String(data.operatorName || data.operatorRef || 'Operator unknown');
  const fontSize = text.length > 260 ? 52 : text.length > 180 ? 60 : 74;
  const maxCharacters = fontSize >= 70 ? 27 : fontSize >= 60 ? 33 : 38;
  const lines = wrapWords(text, maxCharacters, 7);
  const lineHeight = Math.round(fontSize * 1.16);
  const delay = Number(data.delayMinutes);
  const badge = Number.isFinite(delay)
    ? delay > 0 ? `+${delay} MIN` : delay < 0 ? `−${Math.abs(delay)} MIN` : 'ON TIME'
    : 'LIVE';
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${WIDTH}" height="${HEIGHT}" viewBox="0 0 ${WIDTH} ${HEIGHT}">
    <style>${css}</style>
    <defs><pattern id="led-dots" width="5" height="5" patternUnits="userSpaceOnUse"><circle cx="1.1" cy="1.1" r="1.1" fill="rgba(255,255,255,.045)"/></pattern></defs>
    <rect width="1080" height="1350" fill="#0d0f11"/>
    <rect width="1080" height="1350" fill="url(#led-dots)"/>
    <rect width="1080" height="12" fill="${COLORS.amber}"/>
    <rect x="82" y="82" width="158" height="96" rx="12" fill="${COLORS.yellow}"/>
    <text x="161" y="148" text-anchor="middle" font-size="58" font-weight="850" fill="#00512c">${xml(data.route || 'BUS')}</text>
    <text x="266" y="125" class="mono" font-size="26" font-weight="750" fill="#e8e2d5">@bristolbusbot.live</text>
    <circle cx="272" cy="158" r="6" fill="${COLORS.liveGreen}"/>
    <text x="291" y="165" class="matrix" font-size="20" font-weight="700" letter-spacing="1.6" fill="${COLORS.amber}">${xml(operator.toUpperCase())} · LIVE DATA</text>
    ${quoteLines(lines, 82, 304, lineHeight, fontSize)}
    ${recentObservationStrip(data)}
    <text x="82" y="1240" font-size="30" font-weight="750" fill="${COLORS.boardInk}">${xml(data.stop || 'Bristol')} · ${xml(formatDateTime(data.observedAt))}</text>
    <text x="82" y="1282" class="mono" font-size="24" font-weight="650" fill="${COLORS.boardMuted}">bristolbuses.live · public live data</text>
    <rect x="854" y="1228" width="144" height="54" rx="8" fill="${COLORS.amber}"/>
    <text x="926" y="1264" text-anchor="middle" class="mono" font-size="25" font-weight="800" fill="#0d0f11">${xml(badge)}</text>
  </svg>`;
}

function squareGrid(onTime) {
  const cell = 63;
  const gap = 10;
  return Array.from({ length: 100 }, (_, index) => {
    const x = 180 + (index % 10) * (cell + gap);
    const y = 382 + Math.floor(index / 10) * (cell + gap);
    if (index < onTime) return `<rect x="${x}" y="${y}" width="${cell}" height="${cell}" rx="5" fill="${COLORS.liveGreen}"/>`;
    return `<rect x="${x}" y="${y}" width="${cell}" height="${cell}" rx="5" fill="rgba(255,107,87,.12)" stroke="${COLORS.lateRed}" stroke-width="3"/>`;
  }).join('');
}

export function busWeekSvg(data, css = '') {
  const onTime = Math.max(0, Math.min(100, Math.round(Number(data.onTimePct))));
  const notOnTime = 100 - onTime;
  const scope = String(data.operatorName || 'Bristol buses');
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${WIDTH}" height="${HEIGHT}" viewBox="0 0 ${WIDTH} ${HEIGHT}">
    <style>${css}</style>
    <defs><pattern id="led-dots-b" width="5" height="5" patternUnits="userSpaceOnUse"><circle cx="1.1" cy="1.1" r="1.1" fill="rgba(255,255,255,.045)"/></pattern></defs>
    <rect width="1080" height="1350" fill="#0d0f11"/>
    <rect width="1080" height="1350" fill="url(#led-dots-b)"/>
    <rect width="1080" height="12" fill="${COLORS.amber}"/>
    <text x="72" y="114" class="matrix" font-size="25" font-weight="750" letter-spacing="2.2" fill="${COLORS.amber}">OPERATOR: ${xml(scope.toUpperCase())}</text>
    <text x="72" y="220" font-size="86" font-weight="800" letter-spacing="-3" fill="${COLORS.boardInk}">${notOnTime} in every 100</text>
    <text x="72" y="310" font-size="86" font-weight="800" letter-spacing="-3" fill="${COLORS.boardInk}">weren't on time.</text>
    ${squareGrid(onTime)}
    <rect x="72" y="1148" width="28" height="28" rx="5" fill="${COLORS.liveGreen}"/>
    <text x="116" y="1172" font-size="26" font-weight="700" fill="${COLORS.boardInk}">${onTime} on time</text>
    <rect x="315" y="1148" width="28" height="28" rx="5" fill="rgba(255,107,87,.12)" stroke="${COLORS.lateRed}" stroke-width="3"/>
    <text x="359" y="1172" font-size="24" font-weight="700" fill="${COLORS.boardInk}">${notOnTime} not on time</text>
    <text x="570" y="1172" class="mono" font-size="20" font-weight="650" fill="${COLORS.boardMuted}">each square = 1% · ${xml(Number(data.readings).toLocaleString('en-GB'))} readings</text>
    <line x1="72" y1="1218" x2="1008" y2="1218" stroke="rgba(255,255,255,.16)" stroke-width="2"/>
    <text x="72" y="1270" class="mono" font-size="25" font-weight="650" fill="${COLORS.boardMuted}">bristolbuses.live</text>
    <text x="1008" y="1270" text-anchor="end" class="mono" font-size="25" font-weight="750" fill="${COLORS.boardInk}">${Number(data.onTimePct).toFixed(1)}% · ${xml(data.serviceDays)} days measured</text>
  </svg>`;
}

export function weeklyTargetSvg(data, css = '') {
  const actual = Number(data.onTimePct);
  const target = Number(data.targetPct);
  const longTarget = Number(data.longTermTargetPct);
  const gap = Number(data.targetGapPoints);
  const longGap = Number(data.longTermTargetGapPoints);
  const barX = 100;
  const barWidth = 880;
  const actualX = barX + barWidth * actual / 100;
  const targetX = barX + barWidth * target / 100;
  const longTargetX = barX + barWidth * longTarget / 100;
  const scope = String(data.operatorName || 'Bristol buses');
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${WIDTH}" height="${HEIGHT}" viewBox="0 0 ${WIDTH} ${HEIGHT}">
    <style>${css}</style>
    <defs><pattern id="led-dots-target" width="5" height="5" patternUnits="userSpaceOnUse"><circle cx="1.1" cy="1.1" r="1.1" fill="rgba(255,255,255,.045)"/></pattern></defs>
    <rect width="1080" height="1350" fill="#0d0f11"/>
    <rect width="1080" height="1350" fill="url(#led-dots-target)"/>
    <rect width="1080" height="12" fill="${COLORS.amber}"/>
    <text x="72" y="112" class="matrix" font-size="25" font-weight="750" letter-spacing="2.2" fill="${COLORS.amber}">OPERATOR: ${xml(scope.toUpperCase())} · WECA TARGET</text>
    <text x="72" y="228" font-size="86" font-weight="800" letter-spacing="-3" fill="${COLORS.boardInk}">${gap.toFixed(1)} points short.</text>
    <text x="72" y="302" font-size="30" fill="${COLORS.boardMuted}">Latest published annual area target</text>
    <rect x="72" y="372" width="936" height="530" rx="14" fill="#15181b" stroke="rgba(255,255,255,.12)" stroke-width="2"/>
    <text x="100" y="438" class="mono" font-size="22" font-weight="750" fill="${COLORS.boardMuted}">0–100% ON-TIME SCALE</text>
    <rect x="${barX}" y="548" width="${barWidth}" height="72" rx="8" fill="#666c73"/>
    <rect x="${barX}" y="548" width="${(barWidth * actual / 100).toFixed(1)}" height="72" rx="8" fill="${COLORS.liveGreen}"/>
    <line x1="${targetX.toFixed(1)}" y1="514" x2="${targetX.toFixed(1)}" y2="654" stroke="${COLORS.amber}" stroke-width="8"/>
    <line x1="${longTargetX.toFixed(1)}" y1="530" x2="${longTargetX.toFixed(1)}" y2="638" stroke="${COLORS.boardInk}" stroke-width="4"/>
    <text x="${actualX.toFixed(1)}" y="682" text-anchor="middle" class="mono" font-size="24" font-weight="800" fill="${COLORS.liveGreen}">${actual.toFixed(1)}% ACTUAL</text>
    <text x="${targetX.toFixed(1)}" y="506" text-anchor="middle" class="mono" font-size="24" font-weight="800" fill="${COLORS.amber}">${target.toFixed(0)}% TARGET</text>
    <text x="${longTargetX.toFixed(1)}" y="682" text-anchor="end" class="mono" font-size="21" font-weight="750" fill="${COLORS.boardInk}">${longTarget.toFixed(0)}% BY 2030</text>
    <line x1="100" y1="744" x2="980" y2="744" stroke="rgba(255,255,255,.14)" stroke-width="2"/>
    <text x="100" y="824" class="mono" font-size="52" font-weight="850" fill="${COLORS.liveGreen}">${actual.toFixed(1)}%</text>
    <text x="100" y="866" font-size="24" fill="${COLORS.boardMuted}">${xml(scope)} actual</text>
    <text x="430" y="824" class="mono" font-size="52" font-weight="850" fill="${COLORS.amber}">${target.toFixed(0)}%</text>
    <text x="430" y="866" font-size="24" fill="${COLORS.boardMuted}">latest WECA target</text>
    <text x="745" y="824" class="mono" font-size="52" font-weight="850" fill="${COLORS.boardInk}">${longTarget.toFixed(0)}%</text>
    <text x="745" y="866" font-size="24" fill="${COLORS.boardMuted}">goal by 2030</text>
    <rect x="72" y="962" width="936" height="144" rx="12" fill="rgba(245,158,11,.10)" stroke="rgba(245,158,11,.42)" stroke-width="2"/>
    <text x="104" y="1024" class="matrix" font-size="23" font-weight="750" fill="${COLORS.amber}">THE LONG-TERM GAP</text>
    <text x="104" y="1080" font-size="38" font-weight="750" fill="${COLORS.boardInk}">${longGap.toFixed(1)} percentage points to the 2030 goal.</text>
    <line x1="72" y1="1218" x2="1008" y2="1218" stroke="rgba(255,255,255,.16)" stroke-width="2"/>
    <text x="72" y="1270" class="mono" font-size="25" font-weight="650" fill="${COLORS.boardMuted}">bristolbuses.live</text>
    <text x="1008" y="1270" text-anchor="end" class="mono" font-size="23" font-weight="650" fill="${COLORS.boardMuted}">on time: 1 min early to 5 min 59 s late</text>
  </svg>`;
}

function dailySeries(data) {
  const start = new Date(`${String(data.startDate).slice(0, 10)}T12:00:00Z`);
  return data.daily.map((raw, index) => {
    const date = new Date(start);
    date.setUTCDate(start.getUTCDate() + index);
    return {
      value: Number(raw),
      day: new Intl.DateTimeFormat('en-GB', {
        weekday: 'short', timeZone: 'UTC',
      }).format(date),
      dayLong: new Intl.DateTimeFormat('en-GB', {
        weekday: 'long', timeZone: 'UTC',
      }).format(date),
    };
  });
}

export function weeklyDaysSvg(data, css = '') {
  const days = dailySeries(data);
  const values = days.map(day => day.value);
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const axisMinimum = Math.max(0, Math.floor(minimum) - 1);
  const axisMaximum = Math.min(100, Math.ceil(maximum) + 1);
  const axisRange = Math.max(1, axisMaximum - axisMinimum);
  const average = Number(data.onTimePct);
  const scope = String(data.operatorName || 'Bristol buses');
  const highestIndex = values.indexOf(maximum);
  const lowestIndex = values.indexOf(minimum);
  const yFor = value => 725 - ((value - axisMinimum) / axisRange) * 255;
  const averageY = yFor(average);
  const plot = days.map((day, index) => {
    const x = 132 + index * 136;
    const y = yFor(day.value);
    const highlighted = index === highestIndex || index === lowestIndex;
    const colour = index === lowestIndex ? COLORS.lateRed : COLORS.liveGreen;
    const dayLabel = index === highestIndex ? `${day.day} best`
      : index === lowestIndex ? `${day.day} worst` : day.day;
    return `<line x1="${x}" y1="725" x2="${x}" y2="${y.toFixed(1)}" stroke="rgba(255,255,255,.36)" stroke-width="4"/>
      <circle cx="${x}" cy="${y.toFixed(1)}" r="${highlighted ? 24 : 18}" fill="${colour}"${highlighted ? ` stroke="${colour}" stroke-opacity=".15" stroke-width="14"` : ''}/>
      <text x="${x}" y="792" text-anchor="middle" class="mono" font-size="27" font-weight="800" fill="${COLORS.boardInk}">${day.value.toFixed(1)}</text>
      <text x="${x}" y="834" text-anchor="middle" font-size="21" font-weight="${highlighted ? 800 : 650}" fill="${index === highestIndex ? COLORS.liveGreen : index === lowestIndex ? COLORS.lateRed : COLORS.boardMuted}">${dayLabel}</text>`;
  }).join('');
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${WIDTH}" height="${HEIGHT}" viewBox="0 0 ${WIDTH} ${HEIGHT}">
    <style>${css}</style>
    <defs><pattern id="led-dots-c" width="5" height="5" patternUnits="userSpaceOnUse"><circle cx="1.1" cy="1.1" r="1.1" fill="rgba(255,255,255,.045)"/></pattern></defs>
    <rect width="1080" height="1350" fill="#0d0f11"/>
    <rect width="1080" height="1350" fill="url(#led-dots-c)"/>
    <rect width="1080" height="12" fill="${COLORS.amber}"/>
    <text x="72" y="112" class="matrix" font-size="25" font-weight="750" letter-spacing="2.2" fill="${COLORS.amber}">OPERATOR: ${xml(scope.toUpperCase())} · DAILY RESULTS</text>
    <text x="72" y="216" font-size="72" font-weight="800" letter-spacing="-2.5" fill="${COLORS.boardInk}">Best day: ${days[highestIndex].dayLong}.</text>
    <text x="72" y="294" font-size="72" font-weight="800" letter-spacing="-2.5" fill="${COLORS.boardInk}">Worst: ${days[lowestIndex].dayLong}.</text>
    <rect x="72" y="360" width="936" height="520" rx="14" fill="#15181b" stroke="rgba(255,255,255,.12)" stroke-width="2"/>
    <text x="106" y="414" class="mono" font-size="23" font-weight="750" fill="${COLORS.boardMuted}">ON TIME EACH DAY · SHOWING ${axisMinimum}–${axisMaximum}%</text>
    <line x1="106" y1="725" x2="974" y2="725" stroke="rgba(255,255,255,.36)" stroke-width="2"/>
    <line x1="106" y1="${averageY.toFixed(1)}" x2="974" y2="${averageY.toFixed(1)}" stroke="${COLORS.boardMuted}" stroke-width="3" stroke-dasharray="10 9"/>
    <rect x="774" y="${(averageY - 34).toFixed(1)}" width="200" height="30" fill="#15181b"/>
    <text x="974" y="${(averageY - 10).toFixed(1)}" text-anchor="end" class="mono" font-size="21" font-weight="750" fill="${COLORS.boardMuted}">WEEK ${average.toFixed(1)}%</text>
    ${plot}
    <rect x="72" y="930" width="936" height="166" rx="12" fill="#15181b" stroke="rgba(255,255,255,.12)" stroke-width="2"/>
    <text x="100" y="977" class="mono" font-size="19" font-weight="750" fill="${COLORS.boardMuted}">WHOLE 0–100% SCALE</text>
    <rect x="100" y="1000" width="430" height="34" rx="5" fill="#666c73"/>
    <rect x="100" y="1000" width="${(430 * average / 100).toFixed(1)}" height="34" rx="5" fill="${COLORS.liveGreen}"/>
    <text x="100" y="1071" class="mono" font-size="20" font-weight="750" fill="${COLORS.boardInk}">${average.toFixed(1)}% OBSERVED</text>
    <text x="580" y="1013" font-size="28" font-weight="750" fill="${COLORS.boardInk}">${xml(Number(data.readings).toLocaleString('en-GB'))} readings</text>
    <text x="580" y="1057" font-size="23" fill="${COLORS.boardMuted}">over ${xml(data.serviceDays)} service days</text>
    <line x1="72" y1="1218" x2="1008" y2="1218" stroke="rgba(255,255,255,.16)" stroke-width="2"/>
    <text x="72" y="1270" class="mono" font-size="25" font-weight="650" fill="${COLORS.boardMuted}">bristolbuses.live</text>
    <text x="1008" y="1270" text-anchor="end" class="mono" font-size="25" font-weight="750" fill="${COLORS.boardInk}">${xml(formatDate(data.startDate))}–${xml(formatDate(data.endDate))}</text>
  </svg>`;
}

function signedMinutes(seconds) {
  const minutes = Number(seconds) / 60;
  if (minutes === 0) return '0';
  const value = Number.isInteger(Math.abs(minutes))
    ? Math.abs(minutes).toFixed(0) : Math.abs(minutes).toFixed(1);
  return `${minutes > 0 ? '+' : '−'}${value}`;
}

function timingWords(seconds) {
  const minutes = Number(seconds) / 60;
  if (minutes === 0) return 'on time';
  const value = Number.isInteger(Math.abs(minutes))
    ? Math.abs(minutes).toFixed(0) : Math.abs(minutes).toFixed(1);
  return `${value} min ${minutes > 0 ? 'late' : 'early'}`;
}

export function weeklyDistributionSvg(data, css = '') {
  const distribution = data.distribution;
  const counts = distribution.counts.map(Number);
  const maximum = Math.max(...counts, 1);
  const barWidth = (936 - (counts.length - 1) * 4) / counts.length;
  const onTimeStart = 5;
  const onTimeEnd = 12;
  const bandX = 72 + onTimeStart * (barWidth + 4);
  const bandWidth = (onTimeEnd - onTimeStart) * barWidth + (onTimeEnd - onTimeStart - 1) * 4;
  const bars = counts.map((count, index) => {
    const x = 72 + index * (barWidth + 4);
    const height = 380 * count / maximum;
    const colour = index < onTimeStart ? COLORS.earlyBlue
      : index < onTimeEnd ? COLORS.liveGreen : COLORS.lateRed;
    return `<rect x="${x.toFixed(1)}" y="${(866 - height).toFixed(1)}" width="${barWidth.toFixed(1)}" height="${height.toFixed(1)}" rx="3" fill="${colour}"/>`;
  }).join('');
  const median = Number(distribution.medianDelaySeconds);
  const typicalResult = timingWords(median);
  const middleRange = `${signedMinutes(distribution.p10DelaySeconds)} to ${signedMinutes(distribution.p90DelaySeconds)} min`;
  const p90Minutes = Math.abs(Number(distribution.p90DelaySeconds) / 60).toFixed(1);
  const scope = String(data.operatorName || 'Bristol buses');
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${WIDTH}" height="${HEIGHT}" viewBox="0 0 ${WIDTH} ${HEIGHT}">
    <style>${css}</style>
    <defs><pattern id="led-dots-d" width="5" height="5" patternUnits="userSpaceOnUse"><circle cx="1.1" cy="1.1" r="1.1" fill="rgba(255,255,255,.045)"/></pattern></defs>
    <rect width="1080" height="1350" fill="#0d0f11"/>
    <rect width="1080" height="1350" fill="url(#led-dots-d)"/>
    <rect width="1080" height="12" fill="${COLORS.amber}"/>
    <text x="72" y="112" class="matrix" font-size="24" font-weight="700" letter-spacing="2.3" fill="${COLORS.amber}">OPERATOR: ${xml(scope.toUpperCase())} · ${xml(Number(data.readings).toLocaleString('en-GB'))} READINGS</text>
    <text x="72" y="210" font-size="72" font-weight="800" letter-spacing="-2.5" fill="${COLORS.boardInk}">1 in 10 was over</text>
    <text x="72" y="286" font-size="72" font-weight="800" letter-spacing="-2.5" fill="${COLORS.boardInk}">${xml(p90Minutes)} min late.</text>
    <text x="72" y="350" font-size="28" fill="#b8b3a7">The bars show how early or late the readings were.</text>
    <rect x="${bandX.toFixed(1)}" y="430" width="${bandWidth.toFixed(1)}" height="436" fill="rgba(52,211,153,.12)"/>
    <line x1="${bandX.toFixed(1)}" y1="430" x2="${bandX.toFixed(1)}" y2="866" stroke="${COLORS.liveGreen}" stroke-width="3"/>
    ${bars}
    <line x1="72" y1="866" x2="1008" y2="866" stroke="rgba(255,255,255,.2)" stroke-width="2"/>
    <text x="72" y="918" class="mono" font-size="23" font-weight="750" fill="${COLORS.boardMuted}">10+ min early</text>
    <text x="${(bandX + bandWidth / 2).toFixed(1)}" y="918" text-anchor="middle" class="mono" font-size="23" font-weight="750" fill="${COLORS.liveGreen}">on time</text>
    <text x="1008" y="918" text-anchor="end" class="mono" font-size="23" font-weight="750" fill="${COLORS.boardMuted}">20+ min late</text>
    <text x="72" y="1070" class="mono" font-size="40" font-weight="800" fill="${COLORS.boardInk}">${xml(typicalResult)}</text>
    <text x="72" y="1112" font-size="23" font-weight="650" fill="${COLORS.boardMuted}">typical result</text>
    <text x="390" y="1070" class="mono" font-size="34" font-weight="800" fill="${COLORS.boardInk}">${xml(middleRange)}</text>
    <text x="390" y="1112" font-size="23" font-weight="650" fill="${COLORS.boardMuted}">8 in 10 readings</text>
    <text x="790" y="1070" class="mono" font-size="50" font-weight="800" fill="${COLORS.liveGreen}">${Number(data.onTimePct).toFixed(1)}<tspan font-size="28">%</tspan></text>
    <text x="790" y="1112" font-size="23" font-weight="650" fill="${COLORS.boardMuted}">on time</text>
    <line x1="72" y1="1218" x2="1008" y2="1218" stroke="rgba(255,255,255,.16)" stroke-width="2"/>
    <text x="72" y="1270" class="mono" font-size="25" font-weight="650" fill="${COLORS.boardMuted}">bristolbuses.live</text>
    <text x="1008" y="1270" text-anchor="end" class="mono" font-size="23" font-weight="650" fill="${COLORS.boardMuted}">on time: 1 min early to 5 min 59 s late</text>
  </svg>`;
}

export function weeklyPowertrainSvg(data, css = '') {
  const powertrain = data.powertrain;
  const electric = powertrain.electric;
  const other = powertrain.dieselOther;
  const electricShare = Number(electric.sharePct);
  const electricOnTime = Number(electric.onTimePct);
  const otherOnTime = Number(other.onTimePct);
  const difference = Math.abs(Number(powertrain.onTimeDifferencePoints));
  const circumference = 2 * Math.PI * 170;
  const electricArc = circumference * electricShare / 100;
  const scope = String(data.operatorName || 'Bristol buses');
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${WIDTH}" height="${HEIGHT}" viewBox="0 0 ${WIDTH} ${HEIGHT}">
    <style>${css}</style>
    <defs><pattern id="led-dots-power" width="5" height="5" patternUnits="userSpaceOnUse"><circle cx="1.1" cy="1.1" r="1.1" fill="rgba(255,255,255,.045)"/></pattern></defs>
    <rect width="1080" height="1350" fill="#0d0f11"/>
    <rect width="1080" height="1350" fill="url(#led-dots-power)"/>
    <rect width="1080" height="12" fill="${COLORS.amber}"/>
    <text x="72" y="112" class="matrix" font-size="25" font-weight="750" letter-spacing="2.0" fill="${COLORS.amber}">OPERATOR: ${xml(scope.toUpperCase())} · ELECTRIC VS DIESEL</text>
    <text x="72" y="218" font-size="78" font-weight="800" letter-spacing="-3" fill="${COLORS.boardInk}">${electricShare.toFixed(1)}% of readings</text>
    <text x="72" y="302" font-size="78" font-weight="800" letter-spacing="-3" fill="${COLORS.boardInk}">were electric.</text>
    <rect x="72" y="374" width="936" height="548" rx="14" fill="#15181b" stroke="rgba(255,255,255,.12)" stroke-width="2"/>
    <circle cx="300" cy="650" r="170" fill="none" stroke="#666c73" stroke-width="68"/>
    <circle cx="300" cy="650" r="170" fill="none" stroke="${COLORS.liveGreen}" stroke-width="68" stroke-dasharray="${electricArc.toFixed(1)} ${(circumference - electricArc).toFixed(1)}" transform="rotate(-90 300 650)"/>
    <text x="300" y="638" text-anchor="middle" class="mono" font-size="68" font-weight="850" fill="${COLORS.liveGreen}">${electricShare.toFixed(0)}%</text>
    <text x="300" y="688" text-anchor="middle" font-size="26" font-weight="700" fill="${COLORS.boardInk}">electric</text>
    <text x="300" y="726" text-anchor="middle" font-size="22" fill="${COLORS.boardMuted}">share of readings</text>
    <circle cx="572" cy="502" r="10" fill="${COLORS.liveGreen}"/>
    <text x="598" y="510" class="matrix" font-size="23" font-weight="750" fill="${COLORS.liveGreen}">ELECTRIC</text>
    <text x="572" y="584" class="mono" font-size="54" font-weight="850" fill="${COLORS.boardInk}">${electricOnTime.toFixed(1)}%</text>
    <text x="572" y="624" font-size="24" fill="${COLORS.boardMuted}">on time</text>
    <text x="572" y="672" class="mono" font-size="22" font-weight="650" fill="${COLORS.boardInk}">${xml(Number(electric.readings).toLocaleString('en-GB'))} readings</text>
    <line x1="572" y1="718" x2="956" y2="718" stroke="rgba(255,255,255,.14)" stroke-width="2"/>
    <circle cx="572" cy="770" r="10" fill="${COLORS.boardInk}"/>
    <text x="598" y="778" class="matrix" font-size="23" font-weight="750" fill="${COLORS.boardInk}">DIESEL / OTHER</text>
    <text x="572" y="850" class="mono" font-size="54" font-weight="850" fill="${COLORS.boardInk}">${otherOnTime.toFixed(1)}%</text>
    <text x="760" y="848" font-size="24" fill="${COLORS.boardMuted}">on time</text>
    <text x="760" y="887" class="mono" font-size="22" font-weight="650" fill="${COLORS.boardInk}">${xml(Number(other.readings).toLocaleString('en-GB'))} readings</text>
    <rect x="72" y="980" width="936" height="128" rx="12" fill="rgba(255,255,255,.035)" stroke="rgba(255,255,255,.12)" stroke-width="2"/>
    <text x="104" y="1032" class="matrix" font-size="22" font-weight="750" fill="${COLORS.boardMuted}">ON-TIME DIFFERENCE</text>
    <text x="104" y="1082" font-size="36" font-weight="750" fill="${COLORS.boardInk}">${difference.toFixed(1)} percentage points apart.</text>
    <line x1="72" y1="1218" x2="1008" y2="1218" stroke="rgba(255,255,255,.16)" stroke-width="2"/>
    <text x="72" y="1270" class="mono" font-size="25" font-weight="650" fill="${COLORS.boardMuted}">bristolbuses.live</text>
    <text x="1008" y="1270" text-anchor="end" class="mono" font-size="23" font-weight="650" fill="${COLORS.boardMuted}">${xml(Number(powertrain.identifiedReadings).toLocaleString('en-GB'))} identified readings</text>
  </svg>`;
}

export function weeklyOperatorsSvg(data, css = '') {
  const comparison = data.operatorComparison || [];
  const scope = String(data.operatorName || 'Bristol buses');
  const rows = comparison.map((operator, index) => {
    const top = 365 + index * 118;
    const percentage = Number(operator.onTimePct);
    const selected = operator.operatorCode === data.operatorCode;
    return `
      <rect x="92" y="${top}" width="896" height="102" rx="10" fill="${selected ? 'rgba(245,158,11,.08)' : 'rgba(255,255,255,.025)'}" stroke="${selected ? 'rgba(245,158,11,.65)' : 'rgba(255,255,255,.10)'}" stroke-width="2"/>
      <text x="118" y="${top + 37}" font-size="29" font-weight="800" fill="${COLORS.boardInk}">${xml(operator.operatorName)}</text>
      ${selected ? `<text x="520" y="${top + 34}" class="matrix" font-size="17" font-weight="750" letter-spacing="1.5" fill="${COLORS.amber}">THIS ROUNDUP</text>` : ''}
      <text x="958" y="${top + 39}" text-anchor="end" class="mono" font-size="34" font-weight="850" fill="${selected ? COLORS.amber : COLORS.boardInk}">${percentage.toFixed(1)}%</text>
      <rect x="118" y="${top + 54}" width="840" height="16" rx="4" fill="#555b61"/>
      <rect x="118" y="${top + 54}" width="${(840 * percentage / 100).toFixed(1)}" height="16" rx="4" fill="${COLORS.liveGreen}"/>
      <text x="118" y="${top + 91}" class="mono" font-size="19" font-weight="650" fill="${COLORS.boardMuted}">${xml(Number(operator.readings).toLocaleString('en-GB'))} readings</text>`;
  }).join('');
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${WIDTH}" height="${HEIGHT}" viewBox="0 0 ${WIDTH} ${HEIGHT}">
    <style>${css}</style>
    <defs><pattern id="led-dots-operators" width="5" height="5" patternUnits="userSpaceOnUse"><circle cx="1.1" cy="1.1" r="1.1" fill="rgba(255,255,255,.045)"/></pattern></defs>
    <rect width="1080" height="1350" fill="#0d0f11"/>
    <rect width="1080" height="1350" fill="url(#led-dots-operators)"/>
    <rect width="1080" height="12" fill="${COLORS.amber}"/>
    <text x="72" y="112" class="matrix" font-size="24" font-weight="750" letter-spacing="2.0" fill="${COLORS.amber}">${xml(scope.toUpperCase())} ROUNDUP · OPERATORS COMPARED</text>
    <text x="72" y="218" font-size="76" font-weight="800" letter-spacing="-3" fill="${COLORS.boardInk}">Same week.</text>
    <text x="72" y="294" font-size="76" font-weight="800" letter-spacing="-3" fill="${COLORS.boardInk}">Different operators.</text>
    ${rows}
    <line x1="72" y1="1218" x2="1008" y2="1218" stroke="rgba(255,255,255,.16)" stroke-width="2"/>
    <text x="72" y="1270" class="mono" font-size="25" font-weight="650" fill="${COLORS.boardMuted}">bristolbuses.live</text>
    <text x="1008" y="1270" text-anchor="end" class="mono" font-size="25" font-weight="750" fill="${COLORS.boardInk}">${xml(formatDate(data.startDate))}–${xml(formatDate(data.endDate))}</text>
  </svg>`;
}

function formatDate(value) {
  const date = new Date(`${String(value).slice(0, 10)}T12:00:00Z`);
  if (Number.isNaN(date.getTime())) return String(value || '');
  return new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(date);
}

function formatDateTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value || '');
  return new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/London' }).format(date);
}

export function validatePack(pack) {
  const errors = [];
  if (!pack?.botSaid?.postText || !pack?.botSaid?.postUrl) errors.push('botSaid requires published postText and postUrl');
  if (!pack?.botSaid?.route || !pack?.botSaid?.observedAt) errors.push('botSaid requires route and observedAt');
  if (!pack?.botSaid?.operatorRef || !pack?.botSaid?.operatorName) errors.push('botSaid requires operator identity');
  const week = pack?.busWeek;
  if (!week || !Number.isFinite(Number(week.onTimePct))) errors.push('busWeek requires onTimePct');
  if (!week || Number(week.serviceDays) !== 7) errors.push('busWeek requires exactly 7 service days');
  if (!week || Number(week.readings) < 1000) errors.push('busWeek requires at least 1,000 readings');
  if (!week || !Number.isFinite(Number(week.onTimeReadings))) errors.push('busWeek requires onTimeReadings');
  if (!week || !Array.isArray(week.daily) || week.daily.length !== 7) errors.push('busWeek requires seven daily percentages');
  if (!week?.operatorCode || !week?.operatorName) errors.push('busWeek requires operator identity');
  if (!Array.isArray(week?.operatorComparison)
      || week.operatorComparison.length < 2
      || week.operatorComparison.some(operator => !operator.operatorCode
        || !operator.operatorName
        || !Number.isFinite(Number(operator.readings))
        || !Number.isFinite(Number(operator.onTimePct)))) {
    errors.push('busWeek requires at least two complete operator comparisons');
  }
  if (!Number.isFinite(Number(week?.targetPct)) || Number(week.targetPct) <= 0
      || Number(week.targetPct) > 100) errors.push('busWeek requires a valid targetPct');
  if (!Number.isFinite(Number(week?.targetGapPoints))) errors.push('busWeek requires targetGapPoints');
  if (!Number.isFinite(Number(week?.longTermTargetPct))
      || Number(week.longTermTargetPct) < Number(week.targetPct)
      || Number(week.longTermTargetPct) > 100) errors.push('busWeek requires a valid longTermTargetPct');
  if (!Number.isFinite(Number(week?.longTermTargetGapPoints))) errors.push('busWeek requires longTermTargetGapPoints');
  const distribution = week?.distribution;
  const edges = distribution?.binEdgesSeconds;
  const counts = distribution?.counts;
  if (!Array.isArray(edges) || !Array.isArray(counts)
      || counts.length !== edges.length + 1) {
    errors.push('busWeek requires a complete delay distribution');
  } else if (JSON.stringify(edges.map(Number)) !== JSON.stringify(DELAY_BIN_EDGES_S)) {
    errors.push('busWeek delay distribution uses unexpected bins');
  } else if (counts.some(value => !Number.isInteger(Number(value)) || Number(value) < 0)
      || counts.reduce((sum, value) => sum + Number(value), 0) !== Number(week.readings)) {
    errors.push('busWeek delay distribution must match readings');
  // bisect_right puts -60..-1 in bin 5 and 300..359 in bin 11.
  // Both ends belong to the published -60..359 second on-time band.
  } else if (counts.slice(5, 12).reduce((sum, value) => sum + Number(value), 0)
      !== Number(week.onTimeReadings)) {
    errors.push('busWeek delay distribution must match onTimeReadings');
  }
  for (const field of ['medianDelaySeconds', 'p10DelaySeconds', 'p90DelaySeconds']) {
    if (!Number.isFinite(Number(distribution?.[field]))) errors.push(`busWeek distribution requires ${field}`);
  }
  const powertrain = week?.powertrain;
  if (!powertrain || Number(powertrain.identifiedReadings) < 1000) {
    errors.push('busWeek requires at least 1,000 fleet-matched readings');
  }
  for (const group of ['electric', 'dieselOther']) {
    if (!Number.isFinite(Number(powertrain?.[group]?.readings))
        || !Number.isFinite(Number(powertrain?.[group]?.sharePct))
        || !Number.isFinite(Number(powertrain?.[group]?.onTimePct))) {
      errors.push(`busWeek powertrain requires complete ${group} figures`);
    }
  }
  if (errors.length) throw new Error(errors.join('; '));
}

export function manifest(pack, files) {
  const bot = pack.botSaid;
  const week = pack.busWeek;
  return {
    schema: 4,
    generatedAt: pack.generatedAt || new Date().toISOString(),
    drafts: [
      {
        kind: 'bot-said', file: files.botSaid,
        caption: `${bot.postText}\n\n${bot.operatorName} route ${bot.route} at ${bot.stop || 'Bristol'}. Track buses live at bristolbuses.live.`,
        altText: `Dark departure-board card quoting Bristol Bus Bot about ${bot.operatorName} route ${bot.route} at ${bot.stop || 'Bristol'}, ${Number(bot.delayMinutes) > 0 ? `${bot.delayMinutes} minutes late` : Number(bot.delayMinutes) < 0 ? `${Math.abs(bot.delayMinutes)} minutes early` : 'on time'}. The quote reads: ${bot.postText}`,
        sources: { postUrl: bot.postUrl, observedAt: bot.observedAt, operatorRef: bot.operatorRef, operatorName: bot.operatorName, vehicleRef: bot.vehicleRef || null, recentObservationCount: bot.recentDepartures?.length || 1 },
      },
      {
        kind: 'weekly-carousel',
        slides: [
          {
            role: 'headline', file: files.weeklyHeadline,
            altText: `${week.operatorName} weekly figures shown as 100 squares: ${Math.round(Number(week.onTimePct))} green squares were on time and ${100 - Math.round(Number(week.onTimePct))} outlined red squares were not. The exact result was ${Number(week.onTimePct).toFixed(1)} percent across ${Number(week.readings).toLocaleString('en-GB')} timing-point readings.`,
          },
          {
            role: 'target', file: files.weeklyTarget,
            altText: `${week.operatorName} recorded ${Number(week.onTimePct).toFixed(1)} percent on time, ${Number(week.targetGapPoints).toFixed(1)} percentage points below WECA's latest published ${Number(week.targetPct).toFixed(0)} percent annual area target. WECA's longer-term goal is ${Number(week.longTermTargetPct).toFixed(0)} percent by 2030, a gap of ${Number(week.longTermTargetGapPoints).toFixed(1)} points.`,
          },
          {
            role: 'daily-detail', file: files.weeklyDays,
            altText: `${week.operatorName} daily on-time percentages from ${formatDate(week.startDate)} to ${formatDate(week.endDate)}: ${dailySeries(week).map(day => `${day.day} ${day.value.toFixed(1)} percent`).join(', ')}.`,
          },
          {
            role: 'distribution', file: files.weeklyDistribution,
            altText: `Bar chart showing how early or late ${Number(week.readings).toLocaleString('en-GB')} ${week.operatorName} readings were. One in ten was more than ${Math.abs(Number(week.distribution.p90DelaySeconds) / 60).toFixed(1)} minutes late. The typical result was ${timingWords(week.distribution.medianDelaySeconds)}. Eight in ten readings were between ${signedMinutes(week.distribution.p10DelaySeconds)} and ${signedMinutes(week.distribution.p90DelaySeconds)} minutes.`,
          },
          {
            role: 'powertrain', file: files.weeklyPowertrain,
            altText: `${Number(week.powertrain.electric.sharePct).toFixed(1)} percent of ${Number(week.powertrain.identifiedReadings).toLocaleString('en-GB')} identified ${week.operatorName} readings were from electric buses. Electric buses were on time in ${Number(week.powertrain.electric.onTimePct).toFixed(1)} percent of readings, compared with ${Number(week.powertrain.dieselOther.onTimePct).toFixed(1)} percent for diesel and other buses, a difference of ${Math.abs(Number(week.powertrain.onTimeDifferencePoints)).toFixed(1)} percentage points.`,
          },
          {
            role: 'operator-comparison', file: files.weeklyOperators,
            altText: `On-time results by operator from ${formatDate(week.startDate)} to ${formatDate(week.endDate)}: ${week.operatorComparison.map(operator => `${operator.operatorName} ${Number(operator.onTimePct).toFixed(1)} percent from ${Number(operator.readings).toLocaleString('en-GB')} readings`).join('; ')}.`,
          },
        ],
        caption: `${week.operatorName} weekly roundup, ${formatDate(week.startDate)} to ${formatDate(week.endDate)}: ${Number(week.onTimePct).toFixed(1)}% of timetable checks were on time, ${Number(week.targetGapPoints).toFixed(1)} points below WECA's latest published ${Number(week.targetPct).toFixed(0)}% annual area target. Electric buses accounted for ${Number(week.powertrain.electric.sharePct).toFixed(1)}% of identified readings. ${Number(week.readings).toLocaleString('en-GB')} readings over ${week.serviceDays} days. Full figures via the link in bio.`,
        sources: {
          operatorCode: week.operatorCode, operatorName: week.operatorName,
          targetPct: week.targetPct, targetGapPoints: week.targetGapPoints,
          longTermTargetPct: week.longTermTargetPct,
          longTermTargetGapPoints: week.longTermTargetGapPoints,
          startDate: week.startDate, endDate: week.endDate,
          readings: week.readings, onTimeReadings: week.onTimeReadings,
          dailyOnTimePct: week.daily,
          delayDistribution: week.distribution,
          powertrain: week.powertrain,
          operatorComparison: week.operatorComparison,
        },
      },
    ],
  };
}

function argumentsFrom(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === '--input') result.input = argv[++index];
    else if (argv[index] === '--output') result.output = argv[++index];
  }
  return result;
}

async function main() {
  const args = argumentsFrom(process.argv.slice(2));
  if (!args.input || !args.output) throw new Error('usage: --input pack.json --output directory');
  const pack = JSON.parse(await fs.readFile(path.resolve(args.input), 'utf8'));
  validatePack(pack);
  const output = path.resolve(args.output);
  await fs.mkdir(output, { recursive: true });
  const { default: sharp } = await import('sharp');
  const css = fontCss();
  const render = svg => new Resvg(svg, {
    font: {
      fontFiles: [
        path.join(HERE, 'fonts', 'google-sans-flex-variable.ttf'),
        path.join(HERE, 'fonts', 'google-sans-code-variable.ttf'),
        path.join(HERE, 'fonts', 'bitcount-grid-double-variable.ttf'),
      ],
      loadSystemFonts: false,
      defaultFontFamily: 'Google Sans Flex',
    },
  }).render().asPng();
  const names = {
    botSaid: '01-the-bot-said.jpg',
    weeklyHeadline: '02-weekly-headline.jpg',
    weeklyTarget: '03-weekly-target.jpg',
    weeklyDays: '04-weekly-days.jpg',
    weeklyDistribution: '05-weekly-distribution.jpg',
    weeklyPowertrain: '06-weekly-powertrain.jpg',
    weeklyOperators: '07-operators-compared.jpg',
  };
  await Promise.all([
    sharp(render(botSaidSvg(pack.botSaid, css))).jpeg({ quality: 92, chromaSubsampling: '4:4:4' }).toFile(path.join(output, names.botSaid)),
    sharp(render(busWeekSvg(pack.busWeek, css))).jpeg({ quality: 92, chromaSubsampling: '4:4:4' }).toFile(path.join(output, names.weeklyHeadline)),
    sharp(render(weeklyTargetSvg(pack.busWeek, css))).jpeg({ quality: 92, chromaSubsampling: '4:4:4' }).toFile(path.join(output, names.weeklyTarget)),
    sharp(render(weeklyDaysSvg(pack.busWeek, css))).jpeg({ quality: 92, chromaSubsampling: '4:4:4' }).toFile(path.join(output, names.weeklyDays)),
    sharp(render(weeklyDistributionSvg(pack.busWeek, css))).jpeg({ quality: 92, chromaSubsampling: '4:4:4' }).toFile(path.join(output, names.weeklyDistribution)),
    sharp(render(weeklyPowertrainSvg(pack.busWeek, css))).jpeg({ quality: 92, chromaSubsampling: '4:4:4' }).toFile(path.join(output, names.weeklyPowertrain)),
    sharp(render(weeklyOperatorsSvg(pack.busWeek, css))).jpeg({ quality: 92, chromaSubsampling: '4:4:4' }).toFile(path.join(output, names.weeklyOperators)),
  ]);
  await fs.writeFile(path.join(output, 'manifest.json'), `${JSON.stringify(manifest(pack, names), null, 2)}\n`);
  process.stdout.write(`Wrote ${output}: ${Object.values(names).join(', ')}, manifest.json\n`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  });
}
