# Running this fork on DeepSeek Harness

Upstream's [SETUP.md](SETUP.md) is the reference for prerequisites — Bun, TeX, poppler,
the Python environment, the macOS/Linux/Windows differences. **Read it first; every step
there still applies.** This file adds only what changes when the runtime is
[`dsh`](https://github.com/deepseek-ai/deepseek-harness) instead of Claude Code, plus the
parts of a personal setup that must never reach a public remote.

If you *don't* need DeepSeek, use [upstream](https://github.com/MadsLorentzen/ai-job-search)
directly — none of this applies there.

---

## 1. What lives where

| Path | Role | Generated? |
| --- | --- | --- |
| `.dsh/skills/<name>/SKILL.md` | The 15 entry points `dsh` discovers: 3 model-invocable skills + 12 user-invocable commands | **Yes** — never edit by hand |
| `.claude/skills/job-application-assistant/01..09` | The methodology, and the candidate profile `/setup` writes | No — this is content |
| `.claude/commands/*.md` | The 12 commands' source text | No |
| `.agents/skills/<portal>/` | The portal CLIs (`freehire`, `linkedin`, 4 Danish boards) | No — shared by every runtime |
| `tools/` | Python verification and porting tools | No |
| `documents/`, `job_scraper/`, `applications/` | Your data (gitignored) | No |

`dsh` discovers skills under `.dsh/skills/` and `.agents/skills/`. It does **not** scan
`.claude/skills/` — it recognises that as Claude Code's format and ignores it.

**Dispatchers are generated, content is not.** `tools/port_skills_to_dsh.py` writes the
15 entry points from `.claude/`; everything they read stays in `.claude/` and is
referenced there. That matters because `/setup` writes your profile into
`.claude/skills/job-application-assistant/01-`…`07-`, and `/apply` writes confirmed facts
back to `01-` as it runs: content in the generated layer would be deleted by the next
converter run. `tests/test_port_skills_to_dsh.py` pins the boundary.

---

## 2. The three prerequisites that actually bite

Upstream covers these; three of them fail in a way that is worth knowing in advance.

**Bun is not optional.** The portal CLIs are TypeScript that imports `./commands/search.js`
from `.ts` sources. Node cannot run them (`ERR_MODULE_NOT_FOUND` — Node does not remap the
extension). Only Bun does. `curl -fsSL https://bun.sh/install | bash`, or `brew install bun`.

**TeX has to be on the PATH of whatever launches `dsh`.** If you install TinyTeX into your
home directory and start `dsh` from a terminal, `export PATH="$HOME/Library/TinyTeX/bin/universal-darwin:$PATH"`
in your shell profile is enough. If you launch the **desktop app** from Finder/Spotlight,
it does not inherit your shell's PATH, and CV compilation fails with `lualatex: command not
found` even though the command works in your terminal. Two fixes, no `sudo` needed either
way:

- symlink the two binaries into a directory the app already sees:
  `ln -s ~/Library/TinyTeX/bin/universal-darwin/lualatex ~/.local/bin/` (same for `xelatex`, `tlmgr`), or
- skip the question by calling the absolute path in the compile step.

**And finding the binary is only half of it — the sandbox also blocks the font cache.** `dsh`
runs bash under `workspace-write`, which refuses writes outside the workspace (`touch
~/Library/TinyTeX/texmf-var` → `Operation not permitted`), and `luaotfload` needs to write its
metrics there. So a compile that finds the binary can still die. Point TeX's cache inside the
repo and it stops being an issue:

```sh
CACHE="$(pwd)/.texcache" && mkdir -p "$CACHE"
TEXMFCACHE="$CACHE/" TEXMFVAR="$CACHE/" TEXMFCONFIG="$CACHE/" \
  lualatex -interaction=nonstopmode -halt-on-error main_example.tex
```

**All three variables, and absolute paths.** `TEXMFCACHE` alone still consults `TEXMFVAR` and
quits with `no writeable cache path, quiting`; a relative path fails the same way. `.texcache/`
is gitignored.

Why this is worth two paragraphs: `/apply`'s compiled-PDF verification is a **mandatory** step,
and it is the only check that catches a structurally broken `.tex`. A `\cventry` missing its
empty fifth argument produces a brace error that a brace-count check reports as *balanced* — one
missing `{` and one extra `}` cancel out — so the file looks fine to everything except the
compiler. If you would rather keep this out of `dsh` entirely, compile in your own terminal:
there both the PATH and the writes are yours.

**Python ≥3.10 plus the optional packages.** `tools/verify_pdf.py`, `tools/lint_skills.py`
and `tools/convert_salary_excel.py` need `pypdf`, `PyYAML` and `openpyxl` respectively. Any
interpreter works; a project venv keeps it out of your system Python:

```sh
python3 -m venv .venv && .venv/bin/pip install pyyaml pypdf openpyxl
```

`.venv/` is already gitignored. Run those tools as `.venv/bin/python tools/<tool>.py`.
Without `pypdf` or poppler's `pdftotext`, `/apply` skips its mechanical PDF check with a
warning and falls back to a visual keyword review — everything else still works.

---

## 3. The key and the model

Put your DeepSeek API key in `dsh`'s own credential store (the app's settings, or
`$DSH_HOME/.credentials.yaml`). Never in this repo, and never in a file that gets
committed — the framework has no place to read one from.

`deepseek-flash` is what this fork was developed against, for three reasons that matter
here: it is the vision-capable one (CV/PDF/layout review needs that), it is the cheap one,
and it has the higher concurrency limit, which matters because `/scrape` fans out across
portals. Subagents (`/apply`'s reviewer runs as one, and it is meant to start from a clean
context) use the same model unless you configure otherwise.

---

## 4. First run

```sh
git clone https://github.com/mancommunis/ai-job-search-deepseek
cd ai-job-search-deepseek
```

Then, in `dsh`, in that directory:

1. **`/setup`** — the interview. Nine sections, conversational, no forms: identity and
   contact, education, experience, skills, publications/awards, behavioural profile,
   career goals, references, and the search configuration that generates your
   `search-queries.md`. You can answer in any order and in your own words; it structures
   the data. It writes `CLAUDE.md`, the profile files above, `cv/main_example.tex`, and
   `job-scraper/search-queries.md`. Re-run single sections later with
   `/setup --section search`, `/setup --section identity`, etc.
2. **`/scrape`** — discovers every portal skill you have, runs your queries across them,
   deduplicates, and writes the results. It auto-skips any portal whose `SKILL.md` says
   `enabled: false`.
3. **`/rank`** — scores the new postings against your profile and produces a shortlist.
   Cheap and batched; this is the filter-everything step.
4. **`/apply <company>`** — one posting at a time, in depth: a tailored CV and cover
   letter, with a fresh-context reviewer checking every claim against your profile.
5. **`/outcome`** — record what happened. The framework calibrates its own templates from
   your outcomes, which is why `/gmail-sync` may not write to the tracker without your
   approval.

The design is **filter in bulk, apply in series.** There is no mass-apply path, by
upstream's design and deliberately: automated applications are against most boards' terms,
and a tailored application is the thing that converts.

The other commands: `/interview <company>` (prep, using the same company research cache),
`/upskill` (skill-gap analysis across your shortlist), `/expand` (adjacent role titles you
haven't considered), `/reset`, `/add-portal`, `/add-template`, `/html-report`,
`/notion-sync`, `/gmail-sync` (the last two need an MCP server — see §7).

---

## 5. Keep your profile off the remote

`/setup` puts your name, contact details, employment history, salary expectations and
referee contacts into **tracked** files: `CLAUDE.md` and
`.claude/skills/job-application-assistant/01-`, `02-`, `04-` through `07-`. The framework
also gitignores your actual working data (`documents/`, `job_scraper/`,
`job_search_tracker.csv`, `applications/`, `company_research/`), but the profile files are
tracked on purpose — they are what makes the framework portable and updatable.

So keep the profile on a branch you never push, and make that mechanical rather than a
habit. One hook, in `.git/hooks/pre-push`:

```sh
#!/bin/sh
# The 'personal' branch carries the profile (name, contact, history, salary expectation)
# in TRACKED files. This repo is public: that it never leaves is a mechanical rule, not
# discipline.
while read -r local_ref local_sha remote_ref remote_sha; do
  case "$remote_ref" in
    refs/heads/personal)
      echo "push refused by .git/hooks/pre-push: 'personal' is never pushed ($remote_ref)" >&2
      exit 1
      ;;
  esac
