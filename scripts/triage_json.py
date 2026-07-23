#!/usr/bin/env python3
"""Run the diagnostics-triage engine against a downloaded diagnostics JSON.

Usage:
    scripts/triage_json.py <diagnostics.json> [--lang en] [--latest-version X.Y.Z]

This is the offline twin of the in-product Troubleshoot step: it feeds a file a
user downloaded from Home Assistant (Settings → Devices → the integration →
Download diagnostics) through the SAME ``run_triage`` rule table, so a gap in one
surface is a gap in the other. Each finding is printed with its rendered prose
and a deep link into the canonical wiki findings page.

Offline seam: the download carries neither per-entity capabilities nor the
policy-derived axis requirements, so rules ``COVER_NOT_READY`` (8a) and
``COVER_FEATURE_MISMATCH`` (13) cannot fire offline — only the runtime
``ENTITY_UNAVAILABLE`` (8b) does. ``STALE_VERSION`` (24) fires only when you pass
``--latest-version``.

NOTE: run this with the project's dev virtualenv
(``venv/bin/python scripts/triage_json.py ...``) — importing the package pulls
Home Assistant, which is not on the system interpreter.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Put the repo root on sys.path so ``custom_components`` imports resolve when the
# script is run as ``venv/bin/python scripts/triage_json.py`` (Python otherwise
# only adds the ``scripts/`` directory). Mirrors scripts/bench_forecast.py.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.adaptive_cover_pro.diagnostics.triage import (  # noqa: E402
    TRIAGE_RULES,
    build_offline_view,
    render_report,
    run_triage,
)
from custom_components.adaptive_cover_pro.troubleshoot_i18n import (  # noqa: E402
    load_troubleshoot_labels,
)

_WIKI_BASE = "https://github.com/jrhubott/adaptive-cover-pro/wiki/"
_RULE_WIKI_BY_CODE = {rule.code: rule.wiki for rule in TRIAGE_RULES}


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Path to a downloaded diagnostics JSON file")
    parser.add_argument("--lang", default="en", help="Language for finding prose")
    parser.add_argument(
        "--latest-version",
        default=None,
        help="Latest released version — enables the STALE_VERSION check",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)

    with open(args.path, encoding="utf-8") as fh:
        doc = json.load(fh)

    view = build_offline_view(doc, latest_version=args.latest_version)
    findings = run_triage(view)
    labels = load_troubleshoot_labels(args.lang)

    if not findings:
        print("No findings — the triage engine flagged nothing in this snapshot.")
        return 0

    print(f"{len(findings)} finding(s):\n")
    print(render_report(findings, labels))
    print("\nWiki:")
    for finding in findings:
        wiki = _RULE_WIKI_BY_CODE.get(finding.reason.code)
        if wiki:
            print(f"  - {finding.reason.code}: {_WIKI_BASE}{wiki}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
