# Active writer context

The single writer receives evidence assembled by `buildStoryPrompt`. Its fact
checker receives that exact brief, including any targeted repair instructions.

- Stop geography comes from the reported stop code's enrichment entry and the
  matching stop-localities record. Locality, street, local authority and ward
  describe that stop, not the vehicle's current position. A ward is administrative
  geography, not necessarily a neighbourhood.
- Service stories can receive the exact collector-matched timetable's termini
  and scheduled stop position. This includes loops and short workings; it is not
  evidence of actual arrivals, departures or timing elsewhere on the journey.
  Public direction still follows the dedicated `towards` eligibility rules.
- Missing, mismatched or low-confidence context is omitted. Local-flavour prose,
  bounding-box neighbourhood guesses and legacy vehicle-model blurbs are not
  promoted into factual evidence.

The production social posting path calls `generatePost(event)` without pattern
or history. Although `buildAIContext` accepts those optional inputs, wiring them
into the prompt alone would not make them available. Route-level delay history
also cannot establish progress or changing delay for one vehicle. Network status
is operational context, not a representative network statistic.

Sourced livery, route, depot and place stories remain separate knowledge work.
The fixture-backed context integration tests exercise real loaders, writer,
repair and verifier using a mocked model transport, with no paid API calls.
