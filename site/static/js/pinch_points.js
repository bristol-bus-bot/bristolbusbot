/* Pinch points (#145): road sections where buses usually take longer than the
   timetable allows, from the audit's nightly "not enough time allowed" results.
   Loaded only when the layer is first switched on. */
import { el } from './util.js';

export const BANDS = [
    ['early', 'Before 7am'], ['am_peak', '7–10am'], ['interpeak', '10am–4pm'],
    ['pm_peak', '4–7pm'], ['evening', 'After 7pm'],
];
export const DAYS = [['weekday', 'Weekdays'], ['weekend', 'Weekends']];
export const LEVELS = [
    { min: 7, level: 'high', colour: '#b91c1c', label: '7+ min' },
    { min: 4, level: 'mid', colour: '#ea580c', label: '4–7 min' },
    { min: 0, level: 'low', colour: '#f2a900', label: '2–4 min' },
];

export function lostLevel(minutes) {
    return LEVELS.find(l => minutes >= l.min) || LEVELS[LEVELS.length - 1];
}

const minutes = value => `${Number(value).toFixed(value % 1 ? 1 : 0)} min`;

export function formatWindow(window) {
    if (!window?.from || !window?.to) return '';
    const date = s => new Date(`${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}T12:00:00Z`)
        .toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' });
    return `${date(window.from)} to ${date(window.to)}`;
}

export function routeLine(route) {
    return `Route ${route.route}: the timetable allows ${minutes(route.timetable_min)}, ` +
        `buses usually take ${minutes(route.typical_min)} (${route.journeys} journeys)`;
}

export function popupContent(props, window, bandLabel) {
    const place = (name, town) => (town ? `${name}, ${town}` : name);
    return el('div', { class: 'pinch-popup' }, [
        el('strong', {}, [`${place(props.from, props.from_place)} → ${place(props.to, props.to_place)}`]),
        el('p', { class: 'pinch-popup-when' }, [`${bandLabel}. Measured ${formatWindow(window)}.`]),
        el('ul', {}, props.routes.map(r => el('li', {}, [routeLine(r)]))),
        props.approximate
            ? el('p', { class: 'pinch-popup-note' }, ['Line drawn straight between the two stops.'])
            : null,
    ].filter(Boolean));
}

export function lineStyle(props) {
    const level = lostLevel(props.lost_min);
    return { color: level.colour, weight: level.level === 'high' ? 7 : 5, opacity: 0.9,
             dashArray: props.approximate ? '6 6' : null, lineCap: 'round' };
}

export function createPinchPointLayer(map, { host, fetchImpl = fetch, L = window.L } = {}) {
    let band = 'pm_peak';
    let day = 'weekday';
    let layer = null;
    let request = 0;
    const status = el('p', { class: 'pinch-status', role: 'status' }, []);
    const bandButtons = BANDS.map(([key, label]) => el('button', {
        class: 'pinch-choice', 'aria-pressed': String(key === band), 'data-band': key,
        onClick: () => { band = key; sync(); load(); },
    }, [label]));
    const dayButtons = DAYS.map(([key, label]) => el('button', {
        class: 'pinch-choice', 'aria-pressed': String(key === day), 'data-day': key,
        onClick: () => { day = key; sync(); load(); },
    }, [label]));
    const legend = el('div', { class: 'pinch-legend', 'aria-label': 'Minutes lost' },
        [...LEVELS].reverse().map(l => el('span', { class: `pinch-key pinch-key-${l.level}` }, [l.label])));
    const panel = el('section', { class: 'pinch-panel', 'aria-label': 'Pinch points' }, [
        el('h2', {}, ['Not enough time allowed']),
        el('div', { class: 'pinch-choices', role: 'group', 'aria-label': 'Time of day' }, bandButtons),
        el('div', { class: 'pinch-choices', role: 'group', 'aria-label': 'Days' }, dayButtons),
        legend,
        status,
        el('p', { class: 'pinch-note' }, [
            'Where buses usually take 2+ minutes longer than timetabled. It describes the timetable, not drivers. Tap a line for figures.']),
    ]);
    panel.hidden = true;
    host.appendChild(panel);
    // Taps on the panel must not reach the map underneath.
    L?.DomEvent?.disableClickPropagation?.(panel);
    L?.DomEvent?.disableScrollPropagation?.(panel);

    function sync() {
        bandButtons.forEach(b => b.setAttribute('aria-pressed', String(b.dataset.band === band)));
        dayButtons.forEach(b => b.setAttribute('aria-pressed', String(b.dataset.day === day)));
    }

    async function load() {
        const mine = ++request;
        status.textContent = 'Loading…';
        let body;
        try {
            const res = await fetchImpl(`/api/pinch-points?band=${band}&day=${day}`);
            body = await res.json();
        } catch {
            body = { available: false };
        }
        if (mine !== request || panel.hidden) return;
        if (layer) { map.removeLayer(layer); layer = null; }
        if (!body.available) {
            status.textContent = 'No current data: the nightly audit files are missing or out of date.';
            return;
        }
        const bandLabel = `${(DAYS.find(d => d[0] === day) || [])[1]}, ${(BANDS.find(b => b[0] === band) || [])[1]}`;
        layer = L.geoJSON(body, {
            style: feature => lineStyle(feature.properties),
            onEachFeature: (feature, line) => {
                line.bindPopup(() => popupContent(feature.properties, body.window, bandLabel),
                               { maxWidth: 320 });
            },
        }).addTo(map);
        const count = body.features.length;
        status.textContent = count
            ? `${count} section${count === 1 ? '' : 's'}, ${formatWindow(body.window)}.`
            : `None at this time, ${formatWindow(body.window)}.`;
    }

    return {
        show() { panel.hidden = false; load(); },
        hide() {
            panel.hidden = true;
            request += 1;
            if (layer) { map.removeLayer(layer); layer = null; }
        },
        set(next = {}) {
            if (BANDS.some(b => b[0] === next.band)) band = next.band;
            if (DAYS.some(d => d[0] === next.day)) day = next.day;
            sync();
        },
        get state() { return { band, day, visible: !panel.hidden }; },
    };
}
