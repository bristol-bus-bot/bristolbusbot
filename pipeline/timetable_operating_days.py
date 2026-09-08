"""Strict ordinary-day evaluation for exact source-calendar corrections.

Return None for unsupported rules: False must mean proven non-operation.
WorkingDays ranges are inclusive; the TXC-PTI profile defines their complement
as holidays (PTI v1.1 sections 3.2 and 9.3). No public-holiday edits are made.
"""
from datetime import date
from functools import lru_cache
import xml.etree.ElementTree as ET

from timetable_calendar_evidence import HOLIDAYS, WEEKDAYS
from frequency_changes import england_wales_bank_holidays


@lru_cache(maxsize=8192)
def operating_day(raw: bytes, day: date) -> bool | None:
    try:
        doc = ET.fromstring(b'<Evidence>' + raw + b'</Evidence>')
        if not len(doc) or doc[0].tag != 'OperatingProfile':
            return None
        root = doc[0]
        allowed = {'RegularDayType', 'BankHolidayOperation', 'ServicedOrganisationDayType'}
        if any(e.tag not in allowed for e in root) or any(
                len(root.findall(tag)) > 1 for tag in allowed):
            return None
        regular = root.find('RegularDayType')
        if regular is None or len(regular) != 1 or regular[0].tag != 'DaysOfWeek':
            return None
        masks = {name.title(): {i} for i, name in enumerate(WEEKDAYS)}
        masks.update(MondayToFriday=set(range(5)), MondayToSaturday=set(range(6)),
                     MondayToSunday=set(range(7)), Weekend={5, 6})
        weekdays = set()
        for e in regular[0]:
            if e.tag not in masks or len(e) or e.attrib or (e.text or '').strip():
                return None
            weekdays.update(masks[e.tag])
        if not weekdays:
            return None
        blocked = set(england_wales_bank_holidays(day.year)) | {
            date(day.year, 1, 1), date(day.year, 12, 24), date(day.year, 12, 25),
            date(day.year, 12, 26), date(day.year, 12, 31)}
        bank = root.find('BankHolidayOperation')
        if bank is not None:
            if len(bank) != 1 or bank[0].tag != 'DaysOfNonOperation':
                return None
            for e in bank[0]:
                if e.tag == 'OtherPublicHoliday':
                    if any(x.tag not in {'Description', 'Date'} for x in e) or len(e.findall('Date')) != 1:
                        return None
                    blocked.add(date.fromisoformat(e.findtext('Date', '')))
                elif e.tag not in HOLIDAYS or len(e):
                    return None
        if day in blocked:
            return None
        organisations = {}
        for e in list(doc)[1:]:
            if e.tag != 'ServicedOrganisation':
                return None
            code = e.findtext('OrganisationCode')
            if not code or code in organisations:
                return None
            organisations[code] = e
        school = root.find('ServicedOrganisationDayType')
        school_day = True
        if school is not None:
            # Multiple organisations and combined rules need explicit precedence;
            # decline them rather than assuming union/intersection semantics.
            if len(school) != 1 or school[0].tag not in {'DaysOfOperation', 'DaysOfNonOperation'}:
                return None
            rule = school[0]
            if len(rule) != 1 or rule[0].tag not in {'WorkingDays', 'Holidays'}:
                return None
            refs = rule[0]
            if len(refs) != 1 or refs[0].tag != 'ServicedOrganisationRef' or len(refs[0]):
                return None
            org = organisations.get(refs[0].text)
            if org is None or any(e.tag not in {'OrganisationCode', 'Name', 'WorkingDays'} for e in org):
                return None
            periods = org.findall('WorkingDays')
            if len(periods) != 1 or not len(periods[0]):
                return None
            ranges = []
            for e in periods[0]:
                if e.tag != 'DateRange' or any(x.tag not in {'StartDate', 'EndDate', 'Description'} for x in e):
                    return None
                if len(e.findall('StartDate')) != 1 or len(e.findall('EndDate')) != 1:
                    return None
                start = date.fromisoformat(e.findtext('StartDate', ''))
                end = date.fromisoformat(e.findtext('EndDate', ''))
                if end < start:
                    return None
                ranges.append((start, end))
            # Do not infer the next academic year's unpublished holiday calendar.
            if day > max(end for _, end in ranges):
                return None
            working = any(start <= day <= end for start, end in ranges)
            school_day = working if refs.tag == 'WorkingDays' else not working
            if rule.tag == 'DaysOfNonOperation':
                school_day = not school_day
        return day.weekday() in weekdays and school_day
    except (ET.ParseError, ValueError, TypeError):
        return None


def nonoperation_witnesses(day, candidates):
    """Require exact journey/full-call witnesses with one unambiguous scope.

    Use the newest available declaration of this exact journey, not absence
    from another edition. Every declaration at that date must explicitly say
    it does not operate. A missing or unsupported source is never a negative.
    """
    eligible = [w for w in candidates if w.start <= day and (w.end is None or day <= w.end)]
    if not eligible or len({w.scope for w in eligible}) != 1:
        return []
    newest = max(w.start for w in eligible)
    selected = [w for w in eligible if w.start == newest]
    return selected if all(operating_day(w.profile, day) is False for w in selected) else []
