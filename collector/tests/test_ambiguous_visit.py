from datetime import datetime
from zoneinfo import ZoneInfo
from collector.delay import settled_reading_result

LDN=ZoneInfo('Europe/London')
# Geometry/time fixture based on the two Cumberland Basin visits. It is not
# a claimed replay of the original GPS message, whose position was not kept.
LOOP=[(3,'07:35:00',1,'OUT',51.45,-2.62),
      (7,'07:43:00',1,'TEMPLE',51.45,-2.58),
      (10,'07:53:00',1,'BROADMEAD',51.46,-2.59),
      (16,'08:07:00',1,'BACK',51.45034,-2.62)]
MIDNIGHT=datetime(2026,8,15,tzinfo=LDN)


def test_late_outbound_and_on_time_return_cannot_be_separated_from_one_position():
    reading,reason=settled_reading_result(51.45,-2.62,
        datetime(2026,8,15,8,6,37,tzinfo=LDN),LOOP,MIDNIGHT)
    assert reading is None
    assert reason=='ambiguous_stop_visit'


def test_genuinely_late_bus_at_unambiguous_stop_keeps_its_delay():
    reading,reason=settled_reading_result(51.45,-2.58,
        datetime(2026,8,15,8,13,tzinfo=LDN),LOOP,MIDNIGHT)
    assert reason is None
    assert reading.observed_delay_s==1800


def test_outbound_visit_before_return_is_time_plausible_still_measures():
    reading,reason=settled_reading_result(51.45,-2.62,
        datetime(2026,8,15,7,36,tzinfo=LDN),LOOP,MIDNIGHT)
    assert reason is None
    assert reading.stop_sequence==3
    assert reading.observed_delay_s==60