done
exit 0
```

`chmod +x .git/hooks/pre-push`. Your `main` stays a clean mirror of what you publish; your
profile lives on `personal` and rebases onto it whenever you update.

If you would rather not fork at all: `git clone` this repo, run `/setup` in place, and
never add a remote. The framework does not need one.

---

## 6. Portals

`freehire-search` (global tech, no API key) and `linkedin-search` are enabled by default.
The four Danish boards — `jobindex-search`, `jobnet-search`, `jobbank-search`,
`jobdanmark-search` — ship with `enabled: false`, because they only make sense for that
market. To turn them on, set `enabled: true` in the frontmatter of each
`.agents/skills/<portal>/SKILL.md`; they also need upstream's
`tools/install_portal_clis.py` for their dependencies. `/add-portal` scaffolds a new one
for any market you need — portals that are market-specific belong in your fork, not
upstream.

Two things that cost real time to discover:

- **`freehire`'s `regions` parameter is plural and the singular is silently ignored.**
  `--region us` is not merely wrong, it returns nothing; `north_america` is the correct
  value, alongside `eu`, `apac`, `latam`, `mena`, `cis`. The parameter-count is also not a
  result count: `meta.total` reflects the whole index for a fuzzy match, not your query.
- **`dsh`'s `web_fetch` presents its own product agent and does not read `robots.txt`.**
  Upstream's web-research skill assumed the fetch tool honored the policy, which changes
  who does the enforcing — so in this fork `tools/robots_check.py` is the *only* place the
  opt-out is honored before escalating around a WAF. Read
  `.claude/skills/job-application-assistant/09-web-research.md` before you change it.

---

## 7. Known limits

Stated plainly, because a framework that hides these wastes your time:

- **Commands receive arguments as text, not through a template variable.** Claude Code
  interpolates `$ARGUMENTS`; `dsh` has no such placeholder, so the ported commands read
  "the argument supplied with this invocation" and each one defines an explicit
  no-argument default. Invoking `/rank` with nothing is a supported path, not an error.
- **Whether user-invocable skills appear in `dsh`'s `/` menu is not yet verified in this
  build.** The files carry the frontmatter `dsh` documents (`user-invocable: true`,
  `disable-model-invocation: true`); the menu entry itself is unconfirmed. Typing the
  command name in a message works regardless.
- **`/notion-sync` and `/gmail-sync` need an MCP server configured in your harness** (a
  `mcpServers` entry in your `dsh` profile config). Without it they exit in one line
  saying so — that is the intended behaviour, not a failure. Their OAuth login happens in
  the harness, outside the command.
- **The framework's own numbers are upstream's**: the author's 69 tailored applications →
  20 first interviews → 1 signed contract. Nothing in this fork re-measures that, and your
  strike rate will differ by market, seniority and how many of the nine profile sections
  you actually filled in.
- **Windows is untested here.** Upstream supports it; this port was developed on macOS.

---

## 8. Updating from upstream

Two lanes, and they update differently:

```sh
git remote add upstream https://github.com/MadsLorentzen/ai-job-search.git
git fetch upstream
git merge upstream/master          # brings in tools/, portal CLIs, LaTeX, methodology
.venv/bin/python tools/port_skills_to_dsh.py   # regenerate the 15 entry points
.venv/bin/python -m unittest discover -s tests -p "test_*.py"
.venv/bin/python tools/lint_skills.py
```

The converter is deterministic and self-cleaning, and the test suite fails if any file in
`.dsh/` is not exactly what it would produce. So a hand edit is never the way to fix a
ported prompt: judgement edits go in the converter's `OVERRIDES` table, where the test
holds them in place and the next upstream merge cannot revert them.

One mirror file in this fork has been edited directly, and upstream merges to it will
conflict by design: `.claude/skills/job-application-assistant/09-web-research.md`. `AGENTS.md`
keeps the list.
