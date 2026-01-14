"""
E2E tests for simple-game-patcher
Tests run in isolated temporary directories with real file operations
"""
import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from threading import Thread

import pytest


@pytest.fixture
def test_env(tmp_path):
    """Create isolated test environment with config, game target, and patches"""
    config_dir = tmp_path / "config"
    game_target = tmp_path / "game"
    patches_dir = config_dir / "patches" / "testgame"

    config_dir.mkdir()
    game_target.mkdir()
    patches_dir.mkdir(parents=True)

    config = {
        "games": {
            "testgame": {
                "target": str(game_target),
                "backup": str(config_dir / "backups" / "testgame")
            }
        }
    }
    (config_dir / "config.json").write_text(json.dumps(config, indent=2))

    (game_target / "file1.txt").write_text("original content 1")
    (game_target / "file2.txt").write_text("original content 2")

    (patches_dir / "file1.txt").write_text("patched content 1")
    (patches_dir / "file2.txt").write_text("patched content 2")
    (patches_dir / "newfile.txt").write_text("new file content")

    return {
        "config_dir": config_dir,
        "game_target": game_target,
        "patches_dir": patches_dir,
        "script": Path(__file__).parent / "simple-game-patcher.py"
    }


def run_patcher(script, config_dir, command):
    """Run patcher command and return result"""
    result = subprocess.run(
        ["python3", str(script), "--config-dir", str(config_dir), command, "testgame"],
        capture_output=True,
        text=True
    )
    return result


def compute_checksum(file_path):
    """Compute SHA256 checksum of a file"""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        sha256.update(f.read())
    return sha256.hexdigest()


def test_happy_path_apply_and_revert(test_env):
    """Test 1: Apply patches to clean game, verify, then revert"""
    script = test_env["script"]
    config_dir = test_env["config_dir"]
    game_target = test_env["game_target"]

    result = run_patcher(script, config_dir, "apply")
    assert result.returncode == 0, f"Apply failed: {result.stderr}"
    assert "Successfully patched 3 file(s)" in result.stdout

    assert (game_target / "file1.txt").read_text() == "patched content 1"
    assert (game_target / "file2.txt").read_text() == "patched content 2"
    assert (game_target / "newfile.txt").read_text() == "new file content"

    result = run_patcher(script, config_dir, "status")
    assert result.returncode == 0
    assert "clean" in result.stdout

    result = run_patcher(script, config_dir, "revert")
    assert result.returncode == 0
    assert "Reverted 3 file(s)" in result.stdout

    assert (game_target / "file1.txt").read_text() == "original content 1"
    assert (game_target / "file2.txt").read_text() == "original content 2"
    assert not (game_target / "newfile.txt").exists()


def test_repatching_preserves_original_checksum(test_env):
    """Test 2: Re-patching preserves original file checksum (not v1 checksum)"""
    script = test_env["script"]
    config_dir = test_env["config_dir"]
    game_target = test_env["game_target"]
    patches_dir = test_env["patches_dir"]
    state_file = config_dir / "backups" / "testgame" / "state.json"

    original_checksum = compute_checksum(game_target / "file1.txt")

    result = run_patcher(script, config_dir, "apply")
    assert result.returncode == 0

    state = json.loads(state_file.read_text())
    assert state["file1.txt"]["original_checksum"] == original_checksum

    (patches_dir / "file1.txt").write_text("patched content v2")

    result = run_patcher(script, config_dir, "apply")
    assert result.returncode == 0
    assert "Successfully patched" in result.stdout

    assert (game_target / "file1.txt").read_text() == "patched content v2"

    state = json.loads(state_file.read_text())
    assert state["file1.txt"]["original_checksum"] == original_checksum

    result = run_patcher(script, config_dir, "revert")
    assert result.returncode == 0
    assert (game_target / "file1.txt").read_text() == "original content 1"


