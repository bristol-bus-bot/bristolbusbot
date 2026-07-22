# Technical decisions

This document records the durable choices behind the current architecture.
Implementation details belong in the component READMEs and source code.

## One collector and one delay value

Only `collector/` polls BODS. The site, audit and bot consume its databases
rather than fetching and interpreting the same feed independently.

Delay is measured from a reported vehicle position against a published
timetable and stored in seconds. It is not blended with historic performance,
smoothed or predicted. Where a trip cannot be matched confidently, no delay is
reported.

The live map and published audit use different samples for different purposes:
the map shows the closest scheduled-stop estimate from the latest poll, while
the audit uses settled closest-approach readings at registered timing points.
Both originate from the same match and observation pipeline.

## Multi-source timetable

No single timetable source is complete enough for the project. The production
database is built in layers:

1. BODS South West GTFS for the main dataset.
2. Operator TransXChange for services lost during GTFS conversion.
3. The Traveline National Dataset for services absent from BODS.

A candidate database must pass integrity, service-date, required-route and
route-shape checks before it can replace the active timetable. Promotion is
atomic and retains the previous database for rollback.

GitHub Actions is the normal heavy-build plane; the Pi is the scheduler and
production safety plane. The unprivileged delivery service verifies one exact
default-branch artifact, while a separate fixed-path root service revalidates,
promotes, restarts consumers, checks health and rolls back. The workstation is
an attended fallback, not a production scheduling dependency.

## SQLite and component boundaries

SQLite matches the scale and operating environment of the project. Each
database has an explicit writer and a small set of readers, documented in
[`ARCHITECTURE.md`](ARCHITECTURE.md). WAL mode is used for live application
databases; timetable deployment uses a closed, read-only database file.

The project deliberately avoids a network database, containers and a frontend
framework. Those would add operational overhead without solving a current
problem.

## Read-only public services

The live website does not accept user accounts or write to the transport
databases. It binds to loopback and is exposed through a named tunnel. Dynamic
browser content is created with safe DOM helpers, and third-party browser
requests are limited to map-image tiles.

The bot control API also binds to loopback and requires a bearer token. Its
production input is the collector event stream; direct SIRI ingest remains an
explicit diagnostic mode rather than a default.

## Immutable deployment and rollback

Application code is deployed as immutable, hash-manifested releases. An atomic
symlink selects the active release for each component. A deployment restarts
only the affected service and keeps the new release only if its health check
passes; otherwise the previous symlink is restored automatically.

Configuration, credentials and mutable databases live outside releases. SSH
host keys are checked strictly, and deployment never copies `.env` files or
runtime databases from the repository.

## Audit publication

The public audit uses the DfT timing-point on-time band of −60 to +359 seconds.
Definitions, limitations and measurement changes are published in
[`AUDIT_METHODOLOGY.md`](AUDIT_METHODOLOGY.md).

The live map shows only a small, linked audit headline. Detailed statistics and
caveats remain on the audit site. The headline and vehicle profiles disappear
when their publication snapshot is stale or below the configured sample gates.

## Social-media expansion

Optional Instagram and Threads publishing must run outside the collector and
existing Bluesky delivery path. A failure of an optional social integration
must not delay or alter collection, the websites, the audit or Bluesky. The
proposed editorial and technical constraints are in
[`plans/SOCIAL_EXPANSION_PLAN.md`](plans/SOCIAL_EXPANSION_PLAN.md).

## Daylight interface

The live site uses a light map and interface inspired by UK transport signage,
with the departure board retained as a deliberately dark object. Mobile views
allow one major overlay at a time so a selected route remains visible and
usable.
