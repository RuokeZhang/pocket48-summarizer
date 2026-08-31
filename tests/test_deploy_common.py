from __future__ import annotations

import subprocess
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "deploy-common.sh"


def run_helper(release: Path, runtime: Path) -> subprocess.CompletedProcess:
    command = r"""
source "$1"
release="$2"
runtime="$3"
VOICE_MONITOR_READY_FILE="$runtime/room-voice-monitor-ready"
VOICE_MONITOR_STATUS_FILE="$runtime/room-voice-monitor-status.json"
VOICE_MONITOR_WANG_RUIQI_READY_FILE="$runtime/room-voice-monitor-wang-ruiqi-ready"
VOICE_MONITOR_WANG_RUIQI_STATUS_FILE="$runtime/room-voice-monitor-wang-ruiqi-status.json"
if voice_monitor_release_ready "$release"; then
  printf 'ready\n'
else
  printf 'not-ready\n'
fi
"""
    return subprocess.run(
        ["bash", "-c", command, "bash", str(SCRIPT), str(release), str(runtime)],
        check=True,
        capture_output=True,
        text=True,
    )


def write_status(path: Path, error_code: str | None = None) -> None:
    value = (
        '{"error_code":null}\n'
        if error_code is None
        else f'{{"error_code":"{error_code}"}}\n'
    )
    path.write_text(value, encoding="utf-8")


def test_voice_monitor_readiness_matches_candidate_targets(tmp_path):
    release = tmp_path / "release"
    target_dir = release / "deploy"
    runtime = tmp_path / "runtime"
    target_dir.mkdir(parents=True)
    runtime.mkdir()
    primary_ready = runtime / "room-voice-monitor-ready"
    primary_status = runtime / "room-voice-monitor-status.json"
    primary_ready.write_text(str(release), encoding="utf-8")
    write_status(primary_status)

    (target_dir / "room-voice-target.env").write_text(
        "POCKET48_VOICE_MEMBER_ID=407126\n",
        encoding="utf-8",
    )
    assert run_helper(release, runtime).stdout == "ready\n"

    (target_dir / "room-voice-target.env").write_text(
        'POCKET48_VOICE_ADDITIONAL_TARGETS_JSON='
        '[{"id":"wang-ruiqi","name":"王睿琦","member_id":530390}]\n',
        encoding="utf-8",
    )
    assert run_helper(release, runtime).stdout == "not-ready\n"

    wang_ready = runtime / "room-voice-monitor-wang-ruiqi-ready"
    wang_status = runtime / "room-voice-monitor-wang-ruiqi-status.json"
    wang_ready.write_text(str(release), encoding="utf-8")
    write_status(wang_status)
    assert run_helper(release, runtime).stdout == "ready\n"

    write_status(wang_status, "configuration_error")
    assert run_helper(release, runtime).stdout == "not-ready\n"
