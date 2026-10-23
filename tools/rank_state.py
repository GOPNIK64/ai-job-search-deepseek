#!/usr/bin/env python3
"""State helper for /rank and /scrape: the only way seen_jobs.json is read.

The state file is append-only by design and most stored entries are `skipped`,
so pulling it into the model's context costs the whole backlog on every run -
whether the run is /rank filtering candidates by eye (Step 1) and re-emitting
the file to record scores (Step 4), or /scrape's Step 0 dedup, which needs one
answer per stored entry ("is this company+title already seen, and is this URL?")
and nothing more. That cost grows for the life of the workspace.

This moves the state-file traffic into code. Four subcommands:

  candidates   select the eligible entries for this run and project only the
               fields a scoring agent needs
  sweep        rule 6's expiry pass over entries this run did not re-score -
               a stored-date comparison, no fetch, no agent
  apply        write scoring results back to seen_jobs.json and print the
               ranked/vetoed/expired/unreachable/gave_up rows Step 5's
               report is built from

Selection and projection follow Step 1's existing rules exactly (status
filter, tracker exclusion, focus filter, `--limit`/`--all`); the write-back
follows Step 4's existing rules exactly (the `location` -> `location_verdict`
legacy migration, the deadline null-is-not-a-correction rule, verbatim
strengths/gaps persistence, idempotent skip of already-ranked entries); the
sweep follows rule 6 exactly (defensive date parsing, an absent deadline left
alone, `--all` making a retired entry revivable).

Nothing here fetches a posting or judges a fit. Scoring stays with the model;
this only removes the state file from the conversation.

Usage:
  python3 tools/rank_state.py candidates [--all] [--focus TEXT] [--limit N]
  python3 tools/rank_state.py keys [--ids-only]
  python3 tools/rank_state.py sweep [--write] [--exclude KEY,KEY]
  python3 tools/rank_state.py apply --results results.json [--dry-run] [--now ISO-DATETIME]

`candidates`, `sweep` and `apply` print JSON on stdout; `keys` prints one
tab-separated line per stored entry. Exit 0 on success, 1 on a usage or state
error, or on `apply` when any result could not be written.
"""

import argparse
import json
import os
import re
import sys
import tempfile
import unicodedata
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "job_scraper" / "seen_jobs.json"
TRACKER = ROOT / "job_search_tracker.csv"

# 04-job-evaluation.md
WEIGHTS = {"technical": 0.30, "experience": 0.25, "behavioral": 0.15, "career": 0.30}
BANDS = ((75, "Strong Fit"), (60, "Good Fit"), (45, "Moderate Fit"), (30, "Weak Fit"), (0, "Poor Fit"))

DEFAULT_LIMIT = 10
URGENT_DAYS = 7
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# The statuses a /rank scoring agent may return (rank.md Step 2). This module
# owns the set; tests/test_rank_command.py asserts rank.md's JSON contract
# names exactly these, so the spec and the tool cannot drift apart.
AGENT_STATUSES = ("scored", "expired", "unreachable")

# The values the two veto gates may take (rank.md Step 2). Step 5's veto is an
# exact match on "FAIL", so any other spelling would shortlist a deal-breaker.
GATE_VERDICTS = ("PASS", "FAIL", "FLAG")

# A posting that could not be fetched is `unreachable`, not `expired`, and is
# retried on later runs (#534). It gives up to `expired` only after this many
# failed attempts with at least this long between the first and the last, so a
# scheduled run plus a manual one on the same day cannot burn every attempt on
# one outage.
UNREACHABLE_MAX_ATTEMPTS = 3
UNREACHABLE_MIN_SPAN = timedelta(hours=48)
FETCH_ERROR_MAX = 200
RETRY_FIELDS = ("fetch_attempts", "first_unreachable", "last_fetch_error")
TIMESTAMP = "%Y-%m-%dT%H:%M:%SZ"