def test_rollback_preserves_existing_backups(test_env):
    """Test 3: Rollback preserves existing backups on failure"""
    script = test_env["script"]
    config_dir = test_env["config_dir"]
    game_target = test_env["game_target"]
    patches_dir = test_env["patches_dir"]
    backup_dir = config_dir / "backups" / "testgame"
    state_file = config_dir / "backups" / "testgame" / "state.json"

    result = run_patcher(script, config_dir, "apply")
    assert result.returncode == 0

    state_after_apply = json.loads(state_file.read_text())

    assert (backup_dir / "file1.txt").exists()
    backup_checksum = compute_checksum(backup_dir / "file1.txt")

    target_file = game_target / "file2.txt"
    target_file.chmod(0o444)

    try:
        result = run_patcher(script, config_dir, "apply")
        assert result.returncode != 0
        assert "rolling back" in result.stdout.lower()
    finally:
        target_file.chmod(0o644)

    assert (backup_dir / "file1.txt").exists()
    assert compute_checksum(backup_dir / "file1.txt") == backup_checksum

    state_after_rollback = json.loads(state_file.read_text())
    assert state_after_rollback == state_after_apply

    assert (game_target / "file1.txt").read_text() == "patched content 1"


def test_conflict_detection(test_env):
    """Test 4: Detect when patched files are manually modified"""
    script = test_env["script"]
    config_dir = test_env["config_dir"]
    game_target = test_env["game_target"]

    result = run_patcher(script, config_dir, "apply")
    assert result.returncode == 0

    (game_target / "file1.txt").write_text("manually modified content")

    result = run_patcher(script, config_dir, "status")
    assert result.returncode == 0
    assert "MODIFIED" in result.stdout
    assert "file1.txt" in result.stdout

    assert (game_target / "file1.txt").read_text() == "manually modified content"
    assert (game_target / "file2.txt").read_text() == "patched content 2"
    assert (game_target / "newfile.txt").read_text() == "new file content"


def test_new_file_handling(test_env):
    """Test 5: New files are created on apply and removed on revert"""
    script = test_env["script"]
    config_dir = test_env["config_dir"]
    game_target = test_env["game_target"]

    assert not (game_target / "newfile.txt").exists()

    result = run_patcher(script, config_dir, "apply")
    assert result.returncode == 0

    assert (game_target / "newfile.txt").exists()
    assert (game_target / "newfile.txt").read_text() == "new file content"

    result = run_patcher(script, config_dir, "revert")
    assert result.returncode == 0

    assert not (game_target / "newfile.txt").exists()


def test_concurrent_operations_blocked(test_env):
    """Test 6: Concurrent operations fail with lock error"""
    script = test_env["script"]
    config_dir = test_env["config_dir"]
    game_target = test_env["game_target"]
    patches_dir = test_env["patches_dir"]

    for i in range(20):
        (patches_dir / f"file{i}.txt").write_text(f"content {i}")

    results = []

    def run_apply(index):
        result = subprocess.run(
            ["python3", str(script), "--config-dir", str(config_dir), "apply", "testgame"],
            capture_output=True,
            text=True
        )
        results.append((index, result))

    thread1 = Thread(target=run_apply, args=(1,))
    thread2 = Thread(target=run_apply, args=(2,))

    thread1.start()
    thread2.start()

    thread1.join()
    thread2.join()

    success_count = sum(1 for _, r in results if r.returncode == 0)
    lock_error_count = sum(
        1 for _, r in results
        if r.returncode != 0 and (
            "lock" in r.stderr.lower() or
            "another patcher operation is in progress" in r.stderr.lower()
        )
    )

    assert success_count == 1, f"Expected 1 success, got {success_count}"
    assert lock_error_count == 1, f"Expected 1 lock error, got {lock_error_count}"

    assert (game_target / "file1.txt").read_text() == "content 1"
    assert (game_target / "file2.txt").read_text() == "content 2"
    assert (game_target / "newfile.txt").read_text() == "new file content"
    for i in range(20):
        assert (game_target / f"file{i}.txt").read_text() == f"content {i}"


