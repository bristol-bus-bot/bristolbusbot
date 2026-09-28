"""One parsed index of First Bristol TransXChange journeys and their editions.

BODS regional GTFS rewrites calendar dates: once a newer edition has started,
every edition's ``start_date`` is clipped to the feed date and open-ended
editions receive a synthetic end date. GTFS dates therefore cannot identify a
source edition. This index identifies editions from First's own TXC instead.

Terms used by the calendar and duplicate reconciliation:

* scope: ``(ServiceCode, LineName)``; an edition is one scope plus its
  ``OperatingPeriod`` start.
* lineage: every edition containing a GTFS trip's exact line, direction,
  vehicle journey code and complete ordered call sequence. First renumbers
  journey codes between editions, but codes can also recur, so lineage is a set.
* in force: the latest edition start on or before a day (and not ended).
* represented: an edition the GTFS actually carries, judged by exact matches
  that do not also match the preceding edition (see ``represented_editions``).

Nothing here decides whether a journey operates. Callers apply the existing
strict profile evaluators and record every witness they rely on.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
import hashlib
import io
from pathlib import Path
import zipfile

import txc_parser as txc
from timetable_calendar_evidence import Evidence, _time

# Reuse the edition-replacement proportion from timetable_editions.py.
MIN_REPRESENTED_RATIO = 0.25


@dataclass(frozen=True)
class SourceJourney:
    line: str
    direction: int
    code: str
    calls: tuple
    evidence: Evidence

    @property
    def start(self) -> date:
        return self.evidence.start

    @property
    def scope(self) -> tuple[str, str]:
        return self.evidence.scope


@dataclass
class SourceIndex:
    journeys: list[SourceJourney] = field(default_factory=list)
    editions: dict = field(default_factory=lambda: defaultdict(set))
    _exact: dict = field(default_factory=lambda: defaultdict(list))
    _schedule: dict = field(default_factory=lambda: defaultdict(list))
    _edition_sizes: dict = field(default_factory=lambda: defaultdict(int))
    _edition_ends: dict = field(default_factory=lambda: defaultdict(set))

    def add(self, journey: SourceJourney) -> None:
        self.journeys.append(journey)
        self.editions[journey.scope].add(journey.start)
        self._exact[(journey.line, journey.direction, journey.code, journey.calls)].append(journey)
        self._schedule[(journey.line, journey.direction, journey.calls)].append(journey)
        self._edition_sizes[(journey.scope, journey.start)] += 1
        self._edition_ends[(journey.scope, journey.start)].add(journey.evidence.end)

    def exact(self, key) -> list[Evidence]:
        """Witnesses for ``(line, direction, code, calls)`` in every edition."""
        return [journey.evidence for journey in self._exact.get(tuple(key), ())]

    def exact_journeys(self, key) -> list[SourceJourney]:
        return list(self._exact.get(tuple(key), ()))

    def same_schedule(self, line: str, direction: int, calls: tuple,
                      scope: tuple[str, str], start: date) -> list[SourceJourney]:
        return [journey for journey in self._schedule.get((line, direction, tuple(calls)), ())
                if journey.scope == scope and journey.start == start]

    def in_force(self, scope, day: date) -> date | None:
        starts = [start for start in self.editions.get(scope, ()) if start <= day]
        if not starts:
            return None
        latest = max(starts)
        ends = self._edition_ends.get((scope, latest), set())
        if ends and all(end is not None and day > end for end in ends):
            return None
        return latest

    def predecessor(self, scope, start: date) -> date | None:
        earlier = [value for value in self.editions.get(scope, ()) if value < start]
        return max(earlier) if earlier else None

    def edition_size(self, scope, start: date) -> int:
        return self._edition_sizes.get((scope, start), 0)


def build_source_index(directory: Path, lines: set[str] | None = None) -> SourceIndex:
    """Parse every First Bristol journey once; unsupported files abort the build."""
    index = SourceIndex()
    for path in sorted(Path(directory).glob('*.zip')):
        with path.open('rb') as handle:
            archive_sha = hashlib.file_digest(handle, 'sha256').hexdigest()
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if not info.filename.lower().endswith('.xml'):
                    continue
                if info.file_size > 128 * 1024 * 1024:
                    raise RuntimeError('TXC member exceeds calendar evidence size limit')
                raw = archive.read(info)
                if b'FBRI' not in raw:
                    continue
                if lines is not None and not any(
                        ('<LineName>' + line + '</LineName>').encode() in raw for line in lines):
                    continue
                doc = txc.TransXChange(io.BytesIO(raw))
                nocs = {op.get('id'): op.findtext('NationalOperatorCode')
                        for op in getattr(doc, 'operators', [])}
                file_sha = hashlib.sha256(raw).hexdigest()
                for service in doc.services.values():
                    if nocs.get(service.operator) != 'FBRI':
                        continue
                    period = service.operating_period
                    if not period.start:
                        raise RuntimeError('First source has no operating period start')
                    for line in service.lines:
                        if lines is not None and line.line_name not in lines:
                            continue
                        scope = (service.service_code, line.line_name)
                        for journey in doc.get_journeys(service.service_code, line.id):
                            if journey.operator and nocs.get(journey.operator) != 'FBRI':
                                continue
                            cells = list(journey.get_times())
                            if len(cells) < 2:
                                continue
                            profile = journey.operating_profile or service.operating_profile
                            calls = tuple((c.stopusage.stop.atco_code, _time(c.arrival_time),
                                           _time(c.departure_time)) for c in cells)
                            index.add(SourceJourney(
                                line.line_name,
                                1 if journey.journey_pattern.is_inbound() else 0,
                                journey.code, calls,
                                Evidence(scope, period.start, period.end,
                                         profile.hash if profile else b'',
                                         archive_sha, file_sha, info.filename)))
    return index


def lineage(index: SourceIndex, key) -> list[Evidence]:
    """All editions declaring this exact journey (possibly several)."""
    return index.exact(key)


def single_scope_lineage(index: SourceIndex, key):
    """Return ``(scope, newest_start, witnesses)`` or None when absent/ambiguous."""
    witnesses = index.exact(key)
    scopes = {witness.scope for witness in witnesses}
    if len(scopes) != 1:
        return None
    return next(iter(scopes)), max(witness.start for witness in witnesses), witnesses


def represented_editions(index: SourceIndex, keys) -> dict:
    """Editions the GTFS carries, from exact matches not shared with the predecessor.

    A handful of journeys can keep their code and schedule across editions, so a
    bare exact match would make an absent newer edition look present. An edition
    counts only when at least ``MIN_REPRESENTED_RATIO`` of its journeys are matched
    by GTFS trips that do not also match the preceding edition of that scope.
    """
    exclusive = defaultdict(int)
    for key in keys:
        found = {(witness.scope, witness.start) for witness in index.exact(key)}
        for scope, start in found:
            previous = index.predecessor(scope, start)
            if previous is None or (scope, previous) not in found:
                exclusive[(scope, start)] += 1
    result = defaultdict(set)
    for (scope, start), count in exclusive.items():
        if count >= MIN_REPRESENTED_RATIO * index.edition_size(scope, start):
            result[scope].add(start)
    return result
