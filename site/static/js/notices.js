import { el } from './util.js';

const date = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Europe/London', weekday: 'short', day: 'numeric', month: 'short',
    hour: '2-digit', minute: '2-digit',
});
const labels = { current: 'Published for now', upcoming: 'Upcoming', uncertain: 'Check dates with source' };

export function noticeCard(notice) {
    return el('details', { class: 'travel-notice' }, [
        el('summary', {}, [el('span', { class: 'notice-state' }, [labels[notice.status]]), notice.summary]),
        notice.status === 'uncertain'
            ? el('p', { class: 'notice-caution' }, ['The supplied dates are incomplete or inconsistent. Check the original notice before travelling.'])
            : el('ul', { class: 'notice-dates' }, notice.periods.map(p => el('li', {}, [
                `${date.format(new Date(p.start))} – ${date.format(new Date(p.end))}`,
            ]))),
        el('p', {}, [notice.description]),
        notice.advice && notice.advice !== notice.description ? el('p', {}, [notice.advice]) : null,
        el('a', { href: notice.url, target: '_blank' }, [`Source: ${notice.source} ↗`]),
        notice.updated_at ? el('p', { class: 'notice-updated' }, [`Notice updated ${date.format(new Date(notice.updated_at))}`]) : null,
    ]);
}

// One line at the top of the board so a closure is seen before the departures.
export function bannerText(notices, kind = 'stop') {
    const where = kind === 'stop' ? 'this stop' : 'this route';
    const statuses = new Set(notices.map(n => n.status));
    if (statuses.has('current')) return { status: 'current', text: `Travel notice affects ${where}` };
    if (statuses.has('uncertain')) return { status: 'uncertain', text: `Travel notice for ${where}: check dates` };
    if (statuses.has('upcoming')) return { status: 'upcoming', text: `Upcoming travel notice for ${where}` };
    return null;
}

// Map marker look for a stop. Quiet grey unless the feed publishes a notice.
export function stopMarkerStyle(status, isSelected) {
    if (status === 'current' || status === 'uncertain')
        return { size: isSelected ? 18 : 14, fill: '#c88c20', ring: isSelected ? '#b48800' : '#fff', mark: true, zIndex: isSelected ? 500 : 400,
            label: status === 'current' ? 'Travel notice affects this stop' : 'Travel notice for this stop: check dates' };
    if (status === 'upcoming')
        return { size: isSelected ? 12 : 9, fill: isSelected ? '#b48800' : '#8b949e', ring: '#c88c20', mark: false, zIndex: isSelected ? 500 : 200,
            label: 'Upcoming travel notice for this stop' };
    return { size: isSelected ? 10 : 6, fill: isSelected ? '#b48800' : '#8b949e', ring: '#fff', mark: false, zIndex: isSelected ? 500 : 0, label: '' };
}

// bannerHost, when given, takes the stop banner so it sits above the stop sign
// (and inside the phone's half-open sheet); onOpen runs when it is tapped.
export function createNoticeController(host, fetcher = fetch, clock = Date.now, { bannerHost = null, onOpen = null } = {}) {
    let selection = '', generation = 0, lastFetch = -Infinity, loading = false;
    function setBanner(banner) {
        if (!bannerHost) return;
        bannerHost.replaceChildren(...(banner ? [banner] : []));
        bannerHost.hidden = !banner;
    }
    function clear() {
        selection = ''; generation++; loading = false; lastFetch = -Infinity;
        host.replaceChildren(); host.hidden = true;
        setBanner(null);
    }
    async function show(params) {
        const query = new URLSearchParams(params).toString();
        if (query !== selection) {
            clear(); selection = query;
        }
        if (loading || clock() - lastFetch < 60000) return;
        loading = true;
        const ticket = ++generation;
        try {
            const response = await fetcher(`/api/notices?${query}`);
            if (!response.ok) throw new Error('Notices unavailable');
            const data = await response.json();
            if (ticket !== generation) return;
            lastFetch = clock();
            if (!data.available) throw new Error('Notices out of date');
            const openIds = new Set(Array.from(host.querySelectorAll('details[open]')).map(n => n.dataset.noticeId));
            const cards = data.notices.map(n => {
                const card = noticeCard(n);
                card.dataset.noticeId = n.id;
                card.open = openIds.has(n.id);
                return card;
            });
            const kind = 'stop' in params ? 'stop' : 'route';
            const wording = bannerText(data.notices, kind);
            const banner = wording && el('button', { class: `notice-banner notice-banner--${wording.status}`,
                'aria-label': `${wording.text}. Show details.`,
                onClick: () => {
                    onOpen?.();
                    cards[0].open = true;
                    cards[0].scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
                } }, [
                el('span', { class: 'notice-banner-icon', 'aria-hidden': 'true' }, [wording.status === 'upcoming' ? '◷' : '⚠']),
                wording.text,
                el('span', { class: 'notice-banner-more', 'aria-hidden': 'true' }, ['Details']),
            ]);
            const separate = bannerHost && kind === 'stop';
            setBanner(separate ? banner : null);
            host.replaceChildren(...(cards.length ? [
                separate ? null : banner,
                el('h3', {}, [`Travel notices · ${cards.length}`]), ...cards,
                el('p', { class: 'notice-updated' }, ['Published closures and diversions. Other disruption may not be listed. Times are UK local time.']),
            ].filter(Boolean) : []));
            host.hidden = !cards.length;
        } catch {
            if (ticket !== generation) return;
            lastFetch = clock();
            setBanner(null);
            host.replaceChildren(el('p', { class: 'notice-updated' }, ['Travel notices are temporarily unavailable.']));
            host.hidden = false;
        } finally {
            if (ticket === generation) loading = false;
        }
    }
    return { show, clear };
}

if (typeof window !== 'undefined') {
    window.BBB = window.BBB || {};
    window.BBB.createNoticeController = createNoticeController;
    window.BBB.stopMarkerStyle = stopMarkerStyle;
}