def test_missing_config_file(tmp_path):
    """Test 7: Error when config.json is missing"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    script = Path(__file__).parent / "simple-game-patcher.py"

    result = run_patcher(script, config_dir, "apply")
    assert result.returncode != 0
    assert "Config file not found" in result.stderr or "not found" in result.stderr.lower()


def test_game_not_in_config(tmp_path):
    """Test 8: Error when game is not defined in config"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    script = Path(__file__).parent / "simple-game-patcher.py"

    config = {"games": {"othergame": {"target": "/tmp/other", "backup": "/tmp/backup"}}}
    (config_dir / "config.json").write_text(json.dumps(config))

    result = subprocess.run(
        ["python3", str(script), "--config-dir", str(config_dir), "apply", "testgame"],
        capture_output=True,
        text=True
    )
    assert result.returncode != 0
    assert "not found in config" in result.stderr or "testgame" in result.stderr


def test_missing_target_directory(tmp_path):
    """Test 9: Error when target directory doesn't exist"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    patches_dir = config_dir / "patches" / "testgame"
    patches_dir.mkdir(parents=True)
    script = Path(__file__).parent / "simple-game-patcher.py"

    config = {
        "games": {
            "testgame": {
                "target": str(tmp_path / "nonexistent"),
                "backup": str(config_dir / "backups" / "testgame")
            }
        }
    }
    (config_dir / "config.json").write_text(json.dumps(config))
    (patches_dir / "file1.txt").write_text("patch content")

    result = run_patcher(script, config_dir, "apply")
    assert result.returncode != 0
    assert "does not exist" in result.stderr or "not exist" in result.stderr.lower()


def test_missing_patches_directory(tmp_path):
    """Test 10: Error when patches directory doesn't exist"""
    config_dir = tmp_path / "config"
    game_target = tmp_path / "game"
    config_dir.mkdir()
    game_target.mkdir()
    script = Path(__file__).parent / "simple-game-patcher.py"

    config = {
        "games": {
            "testgame": {
                "target": str(game_target),
                "backup": str(config_dir / "backups" / "testgame")
            }
        }
    }
    (config_dir / "config.json").write_text(json.dumps(config))

    result = run_patcher(script, config_dir, "apply")
    assert result.returncode != 0
    assert "Patches directory not found" in result.stderr or "not found" in result.stderr


def test_conflict_resolution_abort(test_env):
    """Test 11: Conflict resolution - abort option"""
    script = test_env["script"]
    config_dir = test_env["config_dir"]
    game_target = test_env["game_target"]

    result = run_patcher(script, config_dir, "apply")
    assert result.returncode == 0

    (game_target / "file1.txt").write_text("manually modified content")

    result = subprocess.run(
        ["python3", str(script), "--config-dir", str(config_dir), "apply", "testgame"],
        input="a\n",  # Choose abort
        capture_output=True,
        text=True
    )

    assert "Conflict detected" in result.stdout
    assert "Patching aborted" in result.stdout

    assert (game_target / "file1.txt").read_text() == "manually modified content"


def test_conflict_resolution_force(test_env):
    """Test 12: Conflict resolution - force overwrite option"""
    script = test_env["script"]
    config_dir = test_env["config_dir"]
    game_target = test_env["game_target"]
    patches_dir = test_env["patches_dir"]

    result = run_patcher(script, config_dir, "apply")
    assert result.returncode == 0

    (game_target / "file1.txt").write_text("manually modified content")

    (patches_dir / "file1.txt").write_text("patched content v2")

    result = subprocess.run(
        ["python3", str(script), "--config-dir", str(config_dir), "apply", "testgame"],
        input="f\n",  # Choose force
        capture_output=True,
        text=True
    )

    assert result.returncode == 0
    assert "Conflict detected" in result.stdout
    assert "Successfully patched" in result.stdout

    assert (game_target / "file1.txt").read_text() == "patched content v2"


