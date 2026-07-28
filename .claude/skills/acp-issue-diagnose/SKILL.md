---
name: acp-issue-diagnose
description: Triage an Adaptive Cover Pro GitHub issue end-to-end — fetch the attached diagnostics file (or take a local path), run a Sonnet-powered diagnosis, and draft a reply ready to post on the issue. Use when the user says "triage issue NNN", "look at issue NNN", "diagnose issue NNN", "draft a response to issue NNN", or hands over a local diagnostics file with "draft an issue reply".
---

# ACP Issue Diagnose

End-to-end triage workflow for `adaptive-cover-pro` GitHub issues that include a diagnostics dump. This skill **fetches**, **diagnoses (Sonnet)**, and **drafts a reply**. Posting the reply is gated behind explicit user confirmation.

For one-off analysis of a JSON file with no GitHub-issue context, use the simpler `acp-diagnose` skill instead.

---

## Pick the Input Mode

| User says… | Mode |
|------------|------|
| "issue 285", "#285", a GitHub issue URL | **issue mode** — fetch from GitHub |
| A path like `/tmp/foo.json`, "this file", or a paste of JSON | **local mode** — skip the fetch step |

If the user says only "diagnose this" with no number, file, or attachment, ask: "Issue number (e.g. 285) or path to a diagnostics file?"

---

## Step 1 — Acquire the Diagnostics File

### Issue mode

```bash
gh issue view <N> --json number,title,body,author,state,labels,createdAt,comments
```

Parse the body for attachment URLs. Home Assistant's diagnostics download is a JSON file; users sometimes rename it to `.log`. Match this regex on the body:

```
https://github\.com/user-attachments/files/\d+/[^\s\)]+\.(?:json|log|txt)
```

- **Zero matches** → check the comments array for the same regex; users sometimes attach in a follow-up.
- **Still zero** → draft a reply that asks for diagnostics (template in **Output Format → Missing-data reply**) and stop.
- **Multiple matches** → use the **latest** one (last in the body, then last in comments). Note the others in the report so the user can re-run against an earlier file if needed.

Download to `/tmp/`:

```bash
URL="<attachment-url>"
OUT="/tmp/acp-issue-<N>-$(basename "$URL")"
gh api "$URL" > "$OUT"     # gh handles auth; works for private repos too
```

If `gh api` fails (occasionally happens for the user-attachments host), fall back to:

```bash
curl -sSL -H "Authorization: Bearer $(gh auth token)" "$URL" -o "$OUT"
```

Verify it parsed:

```bash
python3 -c "import json; json.load(open('$OUT'))" && echo OK
```

If parse fails, the file is likely an HTML error page (auth/redirect). Show the first 200 bytes to the user and stop.

### Local mode

Take the path verbatim. If it's a paste, write it to `/tmp/acp-diag-paste.json` first, then validate parse.

---

## Step 2 — Diagnose with Sonnet

Dispatch **one** subagent with the Sonnet model. Do not run the analysis in the main thread — the diagnostics file is large and the structured walkthrough is exactly what a delegated subagent is for.

```
Agent(
  description: "Sonnet diagnosis of ACP diagnostics",
  subagent_type: "general-purpose",
  model: "sonnet",
  prompt: <see template below>
)
```

### Subagent prompt template

```
You are diagnosing an Adaptive Cover Pro diagnostics dump for a GitHub issue.

Diagnostics file: <ABSOLUTE_PATH>
Issue context (may be empty for local-mode runs):
  Number: <N or "n/a">
  Title:  <title or "n/a">
  Reporter: <login or "n/a">
  ACP version reported in body: <version or "n/a">
  HA version reported in body:  <version or "n/a">
  Cover type reported in body:  <type or "n/a">
  Reporter's description (verbatim, may be truncated):
  ---
  <first ~1500 chars of issue body, describe + reproduction sections preferred>
  ---

Your job:
1. Read the diagnostics file with the Read tool. If it exceeds the Read window, load it via Bash+Python (`json.load`) and inspect specific keys.
2. Walk the analysis checklist defined in:
   /home/jrhubott/src/adaptive-cover-pro/.claude/skills/acp-diagnose/SKILL.md
   (sections "Analysis Checklist" 1–7 — Sanity/Version, Control Status, Decision Trace,
    Cover Command Health, Position/Skip Analysis, Configuration Sanity, Climate.)
   Do NOT re-implement the checklist — read that file and follow it.
3. Cross-reference the reporter's described symptom against the diagnostics:
   - Does the diagnostics state corroborate the symptom? (e.g. "covers don't move" + `gave_up=true`)
   - Or contradict it? (e.g. "manual override stuck" + `manual_override_state.entries == []`)
   - Or is the symptom outside what diagnostics can show? (e.g. a UI rendering bug)
4. Identify the most likely root cause(s), ranked.
5. Note any missing information you'd need to confirm — specific entity states, HA logs around a timestamp, repro steps, etc.

Output a single JSON object with this shape — no commentary, no markdown fences:

{
  "header": {
    "integration_version": "...",
    "cover_type": "...",
    "last_update_success": true,
    "last_update_time": "..."
  },
  "critical": ["..."],            // empty array if none
  "warnings": ["..."],
  "findings": ["..."],            // bullet-form, plain English
  "decision_trace_summary": "Winning handler: X (position: Y%, reason: ...)",
  "symptom_vs_diagnostics": "corroborated" | "contradicted" | "orthogonal" | "insufficient",
  "symptom_analysis": "1-3 sentences: how the diagnostics relate to what the reporter described",
  "root_cause_ranked": [
    {"hypothesis": "...", "confidence": "high|medium|low", "evidence": "..."}
  ],
  "info_needed": ["..."],         // empty array if you have enough
  "config_suggestions": ["..."],  // concrete option changes if applicable
  "summary": "1-2 sentences for the issue reply"
}

Keep total output under ~3KB. Be specific — cite handler names, option names, entity IDs.
```