def load_state(path: Path) -> tuple[dict, dict]:
    """Return (document, seen-map). The map is mutated in place by callers."""
    if not path.is_file():
        sys.exit(f"{path} not found - run /scrape first")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"{path} is not valid JSON: {exc}")
    seen = doc.get("seen") if isinstance(doc, dict) and "seen" in doc else doc
    if not isinstance(seen, dict):
        sys.exit(f"{path}: expected an object of job entries")
    return doc, seen


def save_state(path: Path, doc: dict) -> None:
    """Atomic replace: a half-written seen_jobs.json loses the scrape history."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".seen_jobs.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def parse_timestamp(value) -> datetime | None:
    """A stored UTC timestamp, or None for anything else (absent, malformed, a
    bare date) - the defensive-parse rule again: never compared, never guessed."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.strptime(value.strip(), TIMESTAMP)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc)


def parse_now(value: str) -> datetime:
    """--now: an ISO 8601 date-time; a naive value is taken as UTC."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_iso(value) -> date | None:
    """Rule 6's defensive-parse rule: anything that is not YYYY-MM-DD is treated
    exactly like an absent value - never compared, never guessed at."""
    if not isinstance(value, str) or not ISO.match(value.strip()):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def norm(text) -> str:
    """Ignore case and separators without discarding non-Latin identity."""
    text = unicodedata.normalize("NFC", str(text or "").casefold())
    # Combining marks can distinguish names even after NFC (e.g. Indic vowels).
    return "".join(
        char for char in text
        if char.isalnum() or unicodedata.category(char).startswith("M")
    )


def tracker_pairs(path: Path) -> set[tuple[str, str]]:
    """company+role pairs already in the tracker - out of scope for ranking."""
    if not path.is_file():
        return set()
    import csv

    pairs = set()
    try:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                company, role = norm(row.get("company")), norm(row.get("role"))
                if company:
                    pairs.add((company, role))
    except UnicodeDecodeError:
        # A spreadsheet's plain "CSV" save writes the ANSI code page (cp1252 on
        # most Western installs), so any accented company name stops decoding.
        sys.exit(f"{path} is not UTF-8 text - re-save it as CSV UTF-8")
    return pairs


def entry_location_verdict(entry: dict) -> str | None:
    """location_verdict, falling back to a legacy verdict stored under `location`
    (Step 4: "an entry ranked before this rename may carry a legacy PASS/FAIL/
    FLAG string in `location`")."""
    verdict = entry.get("location_verdict")
    if verdict:
        return verdict
    legacy = entry.get("location")
    return legacy if legacy in ("PASS", "FAIL", "FLAG") else None


def url_identity(url) -> str:
    """Dedup identity for a posting URL.

    Scheme, query string, fragment and letter case are tracking, anchor and
    formatting noise that carry no posting identity, so they are dropped: the
    same ad stored once with `?utm_source=...`, once with `#apply` and once
    with a capitalised host must compare equal under Step 2's URL rule, or
    dedup re-presents a job the workspace has already seen.
    """
    text = str(url or "").strip()
    if not text:
        return ""
    text = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", "", text)
    text = text.split("#", 1)[0].split("?", 1)[0]
    return (text.rstrip("/") or text).lower()


def cmd_keys(args) -> int:
    """Project the dedup universe /scrape Step 0 needs, one line per entry.

    The line is `<canonical key>\\t<url identity>`: the key answers Step 2's
    company+title rule and the URL identity answers its URL rule ("skip if the
    URL matches any existing entry, regardless of that entry's key"), which is
    the only reason the URL is emitted at all. Nothing else about an entry is
    needed to decide whether a scraped result is new, so nothing else is
    printed - this is what keeps the state file out of the context.

    Every status is included: `skipped`, `ranked` and `expired` entries are all
    already-seen for dedup purposes. `--ids-only` drops the URL and prints just
    the keys, which is smaller but cannot answer the URL rule.

    A missing state file yields an empty seen set on exit 0 - that is the first
    /scrape run, not an error - but it is announced on stderr, so a mistyped
    path shows up instead of silently deduping against nothing and storing
    every posting twice.
    """
    if not args.state.is_file():
        print(
            f"no state file at {args.state} - the seen set is empty",
            file=sys.stderr,
        )
        return 0
    _, seen = load_state(args.state)
    for key in sorted(seen):
        entry = seen[key] if isinstance(seen[key], dict) else {}
        if args.ids_only:
            print(key)
        else:
            print(f"{key}\t{url_identity(entry.get('url'))}")
    return 0


def cmd_candidates(args) -> int:
    _, seen = load_state(args.state)
    excluded = tracker_pairs(args.tracker)

    selected, skipped_tracker = [], 0
    for key, entry in seen.items():
        status = entry.get("status")
        if args.all:
            if status == "skipped":
                continue
        elif status not in ("new", "unreachable"):
            continue
        if (norm(entry.get("company")), norm(entry.get("title"))) in excluded:
            skipped_tracker += 1
            continue
        if args.focus:
            haystack = " ".join(
                [str(entry.get("title") or ""), str(entry.get("company") or "")]
                + [str(b) for b in entry.get("strengths") or []]
                + [str(b) for b in entry.get("gaps") or []]
            ).lower()
            if args.focus.lower() not in haystack:
                continue
        selected.append(
            {
                "key": key,
                "title": entry.get("title"),
                "company": entry.get("company"),
                "url": entry.get("url"),
                "portal": entry.get("portal"),
                "deadline": entry.get("deadline"),
                "posted_date": entry.get("posted_date"),
            }
        )

    eligible = len(selected)
    if args.limit > 0:
        selected = selected[: args.limit]
    print(
        json.dumps(
            {
                "eligible": eligible,
                "selected": selected,
                "deferred": max(0, eligible - len(selected)),
                "excluded_by_tracker": skipped_tracker,
                "total_entries": len(seen),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def cmd_sweep(args) -> int:
    doc, seen = load_state(args.state)
    today = args.today
    # "a_b, c_d" is as natural a comma-separated list as "a_b,c_d"; keys never
    # carry surrounding whitespace, so a space must not un-exclude a key.
    exclude = {k.strip() for k in (args.exclude or "").split(",") if k.strip()}

    expired, closing, unparseable, checked = [], [], [], 0
    for key, entry in seen.items():
        if entry.get("status") != "ranked" or key in exclude:
            continue
        checked += 1
        raw = entry.get("deadline")
        if raw in (None, ""):
            continue
        parsed = parse_iso(raw)
        if parsed is None:
            unparseable.append({"key": key, "portal": entry.get("portal"), "deadline": raw})
            continue
        row = {
            "key": key,
            "title": entry.get("title"),
            "company": entry.get("company"),
            "url": entry.get("url"),
            "deadline": raw,
        }
        if parsed < today:
            expired.append(row)
        elif (parsed - today).days <= URGENT_DAYS:
            closing.append(row)

    if args.write and expired:
        for row in expired:
            seen[row["key"]]["status"] = "expired"
        save_state(args.state, doc)

    print(
        json.dumps(
            {
                "swept": checked,
                "newly_expired": expired,
                "closing_soon": sorted(closing, key=lambda r: r["deadline"]),
                "unparseable_deadlines": unparseable,
                "written": bool(args.write and expired),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def overall_score(scores: dict) -> int:
    if not isinstance(scores, dict):
        raise ValueError("scores must be an object")
    total = Fraction(0)
    for dim, weight in WEIGHTS.items():
        value = scores.get(dim)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"missing or non-numeric score '{dim}'")
        if not 0 <= value <= 100:
            raise ValueError(f"score '{dim}' must be finite and between 0 and 100")
        # Scores and documented percentage weights are decimal quantities.
        # Binary float summation can turn 59.5 into 59.49999999999999 and drop
        # an otherwise valid result into the lower verdict band.
        total += Fraction(str(value)) * Fraction(str(weight))
    return int(total + Fraction(1, 2))


def band(score: int) -> str:
    for floor, name in BANDS:
        if score >= floor:
            return name
    return "Poor Fit"


def cmd_apply(args) -> int:
    doc, seen = load_state(args.state)
    today = args.today
    now = args.now
    try:
        results = json.loads(Path(args.results).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        sys.exit(f"cannot read results file {args.results}: {exc}")
    if isinstance(results, dict):
        results = results.get("results", [])
    if not isinstance(results, list):
        sys.exit("results file must be a JSON array of scoring objects")

    rows, expired, unreachable, gave_up, errors = [], [], [], [], []
    for result in results:
        if not isinstance(result, dict):
            errors.append({"key": None, "error": "result is not an object"})
            continue
        key = result.get("key")
        entry = seen.get(key) if isinstance(key, str) else None
        if entry is None:
            errors.append({"key": key, "error": "no such key in seen_jobs.json"})
            continue

        # A missing status is a score, as it always was; a misspelt one is an
        # error rather than a fall-through that reports a "missing score".
        status = result.get("status", "scored")
        if status not in AGENT_STATUSES:
            errors.append(
                {"key": key, "error": f"unknown status {status!r}; expected one of {', '.join(AGENT_STATUSES)}"}
            )
            continue

        if status == "unreachable":
            # A stored deadline that has already passed is positive evidence the
            # posting is closed, whatever the fetch did: expire it, as the scored
            # path and rule 6 would, instead of retrying it and later listing it
            # as possibly live.
            deadline = parse_iso(entry.get("deadline"))
            if deadline is not None and deadline < today:
                entry["status"] = "expired"
                for field in (*RETRY_FIELDS, "expired_reason"):
                    entry.pop(field, None)
                expired.append(
                    {"key": key, "title": entry.get("title"), "company": entry.get("company"), "url": entry.get("url")}
                )
                continue

            previous = entry.get("fetch_attempts")
            valid = isinstance(previous, int) and not isinstance(previous, bool)
            attempts = (previous if valid else 0) + 1
            first = parse_timestamp(entry.get("first_unreachable")) or now
            # Plain, bounded text: it is shown in the report, never acted on.
            error = " ".join(str(result.get("fetch_error") or "").split())[:FETCH_ERROR_MAX]
            entry["fetch_attempts"] = attempts
            entry["first_unreachable"] = first.strftime(TIMESTAMP)
            if error:
                entry["last_fetch_error"] = error
            row = {
                "key": key,
                "title": entry.get("title"),
                "company": entry.get("company"),
                "url": entry.get("url"),
                "fetch_attempts": attempts,
                "last_fetch_error": entry.get("last_fetch_error"),
            }
            if attempts >= UNREACHABLE_MAX_ATTEMPTS and now - first >= UNREACHABLE_MIN_SPAN:
                # The retry fields stay on the entry as its history; `--all`
                # can still revive it, like any other expired entry.
                entry["status"] = "expired"
                entry["expired_reason"] = "unreachable"
                gave_up.append(row)
            else:
                entry["status"] = "unreachable"
                unreachable.append(row)
            continue

        if status == "expired":
            entry["status"] = "expired"
            for field in (*RETRY_FIELDS, "expired_reason"):
                entry.pop(field, None)
            expired.append(
                {"key": key, "title": entry.get("title"), "company": entry.get("company"), "url": entry.get("url")}
            )
            continue

        try:
            score = overall_score(result.get("scores") or {})
        except ValueError as exc:
            errors.append({"key": key, "error": str(exc)})
            continue
        bad_gate = next(
            (f for f in ("location_verdict", "language_gate") if result.get(f) and result[f] not in GATE_VERDICTS),
            None,
        )
        if bad_gate:
            errors.append(
                {"key": key, "error": f"unknown {bad_gate} {result[bad_gate]!r}; expected one of {', '.join(GATE_VERDICTS)}"}
            )
            continue

        # A missing deadline from the fresh fetch is not a correction to the
        # stored date. Scored keys are excluded from the later sweep, so apply
        # must retire a passed deadline before updating any ranking metadata.
        parsed = parse_iso(result.get("deadline") or entry.get("deadline"))
        if parsed is not None and parsed < today:
            entry["status"] = "expired"
            for field in (*RETRY_FIELDS, "expired_reason"):
                entry.pop(field, None)  # fetched after all: closed on its deadline
            if result.get("deadline"):
                entry["deadline"] = result["deadline"]
            expired.append(
                {"key": key, "title": entry.get("title"), "company": entry.get("company"), "url": entry.get("url")}
            )
            continue

        legacy = entry_location_verdict(entry)
        if entry.get("location") in ("PASS", "FAIL", "FLAG"):
            entry.pop("location", None)  # legacy verdict, never a place
        for field in (*RETRY_FIELDS, "expired_reason"):
            entry.pop(field, None)  # fetched after all: the retry history is moot
        entry["status"] = "ranked"
        entry["rank_score"] = score
        entry["rank_verdict"] = band(score)
        entry["rank_date"] = today.isoformat()
        entry["location_verdict"] = result.get("location_verdict") or legacy or "PASS"
        entry["language_gate"] = result.get("language_gate") or "PASS"
        if entry["language_gate"] == "PASS":
            entry.pop("language_note", None)
        else:
            entry["language_note"] = result.get("language_note")
        # Two more verdicts the calibrated framework produces must outlive the
        # run: the compensation gate (04's hard filter on unpaid and
        # equity-only roles) and the city note the location rule asks for on a
        # hybrid or on-site role in a city he does not live in. Both additive,
        # and a PASS compensation verdict drops a stale note exactly the way a
        # PASS language gate does.
        if result.get("location_note"):
            entry["location_note"] = str(result["location_note"])
        if result.get("compensation_gate"):
            entry["compensation_gate"] = result["compensation_gate"]
            if entry["compensation_gate"] == "PASS":
                entry.pop("compensation_note", None)
            elif result.get("compensation_note"):
                entry["compensation_note"] = str(result["compensation_note"])
        # Absence is not a correction: a fetch that degraded to a listing page
        # returns no deadline, and blanking a stored one would erase a real
        # date and make the entry immortal to rule 6's sweep.
        if result.get("deadline"):
            entry["deadline"] = result["deadline"]
        for field in ("strengths", "gaps"):
            value = result.get(field)
            if isinstance(value, list):
                entry[field] = [str(b) for b in value][:3]
        # The scoring agent's posting_guard verdict line ("clean" or the ⚠ line).
        # Additive and optional: an agent on an older prompt returns none, and
        # nothing is written. Stored so a later reader can see why a row carried
        # a marker without re-fetching the posting.
        # Normalised so Step 5's "anything other than clean" rule holds whether
        # the agent returned "clean" or the gate's first line verbatim: any clean
        # verdict (with or without its context parenthetical) stores as "clean";
        # a ⚠ verdict stores without the "posting_guard:" prefix. A rescoring
        # result without a guard drops a stale one - absence means "not run",
        # and last run's verdict would mislabel this run's fetch.
        guard = result.get("guard")
        if isinstance(guard, str) and guard.strip():
            text = guard.strip()
            if text.lower().startswith("posting_guard:"):
                text = text[len("posting_guard:"):].strip()
            entry["guard"] = "clean" if text.lower().startswith("clean") else text[:200]
        else:
            entry.pop("guard", None)

        rows.append(
            {
                "key": key,
                "title": entry.get("title"),
                "company": entry.get("company"),
                "location": entry.get("location"),
                "url": entry.get("url"),
                "score": score,
                "verdict": entry["rank_verdict"],
                "location_verdict": entry["location_verdict"],
                "location_note": entry.get("location_note"),
                "language_gate": entry["language_gate"],
                "language_note": entry.get("language_note"),
                "compensation_gate": entry.get("compensation_gate"),
                "compensation_note": entry.get("compensation_note"),
                "deadline": entry.get("deadline"),
                "posted_date": entry.get("posted_date"),
                "urgent": bool(parsed and today <= parsed <= today + timedelta(days=URGENT_DAYS)),
                "strengths": entry.get("strengths", []),
                "gaps": entry.get("gaps", []),
                "guard": entry.get("guard"),
            }
        )

    if not args.dry_run:
        save_state(args.state, doc)

    rows.sort(key=lambda r: (r["score"], r["urgent"]), reverse=True)
    veto = lambda r: r["location_verdict"] == "FAIL" or r["language_gate"] == "FAIL"
    vetoed = [r for r in rows if veto(r)]
    ranked = [r for r in rows if not veto(r)]
    print(
        json.dumps(
            {
                "ranked": ranked,
                "vetoed": vetoed,
                "expired": expired,
                "unreachable": unreachable,
                "gave_up": gave_up,
                "errors": errors,
                "written": not args.dry_run,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 1 if errors else 0


EDIT_ENUMS = {
    # Reachable statuses are restricted on purpose. "ranked" and "expired" both
    # require evidence apply or sweep already holds (a score, a date
    # comparison), so reaching them from here would be a re-score wearing a
    # correction's name. These two are the corrections /rank actually needs:
    # retiring a row a gate refuses, and putting one back in the queue.
    "status": ("skipped", "new"),
    "language_gate": ("PASS", "FAIL", "FLAG"),
    "location_verdict": ("PASS", "FAIL", "FLAG"),
    "compensation_gate": ("PASS", "FAIL"),
}
EDIT_FREE = (
    "url",
    # The aggregator's `company` field is sometimes the ATS vendor ("Lever,
    # Inc.", "SmartRecruiters, Inc.") or a literal "unknown-company", which
    # poisons the dedup identity - company+title is the key. Writing the
    # recovered employer here is what makes such a row collidable and
    # applicable again; `employer_recovered` keeps the vendor name for
    # provenance, and the key string itself is immutable.
    "company",
    "employer_recovered",
    "language_note",
    "location_note",
    "compensation_note",
    # A row parked on an unreachable URL gets one more batch, then a skip with
    # the reason recorded rather than an unbounded retry loop.
    "skip_reason",
)
EDIT_BOOL = ("needs_rescrape",)
# Named so the refusal is explicit rather than a silent no-op: these carry a
# score or the evidence behind one, and a correction must never become a
# re-score. That guarantee is the reason this subcommand exists.
SCORE_FIELDS = ("scores", "rank_score", "rank_verdict", "rank_date", "strengths", "gaps")


def cmd_edit(args) -> int:
    """Apply the gate and status corrections that need neither a fetch nor a score.

    State grows rows the scoring runs cannot express: a role the compensation
    gate refuses has to stop coming back in later batches, a mis-paired URL has
    to be blanked, and a language verdict can change while the score stands
    still. None of those is a re-score, so none of them belongs in `apply`.
    """
    doc, seen = load_state(args.state)
    try:
        updates = json.loads(Path(args.updates).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        sys.exit(f"cannot read updates file {args.updates}: {exc}")
    if isinstance(updates, dict):
        updates = updates.get("updates", [])
    if not isinstance(updates, list):
        sys.exit("updates file must be a JSON array of {key, set} objects")

    changed, errors = [], []
    for item in updates:
        if not isinstance(item, dict):
            errors.append({"key": None, "error": "each update must be an object with key and set"})
            continue
        key, fields = item.get("key"), item.get("set")
        entry = seen.get(key)
        if entry is None:
            errors.append({"key": key, "error": "no such key in seen_jobs.json"})
            continue
        if not isinstance(fields, dict) or not fields:
            errors.append({"key": key, "error": "nothing to set"})
            continue
        bad = None
        for name, value in fields.items():
            if name in SCORE_FIELDS:
                bad = f"'{name}' carries a score; edit never re-scores"
            elif name in EDIT_ENUMS:
                if value not in EDIT_ENUMS[name]:
                    bad = f"'{name}' must be one of {EDIT_ENUMS[name]}"
            elif name in EDIT_FREE:
                if value is not None and not isinstance(value, str):
                    bad = f"'{name}' must be a string or null"
            elif name in EDIT_BOOL:
                if not isinstance(value, bool):
                    bad = f"'{name}' must be true or false"
            else:
                bad = f"'{name}' is not an editable field"
            if bad:
                break
        if bad:
            errors.append({"key": key, "error": bad})
            continue
        was = {name: entry.get(name) for name in fields}
        for name, value in fields.items():
            if value is None:
                entry.pop(name, None)
            else:
                entry[name] = value
        changed.append(
            {
                "key": key,
                "title": entry.get("title"),
                "company": entry.get("company"),
                "status": entry.get("status"),
                "changed": {n: was[n] for n in fields if was[n] != entry.get(n)},
                "now": {n: entry.get(n) for n in fields},
            }
        )

    if not args.dry_run:
        save_state(args.state, doc)
    print(
        json.dumps(
            {"changed": changed, "errors": errors, "written": not args.dry_run},
            indent=2,
            ensure_ascii=False,
        )
    )
    return 1 if errors else 0


def _force_utf8_output() -> None:
    """Write UTF-8 whatever the host's default encoding is.

    A piped stdout on Windows defaults to the ANSI code page (cp1252 on most
    Western installs), so printing a company, title or file name outside it
    raised UnicodeEncodeError before the workflow saw any output.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)  # absent on a StringIO under test
        if reconfigure:
            reconfigure(encoding="utf-8")


def main() -> int:
    _force_utf8_output()
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--state", type=Path, default=STATE)
    common.add_argument("--today", type=date.fromisoformat, default=date.today())
    common.add_argument(
        "--now", type=parse_now, default=datetime.now(timezone.utc),
        help="date-time for the unreachable retry floor (default: now, UTC)",
    )

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)

    cand = sub.add_parser("candidates", parents=[common], help="select the entries to score")
    cand.add_argument("--tracker", type=Path, default=TRACKER)
    cand.add_argument("--all", action="store_true", help="include every non-skipped status")
    cand.add_argument("--focus", help="substring filter over title, company and stored fit notes")
    cand.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="0 for no cap")
    cand.set_defaults(func=cmd_candidates)

    keys = sub.add_parser("keys", parents=[common], help="the dedup projection /scrape Step 0 reads")
    keys.add_argument("--ids-only", action="store_true", help="print the canonical keys without URLs")
    keys.set_defaults(func=cmd_keys)

    sweep = sub.add_parser("sweep", parents=[common], help="rule 6's expiry pass, no fetch")
    sweep.add_argument("--write", action="store_true", help="persist the expiries")
    sweep.add_argument("--exclude", help="comma-separated keys re-scored this run")
    sweep.set_defaults(func=cmd_sweep)

    app = sub.add_parser("apply", parents=[common], help="write scoring results back and print the ranking")
    app.add_argument("--results", required=True, help="JSON array from the scoring agents")
    app.add_argument("--dry-run", action="store_true")
    app.set_defaults(func=cmd_apply)

    edit = sub.add_parser(
        "edit", parents=[common], help="gate and status corrections that never re-score"
    )
    edit.add_argument("--updates", required=True, help="JSON array of {key, set} objects")
    edit.add_argument("--dry-run", action="store_true")
    edit.set_defaults(func=cmd_edit)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
