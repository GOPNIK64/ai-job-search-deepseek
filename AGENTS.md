---
framework_version: 1.0.0
---

# Agent Guidelines: AI Job Search

This workspace is structured to manage job search activities, scraper tools, CVs, cover letters, and interview preparation.

## Thin-Pointer Design (Single Source of Truth)

To prevent duplication and configuration drift across different AI agent frameworks (Claude Code, Google Antigravity, Codex, Cursor, Gemini CLI, etc.), this workspace uses a unified thin-pointer design. All agent runtimes should load the canonical specifications and candidate profiles from the files and directories below:

> **Fork note (DeepSeek Harness).** This fork retargets the runtime to `dsh`. The
> workflow specifications are ported to [.dsh/skills/](.dsh/skills/) — the layer `dsh`
> discovers, at discovery rank 100. `.claude/skills/` and `.claude/commands/` remain the
> **upstream mirror**: they are kept so `tools/check_upstream_updates.py` and
> `tools/upstream_triage.py` can keep tracking upstream through the `framework_version`
> markers, and they are not the layer a `dsh` session should follow. The portal skills in
> `.agents/skills/` are shared by both runtimes and are byte-identical in each.
>
> The ported layer is generated, never hand-edited: `python3 tools/port_skills_to_dsh.py`
> rewrites `.dsh/skills/*/SKILL.md` from `.claude/skills/*/SKILL.md` and
> `.claude/commands/*.md`, and `tests/test_port_skills_to_dsh.py` fails on any file the
> converter would not produce. Judgement edits belong in that tool's `OVERRIDES` table.
>
> Layer boundary — dispatchers are generated, content is not. `.dsh/skills/` holds only
> the entry points dsh discovers; everything they read stays in `.claude/` and is
> referenced there. That is what keeps `/setup` alive: it writes the candidate profile
> into `.claude/skills/job-application-assistant/01-`, `02-` and `04-` through `07-`, and
> command bodies write newly confirmed facts back to `01-` as they run. Content in the
> generated layer would be deleted by the next converter run, with no copy anywhere.
>
> Mirror files this fork has edited directly (upstream merges to these will conflict, by
> design): `.claude/skills/job-application-assistant/09-web-research.md`, whose web-research
> escalation rule names this runtime's fetch tool and its robots.txt behaviour; and
> `README.md`, whose fork block states the port's status and points at `SETUP-DSH.md`.
>
> `SETUP-DSH.md` is this fork's install guide for `dsh`. It deliberately does not restate
> prerequisites — upstream's `SETUP.md` covers Bun, TeX, poppler and the Python environment,
> and every step there still applies. It documents only the delta: what `dsh` discovers and
> where, the prerequisites that fail in non-obvious ways, the credential location, the
> profile-never-pushed rule, the portals, and what is still unverified.
>
> Optional Python dependencies (PyYAML for `tools/lint_skills.py`, pypdf for
> `tools/verify_pdf.py`, openpyxl for `tools/convert_salary_excel.py`) live in the
> project's `.venv/`, so run those tools with `.venv/bin/python` when the ambient
> `python3` does not have them.

1. **Personal Candidate Profile:**
   - The candidate profile, contact details, education, and target preferences are defined in [CLAUDE.md](CLAUDE.md) and the individual profile methodology files under [.claude/skills/job-application-assistant/](.claude/skills/job-application-assistant/) (specifically `01-*.md` etc.).
2. **Canonical Workflow Specifications:**
   - The step-by-step instructions and triggers for tasks (setup, scrape, rank, apply, upskill, interview) are defined in the [.claude/](.claude/) directory (specifically under `.claude/skills/` and `.claude/commands/`).
   - Do not duplicate these rules or specifications. Treat `.claude/` files as the single source of truth.
3. **Portal Search Skills:**
   - Job-portal search CLIs live under [.agents/skills/](.agents/skills/) in the portable Agent Skills format (with a `SKILL.md` per portal). Codex and Antigravity discover these automatically; the `/scrape` workflow in [.claude/skills/job-scraper/](.claude/skills/job-scraper/) orchestrates them.
