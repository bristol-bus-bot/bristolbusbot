"""Python components install on the Pi only from committed, hashed locks (#97)."""
import re
import sys
import tomllib
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import push  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
LOCKS = {
    "collector": ["collector/pyproject.toml"],
    "site": ["site/pyproject.toml", "collector/pyproject.toml"],
    "pipeline": ["pipeline/requirements-runtime.txt"],
}
PIN = re.compile(r"^([A-Za-z0-9_.-]+)==\S+ \\$", re.M)


def _name(requirement: str) -> str:
    return re.split(r"[<>=!~;\[ ]", requirement, maxsplit=1)[0].strip().lower().replace("_", "-")


def _declared(source: str) -> set[str]:
    path = REPO / source
    if path.suffix == ".toml":
        project = tomllib.loads(path.read_text(encoding="utf-8"))["project"]
        names = set()
        for requirement in project["dependencies"]:
            if "sys_platform == 'win32'" in requirement:
                continue  # never installed on the Pi
            names.add(_name(requirement))
        return names
    return {_name(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")}


def _lock(component: str) -> str:
    return (REPO / component / "requirements.lock").read_text(encoding="utf-8")


def test_every_lock_is_built_for_the_pi_with_hashes():
    for component in LOCKS:
        text = _lock(component)
        header = text.splitlines()[1]
        # Keep uv's header: Dependabot regenerates the lock from it (#108).
        assert "uv pip compile" in header
        assert "--python-platform aarch64-manylinux_2_28" in header
        assert "--python-version 3.13" in header
        assert "--generate-hashes" in header
        pins = PIN.findall(text)
        assert pins, component
        assert text.count("--hash=sha256:") >= len(pins)


def test_every_declared_dependency_is_in_its_lock():
    """Adding a dependency without regenerating the lock fails here."""
    for component, sources in LOCKS.items():
        locked = {_name(name) for name in PIN.findall(_lock(component))}
        for source in sources:
            missing = _declared(source) - locked
            assert not missing, f"{component} lock is missing {sorted(missing)}"


def test_build_tools_are_locked_for_components_built_on_the_pi():
    for component in ("collector", "site"):
        assert re.search(r"^setuptools==", _lock(component), re.M)


def test_pi_installs_only_from_the_lock_and_records_its_runtime():
    release = PurePosixPath("/srv/releases/x/r1")
    for component in LOCKS:
        command = push.setup_command(component, release)
        assert "--require-hashes --no-deps -r requirements.lock" in command
        assert "runtime.json" in command
        assert "pip install -q ." not in command
        assert "gunicorn" not in command  # comes from the lock now
    for component in ("collector", "site"):
        assert "--no-deps --no-build-isolation" in push.setup_command(component, release)


def test_releases_ship_their_lock(tmp_path, monkeypatch):
    for component in LOCKS:
        root = tmp_path / component
        root.mkdir()
        push.populate_release(component, root)
        assert (root / "requirements.lock").is_file(), component
