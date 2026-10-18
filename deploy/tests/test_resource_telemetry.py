import csv
import sys

import pytest

from deploy import sample_resources as resources


def test_host_memory_uses_available_not_just_free():
    values = resources.host_values(
        'MemTotal: 1000 kB\nMemFree: 10 kB\nMemAvailable: 400 kB\n'
        'SwapTotal: 500 kB\nSwapFree: 300 kB\n', '1.25 0.5 0.3 1/40 123')
    assert values == [600, 400, 200, 1.25]


@pytest.mark.skipif(sys.platform == 'win32', reason='Linux flock sampler')
def test_rotation_keeps_previous_complete_csv_and_header(tmp_path, monkeypatch):
    path = tmp_path / 'samples.csv'
    resources.append_rows(path, ['a', 'b'], [[1, 2]])
    old = path.read_bytes()
    monkeypatch.setattr(resources, 'MAX_SAMPLE_BYTES', 1)
    resources.append_rows(path, ['a', 'b'], [[3, 4]])
    assert path.with_name('samples.csv.1').read_bytes() == old
    with path.open(newline='') as stream:
        assert list(csv.reader(stream)) == [['a', 'b'], ['3', '4']]
