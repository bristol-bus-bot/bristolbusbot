// Short, checked notes about bus models, keyed by the exact `vehicle_type.name`
// in the fleet file. They are evidence for the "model" post subject, so each
// note may contain only claims that were checked against a source on
// 2026-10-18 (manufacturer or operator material, trade press such as Route One
// and Coach & Bus Week, or the model's Wikipedia article and its references).
// Claims that sources contradicted or did not support were removed rather than
// softened. Keep notes to one or two plain sentences and add no opinions: the
// writer will repeat them as fact. "Unknown" deliberately has no note.

export const BUS_MODEL_BLURBS: Record<string, string> = {
  "Volvo B9TL Wright Eclipse Gemini": "Wrightbus's Eclipse Gemini double-deck body first appeared in 2001 on Volvo's B7TL chassis, and was built on the B9TL from 2006.",
  "Scania N280UD ADL Enviro400 City CBG": "A gas-powered double-decker: a Scania N280UD chassis running on compressed biomethane, with an Alexander Dennis Enviro400 City body. First West of England ordered 77 of them for Bristol in 2019.",
  "ADL Enviro400 MMC": "Alexander Dennis unveiled the Enviro400 MMC ('Major Model Change') in 2014 as the successor to the original Enviro400. It is built in Falkirk and Scarborough, as a complete bus or as a body on Scania and Volvo chassis.",
  "Yutong U11DD": "Yutong's battery-electric double-decker for the UK went into production in 2023, with a 220 kW electric motor. USB chargers are fitted in the seat backs, and the redesigned lower deck is step-free to the rear.",
  "Wright StreetDeck Ultroliner": "Wrightbus's diesel StreetDeck launched with a 5.1-litre, four-cylinder Daimler engine, and an optional micro-hybrid system that uses braking energy to power lights and air systems. The diesel version was renamed Ultroliner in 2021, to tell it apart from the electric and hydrogen StreetDecks.",
  "ADL Enviro400": "The original Alexander Dennis Enviro400 was built from 2005 to 2018, both as a complete bus and as a body on Scania and Volvo chassis. It was succeeded by the Enviro400 MMC.",
  "Scania N250UD ADL Enviro400 MMC": "Scania's N250UD double-deck chassis, launched in 2015 as the successor to the N230UD, carrying an Alexander Dennis Enviro400 MMC body.",
  "ADL Enviro200 MMC": "The Enviro200 MMC single-decker was unveiled in 2014 as the successor to the original Enviro200. Modified Enviro200s carried the UK's first passenger-carrying autonomous bus service, across the Forth Road Bridge in 2023.",
  "Volvo B7RLE Wright Eclipse 2": "Wrightbus unveiled the Eclipse 2 single-deck body in 2008, built only on Volvo's B7RLE chassis. It was replaced by the Eclipse 3 on the B8RLE in 2015.",
  "Wright StreetLite DF": "The StreetLite DF ('Door Forward') was launched in 2011, with the front axle behind the entrance door. It comes in 10.2 m and 10.8 m lengths.",
  "ADL Enviro200": "The first-generation Alexander Dennis Enviro200 single-decker was built from 2003 to 2018. It replaced the Dennis Dart SLF and was succeeded by the Enviro200 MMC.",
  "Volvo B7RLE Wright Eclipse Urban": "Wrightbus's Eclipse Urban body on Volvo's B7RLE low-entry chassis was launched in 2003. It was followed by the Eclipse 2 in 2008.",
  "Volvo B9TL Wright Eclipse Gemini 2": "The second-generation Wright Gemini double-deck body appeared in 2008-09 on Volvo's B9TL and hybrid B5LH chassis, with a low-roof version added in 2012. Lothian Buses and First West Yorkshire were among its UK buyers.",
  "Optare Solo SR": "Optare unveiled the Solo SR midibus in 2007, a restyled Solo with curved side windows. After a 2012 facelift it came in lengths from 7.2 m to 9.6 m, with a battery-electric version from 2012.",
  "Yutong E12": "Yutong's 12-metre battery-electric single-decker. Yutong lists a 422 kWh battery, and Newport Transport put its first E12s into service in 2019.",
  "ADL Enviro400 City": "Alexander Dennis launched the Enviro400 City in late 2015 as a premium version of the Enviro400 MMC, with a restyled front and a glass staircase as standard.",
  "Volvo B9R Plaxton Panther": "Plaxton launched the second-generation Panther coach body in 2010. On Volvo's B9R chassis it typically seats 53.",
  "Volvo B7TL Wright Eclipse Gemini": "The first-generation Wright Eclipse Gemini double-deck body, introduced in 2001 on Volvo's B7TL chassis.",
  "VDL DB300 Wright Gemini 2 HEV": "Only ten of these early diesel-electric hybrids were built, five each for Arriva London and First London. First's later moved to First West of England and First York.",
  "Wright StreetDeck Electroliner": "Wrightbus launched the battery-electric StreetDeck Electroliner in 2021, with a 454 kWh battery and a quoted range of up to 200 miles.",
  "Wright StreetDeck NewPower EV": "A StreetDeck converted from diesel to battery-electric under Wrightbus's NewPower repowering programme, run from a site in Bicester. Wrightbus offers 308 kWh and 385 kWh battery options.",
  "ADL Enviro100EV": "Alexander Dennis's 8.5-metre battery-electric midibus, first unveiled in 2023. It carries 45 passengers (25 seated), with a Voith electric drive and a 236 or 354 kWh battery.",
  "Yutong E9L": "A 9.5-metre version of Yutong's E9 electric midibus, about 60 cm longer than the E9, with a 120 kW motor and a 255 kWh battery. The longer front overhang lets wheelchair users board through the front door.",
  "Mercedes-Benz Tourismo": "Mercedes-Benz has built the Tourismo coach since 1994, with a revised version from 2006. By 2014, 21,000 had been sold.",
  "Yutong GT12": "A 12.2-metre Yutong coach with 53 seats, a 450 bhp DAF MX-11 engine and a ZF automatic gearbox. USB charging points are provided for every passenger.",
  "Scania N230UD OmniCity": "Scania's integral OmniCity double-decker, announced in 2004 and built in Slupsk, Poland, with a five-cylinder 9-litre engine. London operators took 462 of them.",
  "Scania CN230UD OmniCity": "Scania's integral OmniCity double-decker, announced in 2004 and built in Slupsk, Poland, with a five-cylinder 9-litre engine.",
  "Volvo B5TL MCV EvoSeti": "MCV's EvoSeti double-deck body was unveiled in 2015. On Volvo's B5TL chassis, with its D5K engine, it seats 75 to 82 passengers.",
  "Scania K410CB6 Caetano Levante 3A": "A coach with Caetano's Levante III body, which National Express unveiled in 2017. Caetano builds it in Portugal on Volvo and, as here, Scania chassis.",
  "Scania K410EB6 Caetano Levante 3": "A coach with Caetano's Levante III body, which National Express unveiled in 2017. Caetano builds it in Portugal on Volvo and, as here, Scania chassis.",
  "Scania N230UD ADL Enviro400": "An original Alexander Dennis Enviro400 body on a Scania N230UD chassis. The Enviro400 was built from 2005 to 2018, and when it was sold as a body alone, the N230UD was the chassis it most often went on.",
  "ADL/TransBus Enviro300": "TransBus launched the Enviro300 single-decker in 2001 as the first model in the Enviro range. Alexander Dennis took it over after TransBus collapsed in 2004 and built it until 2015.",
  "Scania K230UB ADL Enviro300": "An Alexander Dennis Enviro300 body on Scania's K230UB chassis, one of the chassis offered for the second-generation Enviro300, built from 2007 to 2015.",
  "ADL Enviro400EV": "Alexander Dennis's battery-electric double-decker, first launched with BYD in 2018. A second generation with a Voith drive followed, starting with 55 for Stagecoach West's Oxford depot in 2024.",
  "Optare Solo": "Optare's original Solo midibus, with its front wheels ahead of the door. The first production Solos entered service in 1998 with Wilts & Dorset in Poole.",
  "Mercedes-Benz Tourismo M/2": "Mercedes-Benz has built the Tourismo coach since 1994, with a revised version from 2006. By 2014, 21,000 had been sold.",
  "Mercedes-Benz Tourismo M/3": "Mercedes-Benz has built the Tourismo coach since 1994, with a revised version from 2006. By 2014, 21,000 had been sold.",
  "Yutong E10": "Yutong's 10.9-metre battery-electric single-decker. The first UK examples entered service with Go North East in 2020, and Newport Transport added E10s from 2022.",
  "Volvo B11RT Plaxton Elite i": "Plaxton's Elite i is a 15-metre interdeck coach on Volvo's B11RT chassis, with the passenger deck running over the driver's cab. Stagecoach first commissioned it for Megabus in 2012."
};
