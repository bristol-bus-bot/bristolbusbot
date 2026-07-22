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
    assert '--source-status "$TMPDIR/busaudit_timetable_source_status.json"' in text
    assert "-eq 3" in text
    assert "BODS_API_KEY: ${{ secrets.BODS_API_KEY }}" in text
    assert "TNDS_USER: ${{ secrets.TNDS_USER }}" in text
    assert "TNDS_PASS: ${{ secrets.TNDS_PASS }}" in text


def test_secrets_are_environment_gated_and_scoped_to_build_step():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "environment: timetable-build" in text
    job_env = text.split("    env:", 1)[1].split("    steps:", 1)[0]
    assert "secrets." not in job_env
    build_step = text.split(
        "      - name: Build complete timetable candidate", 1)[1].split(
            "      - name:", 1)[0]
    assert "BODS_API_KEY: ${{ secrets.BODS_API_KEY }}" in build_step
    assert "TNDS_USER: ${{ secrets.TNDS_USER }}" in build_step
    assert "TNDS_PASS: ${{ secrets.TNDS_PASS }}" in build_step


def test_runner_temp_paths_are_set_only_after_the_runner_starts():
    text = WORKFLOW.read_text(encoding="utf-8")
    job_env = text.split("    env:", 1)[1].split("    steps:", 1)[0]
    assert "runner.temp" not in job_env
    prepare_step = text.split(
        "      - name: Prepare disposable build directories", 1)[1].split(
            "      - name:", 1)[0]
    assert 'TMPDIR=$RUNNER_TEMP/bbb-timetable-sources' in prepare_step
    assert 'SQLITE_TMPDIR=$RUNNER_TEMP/bbb-sqlite' in prepare_step
    assert 'BBB_TIMETABLE_DB=$RUNNER_TEMP/bbb-candidate/timetable.db' in prepare_step


def test_external_actions_are_pinned_to_immutable_commits():
    text = WORKFLOW.read_text(encoding="utf-8")
    uses_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- uses:"):
            stripped = stripped[2:]
        if stripped.startswith("uses:"):
            uses_lines.append(stripped)
    assert len(uses_lines) == 3
    for line in uses_lines:
        reference = line.split("@", 1)[1].split()[0]
        assert len(reference) == 40
        assert all(character in "0123456789abcdef" for character in reference)