When the subagent returns, parse its JSON. If parsing fails, show the user what came back and ask whether to retry; do not silently fix.

---

## Step 3 — Draft the Reply

Use the structured output from Step 2 to assemble a markdown comment. **Voice: first-person ("I"), matching the maintainer's tone in the existing repo.** Tone: direct, technical, friendly. No filler ("Thanks for reporting!"), no emoji unless flagging a critical issue with a single 🔴.

### Reply template

```markdown
Looked at the diagnostics — running ACP {version}, cover type `{cover_type}`, last update {ok/FAILED} at {time}.

**What I see:**
{2–4 bullets of the most relevant findings — pull from `findings` and `decision_trace_summary`. Lead with anything in `critical`.}

**Most likely cause:**
{`root_cause_ranked[0].hypothesis` with one-sentence evidence. If confidence is low, hedge ("might be"). If multiple hypotheses are roughly tied, list the top two.}

**Suggested next step:**
{Pull from `config_suggestions` if any. Otherwise: what to try, or what info you need.}

{If `info_needed` is non-empty, add:}
**To confirm, could you share:**
- {bullet per item}
```

### Critical-finding override

If `critical` is non-empty, lead with a single line:

```
🔴 The diagnostics show {first critical item}.
```

…then the rest of the template.

### Missing-data reply (no diagnostics found)

```markdown
I don't see a diagnostics dump on this issue yet — could you attach one?

In Home Assistant: Settings → Devices & Services → Adaptive Cover Pro → ⋮ (the row's overflow menu) → Download diagnostics. Drop the resulting `.json` file into a comment here.

If you can also include:
- A rough timestamp of when the misbehavior happened (so I can correlate against the diagnostics)
- The relevant `cover.*` entity ID

…that'd let me get to a root cause faster.
```

---

## Step 4 — Show, Confirm, Optionally Post

Always show the drafted reply to the user first. Then ask **one** question:

> Post this comment on issue #N, edit it first, or leave it as-is for me to post manually?

Only if the user explicitly says "post it" / "yes, post" do you run:

```bash
gh issue comment <N> --body "$(cat <<'EOF'
<reply markdown>
EOF
)"
```

⚠️ **Posting to GitHub is shared state.** Never post without explicit confirmation in the current turn. A prior approval for a different issue does not carry over.

Do not add labels, close the issue, or assign. Those are maintainer judgments outside this skill's scope.

---

## Output Format (skill-final report to user)

After Step 3 (drafted but not posted), report:

```
ACP issue triage — #<N> "<title>" by @<author>

Diagnostics: <path or attachment URL>
ACP <version> · <cover_type> · last update <ok|FAILED>

Verdict: <one-sentence summary from Sonnet>
Confidence: <high|medium|low — from root_cause_ranked[0]>

--- DRAFT REPLY ---
<full markdown reply>
--- END DRAFT ---

Post this on #<N>, edit, or leave for manual posting?
```

For local-mode runs (no issue number), drop the `#N` lines and end with:

```
No issue number provided — copy the draft above into your reply manually, or give me an issue number to post it to.
```

---

## Safety Rules

- **No automatic posting.** Always wait for explicit confirmation in the same turn.
- **No label/state changes.** This skill comments only.
- **Never delete the downloaded diagnostics file** during the session — the user may want to re-run.
- **If the diagnostics file is corrupt or HTML**, stop and surface the raw content excerpt; do not fabricate findings.
- **If the reporter's symptom contradicts the diagnostics** (`symptom_vs_diagnostics == "contradicted"`), say so plainly in the reply rather than papering over it. The contradiction is itself a useful finding.
- **Cost budget:** one Sonnet diagnosis per issue. If the user re-runs after editing the prompt, that's fine; do not auto-loop.

---

## Notes for Future Maintenance

- The Sonnet subagent reads `acp-diagnose/SKILL.md` as its analysis playbook. If the diagnostics JSON schema changes, update **only** that file — this skill picks up the new logic automatically.
- If GitHub changes the user-attachments URL format, update the regex in **Step 1**. The current pattern is `https://github.com/user-attachments/files/<id>/<filename>`.
- HA's diagnostics download produces filenames like `config_entry-adaptive_cover_pro-<ULID>.json`. Some reporters rename to `.log` — that's why the regex accepts `.json|.log|.txt`.
