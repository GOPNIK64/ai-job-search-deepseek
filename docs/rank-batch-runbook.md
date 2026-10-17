# /rank batch runbook (workspace-local)

How a `/rank` batch is run here, beyond what the ported skill says. Every step
below exists because a run got it wrong first.

## Size and shape

- One batch is **50 postings**, split into 8 lots of 6-7 and scored by 8 parallel
  subagents (the harness caps active children at 8, so 8 lots means no queue).
- Each agent reads a batch rubric (`/tmp/rank/bN/rubric.md`, written per batch
  from `04-job-evaluation.md` plus the current calibration) and its own job file,
  and writes its JSON array to disk. The state file never enters the context.
- Record the per-batch count in the summary: the user runs the backlog in
  batches on purpose, and "deferred" is the number that answers "how much is
  left".

## Gates at the close of every batch

1. **Compensation FAIL** (unpaid / equity-only): never written as `ranked` (04
   says the gate does not let one through ranked), never as `expired` (it is not
   dead). It is listed for the user and then `edit`-ed to `skipped` with the
   quoted wording, so it does not return in the next batch.
2. **Aggregator failure**: never written as `expired` - upstream says `expired`
   is never retried (#534), so a Cloudflare 403 would silently retire a posting
   that may be live. It is parked (`needs_rescrape`), reported with its URL, and
   **if the same row fails again in the next batch it is `skipped` with the
   reason** instead of being retried a third time.
3. **URL cleanup, every batch, not on request.** Most aggregator rows are scored
   from the employer's own text but keep the aggregator URL, which cannot be
   applied through. Every agent returns `employer_url`; the collector turns those
   into `edit` updates. A row whose employer URL could not be recovered is
   reported as such and flagged, never left silently pointing at a shell.
4. **Company recovery.** When the feed's `company` is the ATS vendor ("Lever,
   Inc.", "SmartRecruiters, Inc."), an intermediary ("Jobgether") or
   "unknown-company", the real employer is written via `edit` (`company` plus
   `employer_recovered` for provenance). Company+title **is** the dedup identity,
   so leaving the vendor name in place breaks both dedup and tracker exclusion.
5. **The sweeps**: `rank_state.py sweep --write --exclude <this batch's keys>`
   retires already-ranked rows whose stored deadline passed, and re-derives the
   closing-soon list. It costs no fetch.

## State writes

`apply` writes scores and verdicts; `edit` writes the corrections that are not
scores (status, url, company, gate verdicts and notes) and **refuses by name** to
touch `rank_score`, `rank_verdict`, `rank_date`, `strengths` or `gaps`. A
language or location verdict can therefore be corrected without the score moving,
which is the only reason a "correction" is safe to run on a ranked row.

## Reading the output

- The language tally is per batch and reported with the quoted requirement for
  every FLAG and FAIL. A FAIL is always a language that is not on the table.
- The years cap is reported by the number of rows where it *bound* (Experience
  exactly 59), not by how many postings mentioned years.