def test_conflict_resolution_rebackup(test_env):
    """Test 13: Conflict resolution - re-backup option"""
    script = test_env["script"]
    config_dir = test_env["config_dir"]
    game_target = test_env["game_target"]
    patches_dir = test_env["patches_dir"]
    backup_dir = config_dir / "backups" / "testgame"

    result = run_patcher(script, config_dir, "apply")
    assert result.returncode == 0

    modified_content = "manually modified content"
    (game_target / "file1.txt").write_text(modified_content)

    (patches_dir / "file1.txt").write_text("patched content v2")

    result = subprocess.run(
        ["python3", str(script), "--config-dir", str(config_dir), "apply", "testgame"],
        input="r\n",  # Choose re-backup
        capture_output=True,
        text=True
    )

    assert result.returncode == 0
    assert "Conflict detected" in result.stdout
    assert "Successfully patched" in result.stdout

    assert (game_target / "file1.txt").read_text() == "patched content v2"

    assert (backup_dir / "file1.txt").read_text() == modified_content

    result = run_patcher(script, config_dir, "revert")
    assert result.returncode == 0
    assert (game_target / "file1.txt").read_text() == modified_content


def test_subdirectory_patches(test_env):
    """Test 14: Patches in subdirectories are handled correctly"""
    script = test_env["script"]
    config_dir = test_env["config_dir"]
    game_target = test_env["game_target"]
    patches_dir = test_env["patches_dir"]

    (game_target / "data" / "levels").mkdir(parents=True)
    (game_target / "data" / "levels" / "level1.dat").write_text("original level 1")

    (patches_dir / "data" / "levels").mkdir(parents=True)
    (patches_dir / "data" / "levels" / "level1.dat").write_text("modded level 1")
    (patches_dir / "data" / "config.ini").write_text("modded config")

    result = run_patcher(script, config_dir, "apply")
    assert result.returncode == 0

    assert (game_target / "data" / "levels" / "level1.dat").read_text() == "modded level 1"
    assert (game_target / "data" / "config.ini").read_text() == "modded config"

    result = run_patcher(script, config_dir, "revert")
    assert result.returncode == 0

    assert (game_target / "data" / "levels" / "level1.dat").read_text() == "original level 1"
    assert not (game_target / "data" / "config.ini").exists()

    backup_dir = config_dir / "backups" / "testgame"
    assert not (backup_dir / "data").exists() or not any((backup_dir / "data").rglob("*"))


def test_no_patches_applied_status(test_env):
    """Test 15: Status/revert on clean game with no patches applied"""
    script = test_env["script"]
    config_dir = test_env["config_dir"]
    game_target = test_env["game_target"]

    result = run_patcher(script, config_dir, "status")
    assert result.returncode == 0
    assert "No patches applied" in result.stdout

    assert (game_target / "file1.txt").read_text() == "original content 1"
    assert (game_target / "file2.txt").read_text() == "original content 2"
    assert not (game_target / "newfile.txt").exists()

    result = run_patcher(script, config_dir, "revert")
    assert result.returncode == 0
    assert "No patches applied" in result.stdout

    assert (game_target / "file1.txt").read_text() == "original content 1"
    assert (game_target / "file2.txt").read_text() == "original content 2"
    assert not (game_target / "newfile.txt").exists()


def test_patched_file_missing(test_env):
    """Test 16: Status correctly detects when patched file goes missing"""
    script = test_env["script"]
    config_dir = test_env["config_dir"]
    game_target = test_env["game_target"]

    result = run_patcher(script, config_dir, "apply")
    assert result.returncode == 0

    (game_target / "file1.txt").unlink()

    result = run_patcher(script, config_dir, "status")
    assert result.returncode == 0
    assert "MISSING" in result.stdout
    assert "file1.txt" in result.stdout

    assert not (game_target / "file1.txt").exists()
    assert (game_target / "file2.txt").read_text() == "patched content 2"
    assert (game_target / "newfile.txt").read_text() == "new file content"


