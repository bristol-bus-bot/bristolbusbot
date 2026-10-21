// Checked livery facts, keyed by the fleet file's livery ID (livery.id).
// From Tom's livery research spreadsheet (Research tab, 15 Sep 2026). Every
// claim was re-checked against its source on 2026-10-21: unsupported claims,
// enthusiast-wiki and photo-page sources, volatile figures and one region
// mismatch were removed; corrected wording replaces the original where the
// source said something different. A fact describes the livery or its
// operator, never this particular bus's history. 53 facts, 47 liveries.

export interface LiveryFact { claim: string; qualification: string; source: string }

export const LIVERY_FACTS: Record<string, { name: string; facts: LiveryFact[] }> = {
  "600": {
    "name": "Stagecoach Local",
    "facts": [
      {
        "claim": "Stagecoach's 2020 livery colour-codes buses by type of service: azure blue for local routes and ocean green for specialist services such as park-and-ride.",
        "qualification": "Group-wide Stagecoach livery, unveiled in January 2020.",
        "source": "https://www.keybuses.com/article/stagecoach-dons-new-clothes"
      },
      {
        "claim": "When Stagecoach unveiled its 2020 livery it said 69% of customers often found it confusing to find the bus they wanted, and that all 8,400 of its vehicles would be treated within three years.",
        "qualification": "Stagecoach's own figures from 2020.",
        "source": "https://www.route-one.net/operators/stagecoach-new-livery-unveiled-by-group/"
      }
    ]
  },
  "643": {
    "name": "National Express",
    "facts": [
      {
        "claim": "The National Express brand was first used in 1974.",
        "qualification": "Brand-level; many coaches are run by partner operators.",
        "source": "https://en.wikipedia.org/wiki/National_Express_Coaches"
      }
    ]
  },
  "3309": {
    "name": "National Express (Stansted Airport)",
    "facts": [
      {
        "claim": "National Express created its Airport brand in 2003 by merging its Airlink, Flightlink, Jetlink and Speedlink identities.",
        "qualification": "Brand-level airport sub-brand.",
        "source": "https://en.wikipedia.org/wiki/National_Express_Coaches"
      }
    ]
  },
  "3463": {
    "name": "WESTbus",
    "facts": [
      {
        "claim": "WESTbus is the West of England's regional bus brand. Its first 74 fully electric buses, in bright green, were announced in March 2025, funded with £37 million from First Bus and £6.6 million from the West of England Combined Authority.",
        "qualification": "First West of England area; figures as announced in March 2025.",
        "source": "https://hellorayo.co.uk/greatest-hits/bristol/news/electric-buses-bristol"
      }
    ]
  },
  "8": {
    "name": "Stagecoach Beachball",
    "facts": [
      {
        "claim": "Stagecoach's 'beachball' logo dates from a group rebrand in December 2000.",
        "qualification": "Group-wide Stagecoach brand.",
        "source": "https://en.wikipedia.org/wiki/Stagecoach_Group"
      }
    ]
  },
  "24": {
    "name": "Swindon",
    "facts": [
      {
        "claim": "Swindon's Bus Company has used a green and blue livery with red and yellow highlights since April 2017, two months after Go-Ahead took over.",
        "qualification": "Swindon's Bus Company, part of Go-Ahead.",
        "source": "https://en.wikipedia.org/wiki/Swindon%27s_Bus_Company"
      }
    ]
  },
  "151": {
    "name": "Swindon decker",
    "facts": [
      {
        "claim": "Swindon's Bus Company has used a green and blue livery with red and yellow highlights since April 2017, two months after Go-Ahead took over.",
        "qualification": "Swindon's Bus Company, part of Go-Ahead.",
        "source": "https://en.wikipedia.org/wiki/Swindon%27s_Bus_Company"
      }
    ]
  },
  "2126": {
    "name": "Swindon Corporation",
    "facts": [
      {
        "claim": "Swindon Corporation ran electric trams from 1904 and buses from 1927.",
        "qualification": "Heritage livery; the fact is about the old corporation, not this repaint.",
        "source": "https://en.wikipedia.org/wiki/Swindon%27s_Bus_Company"
      }
    ]
  },
  "1046": {
    "name": "FlixBus",
    "facts": [
      {
        "claim": "FlixBus was founded in Munich in 2011 and ran its first three routes, in Bavaria, in February 2013.",
        "qualification": "Brand-level.",
        "source": "https://en.wikipedia.org/wiki/FlixBus"
      },
      {
        "claim": "FlixBus often plans the network and handles booking and marketing, while partner companies run the coaches.",
        "qualification": "Brand-level; describes how the network is run.",
        "source": "https://en.wikipedia.org/wiki/FlixBus"
      }
    ]
  },
  "159": {
    "name": "Faresaver",
    "facts": [
      {
        "claim": "Faresaver traces its roots to 1979, when John Pickford started Fosseway Coaches with one minibus on a school contract. It is based in Chippenham.",
        "qualification": "Operator history.",
        "source": "https://en.wikipedia.org/wiki/Faresaver"
      }
    ]
  },
  "553": {
    "name": "Pulhams coach",
    "facts": [
      {
        "claim": "Pulhams of Bourton-on-the-Water began in 1880, with William Pulham's two-horse carrier's cart to Cheltenham.",
        "qualification": "Operator history.",
        "source": "https://www.pulhams.co.uk/about/history/"
      }
    ]
  },
  "854": {
    "name": "Pulhams",
    "facts": [
      {
        "claim": "Pulhams of Bourton-on-the-Water began in 1880, with William Pulham's two-horse carrier's cart to Cheltenham.",
        "qualification": "Operator history.",
        "source": "https://www.pulhams.co.uk/about/history/"
      },
      {
        "claim": "Pulhams' bus services have been numbered in the 800 series for at least four decades.",
        "qualification": "Operator history; the number of routes has fallen over time.",
        "source": "https://cbwmagazine.com/pulhams-coaches-past-and-present/"
      }
    ]
  },
  "2573": {
    "name": "Pulhams Travel",
    "facts": [
      {
        "claim": "Pulhams of Bourton-on-the-Water began in 1880, with William Pulham's two-horse carrier's cart to Cheltenham.",
        "qualification": "Operator history.",
        "source": "https://www.pulhams.co.uk/about/history/"
      }
    ]
  },
  "475": {
    "name": "Pulhams",
    "facts": [
      {
        "claim": "Pulhams of Bourton-on-the-Water began in 1880, with William Pulham's two-horse carrier's cart to Cheltenham.",
        "qualification": "Operator history.",
        "source": "https://www.pulhams.co.uk/about/history/"
      }
    ]
  },
  "4152": {
    "name": "Pulhams 99",
    "facts": [
      {
        "claim": "Pulhams bought four Mercedes-Benz Citaros new for route 99, with 41 leather seats, USB charging and wi-fi.",
        "qualification": "Describes the route 99 fleet as reported; this bus may not be one of them.",
        "source": "https://www.pulhams.co.uk/pulhams-on-point/"
      }
    ]
  },
  "2075": {
    "name": "Newport Bus Electric",
    "facts": [
      {
        "claim": "Newport Bus, owned by Newport City Council, ran the first electric buses in Wales, starting with 15 Yutong E12s ordered with a £1 million grant in 2019.",
        "qualification": "Operator history.",
        "source": "https://en.wikipedia.org/wiki/Newport_Bus"
      }
    ]
  },
  "1055": {
    "name": "Newport Bus",
    "facts": [
      {
        "claim": "Newport Bus is owned by Newport City Council and has used a dark green and white livery since August 2009.",
        "qualification": "Municipal operator, Newport.",
        "source": "https://en.wikipedia.org/wiki/Newport_Bus"
      }
    ]
  },
  "7": {
    "name": "Olympia",
    "facts": [
      {
        "claim": "First began introducing its purple, white and lilac 'Olympia' livery in 2012, bringing back local branding at the same time.",
        "qualification": "First Bus UK-wide; since succeeded by Ultraviolet.",
        "source": "https://en.wikipedia.org/wiki/FirstGroup"
      }
    ]
  },
  "3029": {
    "name": "Ultraviolet",
    "facts": [
      {
        "claim": "Ultraviolet is First Bus's purple and grey livery, already being rolled out before First's December 2024 rebrand and new logo.",
        "qualification": "First Bus UK-wide.",
        "source": "https://en.wikipedia.org/wiki/FirstGroup"
      }
    ]
  },
  "42": {
    "name": "Stagecoach Gold",
    "facts": [
      {
        "claim": "Stagecoach Gold began in 2007 as Goldline, a premium brand trialled in Perth and Warwick with leather seats, metallic paint and free wi-fi. It became Stagecoach Gold in 2009.",
        "qualification": "Brand-level premium sub-brand.",
        "source": "https://en.wikipedia.org/wiki/Stagecoach_Gold"
      },
      {
        "claim": "Stagecoach South Wales put 24 new Gold buses on services 120 and 130, between the Rhondda and Caerphilly, in December 2017, an investment of over £4 million.",
        "qualification": "South Wales only.",
        "source": "https://www.stagecoachbus.com/news/south-wales/2017/december/stagecoach-gold"
      }
    ]
  },
  "4150": {
    "name": "Bristol Airport Flyer",
    "facts": [
      {
        "claim": "The Airport Flyer A1 links Bristol Airport with Bedminster, Temple Meads and Bristol Bus Station, and runs 24 hours a day.",
        "qualification": "Route-branded livery; timetables change.",
        "source": "https://www.bristolairport.co.uk/to-and-from-the-airport/by-airport-flyer/"
      }
    ]
  },
  "1107": {
    "name": "The Big Lemon",
    "facts": [
      {
        "claim": "The Big Lemon began in 2006, when a group of people met in a Brighton pub and set up a community interest company to run affordable, sustainable buses.",
        "qualification": "Operator history, by its own account.",
        "source": "https://thebiglemon.com/"
      },
      {
        "claim": "The Big Lemon says it launched the UK's first solar-powered electric bus in 2017.",
        "qualification": "The operator's own claim.",
        "source": "https://thebiglemon.com/"
      }
    ]
  },
  "2152": {
    "name": "The Big Lemon",
    "facts": [
      {
        "claim": "The Big Lemon began in 2006, when a group of people met in a Brighton pub and set up a community interest company to run affordable, sustainable buses.",
        "qualification": "Operator history, by its own account.",
        "source": "https://thebiglemon.com/"
      }
    ]
  },
  "1488": {
    "name": "metrobus",
    "facts": [
      {
        "claim": "Bristol's metrobus was a joint project of Bristol, North Somerset and South Gloucestershire councils. Its first route, the m3, began on 29 May 2018.",
        "qualification": "System-level branding.",
        "source": "https://en.wikipedia.org/wiki/MetroBus_(Bristol)"
      }
    ]
  },
  "352": {
    "name": "metrobus m3",
    "facts": [
      {
        "claim": "The m3 was the first metrobus route, starting on 29 May 2018 from Emersons Green.",
        "qualification": "Route-specific branding.",
        "source": "https://en.wikipedia.org/wiki/MetroBus_(Bristol)"
      }
    ]
  },
  "353": {
    "name": "metrobus m2",
    "facts": [
      {
        "claim": "The metrobus m2 from Long Ashton began on 3 September 2018, partly on a guided busway of about 2 km.",
        "qualification": "Route-specific branding.",
        "source": "https://en.wikipedia.org/wiki/MetroBus_(Bristol)"
      }
    ]
  },
  "351": {
    "name": "metrobus m1",
    "facts": [
      {
        "claim": "The metrobus m1 began running on 6 January 2019.",
        "qualification": "Route-specific branding.",
        "source": "https://en.wikipedia.org/wiki/MetroBus_(Bristol)"
      }
    ]
  },
  "2677": {
    "name": "metrobus m4",
    "facts": [
      {
        "claim": "The metrobus m4 started on 22 January 2023, from The Mall at Cribbs Causeway to the city centre via Bristol Parkway and UWE, using the £57 million Cribbs Patchway MetroBus Extension.",
        "qualification": "Route-specific branding.",
        "source": "https://www.patchwaytowncouncil.gov.uk/post/new-metrobus-service-launched-this-weekend"
      }
    ]
  },
  "910": {
    "name": "Megabus",
    "facts": [
      {
        "claim": "Stagecoach's Megabus ran its first service, Oxford to London, on 11 August 2003, using former Hong Kong Citybus Leyland Olympians.",
        "qualification": "Brand history.",
        "source": "https://cbwmagazine.com/the-uks-first-internet-only-coach-operator-turns-20/"
      }
    ]
  },
  "221": {
    "name": "Badgerline",
    "facts": [
      {
        "claim": "Badgerline began in April 1985, running Bristol Omnibus services in Somerset and Avon outside the city of Bristol.",
        "qualification": "Historical brand.",
        "source": "https://en.wikipedia.org/wiki/Badgerline"
      },
      {
        "claim": "First relaunched its Weston-super-Mare services as Badgerline in August 2018, with a new take on the classic livery.",
        "qualification": "Revived brand, First West of England.",
        "source": "https://en.wikipedia.org/wiki/Badgerline"
      }
    ]
  },
  "1494": {
    "name": "Eurocoaches",
    "facts": [
      {
        "claim": "Eurocoaches started as a taxi firm in the early 1980s, became Eurotaxis in 1991 and grew into coaches. It runs from a three-acre site in Yate.",
        "qualification": "Operator history.",
        "source": "https://eurocoaches.com/about-us/"
      }
    ]
  },
  "2514": {
    "name": "Coachstyle",
    "facts": [
      {
        "claim": "Coachstyle has run bus and coach services from Castle Combe, Wiltshire, since 1995.",
        "qualification": "Operator history.",
        "source": "https://www.coachstyle.uk/"
      }
    ]
  },
  "2516": {
    "name": "Coachstyle",
    "facts": [
      {
        "claim": "Coachstyle has run bus and coach services from Castle Combe, Wiltshire, since 1995.",
        "qualification": "Operator history.",
        "source": "https://www.coachstyle.uk/"
      }
    ]
  },
  "328": {
    "name": "Salisbury Reds",
    "facts": [
      {
        "claim": "Salisbury Reds was introduced in 2010 as a sub-brand on Wilts & Dorset routes in Salisbury.",
        "qualification": "Out-of-area livery; its presence here reflects transfers within Go-Ahead.",
        "source": "https://en.wikipedia.org/wiki/Salisbury_Reds_(bus_company)"
      }
    ]
  },
  "3201": {
    "name": "Stagecoach Specialist",
    "facts": [
      {
        "claim": "Specialist is the ocean green scheme in Stagecoach's 2020 livery, used for services such as park-and-ride.",
        "qualification": "Group-wide Stagecoach livery.",
        "source": "https://www.keybuses.com/article/stagecoach-dons-new-clothes"
      }
    ]
  },
  "584": {
    "name": "Stagecoach Specialist",
    "facts": [
      {
        "claim": "Specialist is the ocean green scheme in Stagecoach's 2020 livery, used for services such as park-and-ride.",
        "qualification": "Group-wide Stagecoach livery.",
        "source": "https://www.keybuses.com/article/stagecoach-dons-new-clothes"
      }
    ]
  },
  "4197": {
    "name": "Stagecoach Pride",
    "facts": [
      {
        "claim": "Stagecoach West decorated a bus in Progress Pride flag colours for its sponsorship of Swindon and Wiltshire Pride on 12 August 2023.",
        "qualification": "Stagecoach West.",
        "source": "https://www.route-one.net/news/stagecoach-west-to-sponsor-swindon-pride/"
      }
    ]
  },
  "3106": {
    "name": "Berrys Exclusive",
    "facts": [
      {
        "claim": "Berrys Exclusive tours carry two staff on board, with drinks and snacks served at your seat.",
        "qualification": "Berry's Coaches tour branding.",
        "source": "https://www.berryscoaches.co.uk/day-tours/exclusive-tours/"
      }
    ]
  },
  "1289": {
    "name": "First supporting Pride",
    "facts": [
      {
        "claim": "First West of England introduced a bus in Progress Pride flag colours on 8 July 2021.",
        "qualification": "First West of England.",
        "source": "https://www.route-one.net/news/first-west-of-england-celebrates-pride-with-progress-flag-livery/"
      }
    ]
  },
  "797": {
    "name": "LeedsCity Green",
    "facts": [
      {
        "claim": "First Leeds's services were rebranded LeedsCity in early 2018, with a mainly green livery.",
        "qualification": "Out-of-area livery; its presence here reflects transfers within First.",
        "source": "https://en.wikipedia.org/wiki/First_West_Yorkshire"
      }
    ]
  },
  "1907": {
    "name": "TrawsCymru Red",
    "facts": [
      {
        "claim": "TrawsCymru is the Welsh Government's long-distance bus network. Newport Bus took over the T7 between Chepstow and Bristol in January 2021.",
        "qualification": "Network-level branding.",
        "source": "https://en.wikipedia.org/wiki/TrawsCymru"
      }
    ]
  },
  "455": {
    "name": "Abus",
    "facts": [
      {
        "claim": "Abus was founded in May 1991 by former Badgerline manager Alan Peters, and is based at St Philip's Marsh in Bristol.",
        "qualification": "Operator history.",
        "source": "https://en.wikipedia.org/wiki/Abus_(bus_operator)"
      }
    ]
  },
  "3523": {
    "name": "abus",
    "facts": [
      {
        "claim": "Abus was founded in May 1991 by former Badgerline manager Alan Peters, and is based at St Philip's Marsh in Bristol.",
        "qualification": "Operator history.",
        "source": "https://en.wikipedia.org/wiki/Abus_(bus_operator)"
      }
    ]
  },
  "2661": {
    "name": "CT Coaches",
    "facts": [
      {
        "claim": "CT Coaches is a family-run coach hire business in Radstock, near Bath, established more than 30 years ago.",
        "qualification": "Operator's own description.",
        "source": "https://coachhirebath.com/"
      }
    ]
  },
  "1088": {
    "name": "Somerbus",
    "facts": [
      {
        "claim": "Somerbus was set up by Tim Jennings in 1987; he retired in July 2021 after 33 years of running buses.",
        "qualification": "Historical operator; the fleet file lists this vehicle under Abus.",
        "source": "https://www.mnrjournal.co.uk/news/thirty-three-years-of-running-buses-comes-to-an-end-279728"
      }
    ]
  },
  "4225": {
    "name": "WESTBus Pride",
    "facts": [
      {
        "claim": "Two green WESTbus buses were unveiled in a rainbow design ahead of Bristol Pride on 11 July, keeping it for 12 months to promote LGBTQ+ inclusion.",
        "qualification": "Temporary livery.",
        "source": "https://www.route-one.net/people/more-pride-buses-take-to-the-road-in-bristol-and-brighton/"
      }
    ]
  },
  "4289": {
    "name": "WestLink",
    "facts": [
      {
        "claim": "WESTlink is the West of England's on-demand service: green minibuses booked by app, website or phone, from 7am to 7pm, Monday to Saturday.",
        "qualification": "Hours may change.",
        "source": "https://www.westofengland-ca.gov.uk/what-we-do/transport/bus/getonboard/"
      }
    ]
  }
};
