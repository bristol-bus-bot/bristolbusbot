// Derived from the 13 November 2026 timetable build for Wednesday 18 November 2026.
// Route IDs distinguish services that share a line number.
// Stop localities are from the Department for Transport's NaPTAN directory.
// Timetable and NaPTAN data contain public sector information licensed under the
// Open Government Licence v3.0: https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/

interface RouteCardStop {
    id: string;
    name: string;
    locality: string;
}

interface RouteCardDirection {
    directionId: 0 | 1;
    fromStop: RouteCardStop;
    toStop: RouteCardStop;
    stopCount: number;
    approximateLengthKm: number | null;
    shapeVariant: number;
    patternJourneys: number;
    otherPatterns: number;
    shapeNote?: string;
}

interface RouteCard {
    operatorRef: string;
    line: string;
    source: 'derived';
    timetableBuildDate: string;
    serviceDate: string;
    scheduledJourneys: number;
    directions: RouteCardDirection[];
}

export const ROUTE_CARDS: Record<string, RouteCard> = {
    '32450': {
        operatorRef: "FBRI",
        line: "A1",
        source: "derived",
        timetableBuildDate: "2026-11-13",
        serviceDate: "2026-11-18",
        scheduledJourneys: 257,
        directions: [{
            directionId: 0,
            fromStop: { id: "0190NSC31605", name: "Bristol Airport Interchange", locality: "Bristol Airport" },
            toStop: { id: "01000053214", name: "Bristol Bus Station", locality: "Bristol" },
            stopCount: 22,
            approximateLengthKm: 15.5,
            shapeVariant: 0,
            patternJourneys: 128,
            otherPatterns: 0,
        }, {
            directionId: 1,
            fromStop: { id: "01000053214", name: "Bristol Bus Station", locality: "Bristol" },
            toStop: { id: "0190NSC31605", name: "Bristol Airport Interchange", locality: "Bristol Airport" },
            stopCount: 23,
            approximateLengthKm: 15.5,
            shapeVariant: 0,
            patternJourneys: 129,
            otherPatterns: 0,
        }],
    },
    '120680': {
        operatorRef: "FBRI",
        line: "U1",
        source: "derived",
        timetableBuildDate: "2026-11-13",
        serviceDate: "2026-11-18",
        scheduledJourneys: 253,
        directions: [{
            directionId: 0,
            fromStop: { id: "0180BAA01356", name: "Arrivals Square", locality: "University of Bath" },
            toStop: { id: "0180BAC30612", name: "Grand Parade", locality: "Bath City Centre" },
            stopCount: 13,
            approximateLengthKm: 3.6,
            shapeVariant: 0,
            patternJourneys: 120,
            otherPatterns: 0,
            shapeNote: "Approximate between termini on the stored shape. Some scheduled stops are up to 292 metres from that shape.",
        }, {
            directionId: 1,
            fromStop: { id: "0180BAC30612", name: "Grand Parade", locality: "Bath City Centre" },
            toStop: { id: "0180BAC30035", name: "Arrivals Square", locality: "University of Bath" },
            stopCount: 13,
            approximateLengthKm: 3.8,
            shapeVariant: 0,
            patternJourneys: 123,
            otherPatterns: 1,
        }],
    },
    '3144': {
        operatorRef: "FBRI",
        line: "m1",
        source: "derived",
        timetableBuildDate: "2026-11-13",
        serviceDate: "2026-11-18",
        scheduledJourneys: 188,
        directions: [{
            directionId: 0,
            fromStop: { id: "0170SGP90855", name: "Cribbs Causeway Bus Station", locality: "Cribbs Causeway" },
            toStop: { id: "010000008", name: "Hengrove Park", locality: "Hengrove Park" },
            stopCount: 30,
            approximateLengthKm: 29.4,
            shapeVariant: 0,
            patternJourneys: 94,
            otherPatterns: 0,
        }, {
            directionId: 1,
            fromStop: { id: "010000008", name: "Hengrove Park", locality: "Hengrove Park" },
            toStop: { id: "0170SGP90855", name: "Cribbs Causeway Bus Station", locality: "Cribbs Causeway" },
            stopCount: 29,
            approximateLengthKm: 29.5,
            shapeVariant: 0,
            patternJourneys: 94,
            otherPatterns: 0,
        }],
    },
    '4950016': {
        operatorRef: "FBRI",
        line: "U3",
        source: "derived",
        timetableBuildDate: "2026-11-13",
        serviceDate: "2026-11-18",
        scheduledJourneys: 166,
        directions: [{
            directionId: 0,
            fromStop: { id: "0180BAA01356", name: "Arrivals Square", locality: "University of Bath" },
            toStop: { id: "0180BAC30436", name: "Newton Park Library", locality: "Bath Spa University" },
            stopCount: 30,
            approximateLengthKm: 12.0,
            shapeVariant: 0,
            patternJourneys: 83,
            otherPatterns: 0,
        }, {
            directionId: 1,
            fromStop: { id: "0180BAC30436", name: "Newton Park Library", locality: "Bath Spa University" },
            toStop: { id: "0180BAC30035", name: "Arrivals Square", locality: "University of Bath" },
            stopCount: 29,
            approximateLengthKm: 12.9,
            shapeVariant: 0,
            patternJourneys: 58,
            otherPatterns: 3,
        }],
    },
    '120591': {
        operatorRef: "FBRI",
        line: "4",
        source: "derived",
        timetableBuildDate: "2026-11-13",
        serviceDate: "2026-11-18",
        scheduledJourneys: 145,
        directions: [{
            directionId: 0,
            fromStop: { id: "0180BAC30414", name: "Haviland Park", locality: "Weston" },
            toStop: { id: "0180BAC56455", name: "Odd Down P&R", locality: "Odd Down" },
            stopCount: 41,
            approximateLengthKm: 11.1,
            shapeVariant: 0,
            patternJourneys: 35,
            otherPatterns: 2,
        }, {
            directionId: 1,
            fromStop: { id: "0180BAC56455", name: "Odd Down P&R", locality: "Odd Down" },
            toStop: { id: "0180BAC30414", name: "Haviland Park", locality: "Weston" },
            stopCount: 33,
            approximateLengthKm: 10.1,
            shapeVariant: 0,
            patternJourneys: 36,
            otherPatterns: 3,
        }],
    },
    '120729': {
        operatorRef: "FBRI",
        line: "1",
        source: "derived",
        timetableBuildDate: "2026-11-13",
        serviceDate: "2026-11-18",
        scheduledJourneys: 131,
        directions: [{
            directionId: 0,
            fromStop: { id: "0170SGP90858", name: "Cribbs Causeway Bus Station", locality: "Cribbs Causeway" },
            toStop: { id: "0100BRA10453", name: "Whitmore Avenue", locality: "Broom Hill" },
            stopCount: 46,
            approximateLengthKm: 20.0,
            shapeVariant: 1,
            patternJourneys: 60,
            otherPatterns: 1,
        }, {
            directionId: 1,
            fromStop: { id: "0100BRA10453", name: "Whitmore Avenue", locality: "Broom Hill" },
            toStop: { id: "0170SGP90858", name: "Cribbs Causeway Bus Station", locality: "Cribbs Causeway" },
            stopCount: 49,
            approximateLengthKm: 20.1,
            shapeVariant: 0,
            patternJourneys: 59,
            otherPatterns: 2,
        }],
    },
    '9963498': {
        operatorRef: "SSWL",
        line: "9",
        source: "derived",
        timetableBuildDate: "2026-11-13",
        serviceDate: "2026-11-18",
        scheduledJourneys: 131,
        directions: [{
            directionId: 0,
            fromStop: { id: "0100BRZ00686", name: "Portway P&R", locality: "Shirehampton" },
            toStop: { id: "0100BRA10891", name: "Brislington P&R", locality: "Brislington" },
            stopCount: 16,
            approximateLengthKm: 16.5,
            shapeVariant: 0,
            patternJourneys: 67,
            otherPatterns: 0,
        }, {
            directionId: 1,
            fromStop: { id: "0100BRA10891", name: "Brislington P&R", locality: "Brislington" },
            toStop: { id: "0100BRZ00686", name: "Portway P&R", locality: "Shirehampton" },
            stopCount: 15,
            approximateLengthKm: 16.9,
            shapeVariant: 0,
            patternJourneys: 64,
            otherPatterns: 0,
        }],
    },
    '8396': {
        operatorRef: "FBRI",
        line: "75",
        source: "derived",
        timetableBuildDate: "2026-11-13",
        serviceDate: "2026-11-18",
        scheduledJourneys: 128,
        directions: [{
            directionId: 0,
            fromStop: { id: "0100FBX18342", name: "Hengrove Depot", locality: "Hengrove" },
            toStop: { id: "0170SGP90854", name: "Cribbs Causeway Bus Station", locality: "Cribbs Causeway" },
            stopCount: 66,
            approximateLengthKm: 26.5,
            shapeVariant: 0,
            patternJourneys: 40,
            otherPatterns: 1,
        }, {
            directionId: 1,
            fromStop: { id: "0170SGP90854", name: "Cribbs Causeway Bus Station", locality: "Cribbs Causeway" },
            toStop: { id: "0100FBX18339", name: "Hengrove Depot", locality: "Hengrove" },
            stopCount: 65,
            approximateLengthKm: null,
            shapeVariant: 0,
            patternJourneys: 47,
            otherPatterns: 1,
            shapeNote: "The stored shape covers about 25.4 km to Hengrove Park. The common pattern continues to Hengrove Depot.",
        }],
    },
    '4738': {
        operatorRef: "FBRI",
        line: "76",
        source: "derived",
        timetableBuildDate: "2026-11-13",
        serviceDate: "2026-11-18",
        scheduledJourneys: 127,
        directions: [{
            directionId: 0,
            fromStop: { id: "0100FBX18342", name: "Hengrove Depot", locality: "Hengrove" },
            toStop: { id: "0100BRP91055", name: "Crow Lane Lay-by", locality: "Henbury" },
            stopCount: 55,
            approximateLengthKm: 22.1,
            shapeVariant: 0,
            patternJourneys: 37,
            otherPatterns: 2,
        }, {
            directionId: 1,
            fromStop: { id: "0100BRP91057", name: "Crow Lane Lay-by", locality: "Henbury" },
            toStop: { id: "0100FBX18339", name: "Hengrove Depot", locality: "Hengrove" },
            stopCount: 54,
            approximateLengthKm: 23.1,
            shapeVariant: 0,
            patternJourneys: 39,
            otherPatterns: 2,
        }],
    },
    '120585': {
        operatorRef: "FBRI",
        line: "41",
        source: "derived",
        timetableBuildDate: "2026-11-13",
        serviceDate: "2026-11-18",
        scheduledJourneys: 126,
        directions: [{
            directionId: 0,
            fromStop: { id: "0180BAC30800", name: "Odd Down P&R", locality: "Odd Down" },
            toStop: { id: "0180BAC30592", name: "SouthGate", locality: "Bath City Centre" },
            stopCount: 5,
            approximateLengthKm: 4.5,
            shapeVariant: 0,
            patternJourneys: 63,
            otherPatterns: 0,
        }, {
            directionId: 1,
            fromStop: { id: "0180BAC30592", name: "SouthGate", locality: "Bath City Centre" },
            toStop: { id: "0180BAC30800", name: "Odd Down P&R", locality: "Odd Down" },
            stopCount: 5,
            approximateLengthKm: 4.3,
            shapeVariant: 0,
            patternJourneys: 63,
            otherPatterns: 0,
        }],
    },
};
