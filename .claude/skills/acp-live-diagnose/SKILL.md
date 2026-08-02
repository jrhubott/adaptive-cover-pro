---
name: acp-live-diagnose
description: Root-cause a misbehaving Adaptive Cover Pro cover on a RUNNING Home Assistant install by pulling live diagnostics over the HA MCP, correlating against the recorder, proving the mechanism with a reproduction test in the local checkout, then drafting a GitHub issue for approval and offering handoff to implement-issue. Use when the user describes live misbehavior in the present tense and names a cover or entity — "figure out why the minimum floor is overriding a manual position on X", "why does X keep reopening", "X isn't moving", "my cover bounces back", "diagnose X on my install" — or asks to investigate a live symptom and file an issue about it. NOT for a downloaded diagnostics JSON (use acp-diagnose) or a GitHub issue number (use acp-issue-diagnose).
---

# ACP Live Diagnose

Turn "my cover is doing something wrong right now" into a filed, root-caused GitHub issue.

The three ACP diagnosis skills differ only in where the evidence comes from:

| Input                         | Skill                |
| ----------------------------- | -------------------- |
| Downloaded `diagnostics.json` | `acp-diagnose`       |
| GitHub issue number           | `acp-issue-diagnose` |
| **Running HA install (MCP)**  | **this skill**       |

The distinguishing value here is that the install is live: the recorder can be queried, the code is checked out locally, and the mechanism can be **proven by execution** rather than argued from reading. Do not skip that proof.

---

## Step 0 — Session Startup

`CLAUDE.md` requires it before anything else:

```bash
cat HANDOFF.md && git status && git log --oneline -5
```

The handoff often names recent work in the same subsystem, which is exactly the context that turns "this looks wrong" into "this regressed in #NNNN."

---

## Step 1 — Resolve the Target

Each ACP cover is its own config entry. Match on the title the user used:

```
ha_get_integration(query="<cover name fragment>")
```

Zero matches → widen with `ha_get_integration(query="adaptive")` and show the list. Multiple matches → ask which one; do not guess.

Record the `entry_id` and, from `entry.options`, the config that bears on the symptom. Options are the reporter's actual configuration — read them before forming any hypothesis. Watch for keys stored as `null` that fall back to a `DEFAULT_*` constant (`custom_position_priority_N` → `DEFAULT_CUSTOM_POSITION_PRIORITY`); the effective value is what matters, and it is not in the dump.

---

## Step 2 — Pull Live Diagnostics

```
ha_get_integration(entry_id=..., include_diagnostics=True,
                   diagnostics_data_path="data.diagnostics",
                   diagnostics_truncate_at_bytes=1200)
```

That returns `available_fields` — the section list. Drill in one section at a time:

```
ha_get_integration(entry_id=..., include_diagnostics=True,
                   diagnostics_data_path="data.diagnostics.<section>",
                   diagnostics_fields=["data"])
```

⚠️ **Always pass `diagnostics_fields=["data"]` on drill-in calls.** Without it every response re-echoes the entire `entry.options` blob, which is several hundred tokens per call and adds nothing after Step 1.

Sections that carry the most signal, roughly in order:

- `decision_trace` — the pipeline chain with each handler's match/reason/priority. The starting point for almost every symptom.
- `event_timeline` — chronological `pipeline_evaluated` / `cover_command_sent` / `cover_command_skipped` records. **Paginated, ~250 entries, and the interesting part is nearly always the tail.** Probe `diagnostics_data_limit=25, diagnostics_data_offset=0` to learn `total`, then jump to `offset=total-30`.
- `manual_override_history` / `manual_override_state` — engage/reject/reset records with the threshold reasoning.
- `cover_commands` — per-entity `target_call`, `wait_for_target`, `retry_count`, `gave_up`.
- `last_cover_action`, `last_skipped_action` — the most recent dispatch and the most recent suppression.
- `meta` — `integration_version`, `cover_type`. Always capture; the issue needs it and a develop/alpha build changes what "latest" means.

Diagnostics timestamps are **UTC**. The HA MCP converts recorder timestamps to the install's local zone. Never compare the two without converting, and state which zone any timestamp in the issue is in.

---

## Step 3 — Corroborate Against the Recorder

Diagnostics say what ACP believed. The recorder says what the hardware did. A root cause is not established until both agree.

```
ha_get_history(entity_ids="cover.<target>",
               start_time="<UTC iso>", end_time="<UTC iso>",
               minimal_response=False, order="asc")
```

