// Place cards, keyed by the reported stop's local authority and locality from
// stop enrichment (not bounding-box guesses). Each fact was checked against its
// source on 2026-10-22. A card is used only once Tom marks it 'approved';
// tone hints are optional colour for the writer and are never evidence.

export interface PlaceFact { claim: string; qualification: string; source: string }
export interface PlaceCard { status: 'draft' | 'approved'; facts: PlaceFact[]; toneHint?: string }

const wiki = (page: string) => `https://en.wikipedia.org/wiki/${page}`;
const card = (facts: PlaceFact[], toneHint?: string): PlaceCard => ({ status: 'draft', facts, ...(toneHint ? { toneHint } : {}) });
const place = 'About the place; not where the bus is now or where it has been.';

export const PLACE_CARDS: Record<string, PlaceCard> = {
    'South Gloucestershire|Yate': card([
        { claim: "Yate's celestine, a strontium mineral, once made up 95 per cent of the world's production.", qualification: 'Historical industry. ' + place, source: wiki('Yate') },
        { claim: 'J.K. Rowling was born in Yate in 1965.', qualification: place, source: wiki('Yate') },
    ]),
    'North Somerset|Clevedon': card([
        { claim: 'Clevedon Pier opened on Easter Monday 1869.', qualification: place, source: wiki('Clevedon') },
        { claim: "Clevedon's Curzon cinema dates from 1912 and is one of the world's oldest purpose-built, continuously operated cinemas.", qualification: place, source: wiki('Clevedon') },
    ], 'Victorian seaside town on the Severn Estuary.'),
    'North Somerset|Portishead': card([
        { claim: 'The band Portishead took its name from the town.', qualification: place, source: wiki('Portishead,_Somerset') },
    ]),
    'Bath and North East Somerset|Keynsham': card([
        { claim: "J. S. Fry & Sons moved their chocolate factory from central Bristol to Keynsham's Somerdale in 1935.", qualification: 'Historical. ' + place, source: wiki('Keynsham') },
        { claim: 'Keynsham Abbey was founded around 1170.', qualification: 'Historical. ' + place, source: wiki('Keynsham') },
    ]),
    'North Somerset|Weston-super-Mare': card([
        { claim: "Weston-super-Mare's Grand Pier opened in 1904.", qualification: place, source: wiki('Weston-super-Mare') },
        { claim: "Banksy used Weston-super-Mare's old Tropicana for his Dismaland installation in August 2015.", qualification: 'Temporary 2015 installation. ' + place, source: wiki('Weston-super-Mare') },
    ], 'Seaside resort.'),
    'South Gloucestershire|Kingswood': card([
        { claim: 'The King\'s Wood was a royal hunting estate around Bristol in Saxon times.', qualification: 'Historical. ' + place, source: wiki('Kingswood,_South_Gloucestershire') },
        { claim: 'Kingswood first came into industrial prominence in the late 17th century because of coal mining.', qualification: 'Historical. ' + place, source: wiki('Kingswood,_South_Gloucestershire') },
    ]),
    'South Gloucestershire|Thornbury': card([
        { claim: 'Thornbury Castle was begun in 1511, and Henry VIII stayed there for ten days in 1535 with Anne Boleyn.', qualification: 'Historical. ' + place, source: wiki('Thornbury,_Gloucestershire') },
    ]),
    'Bath and North East Somerset|Bath City Centre': card([
        { claim: 'Bath became a UNESCO World Heritage Site in 1987.', qualification: place, source: wiki('Bath,_Somerset') },
        { claim: "Bath's Royal Crescent was built between 1767 and 1774, designed by the younger John Wood.", qualification: place, source: wiki('Bath,_Somerset') },
    ]),
    'Bristol, City of|Clifton': card([
        { claim: "Clifton's Berkeley Square and Berkeley Crescent, built around 1790, are examples of Georgian architecture.", qualification: place, source: wiki('Clifton,_Bristol') },
    ]),
    'Bristol, City of|Bedminster': card([
        { claim: 'Cigarette and cigar makers W.D. & H.O. Wills moved to Bedminster in the 1880s.', qualification: 'Historical. ' + place, source: wiki('Bedminster,_Bristol') },
        { claim: 'By the end of the 19th century there were eighteen coal pits in the Bedminster and Ashton Vale coalfield.', qualification: 'Historical. ' + place, source: wiki('Bedminster,_Bristol') },
    ]),
    'Bristol, City of|Southmead': card([
        { claim: 'Southmead Hospital began as a workhouse that opened in 1902 and became Southmead Infirmary in 1924.', qualification: 'Historical. ' + place, source: wiki('Southmead') },
    ]),
    'Bristol, City of|Shirehampton': card([
        { claim: 'For centuries a small rowed ferry from near The Lamplighters pub in Shirehampton to Pill was the only direct link to Somerset, until 1974.', qualification: 'Historical. ' + place, source: wiki('Shirehampton') },
    ]),
    'Bristol, City of|Brislington': card([
        { claim: "When Brislington House opened in 1806 it was one of England's first purpose-built asylums.", qualification: 'Historical. ' + place, source: wiki('Brislington') },
    ]),
    'Bristol, City of|Fishponds': card([
        { claim: 'Fishponds takes its name from old quarries that became large fishponds once they were abandoned.', qualification: place, source: wiki('Fishponds') },
    ]),
    'South Gloucestershire|Hanham': card([
        { claim: 'Tom Cribb, once world champion bare-knuckle boxer, was born in Hanham.', qualification: 'Historical. ' + place, source: wiki('Hanham') },
        { claim: "Hanham's Blue Bowl has been claimed to be one of the oldest pubs in the country.", qualification: 'A claim, not established fact; say it is claimed. ' + place, source: wiki('Hanham') },
    ]),
};

/** An approved card's fact for this stop's locality, rotating daily, if any. */
export function placeFact(localAuthority: string | undefined, locality: string | undefined, observedIso?: string,
    cards: Record<string, PlaceCard> = PLACE_CARDS) {
    if (!localAuthority || !locality) return undefined;
    const entry = cards[`${localAuthority.trim()}|${locality.trim()}`];
    if (!entry || entry.status !== 'approved' || !entry.facts.length) return undefined;
    const day = Math.floor((observedIso ? Date.parse(observedIso) : Date.now()) / 86_400_000);
    return { fact: entry.facts[(Number.isFinite(day) ? day : 0) % entry.facts.length], toneHint: entry.toneHint };
}
