# Weather and depot story gates

Weather uses only the fresh area observation already supplied by WeatherService.
The writer sees qualitative conditions, area and observation time. Routine wind,
humidity, visibility and temperature figures are omitted; temperature is retained
only at 3 C or below / 25 C or above. Mild cloudy weather is ineligible. Poor
visibility alone never establishes fog. Unrecognised observation formats are
omitted. These are editorial thresholds, not official weather warnings.

Depot subjects require an exact garage-name match to a reviewed yard in the
website's depot boundary data and exact reported-stop coordinates. Approximate
straight-line stop-to-yard distance must be at least DEPOT_STORY_MIN_KM (default
15; allowed 15–200). Unknown depots, missing coordinates and low-confidence events
are ineligible. The distance does not establish an unusual allocation, distance
travelled, departure or return. The checked-in centres derive from the reviewed
2026-09-10 boundaries; new names require a reviewed mapping, never fuzzy matching.

No paid model audition was run. Deterministic tests cover mapping, evidence scope,
missing inputs and observed weather descriptions; live wording remains to assess.
