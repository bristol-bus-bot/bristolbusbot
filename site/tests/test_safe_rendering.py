"""Prevent dynamic HTML strings from bypassing the frontend render helpers.

Static constant assignments must carry a ``// safe:`` annotation.
"""
import pathlib

JS_DIR = pathlib.Path(__file__).resolve().parent.parent / "static" / "js"


def test_no_unannotated_innerhtml():
    offenders = []
    for path in JS_DIR.rglob("*.js"):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if ".innerHTML" in line and "// safe:" not in line:
                offenders.append(f"{path.name}:{i}: {line.strip()[:80]}")
    assert not offenders, "innerHTML without safe-annotation:\n" + "\n".join(offenders)


def test_no_inline_event_handlers_in_modules():
    # modules must use el()'s listener support, never onclick= strings
    for path in JS_DIR.glob("*.js"):
        if path.name == "app.js":
            continue  # Archived compatibility code is outside the module policy.
        text = path.read_text(encoding="utf-8")
        assert 'onclick="' not in text, f"inline onclick in {path.name}"
