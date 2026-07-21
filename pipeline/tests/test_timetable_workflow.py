from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "timetable-build.yml"


def test_timetable_workflow_is_manual_shadow_build_only():
    text = WORKFLOW.read_text(encoding="utf-8")
    trigger_block = text.split("permissions:", 1)[0]
    assert "workflow_dispatch:" in trigger_block
    assert "pull_request" not in trigger_block
    assert "schedule:" not in trigger_block
    assert "cancel-in-progress: false" in text
    assert "contents: read" in text


def test_timetable_artifact_has_short_retention_and_exact_payload_gate():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "retention-days: 7" in text
    assert "if-no-files-found: error" in text
    assert "timetable_manifest.py verify" in text
    assert "-eq 3" in text
    assert "BODS_API_KEY: ${{ secrets.BODS_API_KEY }}" in text
    assert "TNDS_USER: ${{ secrets.TNDS_USER }}" in text
    assert "TNDS_PASS: ${{ secrets.TNDS_PASS }}" in text
