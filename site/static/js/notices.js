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

export function createNoticeController(host, fetcher = fetch, clock = Date.now) {
    let selection = '', generation = 0, lastFetch = -Infinity, loading = false;
    function clear() {
        selection = ''; generation++; loading = false; lastFetch = -Infinity;
        host.replaceChildren(); host.hidden = true;
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
            host.replaceChildren(...(cards.length ? [
                el('h3', {}, [`Travel notices · ${cards.length}`]), ...cards,
                el('p', { class: 'notice-updated' }, ['Published closures and diversions. Other disruption may not be listed. Times are UK local time.']),
            ] : []));
            host.hidden = !cards.length;
        } catch {
            if (ticket !== generation) return;
            lastFetch = clock();
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
}
