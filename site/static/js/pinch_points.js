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
    { min: 7, level: 'high', colour: '#b91c1c', label: '7+', words: '7 or more' },
    { min: 4, level: 'mid', colour: '#ea580c', label: '4–7', words: '4 to 7' },
    { min: 0, level: 'low', colour: '#f2a900', label: '2–4', words: '2 to 4' },
];
export const DEFINITION = 'Road sections where buses usually take at least 2 minutes longer than the '
    + 'timetable allows, over 4 weeks. It describes the timetable, not the drivers.';

export function lostLevel(minutes) {
    return LEVELS.find(l => minutes >= l.min) || LEVELS[LEVELS.length - 1];
}

const wholeMinutes = value => `${Math.round(Number(value))} min`;

export function formatWindow(window) {
    if (!window?.from || !window?.to) return '';
    const date = s => new Date(`${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}T12:00:00Z`)
        .toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' });
    return `${date(window.from)} to ${date(window.to)}`;
}

/** Two bars on one scale, as the audit page's timetable-fit bars. */
export function minuteBars(route, scale) {
    const row = (label, value, kind) => {
        const fill = el('span', { class: `pinch-fill pinch-fill-${kind}` }, []);
        fill.style.width = `${Math.min(100, (100 * Number(value)) / scale)}%`;
        return el('div', { class: 'pinch-mins' }, [
            el('span', {}, [label]),
            el('span', { class: 'pinch-track' }, [fill]),
            el('span', {}, [wholeMinutes(value)]),
        ]);
    };
    return [row('Timetable', route.timetable_min, 'timetable'), row('Usually', route.typical_min, 'usual')];
}

export function popupContent(props, window, when) {
    const place = (name, town) => (town ? `${name}, ${town}` : name);
    const stretch = `${place(props.from, props.from_place)} → ${place(props.to, props.to_place)}`;
    const scale = Math.max(1, ...props.routes.map(r => Number(r.typical_min) || 0));
    const range = formatWindow(window);
    const label = `${stretch}. ${DEFINITION}`
        + (props.approximate ? ' The line is drawn straight between the two stops.' : '');
    return el('div', { class: 'pinch-popup', 'aria-label': label }, [
        el('strong', { class: 'pinch-popup-heading' }, [stretch]),
        ...props.routes.map(r => el('div', {
            class: 'pinch-popup-route',
            'aria-label': `Route ${r.route}, ${when}: the timetable allows ${wholeMinutes(r.timetable_min)}, `
                + `buses usually take ${wholeMinutes(r.typical_min)}, from ${r.journeys} journeys, ${range}.`,
        }, [
            el('p', { class: 'pinch-popup-when' }, [`Route ${r.route} · ${when}`]),
            ...minuteBars(r, scale),
            el('p', { class: 'pinch-popup-count' }, [`${r.journeys} journeys · ${range}`]),
        ])),
    ]);
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
    const legend = el('div', {
        class: 'pinch-legend',
        'aria-label': 'Extra minutes buses usually take, compared with the timetable: '
            + [...LEVELS].reverse().map(l => `${l.words} minutes`).join(', '),
    }, [
        el('span', { class: 'pinch-legend-label' }, ['Extra minutes buses usually take']),
        el('span', { class: 'pinch-keys' },
            [...LEVELS].reverse().map(l => el('span', { class: `pinch-key pinch-key-${l.level}` }, [l.label]))),
    ]);
    const panel = el('section', { class: 'pinch-panel', 'aria-label': `Where the timetable is too tight. ${DEFINITION}` }, [
        el('h2', {}, ['Where the timetable is too tight']),
        el('div', { class: 'pinch-choices', role: 'group', 'aria-label': 'Time of day' }, bandButtons),
        el('div', { class: 'pinch-choices', role: 'group', 'aria-label': 'Days' }, dayButtons),
        legend,
        status,
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

    /** Pop-ups open clear of the panel, so its heading is never hidden (phones). */
    function popupOptions() {
        // On a phone the panel spans the map's width; on a desktop it sits to one side.
        const narrow = (map.getSize?.().x ?? 1000) < 769;
        const below = panel.hidden || !narrow ? 10 : (panel.offsetTop || 0) + (panel.offsetHeight || 0) + 12;
        return { maxWidth: 300, minWidth: 220, autoPan: true,
                 autoPanPaddingTopLeft: L?.point ? L.point(10, below) : [10, below],
                 autoPanPaddingBottomRight: L?.point ? L.point(10, 90) : [10, 90] };
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
            status.textContent = 'No current data';
            status.setAttribute('aria-label', 'No current data: the nightly audit files are missing or out of date.');
            return;
        }
        const when = `${(DAYS.find(d => d[0] === day) || [])[1]} · ${(BANDS.find(b => b[0] === band) || [])[1]}`;
        layer = L.geoJSON(body, {
            style: feature => lineStyle(feature.properties),
            onEachFeature: (feature, line) => {
                line.bindPopup(() => popupContent(feature.properties, body.window, when), popupOptions());
            },
        }).addTo(map);
        const count = body.features.length;
        status.textContent = `${formatWindow(body.window)} · tap a line`;
        status.setAttribute('aria-label',
            `${count} section${count === 1 ? '' : 's'} shown, measured ${formatWindow(body.window)}. Tap a line for its figures.`);
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
