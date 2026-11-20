// Checked depot facts, keyed by operator and the fleet file's garage name.
// The first four depots were checked on 2026-10-22. Lawrence Hill and Wells
// were checked on 2026-11-20. A fact describes the depot, never this bus's
// movements. The Bedminster biomethane station belongs to Bristol Community
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

// These cards can be reviewed before being made available to the writer.
export const DEPOT_CARDS_FOR_REVIEW: Record<string, DepotFact[]> = {
    'FBRI:Lawrence Hill': [{
        claim: 'In May 2026 First Bus said electrification work was underway at Lawrence Hill depot and planned to run 116 new electric buses from there on the Bristol network from autumn.',
        qualification: 'The operator\'s May 2026 plan.',
        source: 'https://www.firstbus.co.uk/news-and-service-updates/news/bath-welcomes-first-electric-buses-and-first-repowers-west-england',
    }],
    'FBRI:Wells': [{
        claim: 'In February 2024 Somerset Council reported offering temporary overnight parking at Wells Bus Station. First had asked for space for up to six buses, and the council understood that First\'s Wells depot was short of space.',
        qualification: 'A temporary offer recorded by the council in 2024.',
        source: 'https://somerset.moderngov.co.uk/documents/g6870/Public%20reports%20pack%2006th-Feb-2024%2010.00%20Bus%20Advisory%20Board.pdf?T=10',
    }],
};

/** One checked fact for this operator's garage, rotating daily, if any. */
export function depotFact(operatorRef: string | undefined, garage: string, observedIso?: string): DepotFact | undefined {
    const facts = DEPOT_FACTS[`${(operatorRef || '').trim().toUpperCase()}:${garage.trim()}`];
    if (!facts?.length) return undefined;
    const day = Math.floor((observedIso ? Date.parse(observedIso) : Date.now()) / 86_400_000);
    return facts[(Number.isFinite(day) ? day : 0) % facts.length];
}
