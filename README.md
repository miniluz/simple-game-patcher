# Simple Game Patcher

A file-based patching system for managing game modifications. Applies patch files to game directories while maintaining backups for safe reversion.

This was coded with AI almost exclusively, and is a personal project. It's tested, but the tests were written by AI.
I feel comfortable enough to use it myself, but you probably shouldn't unless you know how to debug stuff yourself.

## Requirements

- Python 3.6 or higher
- No external dependencies

## Installation

Download or copy `simple-game-patcher.py` from this repository. The script is self-contained and requires no additional files or libraries.

## Usage

### Quick start

```bash
# Initialize configuration
python3 simple-game-patcher.py init

# Edit config.json to add your game
# Add patch files to patches/mygame/

# Apply patches
python3 simple-game-patcher.py apply mygame

# Check status
python3 simple-game-patcher.py status mygame

# Revert when done
python3 simple-game-patcher.py revert mygame
```

### Initialize Configuration

Create a template configuration file and patches directory structure:

```bash
python3 simple-game-patcher.py init
```

This creates:
- `config.json` - Configuration file with an example game entry
- `patches/example-game/` - Empty directory for patch files

### Configure Games

Edit `config.json` to define your games:

```json
{
  "games": {
    "actual-game": {
      "target": "/path/to/actual-game/directory",
      "backup": "/path/to/backups/actual-game"
    }
  }
}
```

- `target`: Game installation directory where patches will be applied
- `backup`: Directory where original files will be backed up

### Add Patch Files

Place your patch files in `patches/<game-name>/` with the same directory structure as the target game:

```
patches/
└── mygame/
    ├── data/
    │   └── config.ini
    └── game.exe
```

### Apply Patches

Apply patches to a configured game:

```bash
python3 simple-game-patcher.py apply <game-name>
```

This will:
- Back up original files before modification
- Copy patch files to the target directory
- Track applied patches in state.json

### Check Status

View the status of applied patches:

```bash
python3 simple-game-patcher.py status <game-name>
```

Shows whether each patched file is clean, modified, or missing.

### Revert Patches

Restore original files:

```bash
python3 simple-game-patcher.py revert <game-name>
```

This removes all patches and restores backed-up files.

## Features

### Conflict Detection

When applying patches, the patcher detects if files have been modified since the last patch. You will be prompted to:
- Abort the operation
- Force overwrite (discard changes)
- Re-backup (treat modified file as new baseline)

### Re-patching

Applying patches multiple times updates to the newest patch version while preserving the original file backup. This allows you to update patches without needing to revert first.

### Rollback on Failure

If patching fails partway through, changes are automatically rolled back to maintain consistency.

### Concurrent Operation Prevention

File-based locking prevents multiple operations from running simultaneously on the same game.

## Configuration Options

The `--config-dir` flag specifies where to find `config.json` and the `patches/` directory. Defaults to the script's directory.

```bash
python3 simple-game-patcher.py --config-dir /path/to/config apply mygame
```
## State Management

The patcher maintains state in `<backup-dir>/state.json`, tracking:
- Applied patches and their checksums
- Original file checksums for conflict detection
- Whether files existed before patching

Do not manually edit this file.

## Development Environment

This repository includes Nix packaging for reproducible development environments.

### Using Nix Shell

Enter a development shell with all dependencies:

```bash
nix-shell
```

This uses `shell.nix` with flake-compat, so Nix flakes are not required.

### Using direnv

If you have direnv installed, the repository will automatically load the Nix environment when you enter the directory:

```bash
cd simple-game-patcher
# Environment loads automatically
```

Make sure to allow direnv on first use:

```bash
direnv allow
```

### Running Tests

The test suite can be run with:

```bash
pytest test_patcher.py -v
```

