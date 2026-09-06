# Observed-position animation

The API exposes the original vehicle `recordedAt` timestamp. Marker movement
uses only strictly newer observations. A duplicate report does not restart an
animation; a newer report cancels the previous frame chain and starts from the
currently displayed position. Removed markers cancel their frames. Journey
changes, jumps over two kilometres and reduced-motion preferences use direct
placement rather than inventing a drive between unrelated positions.

Movement uses a 12-second glide at constant speed along the transition path.
The short 0.6–3-second catch-up trial was withdrawn after real-use feedback:
it made buses rush forward and then sit still. The glide stops at
the exact reported GPS coordinate and never extrapolates beyond it. The former
fixed 12-second transition and nearest-route-vertex destination are removed.

Only shapes with the exact operator, route and direction can guide the path.
Both ends must project unambiguously within 35 metres of the road segments.
Reverse paths, detours longer than 1.8 times direct distance plus 100 metres,
and multiple compatible variants fall back to the GPS-to-GPS transition.
The raw coordinates remain the start and endpoint even with route guidance.
These safeguards do not certify GPS accuracy or eliminate all intermediate
corner-cutting where trustworthy road geometry is unavailable.

Popups and the vehicle sidebar display the age of the observation. Visible
age labels update every second without fetching new data. Browser bus requests
remain at 15 seconds, cannot overlap, and time out after ten seconds. The
collector remains at 30 seconds; increasing that interval's frequency needs a
separate measurement of upstream publication cadence and request constraints.

Validation includes deterministic frame cancellation, duplicate/out-of-order
reports, reduced motion, journey changes, ambiguous geometry, sparse vertices,
and real-browser Leaflet movement. A bounded 50-second live sample on 6 September
contained 60 records across ten route 42/43 vehicles. The old nearest-vertex rule
would displace those points by a median 41 metres and maximum 113 metres.
Repeated samples are included; this is a geometry check, not a population error
rate or proof of the cause of the reported previous-night incident.

The exact Two Mile Hill westbound stop still needs confirmation: the timetable
uses nearby labels including Two Mile Court. No stop coordinates were changed.
