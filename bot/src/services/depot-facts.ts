// Checked depot facts, keyed by operator and the fleet file's garage name.
// Checked against their sources on 2026-10-22. A fact describes the depot,
// never this bus's movements. Lawrence Hill and Wells have no checked fact
// yet; the Bedminster biomethane station belongs to Bristol Community
// Transport, not a First depot.

export interface DepotFact { claim: string; qualification: string; source: string }

export const DEPOT_FACTS: Record<string, DepotFact[]> = {
    'FBRI:Hengrove': [{
        claim: 'Hengrove became Bristol\'s first electrified bus depot when it opened on 3 June 2025, with overhead charging gantries for 74 new electric buses.',
        qualification: 'Depot history; not evidence of where this bus has been.',
        source: 'https://www.westofengland-ca.gov.uk/news/bristols-first-electrified-bus-depot-opens/',
    }],
    'FBRI:Weston': [{
        claim: 'First\'s Weston-super-Mare depot became the West of England\'s first electrified bus depot when it was unveiled on 20 March 2025, a £14.9 million scheme for 24 new electric buses.',
        qualification: 'Depot history; not evidence of where this bus has been.',
        source: 'https://n-somerset.gov.uk/news/first-electric-depot-buses-unveiled-west-england',
    }],
    'FBRI:Bath': [{
        claim: 'First describes its Bath depot as its only depot on an island, between the River Avon and the Kennet and Avon Canal.',
        qualification: "The operator's own description.",
        source: 'https://www.electrive.com/2026/06/02/first-bus-launches-first-repowered-electric-buses-in-bath/',
    }, {
        claim: 'In June 2026 First said 27 diesel Wrightbus double-deckers at its Bath depot were being converted to battery-electric by Wrightbus\'s NewPower.',
        qualification: 'Announced June 2026; this bus may not be one of them.',
        source: 'https://www.electrive.com/2026/06/02/first-bus-launches-first-repowered-electric-buses-in-bath/',
    }],
    'FBRI:Marlborough Street': [{
        claim: 'Bristol\'s Marlborough Street bus station first opened in 1958 with its own depot; the current bus station opened in 2006.',
        qualification: 'Site history.',
        source: 'https://en.wikipedia.org/wiki/Bristol_bus_station',
    }],
};

/** One checked fact for this operator's garage, rotating daily, if any. */
export function depotFact(operatorRef: string | undefined, garage: string, observedIso?: string): DepotFact | undefined {
    const facts = DEPOT_FACTS[`${(operatorRef || '').trim().toUpperCase()}:${garage.trim()}`];
    if (!facts?.length) return undefined;
    const day = Math.floor((observedIso ? Date.parse(observedIso) : Date.now()) / 86_400_000);
    return facts[(Number.isFinite(day) ? day : 0) % facts.length];
}
