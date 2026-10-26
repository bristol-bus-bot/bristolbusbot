import { el } from './util.js';

// The service day starts at 04:00, so late-night hours sit after 23:00.
const HOUR_ORDER = [...Array(20).keys()].map(h => h + 4).concat([0, 1, 2, 3]);
const BAND_LABELS = {
    am_peak: '7–10am', interpeak: '10am–4pm', pm_peak: '4–7pm',
    evening: 'evenings and early mornings',
};

const hourLabel = hour => `${String(hour).padStart(2, '0')}:00`;
const inTen = (part, whole) => `${Math.round((10 * part) / whole)} in 10`;
const inHundred = (part, whole) => `${Math.round((100 * part) / whole)} in 100`;

// One cell per hour the route runs, from its first hour to its last.
export function hourCells(byKind, kind, minReadings) {
    const present = HOUR_ORDER.filter(h => (byKind?.weekday || {})[h] || (byKind?.weekend || {})[h]);
    if (!present.length) return [];
    const first = HOUR_ORDER.indexOf(present[0]);
    const last = HOUR_ORDER.indexOf(present[present.length - 1]);
    return HOUR_ORDER.slice(first, last + 1).map(hour => {
        const [on = 0, early = 0, late = 0] = (byKind?.[kind] || {})[hour] || [];
        const total = on + early + late;
        return { hour, on, early, late, total, pct: total ? (100 * on) / total : null, low: total < minReadings };
    });
}

export function hourSummary(cells, kind) {
    const solid = cells.filter(c => c.total && !c.low);
    const when = kind === 'weekday' ? 'On weekdays' : 'At weekends';
    if (solid.length < 2) return `${when} there aren't enough readings yet to compare times of day.`;
    const worst = solid.reduce((a, b) => (b.pct < a.pct ? b : a));
    const best = solid.reduce((a, b) => (b.pct > a.pct ? b : a));
    if (Math.round(best.pct / 10) === Math.round(worst.pct / 10))
        return `${when} it's about as punctual all day: around ${inTen(best.on, best.total)} on time.`;
    return `${when} it's least punctual from ${hourLabel(worst.hour)} (${inTen(worst.on, worst.total)} on time) and most punctual from ${hourLabel(best.hour)} (${inTen(best.on, best.total)}).`;
}

export function cellLevel(cell) {
    if (!cell.total) return 'none';
    return cell.pct >= 80 ? 'good' : cell.pct >= 60 ? 'ok' : 'bad';
}

export function cellText(cell) {
    if (!cell.total) return `${hourLabel(cell.hour)}: no readings`;
    return `${hourLabel(cell.hour)}: ${inTen(cell.on, cell.total)} on time, ${cell.late} late, ${cell.early} early, from ${cell.total} readings${cell.low ? ' (too few to rely on)' : ''}`;
}

// Three separate numbers, never added together into "cancelled".
export function journeyLines(entry, dutyChecked) {
    if (!entry || !entry.total?.[0]) return [];
    const [timetabled, seen, likely] = entry.total;
    const unseen = Math.max(0, timetabled - seen - (dutyChecked ? likely : 0));
    const lines = [
        `${timetabled.toLocaleString()} journeys were timetabled. We saw ${seen.toLocaleString()} of them running (${inHundred(seen, timetabled)}).`,
    ];
    if (dutyChecked && likely)
        lines.push(`${likely.toLocaleString()} very likely didn't run: the same bus was tracked on the journey before and the journey after, but not on this one.`);
    lines.push(`We didn't see the other ${unseen.toLocaleString()}. That could be a cancellation or a bus with its tracker off, so we don't count them as cancelled.`);
    const overall = (timetabled - seen) / timetabled;
    const worst = Object.entries(entry.bands || {})
        .map(([band, [t = 0, s = 0] = []]) => ({ band, t, s, missing: t ? (t - s) / t : 0 }))
        .filter(b => b.t >= 50)
        .sort((a, b) => b.missing - a.missing)[0];
    if (worst && worst.missing >= 0.03 && worst.missing >= 1.5 * overall)
        lines.push(`Most often missing: ${BAND_LABELS[worst.band] || worst.band}, when ${inHundred(worst.t - worst.s, worst.t)} journeys weren't seen.`);
    return lines;
}

export function createRouteHoursController(host, fetcher = fetch) {
    let key = '', generation = 0, data = null, kind = 'weekday';

    function clear() {
        key = ''; generation++; data = null;
        host.replaceChildren(); host.hidden = true;
    }

    function render() {
        if (!data) return;
        const cells = hourCells(data.hours, kind, data.min_readings);
        const detail = el('p', { class: 'rh-detail', 'aria-live': 'polite' },
            [cells.length ? 'Tap an hour for its figures.' : '']);
        const strip = el('ol', { class: 'rh-strip', 'aria-label': `Route ${data.route} on time by hour` },
            cells.map(cell => {
                const item = el('li', {
                    class: `rh-cell rh-${cellLevel(cell)}${cell.low ? ' rh-low' : ''}`,
                    title: cellText(cell), tabindex: 0,
                    onClick: () => { detail.textContent = cellText(cell); },
                    onKeyDown: event => { if (event.key === 'Enter') detail.textContent = cellText(cell); },
                }, [
                    el('span', { class: 'rh-sr' }, [cellText(cell)]),
                    el('span', { class: 'rh-tick', 'aria-hidden': 'true' }, [cell.hour % 3 === 0 ? String(cell.hour).padStart(2, '0') : '']),
                ]);
                return item;
            }));
        const toggle = ['weekday', 'weekend'].map(value => el('button', {
            class: `rh-toggle${kind === value ? ' active' : ''}`,
            'aria-pressed': kind === value ? 'true' : 'false',
            onClick: () => { kind = value; render(); },
        }, [value === 'weekday' ? 'Weekdays' : 'Weekends']));
        const journeys = journeyLines(data.journeys, data.duty_checked);
        const days = (data.window?.days?.weekday || 0) + (data.window?.days?.weekend || 0);
        host.replaceChildren(
            el('h3', {}, [`When is the ${data.route} on time?`]),
            ...(cells.length ? [
                el('div', { class: 'rh-toggles' }, toggle),
                el('p', { class: 'rh-summary' }, [hourSummary(cells, kind)]),
                strip, detail,
            ] : []),
            ...(journeys.length ? [
                el('h4', {}, ['Did the buses turn up?']),
                el('ul', { class: 'rh-journeys' }, journeys.map(line => el('li', {}, [line]))),
            ] : []),
            el('p', { class: 'rh-foot' }, [
                `Last ${days} days, from our punctuality audit. Faded hours have too few readings to rely on. `,
                el('a', { href: 'https://bristol-bus-bot.github.io/weca-bus-audit/', target: '_blank' }, ['How we measure ↗']),
            ]),
        );
        host.hidden = false;
    }

    async function show(operator, route) {
        const next = `${operator}_${route}`;
        if (next === key) return;
        clear(); key = next;
        const ticket = generation;
        try {
            const response = await fetcher(`/api/route-hours?${new URLSearchParams({ operator, route })}`);
            if (!response.ok) throw new Error('unavailable');
            const payload = await response.json();
            if (ticket !== generation || !payload.available) return;
            data = payload;
            render();
        } catch {
            // No chart is better than a broken panel.
        }
    }

    return { show, clear };
}

if (typeof window !== 'undefined') {
    window.BBB = window.BBB || {};
    window.BBB.createRouteHoursController = createRouteHoursController;
}
