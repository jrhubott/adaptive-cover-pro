"""Regression test for issue #1187 — `scripts/release` log leak.

`get_release_notes()` (`scripts/release:756-783`) is captured wholesale via
`RELEASE_NOTES=$(get_release_notes ...)` (`scripts/release:1118`), so
anything the function's call graph writes to stdout becomes part of the
published GitHub release body / tag message. `log_info` and its siblings
(`log_success`, `log_warning`, `log_step`, `log_dry_run`) wrote to stdout
instead of stderr — only `log_error` redirected correctly. This test proves
the captured return value of `get_release_notes` is exactly the notes-file
content, with no leaked `log_info` line prepended.

`scripts/release` is guarded at its bottom (`if [[ "${BASH_SOURCE[0]}" ==
"${0}" ]]; then main "$@"; fi`) so it is safe to `source` for testing without
triggering `main`'s interactive release flow — sourcing the file (rather
than executing it) makes `BASH_SOURCE[0]` differ from `$0`.

`scripts/release:21` unconditionally runs `cd "$(dirname "$0")/.."` on
source (before the entry-point guard, which only wraps `main`), so each
helper pins a fake `scripts/release` path under `tmp_path` as `$0` — this
keeps the cd from mutating the real test-process working directory and
lands the shell inside `tmp_path`, which the auto-detect case relies on for
its relative `release_notes/v${version}.md` lookup.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_RELEASE_SCRIPT = _REPO_ROOT / "scripts" / "release"


def _run_get_release_notes(
    tmp_path: pathlib.Path,
    *,
    notes_file: pathlib.Path | None = None,
    version: str = "2026.7.0",
    release_type: str = "stable",
) -> subprocess.CompletedProcess[str]:
    """Source scripts/release in a fresh bash process and call get_release_notes.

    `$0` is pinned to a fake `scripts/release` path under `tmp_path` so
    scripts/release:21's unconditional `cd "$(dirname "$0")/.."` lands
    inside `tmp_path` (the real `scripts/` directory is untouched) and so
    `BASH_SOURCE[0]` (the real script path passed to `source`) differs from
    `$0`, keeping the entry-point guard from invoking `main`.
    """
    fake_scripts_dir = tmp_path / "scripts"
    fake_scripts_dir.mkdir(exist_ok=True)
    fake_self = fake_scripts_dir / "release"

    notes_file_assignment = f'NOTES_FILE="{notes_file}"' if notes_file else ""

    script = f"""
set -e
source "{_RELEASE_SCRIPT}"
{notes_file_assignment}
get_release_notes "{version}" "{release_type}"
"""
    return subprocess.run(
        ["bash", "-c", script, str(fake_self)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_get_release_notes_stdout_matches_notes_file_for_explicit_notes(
    tmp_path: pathlib.Path,
) -> None:
    """The `--notes FILE` path (scripts/release:764) must not leak `log_info`."""
    notes_file = tmp_path / "notes.md"
    notes_file.write_text("## What's new\n- Fixed the thing\n")

    result = _run_get_release_notes(tmp_path, notes_file=notes_file)

    assert result.returncode == 0, result.stderr
    assert result.stdout == notes_file.read_text()


def test_get_release_notes_stdout_matches_notes_file_for_auto_detect(
    tmp_path: pathlib.Path,
) -> None:
    """The auto-detect `release_notes/vX.Y.Z.md` path (scripts/release:776) must not leak `log_info`."""
    version = "2026.7.0"
    notes_dir = tmp_path / "release_notes"
    notes_dir.mkdir()
    notes_file = notes_dir / f"v{version}.md"
    notes_file.write_text("## Auto-detected notes\n- Another thing\n")

    result = _run_get_release_notes(tmp_path, version=version)

    assert result.returncode == 0, result.stderr
    assert result.stdout == notes_file.read_text()
