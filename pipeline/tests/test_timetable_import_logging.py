from pathlib import Path
import subprocess
import sys


def test_importing_timetable_builders_creates_no_log_files(tmp_path):
    pipeline = Path(__file__).resolve().parents[1]
    subprocess.run(
        [sys.executable, "-B", "-c",
         "import sys; sys.path.insert(0, sys.argv[1]); "
         "import build_timetable, build_timetable_weca",
         str(pipeline)],
        cwd=tmp_path,
        check=True,
    )

    assert list(tmp_path.iterdir()) == []
