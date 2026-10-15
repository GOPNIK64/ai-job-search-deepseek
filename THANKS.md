# Thanks

## Mads Lorentzen — the framework itself

This repository is a derivative of [ai-job-search](https://github.com/MadsLorentzen/ai-job-search)
(MIT, © 2026 Mads Lorentzen). Everything that makes it work came from there:

- the job-application methodology under `.claude/skills/job-application-assistant/` (`01..09`):
  fit evaluation, writing rules, CV/cover-letter templates, interview prep;
- the `/setup`, `/scrape`, `/rank`, `/apply`, `/interview` workflows;
- the portable portal-skill contract under `.agents/skills/`, plus `/add-portal` and
  `/add-template`;
- the deterministic Python tooling: `verify_pdf.py`, `verify_layout.py`, `job_key.py`,
  `rank_state.py`, `robots_check.py`, `security_guards.py`, `salary_lookup.py` — and the
  spec-pinning tests that keep them honest;
- the fork-divergence tooling (`check_upstream_updates.py`, `upstream_triage.py`, the
  `.github/upstream-wontport.txt` ledger). That tooling is the only reason a diverging
  fork like this one is maintainable at all;
- the LaTeX CV and cover-letter templates.

Upstream keeps Claude Code as its reference runtime and does not accept a parallel
runtime tree ([CONTRIBUTING.md](CONTRIBUTING.md)). Runtime ports live in forks — this is
one of them.

## What this fork changes

The runtime: this port targets **DeepSeek Harness (dsh)** instead of Claude Code, so the
model is DeepSeek's own. The methodology, the tools and the templates are upstream's and
stay tracked. *Work in progress — see the commit history.*

## Third-party

- **Lato** (tyPoland, Łukasz Dziedzic) and **Raleway** (The Raleway Project Authors),
  bundled in `cover_letters/OpenFonts/`, under the SIL Open Font License 1.1. The `OFL.txt`
  for each family ships next to its font files.

## Not affiliated

Not affiliated with, endorsed by, or sponsored by Anthropic, DeepSeek, or Mads Lorentzen.
