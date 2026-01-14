#!/usr/bin/env python3
"""
Game Patcher - Manage file overlays for game modifications

Usage:
    ./patcher.py apply <game>
    ./patcher.py revert <game>
    ./patcher.py status <game>
"""

import argparse
import fcntl
import hashlib
import json
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class PatchedFile:
    """Represents a file that has been patched"""

    relative_path: str
    original_checksum: Optional[str]  # None if file didn't exist
    patched_checksum: str
    has_backup: bool


@dataclass
class GameConfig:
    """Configuration for a single game"""

    target: Path
    backup: Path


class PatcherError(Exception):
    """Base exception for patcher errors"""

    pass


class GameLock:
    """File-based lock for preventing concurrent operations on a game"""

    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self.lock_file = None

    def __enter__(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_file = open(self.lock_path, "w")
        try:
            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PatcherError("Another patcher operation is in progress for this game")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.lock_file:
            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
            self.lock_file.close()


class GamePatcher:
    """Manages patching operations for a single game"""

    def __init__(self, game_name: str, config_dir: Path):
        self.game_name = game_name
        self.config_dir = config_dir
        self.patches_dir = config_dir / "patches" / game_name
        self.config = self._load_config()
        self.state_file = self.config.backup / "state.json"
        self.lock_file = self.config.backup / "patcher.lock"

    def _load_config(self) -> GameConfig:
        """Load configuration for this game"""
        config_file = self.config_dir / "config.json"
        if not config_file.exists():
            raise PatcherError(f"Config file not found: {config_file}")

        with open(config_file) as f:
            config_data = json.load(f)

        if self.game_name not in config_data.get("games", {}):
            raise PatcherError(f"Game '{self.game_name}' not found in config")

        game_config = config_data["games"][self.game_name]
        return GameConfig(
            target=Path(game_config["target"]).expanduser(),
            backup=Path(game_config["backup"]).expanduser(),
        )

    def _compute_checksum(self, file_path: Path) -> str:
        """Compute SHA256 checksum of a file"""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def _load_state(self) -> Dict[str, PatchedFile]:
        """Load current patch state"""
        if not self.state_file.exists():
            return {}

        with open(self.state_file) as f:
            state_data = json.load(f)

        return {
            path: PatchedFile(**file_data) for path, file_data in state_data.items()
        }

    def _save_state(self, state: Dict[str, PatchedFile]):
        """Save patch state"""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w") as f:
            json.dump(
                {path: asdict(file_info) for path, file_info in state.items()},
                f,
                indent=2,
            )

    def _get_patch_files(self) -> List[Path]:
        """Get all files in the patches directory"""
        if not self.patches_dir.exists():
            raise PatcherError(f"Patches directory not found: {self.patches_dir}")

        patch_files = []
        for path in self.patches_dir.rglob("*"):
            if path.is_file():
                patch_files.append(path)

        return patch_files

    def _check_conflicts(
        self, relative_path: str, target_file: Path, state: Dict[str, PatchedFile]
    ) -> Optional[str]:
        """Check if a target file has been modified externally. Returns conflict type or None."""
        if not target_file.exists():
            return None

        current_checksum = self._compute_checksum(target_file)

        if relative_path in state:
            file_info = state[relative_path]
            if current_checksum != file_info.patched_checksum:
                return "modified"

        return None

    def _handle_conflict(self, relative_path: str, conflict_type: str) -> str:
        """Prompt user for conflict resolution. Returns action: 'abort', 're-backup', or 'force'."""
        print(f"\nConflict detected for {relative_path}:")
        print(f"  File has been {conflict_type} since last patch")
        print("\nOptions:")
        print("  [a] Abort")
        print("  [r] Re-backup (use current file as new baseline)")
        print("  [f] Force overwrite (discard changes)")

        while True:
            choice = input("\nChoice [a/r/f]: ").lower().strip()
            if choice in ("a", "abort"):
                return "abort"
            elif choice in ("r", "re-backup", "rebackup"):
                return "re-backup"
            elif choice in ("f", "force"):
                return "force"
            else:
                print("Invalid choice. Please enter 'a', 'r', or 'f'.")

    def _backup_file(self, target_file: Path, relative_path: str):
        """Backup a file to the backup directory"""
        backup_file = self.config.backup / relative_path
        backup_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target_file, backup_file)

    def _restore_file(self, relative_path: str, delete_backup: bool = True):
        """Restore a file from backup"""
        backup_file = self.config.backup / relative_path
        target_file = self.config.target / relative_path

        if backup_file.exists():
            shutil.copy2(backup_file, target_file)
            if delete_backup:
                backup_file.unlink()
        else:
            # File didn't exist originally, remove it
            if target_file.exists():
                target_file.unlink()

    def apply(self):
        """Apply patches to the game"""
        with GameLock(self.lock_file):
            if not self.config.target.exists():
                raise PatcherError(
                    f"Target directory does not exist: {self.config.target}"
                )

            state = self._load_state()
            patch_files = self._get_patch_files()

            if not patch_files:
                print(f"No patch files found in {self.patches_dir}")
                return

            # Pre-flight: check all patch files and detect conflicts
            operations = []
            for patch_file in patch_files:
                relative_path = str(patch_file.relative_to(self.patches_dir))
                target_file = self.config.target / relative_path

                needs_backup = target_file.exists()
                conflict = (
                    self._check_conflicts(relative_path, target_file, state)
                    if needs_backup
                    else None
                )

                force_rebackup = False
                if conflict:
                    action = self._handle_conflict(relative_path, conflict)
                    if action == "abort":
                        print("\nPatching aborted.")
                        return
                    elif action == "force":
                        needs_backup = False  # Don't preserve the modified file
                    elif action == "re-backup":
                        force_rebackup = True  # Force a new backup of the modified file

                operations.append(
                    {
                        "patch_file": patch_file,
                        "relative_path": relative_path,
                        "target_file": target_file,
                        "needs_backup": needs_backup,
                        "force_rebackup": force_rebackup,
                    }
                )

            # Execute patching with rollback on failure
            new_state = state.copy()
            patched_files = []

            try:
                for op in operations:
                    patch_file = op["patch_file"]
                    relative_path = op["relative_path"]
                    target_file = op["target_file"]
                    needs_backup = op["needs_backup"]
                    force_rebackup = op["force_rebackup"]

                    # Backup if needed
                    original_checksum = None
                    if needs_backup:
                        if (
                            relative_path not in state
                            or not state[relative_path].has_backup
                            or force_rebackup
                        ):
                            self._backup_file(target_file, relative_path)
                            original_checksum = self._compute_checksum(target_file)
                        else:
                            # Preserve original checksum from existing backup
                            original_checksum = state[relative_path].original_checksum

                    # Copy patch file
                    target_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(patch_file, target_file)

                    # Record operation
                    patched_checksum = self._compute_checksum(patch_file)
                    new_state[relative_path] = PatchedFile(
                        relative_path=relative_path,
                        original_checksum=original_checksum,
                        patched_checksum=patched_checksum,
                        has_backup=needs_backup,
                    )
                    patched_files.append(relative_path)

                # Save state
                self._save_state(new_state)
                print(f"\nSuccessfully patched {len(patched_files)} file(s)")

            except Exception as e:
                # Rollback - restore files but preserve backups and old state
                print(f"\nError during patching: {e}")
                print("Rolling back changes...")

                for relative_path in patched_files:
                    try:
                        # Restore file but don't delete backup (preserve for future operations)
                        self._restore_file(relative_path, delete_backup=False)
                    except Exception as rollback_error:
                        print(f"  Error rolling back {relative_path}: {rollback_error}")

                # Restore original state
                self._save_state(state)

                raise PatcherError("Patching failed and was rolled back")

    def revert(self):
        """Revert all patches"""
        with GameLock(self.lock_file):
            state = self._load_state()

            if not state:
                print("No patches applied")
                return

            for relative_path, file_info in state.items():
                try:
                    self._restore_file(relative_path)
                except Exception as e:
                    print(f"Error reverting {relative_path}: {e}")

            # Clean up backup directory if empty
            if self.config.backup.exists():
                try:
                    # Remove state file
                    if self.state_file.exists():
                        self.state_file.unlink()

                    # Remove empty directories
                    for dirpath in sorted(
                        self.config.backup.rglob("*"),
                        key=lambda p: len(p.parts),
                        reverse=True,
                    ):
                        if dirpath.is_dir() and not any(dirpath.iterdir()):
                            dirpath.rmdir()
                except Exception as e:
                    print(f"Warning: Could not clean up backup directory: {e}")

            print(f"Reverted {len(state)} file(s)")

    def status(self):
        """Show current patch status"""
        state = self._load_state()

        if not state:
            print("No patches applied")
            return

        print(f"\nPatched files for {self.game_name}:\n")

        clean_count = 0
        modified_count = 0
        missing_count = 0

        for relative_path, file_info in sorted(state.items()):
            target_file = self.config.target / relative_path

            if not target_file.exists():
                status = "MISSING"
                missing_count += 1
            else:
                current_checksum = self._compute_checksum(target_file)
                if current_checksum == file_info.patched_checksum:
                    status = "clean"
                    clean_count += 1
                else:
                    status = "MODIFIED"
                    modified_count += 1

            print(f"  [{status:8}] {relative_path}")

        print(
            f"\nSummary: {clean_count} clean, {modified_count} modified, {missing_count} missing"
        )


def main():
    parser = argparse.ArgumentParser(
        description="Manage file overlays for game modifications",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "command", choices=["apply", "revert", "status"], help="Command to execute"
    )
    parser.add_argument("game", help="Game name from config")
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=Path(__file__).parent,
        help="Directory containing config.json and patches/ (default: script directory)",
    )

    args = parser.parse_args()

    try:
        patcher = GamePatcher(args.game, args.config_dir)

        if args.command == "apply":
            patcher.apply()
        elif args.command == "revert":
            patcher.revert()
        elif args.command == "status":
            patcher.status()

    except PatcherError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nOperation cancelled", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