def test_multiple_patch_versions_over_time(test_env):
    """Test 17: Multiple patch versions applied over time"""
    script = test_env["script"]
    config_dir = test_env["config_dir"]
    game_target = test_env["game_target"]
    patches_dir = test_env["patches_dir"]

    result = run_patcher(script, config_dir, "apply")
    assert result.returncode == 0
    assert (game_target / "file1.txt").read_text() == "patched content 1"

    (patches_dir / "file1.txt").write_text("patched content v2")
    result = run_patcher(script, config_dir, "apply")
    assert result.returncode == 0
    assert (game_target / "file1.txt").read_text() == "patched content v2"

    (patches_dir / "file1.txt").write_text("patched content v3")
    (patches_dir / "file3.txt").write_text("new patch file v3")
    result = run_patcher(script, config_dir, "apply")
    assert result.returncode == 0
    assert (game_target / "file1.txt").read_text() == "patched content v3"
    assert (game_target / "file3.txt").read_text() == "new patch file v3"

    result = run_patcher(script, config_dir, "revert")
    assert result.returncode == 0
    assert (game_target / "file1.txt").read_text() == "original content 1"
    assert not (game_target / "file3.txt").exists()


def test_game_update_simulation(test_env):
    """Test 18: Simulate game update while patches are applied"""
    script = test_env["script"]
    config_dir = test_env["config_dir"]
    game_target = test_env["game_target"]
    patches_dir = test_env["patches_dir"]
    state_file = config_dir / "backups" / "testgame" / "state.json"

    result = run_patcher(script, config_dir, "apply")
    assert result.returncode == 0
    assert (game_target / "file1.txt").read_text() == "patched content 1"

    (game_target / "file1.txt").write_text("original content 1 - updated by game")
    (game_target / "file2.txt").write_text("original content 2 - updated by game")

    result = run_patcher(script, config_dir, "status")
    assert result.returncode == 0
    assert "MODIFIED" in result.stdout
    assert "file1.txt" in result.stdout
    assert "file2.txt" in result.stdout

    result = subprocess.run(
        ["python3", str(script), "--config-dir", str(config_dir), "apply", "testgame"],
        input="r\nr\n",
        capture_output=True,
        text=True
    )
    assert result.returncode == 0
    assert (game_target / "file1.txt").read_text() == "patched content 1"

    state = json.loads(state_file.read_text())
    new_baseline_checksum = compute_checksum(config_dir / "backups" / "testgame" / "file1.txt")
    assert state["file1.txt"]["original_checksum"] == new_baseline_checksum

    result = run_patcher(script, config_dir, "revert")
    assert result.returncode == 0
    assert (game_target / "file1.txt").read_text() == "original content 1 - updated by game"
    assert (game_target / "file2.txt").read_text() == "original content 2 - updated by game"