Bracket the window around the timestamps found in Step 2, plus a few minutes either side. `minimal_response=False` is required — `current_position` lives in the attributes.

This step routinely rewrites the hypothesis. It is what distinguishes "ACP computed 40" from "ACP drove the cover from 0 back to 40 thirteen seconds after the user closed it," and only the second is a bug report.

Where relevant, also pull the states of the entities that gate the behavior — trigger `input_boolean`s, sensors named in options, the other covers in a `group`.

---

## Step 4 — Root-Cause Against the Local Checkout

Read the actual code. Never infer a mechanism from a diagnostics reason string — those strings are rendered from `reason_i18n.py` templates and can lag or describe a different frame than the value beside them.

Work from the decision trace inward: the winning handler, then the registry composition pass that post-processes the winner, then whatever seam dispatched. `CLAUDE.md`'s architecture map names the layer for each.

Note for shell searches: quote globs, since zsh expands them first.

```bash
grep -rn "pattern" --include="*.py" custom_components/    # not --include=*.py
```

Two recurring shapes worth checking explicitly, because both have produced real defects:

- **The same rule stated at two seams.** A user-command path and a pipeline path that each independently decide the same question. If one is priority-gated and the other is not, the gated one is defeated one cycle later. Read both; do not assume the one you found first is the only one.
- **Values that are not what their name suggests.** `current_cover_position` is the _mean across every cover in the entry_ (`_compute_mean_cover_position`), not the position of the cover being commanded. Confirm the arithmetic against the observed number before building an argument on it.

Then check whether the behavior is already pinned by a test. If two tests assert opposite contracts for the same input and both pass, that pair _is_ the finding — cite both with file:line.

---

## Step 5 — Prove It With a Reproduction

**Mandatory for any defect claim.** Reconstruct the live numbers using the existing test fixtures and run it.

Find a test module that already builds the relevant snapshot (for pipeline work, `tests/test_pipeline/test_floor_composition.py` and its `make_snapshot` / `_cp_state` / `_registry_with_custom` helpers), import those helpers, substitute the values observed on the install, and print the result fields.

⚠️ **The scratch test must live inside `tests/`.** Running it from the scratchpad fails on `enable_event_loop_debug` — the autouse fixtures come from `tests/conftest.py`. Write it to `tests/<subdir>/test_zz_scratch_repro.py`, run it, then delete it.

```bash
cp <scratchpad>/test_repro.py tests/test_pipeline/test_zz_scratch_repro.py
venv/bin/python -m pytest tests/test_pipeline/test_zz_scratch_repro.py -q -s
rm tests/test_pipeline/test_zz_scratch_repro.py
git status --short          # must be clean before moving on
```

Use `venv/bin/python -m pytest`, never `source venv/bin/activate`. In a linked worktree there is no `venv/` — pass the main checkout's absolute interpreter path.

Paste the **real printed output** into the issue. Do not paraphrase it, and do not write the repro without running it.

---

## Step 6 — Verdict Before Filing

Classify before drafting. Not every live symptom is a defect:

| Verdict                      | Action                                                                                                                                             |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Defect**                   | Proceed to Step 7.                                                                                                                                 |
| **Misconfiguration**         | Explain the option that produces the behavior and what to change. Do not file. Offer to file a docs/UX issue if the config was reasonably misread. |
| **Duplicate**                | Report the existing issue number and what it does or does not already cover. Ask before filing anything new.                                       |
| **Works as designed, badly** | File as `enhancement`, not `bug`, and say so.                                                                                                      |
| **Unproven**                 | Say the mechanism is unconfirmed and name what would confirm it. Do not file a guess.                                                              |

Search the tracker before filing, both states:

```bash
gh issue list --repo jrhubott/adaptive-cover-pro --state all --limit 200 \
  --search "<symptom keywords>" --json number,title,state
```

Read the bodies of the close matches, not just their titles. A closed issue whose _fix_ introduced the current behavior is the most valuable context an issue can carry, and its reporter's configuration usually explains why the fix was written the way it was. An open issue covering the same contract may mean the right move is a comment rather than a new issue — raise that with the user.

---

## Step 7 — Draft the Issue

Invoke the `writing` skill before drafting. **Voice: first person ("I"), matching the maintainer's tone across the repo.** No filler openers, no ceremonial closers.

