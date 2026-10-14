import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _workflow():
    path = REPOSITORY_ROOT / ".github" / "workflows" / "refresh-data.yml"
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _bash():
    if os.name == "nt":
        git_bash = Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "Git" / "bin" / "bash.exe"
        if git_bash.exists():
            return str(git_bash)
    return shutil.which("bash")


def test_manual_dispatch_exposes_refresh_controls_with_safe_defaults():
    inputs = _workflow()["on"]["workflow_dispatch"]["inputs"]

    assert inputs["sources"]["default"] == "auto"
    assert inputs["sources"]["options"] == ["auto", "manual", "both"]
    assert inputs["overwrite"]["default"] == "false"
    assert inputs["share_data"]["default"] == "false"
    assert inputs["generate_insights"]["default"] == "true"
    assert inputs["steelmakers"]["default"] == "NUE STLD CLF CMC"
    assert inputs["years"]["default"] == ""
    assert inputs["periods"]["default"] == ""
    assert "filing-parser" not in str(inputs).lower()


def test_run_parameter_validation_and_safe_bash_argument_arrays():
    workflow = _workflow()
    steps = workflow["jobs"]["build-data"]["steps"]
    params = next(step for step in steps if step.get("id") == "params")
    build_step = next(step for step in steps if step.get("name") == "Build financials and buybacks")
    insights_step = next(step for step in steps if step.get("name") == "Generate insights")

    parameter_script = params["run"]
    build_script = build_step["run"]
    insights_script = insights_step["run"]

    assert 'both) sources=(auto manual)' in parameter_script
    assert 'Invalid reporting year:' in parameter_script
    assert 'Invalid reporting period:' in parameter_script
    assert 'Boolean workflow inputs must be true or false' in parameter_script
    assert 'args=(--steelmakers "${steelmakers[@]}"' in build_script
    assert 'python -m scripts.build_data "${args[@]}"' in build_script
    assert '${{ steps.params.outputs.' not in build_script
    assert 'python -m sec_pipeline.pipeline --steelmakers "${steelmakers[@]}"' in insights_script
    assert '${{ steps.params.outputs.' not in insights_script
    assert insights_step["if"] == "steps.params.outputs.generate_insights == 'true'"
    assert "INPUT_STEELMAKERS" in params["env"]
    assert "INPUT_SOURCES" in params["env"]


def test_refresh_workflow_path_changes_trigger_core_ci():
    ci_path = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
    ci = yaml.load(ci_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)

    assert ".github/workflows/refresh-data.yml" in ci["on"]["push"]["paths"]
    assert ".github/workflows/refresh-data.yml" in ci["on"]["pull_request"]["paths"]


def test_scheduled_run_keeps_auto_without_overwrite_share_or_insights_skip():
    parameter_step = next(
        step
        for step in _workflow()["jobs"]["build-data"]["steps"]
        if step.get("id") == "params"
    )
    script = parameter_step["run"]

    assert 'source_mode="auto"' in script
    assert 'overwrite="false"' in script
    assert 'share_data="false"' in script
    assert 'generate_insights="true"' in script


def test_changed_workflow_scripts_pass_bash_syntax_check():
    bash = _bash()
    if not bash:
        pytest.skip("Bash is unavailable")

    steps = _workflow()["jobs"]["build-data"]["steps"]
    for name in ("Determine run parameters", "Build financials and buybacks", "Generate insights"):
        step = next(item for item in steps if item.get("name") == name)
        result = subprocess.run(
            [bash, "-n", "-c", step["run"]],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


def test_parameter_script_accepts_both_sources_and_validates_inputs(tmp_path):
    bash = _bash()
    if not bash:
        pytest.skip("Bash is unavailable")

    step = next(
        item
        for item in _workflow()["jobs"]["build-data"]["steps"]
        if item.get("id") == "params"
    )
    script = step["run"]

    def run(overrides):
        output_path = tmp_path / "github-output"
        env = {
            **os.environ,
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_OUTPUT": str(output_path),
            "INPUT_STEELMAKERS": "NUE",
            "INPUT_YEARS": "2024",
            "INPUT_PERIODS": "Q2",
            "INPUT_SOURCES": "both",
            "INPUT_OVERWRITE": "false",
            "INPUT_SHARE_DATA": "false",
            "INPUT_GENERATE_INSIGHTS": "true",
            **overrides,
        }
        result = subprocess.run(
            [bash, "-c", script],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        output = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
        return result, output

    accepted, output = run({})
    assert accepted.returncode == 0, accepted.stderr
    assert "sources=auto manual" in output

    invalid_values = [
        {"INPUT_STEELMAKERS": "NOT-A-STEELMAKER"},
        {"INPUT_YEARS": "20xx"},
        {"INPUT_PERIODS": "Q5"},
        {"INPUT_SOURCES": "auto;echo"},
        {"INPUT_OVERWRITE": "yes"},
        {"INPUT_SHARE_DATA": "yes"},
        {"INPUT_GENERATE_INSIGHTS": "yes"},
    ]
    for invalid in invalid_values:
        rejected, _ = run(invalid)
        assert rejected.returncode != 0
