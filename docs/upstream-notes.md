# Notes for the pending upstream PR, and what is deliberately deferred

Pipeline-level notes only: no candidate, market or portal data. Two things live
here - the state of the upstream change this fork carries, and the work that was
evaluated but **not** built, with the measurement that would justify revisiting
it.

## The pending PR: `/scrape` Step 0 uses the existing projection

Files: `.claude/skills/job-scraper/SKILL.md` (Step 0) and `tools/rank_state.py`
(a fourth subcommand, `keys`, plus `url_identity`), with tests added to
`tests/test_rank_state.py`.

Why it is not a fork divergence in kind: upstream already solved this problem for
`/rank` (`candidates`/`apply`); `/scrape` was the remaining caller that still
pulled `seen_jobs.json` through the context whole. If upstream takes the change,
both files revert to upstream's own content and the divergence disappears.

Measured, 569-entry state:

| read path | tokens |
|---|---|
| `seen_jobs.json` read whole (before) | ~64k |
| `rank_state.py keys` | ~16k |
| `rank_state.py keys --ids-only` | ~8k |
| `rank_state.py candidates --limit 10` (already upstream) | ~0.9k |

**Line to include in the PR description:** `tools/check_upstream_updates.py`
compares a fixed list of 11 paths, and neither `job-scraper/SKILL.md` nor
`tools/rank_state.py` is on it - so it reports "All framework files are up to
date with upstream/master" while both are edited. `tools/upstream_triage.py`
(commit-level, patch-id based, no marker list) is the tool that covers them.

## Rides the same PR: the URL rule vs aggregator links

`freehire-search`'s feed carries third-party aggregator URLs (`whatjobs.com`)
that return HTTP 403 to `web_fetch` and to `curl` with browser headers. Step 2
already says "Store a URL that actually resolves to the posting"; what is missing
is applying it to the links the feed hands over.

Measured: 213 of one portal's 362 stored rows (58%) were such links, against 0 of
207 for the other four portals combined. Every stored aggregator URL costs a
downstream run its full retrieval escalation - portal `detail`, `web_fetch`,
`curl` with headers, employer-site search - only to conclude "expired".

The fix belongs in the same file (`job-scraper/SKILL.md`), so it adds no new
divergence. Detail: `docs/portal-cli-findings.md`, findings 10-11.

## Deferred: a membership query instead of a projection (evaluated, not built)

Idea: a tool subcommand that reads the state and prints only the candidates that
are **not** already stored, instead of projecting the whole seen set into the
context.

Cost model (N = stored entries, M = new rows in the run):

| | per run | scales with |
|---|---|---|
| projection (`keys`) | 28.9 tok x N | state size |
| membership query | ~92.7 tok x M | new rows only |

At N=569: 16.4k (projection) against ~10-21k (membership, at M=109-224). The
projection crosses today's full-read cost (64k) at **N ~ 2,200**. A membership
query is already cheaper in raw tokens (crossover at N ~ 321 with M=100), so the
reason to defer is **materiality and PR scope**, not savings: 16.4k is ~2.5% of a
window, and the change rewrites core Step 2.

### Prerequisite — verified 2026-10-15: this is two changes, not one

Step 1b **persists nothing**. `job_scraper/` holds only `.gitkeep` and
`seen_jobs.json`; the skill's only instruction about output is "use
`--format json` for machine-readable output", and Step 4 is the sole persister.
So `unseen --from <files>` cannot exist until, upstream:

1. **Step 1b writes its raw per-portal CLI output to a scratch path**, so a later
   step can read it without it having passed through the context; and
2. **Step 2's dedup becomes the tool query**, instead of the agent comparing each
   candidate against a projection it holds.

Design constraint that makes (1) load-bearing rather than incidental: the input
must be **files, not prompt text**. Handing ~300 raw candidates to the tool in
the prompt costs ~12k tokens and cancels the entire gain.

### When to revisit

N ~ 2,000 entries (~12-24 weeks at 60-120 new entries per week), or earlier if M
grows. Cost of the change when it comes: two coupled prompt edits (Step 1b
persistence, Step 2 dedup), a new tool surface (input format, tracker exclusion,
output schema) and tests - a materially bigger PR than the `keys` fix, which is
precisely why they ship separately.