def test_command_line_validation(tmp_path):
    """Test 19: Command-line argument validation"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    script = Path(__file__).parent / "simple-game-patcher.py"

    config = {
        "games": {
            "testgame": {
                "target": str(tmp_path / "game"),
                "backup": str(config_dir / "backups" / "testgame")
            }
        }
    }
    (config_dir / "config.json").write_text(json.dumps(config))

    result = subprocess.run(
        ["python3", str(script)],
        capture_output=True,
        text=True
    )
    assert result.returncode != 0

    result = subprocess.run(
        ["python3", str(script), "--config-dir", str(config_dir)],
        capture_output=True,
        text=True
    )
    assert result.returncode != 0

    result = subprocess.run(
        ["python3", str(script), "--config-dir", str(config_dir), "invalidcommand", "testgame"],
        capture_output=True,
        text=True
    )
    assert result.returncode != 0

    result = subprocess.run(
        ["python3", str(script), "--config-dir", str(config_dir), "apply"],
        capture_output=True,
        text=True
    )
    assert result.returncode != 0


def test_state_deleted_between_operations(test_env):
    """Test 20: State.json deleted between operations"""
    script = test_env["script"]
    config_dir = test_env["config_dir"]
    game_target = test_env["game_target"]
    state_file = config_dir / "backups" / "testgame" / "state.json"

    result = run_patcher(script, config_dir, "apply")
    assert result.returncode == 0
    assert (game_target / "file1.txt").read_text() == "patched content 1"

    assert state_file.exists()
    state_file.unlink()

    result = run_patcher(script, config_dir, "status")
    assert result.returncode == 0
    assert "No patches applied" in result.stdout or "state" in result.stdout.lower()

    result = run_patcher(script, config_dir, "revert")
    assert result.returncode == 0

    result = run_patcher(script, config_dir, "apply")
    assert result.returncode == 0
    assert (game_target / "file1.txt").read_text() == "patched content 1"


def test_init_creates_template_config(tmp_path):
    """Test 21: Init command creates template config.json with example"""
    config_dir = tmp_path / "config"
    script = Path(__file__).parent / "simple-game-patcher.py"

    result = subprocess.run(
        ["python3", str(script), "--config-dir", str(config_dir), "init"],
        capture_output=True,
        text=True
    )

    assert result.returncode == 0
    assert "Successfully initialized" in result.stdout

    assert (config_dir / "config.json").exists()
    assert (config_dir / "patches" / "example-game").exists()
    assert (config_dir / "patches" / "example-game").is_dir()

    config = json.loads((config_dir / "config.json").read_text())
    assert "games" in config
    assert "example-game" in config["games"]
    assert config["games"]["example-game"]["target"] == "/path/to/game/directory"
    assert "backup" in config["games"]["example-game"]


def test_init_overwrites_existing_config(tmp_path):
    """Test 22: Init can overwrite existing config.json"""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "config.json"
    script = Path(__file__).parent / "simple-game-patcher.py"

    config_file.write_text('{"games": {"oldgame": {"target": "/old/path", "backup": "/old/backup"}}}')

    result = subprocess.run(
        ["python3", str(script), "--config-dir", str(config_dir), "init"],
        input="y\n",
        capture_output=True,
        text=True
    )
    assert result.returncode == 0
    assert "already exists" in result.stdout

    config = json.loads(config_file.read_text())
    assert "example-game" in config["games"]
    assert "oldgame" not in config["games"]


def test_init_abort_overwrite(tmp_path):
    """Test 23: Init aborts when user declines to overwrite"""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "config.json"
    script = Path(__file__).parent / "simple-game-patcher.py"

    original_config = {"games": {"oldgame": {"target": "/old/path", "backup": "/old/backup"}}}
    config_file.write_text(json.dumps(original_config))

    result = subprocess.run(
        ["python3", str(script), "--config-dir", str(config_dir), "init"],
        input="n\n",
        capture_output=True,
        text=True
    )
    assert result.returncode == 0
    assert "cancelled" in result.stdout

    config = json.loads(config_file.read_text())
    assert config == original_config


def test_init_then_apply_patches_integration(tmp_path):
    """Test 24: Init, manual config edit, then apply patches (full integration)"""
    config_dir = tmp_path / "config"
    game_target = tmp_path / "game"
    game_target.mkdir(parents=True)
    script = Path(__file__).parent / "simple-game-patcher.py"

    (game_target / "file1.txt").write_text("original content")

    result = subprocess.run(
        ["python3", str(script), "--config-dir", str(config_dir), "init"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0

    config_file = config_dir / "config.json"
    config = json.loads(config_file.read_text())
    config["games"]["testgame"] = {
        "target": str(game_target),
        "backup": str(config_dir / "backups" / "testgame")
    }
    del config["games"]["example-game"]
    config_file.write_text(json.dumps(config, indent=2))

    patches_dir = config_dir / "patches" / "testgame"
    patches_dir.mkdir(parents=True, exist_ok=True)
    (patches_dir / "file1.txt").write_text("patched content")
    (patches_dir / "newfile.txt").write_text("new content")

    result = subprocess.run(
        ["python3", str(script), "--config-dir", str(config_dir), "apply", "testgame"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0

    assert (game_target / "file1.txt").read_text() == "patched content"
    assert (game_target / "newfile.txt").read_text() == "new content"

    result = subprocess.run(
        ["python3", str(script), "--config-dir", str(config_dir), "revert", "testgame"],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0

    assert (game_target / "file1.txt").read_text() == "original content"
    assert not (game_target / "newfile.txt").exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