Follow `.github/ISSUE_TEMPLATE/bug.yml`'s section headings so the issue looks native (`### Adaptive Cover Pro version`, `### Home Assistant version`, `### Cover type`, `### Describe the issue`, `### Reproduction steps`), then add the analysis sections. Structure that works:

1. **What I observed** — one paragraph, present tense, on the real install. Then the effective config as a short code block, resolving any `null` to its constant.
2. **Recorder evidence** — a small table of state/position transitions with times. This is the part a reader believes.
3. **Reproduction steps** — numbered, generalized away from the specific install, with explicit _Expected_ and _Actual_.
4. **Determination** — the mechanism, one seam at a time, each with a `file.py:line` citation and the smallest quoted snippet that shows it. Include the repro's printed output verbatim. If contradictory tests exist, cite both.
5. **Secondary findings** — anything real but distinct from the main defect, clearly labelled as separate.
6. **Relationship to existing issues** — what each related issue covers and where the boundary is.
7. **Suggested direction** — where the fix goes and what must _not_ change with it. Name the tests that will have to move.

Keep every factual claim traceable to something pulled in Steps 2-5. If a number is not in the diagnostics, the recorder, or the repro output, do not put it in the issue.

---

## Step 8 — Show the Draft, Then Create

⚠️ **Always show the full drafted issue to the user before creating it.** Filing to GitHub is outward-facing and shared; a draft shown after the fact is not a review.

Write the body to the scratchpad, show it, and ask **one** question:

> Create this issue as-is, edit it first, or hold off?

Only on explicit approval in the same turn:

```bash
gh issue create --repo jrhubott/adaptive-cover-pro \
  --title "<title>" --label bug \
  --body-file "<scratchpad>/issue_body.md"
```

Title rule: name the _mechanism_, not the symptom. "Min-mode floor below manual-override priority still raises a manual position via the held-position clamp" beats "cover reopens by itself" — the tracker is searched by maintainers, not by symptom.

Do not assign, milestone, or close anything. Do not comment on related issues; the cross-reference in the body already creates a backlink.

---

## Step 9 — Offer the Handoff

After the issue is created, report its number and URL, then ask:

> Want me to run `implement-issue` on #NNNN to plan and build the fix?

Wait for an answer. On yes, invoke the `implement-issue` skill with the new issue number. On no, stop — do not start a branch, write code, or open a PR.

If the verdict in Step 6 was anything other than **Defect**, skip this step; there is nothing to implement.

---

## Output Format (skill-final report to user)

```
ACP live diagnose — <cover title> (<entry_id>)
ACP <integration_version> · <cover_type>

Symptom:   <one line, as observed>
Verdict:   <defect | misconfiguration | duplicate | enhancement | unproven>
Mechanism: <one or two sentences, with the seam named>
Proof:     <repro test result, one line>

--- DRAFT ISSUE ---
<full markdown body>
--- END DRAFT ---

Create this issue as-is, edit it first, or hold off?
```

After creation, replace the trailing question with the issue URL and the Step 9 handoff question.

---

## Safety Rules

- **Never create the issue without showing the draft and getting approval in the same turn.** Approval for a previous issue does not carry over.
- **Never claim a mechanism that was not executed.** A repro that was written but not run is not evidence.
- **Read-only against Home Assistant.** This skill queries; it does not call services, change options, or move covers. If moving a cover would confirm a hypothesis, ask the user to do it.
- **Leave the working tree clean.** Delete every scratch test and verify with `git status --short`.
- **Do not edit `HANDOFF.md` or `CLAUDE.md` as part of this workflow** — both are locally untracked and managed outside the repo.
- **Report contradictions rather than smoothing them.** If the recorder disagrees with the diagnostics, that gap is the finding.

---

## Notes for Future Maintenance

- The MCP diagnostics payload lives under `data` → `config_options` + `diagnostics`; the triage view is `{"options": config_options, **diagnostics}` — the same data `scripts/triage_json.py` runs over. If a section is renamed, `available_fields` from the Step 2 probe is self-describing; no list in this file needs updating.
- Some hardware pins `current_position` until travel completes and only publishes `opening`/`closing` in between (ZVIDAR shades do this). Position-delta reasoning is blind to those covers — check the recorder's `state` column, not just `current_position`.
- Skills under `.claude/skills/` are repo-tracked (`.gitignore` has `!.claude/skills/`). Changes here go through a PR like any other source change.
