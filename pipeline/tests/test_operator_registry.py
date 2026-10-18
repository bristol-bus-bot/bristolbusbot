from pathlib import Path
import re
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "pipeline"))
import audit_operators as registry


def test_collector_carries_an_identical_copy_of_the_registry():
    canonical = (REPO / "pipeline" / "audit_operators.py").read_bytes()
    copy = (REPO / "collector" / "src" / "collector" / "operator_registry.py").read_bytes()
    assert copy.replace(b"\r\n", b"\n") == canonical.replace(b"\r\n", b"\n"), (
        "copy pipeline/audit_operators.py to collector/src/collector/operator_registry.py")


def test_every_local_bus_operator_is_published_and_coaches_are_not():
    for operator in registry.REGISTRY:
        assert operator.publish == (operator.kind == "local_bus"), operator.code
    assert registry.SHOW_OPERATORS[0] == "FBRI"
    assert len(set(registry.SHOW_OPERATORS)) == len(registry.SHOW_OPERATORS)
    for code in ("NATX", "FLIX", "SDVN", "BFBC", "NSEV"):
        assert code not in registry.SHOW_OPERATORS


def test_every_published_operator_has_a_display_name():
    for code in registry.SHOW_OPERATORS:
        assert registry.operator_name(code) != code


def test_repairs_stay_first_only_until_other_sources_are_inspected():
    assert registry.REPAIR_OPERATORS == ["FBRI"]
    assert "FBRI" in registry.SOURCE_OPERATORS


def test_no_other_module_keeps_its_own_operator_list():
    offenders = []
    for path in list((REPO / "pipeline").glob("*.py")) + list(
            (REPO / "collector" / "src" / "collector").glob("*.py")):
        if path.name in {"audit_operators.py", "operator_registry.py"}:
            continue
        text = path.read_text(encoding="utf-8")
        if '"FBRI", "SCGL", "LEMB"' in text or "'FBRI', 'SCGL', 'LEMB'" in text:
            offenders.append(path.name)
    assert not offenders


def test_live_feed_aliases_map_to_timetable_codes():
    assert registry.canonical_operator(" FBRI ") == "FBRI"
    assert registry.canonical_operator("") == ""


def test_social_cards_name_every_published_operator():
    text = (REPO / "social" / "build_pack.py").read_text(encoding="utf-8")
    block = text.split("DEFAULT_OPERATOR_NAMES = {", 1)[1].split("}", 1)[0]
    names = dict(re.findall(r'"([A-Z]{4})": "([^"]+)"', block))
    assert names == {code: registry.operator_name(code) for code in registry.SHOW_OPERATORS}


def test_bot_writes_about_the_same_local_bus_operators():
    text = (REPO / "bot" / "src" / "services" / "editorial-commentary-policy.ts").read_text(
        encoding="utf-8")
    block = text.split("export const OPERATOR_IDENTITIES", 1)[1].split("\n};", 1)[0]
    local = set(re.findall(r"^\s+([A-Z]{4}): \{[^\n]*localBus: true", block, re.M))
    assert local == set(registry.SHOW_OPERATORS)
