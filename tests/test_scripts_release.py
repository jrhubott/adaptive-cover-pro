"""Regression test for issue #1187 — `scripts/release` log leak.

`get_release_notes()` is captured wholesale via
`RELEASE_NOTES=$(get_release_notes ...)` in `main()`, so anything the
function's call graph writes to stdout becomes part of the published GitHub
release body / tag message. `log_info` and its siblings (`log_success`,
`log_warning`, `log_step`, `log_dry_run`) wrote to stdout instead of
stderr — only `log_error` redirected correctly. These tests prove the
captured return value of `get_release_notes` is exactly the expected
notes/template content for each of its branches, with no leaked `log_info`
line prepended, and that the log line itself still reaches stderr.

`scripts/release` is guarded at its bottom (`if [[ "${BASH_SOURCE[0]}" ==
"${0}" ]]; then main "$@"; fi`) so it is safe to `source` for testing without
triggering `main`'s interactive release flow — sourcing the file (rather
than executing it) makes `BASH_SOURCE[0]` differ from `$0`.

`scripts/release` unconditionally runs `cd "$(dirname "$0")/.."` at the top
of the file, before any function definition (and before the entry-point
guard, which only wraps `main`), so each helper pins a fake `scripts/release`
path under `tmp_path` as `$0` — this keeps the cd from mutating the real
test-process working directory and lands the shell inside `tmp_path`, which
the auto-detect case relies on for its relative `release_notes/v${version}.md`
lookup.
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
    auto_notes: bool = False,
    version: str = "2026.7.0",
    release_type: str = "stable",
) -> subprocess.CompletedProcess[str]:
    """Source scripts/release in a fresh bash process and call get_release_notes.

    `$0` is pinned to a fake `scripts/release` path under `tmp_path` so
    scripts/release's unconditional top-level `cd "$(dirname "$0")/.."`
    lands inside `tmp_path` (the real `scripts/` directory is untouched) and
    so `BASH_SOURCE[0]` (the real script path passed to `source`) differs
    from `$0`, keeping the entry-point guard from invoking `main`.
    """
    fake_scripts_dir = tmp_path / "scripts"
    fake_scripts_dir.mkdir(exist_ok=True)
    fake_self = fake_scripts_dir / "release"

    notes_file_assignment = f'NOTES_FILE="{notes_file}"' if notes_file else ""
    auto_notes_assignment = "AUTO_NOTES=true" if auto_notes else ""

    script = f"""
set -e
source "{_RELEASE_SCRIPT}"
{notes_file_assignment}
{auto_notes_assignment}
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
    """The `--notes FILE` branch of `get_release_notes` must not leak `log_info`."""
    notes_file = tmp_path / "notes.md"
    notes_file.write_text("## What's new\n- Fixed the thing\n")

    result = _run_get_release_notes(tmp_path, notes_file=notes_file)

    assert result.returncode == 0, result.stderr
    assert result.stdout == notes_file.read_text()
    # The log line must still fire — just on stderr, not mixed into stdout.
    assert "Reading release notes from" in result.stderr


def test_get_release_notes_stdout_matches_notes_file_for_auto_detect(
    tmp_path: pathlib.Path,
) -> None:
    """The auto-detect `release_notes/vX.Y.Z.md` branch of `get_release_notes` must not leak `log_info`."""
    version = "2026.7.0"
    notes_dir = tmp_path / "release_notes"
    notes_dir.mkdir()
    notes_file = notes_dir / f"v{version}.md"
    notes_file.write_text("## Auto-detected notes\n- Another thing\n")

    result = _run_get_release_notes(tmp_path, version=version)

    assert result.returncode == 0, result.stderr
    assert result.stdout == notes_file.read_text()
    # The log line must still fire — just on stderr, not mixed into stdout.
    assert "Using release notes from" in result.stderr


def test_get_release_notes_auto_notes_flag_does_not_leak(
    tmp_path: pathlib.Path,
) -> None:
    """The `--auto-notes` branch (`AUTO_NOTES=true`) of `get_release_notes` must not leak `log_info`."""
    result = _run_get_release_notes(tmp_path, auto_notes=True, release_type="stable")

    assert result.returncode == 0, result.stderr
    # A generated template has no notes file to compare against — the
    # meaningful assertions are that stdout is exactly the template (no
    # leading log icon/line) and that the log line landed on stderr instead.
    assert not result.stdout.startswith("ℹ")
    assert "Using auto-generated release notes" not in result.stdout
    assert result.stdout.startswith("# Adaptive Cover Pro")
    assert "Using auto-generated release notes" in result.stderr


def test_get_release_notes_generated_template_fallback_does_not_leak(
    tmp_path: pathlib.Path,
) -> None:
    """The generated-template fallback branch (no notes file, no `--auto-notes`,
    no `--editor`, and no `release_notes/vX.Y.Z.md` on disk) of
    `get_release_notes` must not leak `log_info`.
    """
    # No notes_file, no auto_notes, and no release_notes/ dir created under
    # tmp_path — this naturally falls through to the else/else branch.
    result = _run_get_release_notes(tmp_path)

    expected_log_line = "Using auto-generated release notes (use --editor to customize)"

    assert result.returncode == 0, result.stderr
    # A generated template isn't a notes file, so it won't equal one — the
    # meaningful assertions are: no leading log icon/line on stdout, and the
    # log line present on stderr instead.
    assert not result.stdout.startswith("ℹ")
    assert expected_log_line not in result.stdout
    assert result.stdout.startswith("# Adaptive Cover Pro")
    assert expected_log_line in result.stderr
