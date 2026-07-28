#!/usr/bin/env bash
#
# Shared virtualenv resolution. Sourced by scripts/{test,lint,develop} — not
# meant to be executed directly.
#
# Call activate_venv from a script that has already cd'd to the repo root.
# Returns non-zero when no venv could be found, so each caller decides whether
# that is fatal (develop) or merely a fallback to whatever is on PATH (lint).

activate_venv() {
    if [[ -d "venv" ]]; then
        # shellcheck disable=SC1091
        source venv/bin/activate
        return 0
    fi

    # A linked worktree has no venv/ of its own, so fall back to the main
    # checkout's — git-common-dir points at the shared .git directory, whose
    # parent is the main working tree.
    local main_checkout
    main_checkout="$(dirname "$(git rev-parse --git-common-dir)")"
    if [[ -d "$main_checkout/venv" ]]; then
        # shellcheck disable=SC1091
        source "$main_checkout/venv/bin/activate"
        return 0
    fi

    return 1
}
