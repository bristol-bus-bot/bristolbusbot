# Weather and depot story gates

Weather uses only the fresh area observation already supplied by WeatherService.
The writer sees qualitative conditions, area and observation time. Routine wind,
humidity, visibility and temperature figures are omitted; temperature is retained
only at 3 C or below / 25 C or above. Mild cloudy weather is ineligible. Poor
visibility alone never establishes fog. Unrecognised observation formats are
omitted. These are editorial thresholds, not official weather warnings.

Depot subjects need the bus's garage from the fleet data and exact reported-stop
coordinates. Garages with a reviewed yard boundary use its centre; the fleet
names "Bath", "Weston" and "Keynsham" map by hand to their reviewed yards.
Garages without a reviewed boundary (Marlborough Street, Wells, and other
operators' garages such as Stagecoach West's Gloucester) use their town centre,
keyed by operator, and the evidence says so. The approximate straight-line
stop-to-garage distance must be at least DEPOT_STORY_MIN_KM (default 8; allowed
5-200). Unknown garages, missing coordinates and low-confidence events are
ineligible. The distance does not establish an unusual allocation, distance
travelled, departure or return. New names need a hand mapping, never fuzzy
matching.

Traffic subjects need a nearby road segment reported as moving slowly or very
slowly, or, in the weekday rush hours (07:00-09:59 and 16:00-18:59 UK time),
moving freely while the bus is at least 5 minutes late. The reading must be
under 2 minutes old.

No paid model audition was run. Deterministic tests cover mapping, evidence scope,
missing inputs and observed weather descriptions; live wording remains to assess.
