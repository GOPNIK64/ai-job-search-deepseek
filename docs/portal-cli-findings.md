# Portal CLI findings — filter and flag behaviour

Measurements taken against the shipped portal CLIs, recorded here **without any
candidate or workspace data** so they can be proposed upstream as-is. Nothing in
this file depends on a profile, a market or a candidate; every number is a
property of the portal and its CLI.

Verified 2026-10-15. All counts are `meta.total` from the CLI's own JSON, or the
number of rows returned, with `--jobage 14` unless stated otherwise. Re-run the
commands to confirm before acting on them.

## 1. `freehire-search`: the country flag is `--country`, singular

`--countries` is not a flag. The CLI rejects it rather than ignoring it:

```
$ … search -q angular --countries gb --limit 2 --format json
{"error":"unknown flag --countries for 'search' - flags are never silently ignored,
 because a discarded filter changes what the search returns; see --help …",
 "code":"UNKNOWN_FLAG"}            # exit 1
$ … search -q angular --country gb --limit 2 --format json
{"meta":{"count":2,"page":1,"total":439}, …}   # exit 0
```

Comma-separated lists work and their counts add up: `--country de` 116,
`--country dk` 2, `--country de,dk` 118.

## 2. `freehire-search`: `--country` is silently discarded when `--region` is present

The filter is accepted, and changes nothing:

| Command (`-q angular --jobage 14`) | `meta.total` |
|---|---|
| `--region eu` | 1401 |
| `--region eu --country de` | **1401** (expected 116) |
| `--region eu --country dk` | **1401** (expected 2) |
| `--country de` | 116 |
| `--country dk` | 2 |
| `--region north_america` | 461 |
| `--region north_america --country ca` | **461** |
| `--region north_america --country us` | **461** |
| `--country ca` | 139 |
| `--country us` | 328 |

This is the failure mode the `UNKNOWN_FLAG` guard above exists to prevent — a
discarded filter changing what the search returns — and it is much harder to
notice, because there is no error and the result count stays plausible.

## 3. `freehire-search`: `--region` is a facet bucket, not a geography

- `--region eu` returns postings located outside Europe; a `--region eu --country dk`
  sweep (i.e. effectively `--region eu`) surfaced results in Ireland, Austria,
  the Netherlands, Poland and South Africa.
- `--region us` returns zero results (`{"count":0,"total":0}`) even though `--country us`
  returns 328. The working macro-region for the United States is `north_america`,
  which mixes in Canada.
- `--region latam` returns results (237 for `angular`, 14-day).

Consequence for any consumer: a region-only search is not a scoped search and
must be post-filtered against the location returned with each result. A
country-only search is scoped.

## 4. `freehire-search` / `linkedin-search`: `--jobage` is required for freshness

Without it, results include postings many months old: a sweep without `--jobage`
returned listings dated 2026-02 through 2026-09, all of them outside a 14-day
window and all of them wasted work.

## 5. Term specificity is inversely proportional to yield

`meta.total`, same window, same region/country filter:

| Market | `angular` | `nestjs` | ratio |
|---|---|---|---|
| Canada (`--country ca`) | 461* | 35 | 13× |
| United Kingdom (`--country gb`) | 177 | 29 | 6× |
| Germany (`--region eu --country de`, i.e. `--region eu`) | 1401 | 91 | 15× |

\* measured with `--region north_america --country ca`, i.e. 461 for all of North
America; Canada alone is 139.

Generic stack words (`angular`, `typescript`, `node.js`, `mongodb`) return an
order of magnitude more than framework-specific ones. Ordering a query set from
generic to specific is a better use of a call budget than leading with the
framework.

## 6. `linkedin-search`: `-l "Remote"` is not remote-in-your-market

`-l "Remote"` returns a global grab-bag (measured: United States, Copenhagen,
Oslo, Sydney, Los Angeles, Berlin in a single 10-result page). Concrete cities
are scoped correctly: `-l "Berlin, Germany"` returned 10/10 Berlin.

## 7. `jobindex-search`: free text degrades phrases, and some words are false friends

- A multi-word query degrades to matching one word: `"game developer"` returned
  Full Stack Developers, a Software Developer and a textile
  `Tekstilkonstruktør/Product Developer`.
- `unity` matched a Danish-language teacher at a school named "Unity Skolen", a
  LEGO design internship, the Green Girls Guides (scouts) and a Novo Nordisk ML
  engineer.
- `spiludvikler` returned 0 results.
- `ionic` matched ionic-bond chemistry: a PhD in CO2 valorization, an Assistant
  Professorship in Materials Science and a PhD in soft-matter physics. Zero
  development roles.

Single-word technical terms work well (`nestjs`, `angular`, `typescript`,
`mongodb` each returned relevant engineering roles).

## 8. `jobdanmark-search`: precision requires `--jobtitle-id`, not `--text`

| Query | Relevant engineering roles |
|---|---|
| `--text udvikler` | 0/5 |
| `--category 227978` ("IT, Ingeniør og Energi") | 0/8 |
| `--category 227978 --text udvikler` | 1/8 |
| `--jobtitle-id 463` (Softwareudvikler, backend) | **6/6** |
| `--jobtitle-id 466` (Programmør og systemudvikler) | **3/3** |
| `--jobtitle-id 470` (Softwareudvikler, frontend) | 0 results |

The IDs come from `autocomplete --query "<term>"`. Quirk: `autocomplete "udvikler"`
returns no suggestions, while `softwareudvikler` and `programmør` do.

## 9. Flag names differ per CLI; unknown flags are rejected loudly

`jobindex` uses `-q`, `jobnet` uses `--search-string`, `jobbank` uses `--key`,
`jobdanmark` uses `--text` / `--jobtitle-id`. There is no shared `-q`. Every CLI
observed rejects an unknown flag (`UNKNOWN_FLAG`, exit 1) instead of ignoring it —
worth preserving, given finding 2 shows what an ignored filter costs.

`jobnet` has no recency filter; `--order PublicationDate` is a sort, not a
filter, so its results need a client-side date cut. It also returns external
listings: 31 of 37 URLs in one sample pointed at other domains, all with
`isExternal: true`.

## 10. `jobbank-search`: the portal blocks automated traffic

```
{"error":"Jobbank is blocking automated requests with Cloudflare bot protection.
 Skip this portal or use the WebSearch fallback.","code":"API_ERROR"}
```

Its posting URLs return HTTP 403 to both `web_fetch` and `curl` with browser
headers, so no result from this portal can be verified or stored.

## 11. `freehire-search`: part of the feed arrives as aggregator URLs that cannot be fetched

Some results carry a third-party aggregator URL (`whatjobs.com`, by country
subdomain: `uk.`, `en-ca.`, `es.`, `pl.`, `nl.`, `it.`, `fr.`, `pt.`) instead of
the employer's own posting page. Those URLs do not resolve:

```
$ curl … "https://es.whatjobs.com/pub_api__cpl__358950384__7107?geoID=425"
http=403   <title>Just a moment…</title>    # Cloudflare challenge, same class as finding 10
```

Observed in one workspace's stored state: **213 of 569 URLs (37%)** across
several sweeps were `whatjobs.com` links - and they are concentrated in this one
portal, not spread across the feeds: **213 of `freehire-search`'s 362 stored rows
(58%)**, against **0 of 207** for `linkedin-search`, `jobindex-search`,
`jobnet-search` and `jobdanmark-search` combined.

Consequence for any consumer: a stored aggregator URL is unverifiable, so a
downstream step that must fetch the posting (a ranking or an application pass)
spends its full retrieval escalation - portal `detail`, `web_fetch`, `curl` with
browser headers, employer-site search - only to conclude "expired", once per
row. Prefer the employer's own URL when the feed offers one, verify that a URL
resolves before storing it, or record the aggregator link as a lower-confidence
finding rather than a normal result.

## 12. `jobindex-search`: `search` and `detail` disagree on `deadline` for the same posting

Same listing id, two different application deadlines, from the same CLI:

```
$ … search -q "automatiseringsspecialister" --jobage 9999 --format json
  id: r14036793 | deadline: 2026-12-05 | date: 2026-10-07
$ … detail r14036793 --format json
  id: r14036793 | deadline: 2026-10-21
```

The two commands read different fields upstream, and nothing in the output
flags the disagreement. A consumer that stores the `search` deadline and later
refreshes it from `detail` (the usual "freshest fetch wins" rule) silently
rewrites the value - and because a near deadline is treated as urgency, the
rewrite can turn an eight-week-old listing into a seven-day panic.

Occurrence measured: 1 conflict in the 13 rows of one batch that carried any
deadline at all. Treat a `deadline` as portal-reported, not verified, and keep
the source (`search` vs `detail`) alongside it if the difference matters.

Two more instances in a later batch, both off by one day in the *other*
direction, which rules out "the search field is simply stale":

```
ad "…udvikler til vores AI-team"   stored (search) 2026-12-13 | ad text 2026-11-08
ad "Senior Backend Developer (Python)" stored 2026-11-18 | ad text 2026-11-19
```

Three independent divergences, two of them within a day and one six weeks, is
the signature of two different upstream fields rather than clock skew.

## 13. `jobindex-search detail` is intermittently unparseable on `r`-prefixed ads

Some `r…` ids come back as `PARSE_ERROR` while `h…` ids on the same run work,
and a rerun of a previously-working `r…` id has also failed:

```
$ … detail r14052181 --format json     -> PARSE_ERROR
$ … detail h1708127 h1708003 h1707947 h1708213 --format json -> ok
$ … detail r14036793 --format json     -> ok (earlier run)
```

Consequence: a batch agent must escalate rather than conclude "expired". In one
lot of six postings every `detail` call returned only a teaser - job title,
company and a one-line summary, no body - and `curl` with browser headers
recovered the full text for all six. `detail` returning little is therefore an
escalation trigger, not a dead posting.

## 14. A `302` to the employer's own site is a normal `detail` outcome

`h1706827` redirected (302) to the employer's careers page instead of serving
the ad, because the listing had been filled from the employer's own ATS. The
same id had served the full ad when the state entry was created. Anything that
treats a redirect as a failed fetch loses a live posting; follow it, or fall
back to the employer's vacancy API (a MindKey-based board answered one).

## 15. The same posting can exist under two keys from one portal

`h1706975` (jobindex) and `r14046049` (the same portal, different ad family)
resolve to the **same** Patent- og Varemærkestyrelsen posting - same title, same
body, same deadline. The store keyed them separately because the dedup identity
is company+title and the two feeds spell the company differently between the
two records. Both rows survived into a ranking batch and were scored twice.

The cost is bounded while the two records agree. It stops being bounded when
they drift: one gets a fresh deadline and the other keeps the old one, and the
expiry sweep then retires half a posting.

## 16. An aggregator's uuid-to-posting mapping is not stable over time

`laraveldk.org/jobs/42539c89-15bb-4355-8e21-bb88dffc7119` was stored against an
Adversus posting. Fetched by an agent, it 302-redirected to a **Redpill Linpro**
posting (`…/senior-platform-engineer-at-redpill-linpro-4469440663`) - a
different employer, already present in the store under its own row. Fetched
again minutes later, the same URL 302-redirected to the correct Adversus
posting (`…/senior-full-stack-developer-at-adversus-4478534383`).

So the defect is worse than a bad link: the mapping between an aggregator's
opaque id and the posting it serves can change between two requests. A URL that
resolves correctly today is not evidence it will resolve tomorrow, and a
consumer that trusted it would have ranked one employer's ad under another
employer's row.



## 17. On `whatjobs.*`, HTTP 200 means three different things, none of them "live"

One batch hit every shape, from different country subdomains of the same
aggregator:

```
pl.whatjobs.com/...   -> 403 (bot rejection)
cl.whatjobs.com/...   -> 200 with a Cloudflare "Just a moment" challenge page
es.whatjobs.com/pub_api__cpl__360035282__7107 -> 200, a redirect shell that
                         bounces to the aggregator's own homepage with no
                         requisition anywhere in the HTML
```

A consumer that treats a 200 as liveness stores a shell as a posting. The same
batch produced the mirror case on the employer side, where a 200 is a *closed*
posting: `careers.capgemini.com` rendered "Sorry, this position has been
filled." with no description, an EY requisition served "The Job is no longer
available", and a third employer's URL answered **410**. Status codes are not
liveness on either side; only fetched text is.

## 18. The aggregator's `company` field is sometimes the ATS vendor, not the employer

Six of 50 rows in one batch carried a company that names no employer:

```
"Lever, Inc."            (from a Lever-hosted URL)      x2
"SmartRecruiters, Inc."  (from a SmartRecruiters URL; the employer was Version 1, Dublin)
"unknown-company"        (EURES rows with no employer in the ad)  x3
"Jobgether"              (an intermediary that manages the application)
```

This is worse than a wrong label: the store's dedup identity **is** company+title
(`tools/rank_state.py`), and `/rank`'s tracker exclusion compares the same pair.
Two requisitions from one recruiter, or two rows sharing the vendor name, can
collide or fail to collide on a field that never described the employer. One
row had no recoverable employer at all and had to be reported rather than
scored.

## 19. The upstream index is the reliable recovery path, and it beats the employer's own page

For a row whose stored URL is an aggregator copy, the aggregator's **upstream
source** holds the requisition text that the copy dropped. `freehire-search`'s
`search`/`detail` returned the full stored description for rows whose
`whatjobs`, `adzuna` or `europa.eu` URL failed, and **25 of 28 aggregator rows in
one batch were still scored** because of it. Caveat: the index sometimes keeps
only a summary (one row's own record was 415 characters while the same employer's
same-day sibling posting carried the complete requirements), so a thin index
record is a reason to cross-check, not a substitute for the employer's page.

Employer-side techniques that worked in the same batch, in order of reliability:

| Host | What fails | What works |
|---|---|---|
| Adzuna copies | sometimes 403 | often serve the employer's full text under `web_fetch` |
| Ashby (`jobs.ashbyhq.com`) | `web_fetch` shows only the title | `curl` returns a `<script type="application/ld+json">` JobPosting block with the whole description |
| Zoho Recruit | nothing readable in the DOM | the description sits in `var jobs = JSON.parse('...')`; decode `\xNN` escapes and drop invalid `\-` escapes |
| Greenhouse, Recruitee, Teamtailor, d.vinci, join.com | - | read directly |
| EURES (`europa.eu`) | an Angular SPA: raw fetch returns a 68 KB "Loading..." shell with no server-rendered text | recover the requisition through the upstream index, then reach the employer's own page when the ad names one |

EURES is worth calling out because it is **not** an aggregator copy: it is an
official EU portal that renders client-side, so it fails the same way an
aggregator shell does while being a first-class source. Treating `europa.eu` as
"just another aggregator" (as one rubric did) mislabels it; treating it as
readable text is worse.

## 20. A duplicate that diverges is worse than a duplicate that agrees

Finding 15 measured the same requisition stored under two keys. A later batch
showed why that stops being cosmetic: the two copies were read by different
agents and came back with **opposite gate verdicts on the same posting**.

```
movilges-it-consulting_…-angular-nestjs-hybrid        language_gate: PASS
   note: "Ingles/Portugues fluentes sao valorizados" (fluency valued, not demanded)
movilges-it-consulting_senior-full-stack-tech-lead    language_gate: FAIL
   note: "Portugues e Ingles fluentes (Espanhol valorizado)" (Portuguese demanded)
```

The second reading is the correct one - the parenthetical marks *Spanish* as the
valued extra, which makes Portuguese a condition - and the first row's verdict is
wrong on the same text. Whichever copy a later step reads decides whether the
role is vetoed or ranked, and the store has no way to notice the disagreement.

The same batch produced four more pairs of one-requisition-two-keys rows
(Software Mind, TRBL Services, Leonardo full-stack, Dell 299231 - the last one
under two different company labels). Measured cost of the gap, one batch: five
pairs, one opposite verdict, one posting credited to the wrong employer in the
feed's own `company` field.

## Upstream responses (maintainer reply, discussion #78, 2026-10-15)

The maintainer cloned the fork, read the delta, and answered the three observations raised with it. Recording the answers here so a later session does not re-report them as open problems:

- **Finding 3 (`--region us` returns zero).** Treated as a documentation or mapping bug, not a usage error: the skill documents `us` as a macro-region code, so if the API only answers `north_america`, the skill is wrong about its own parameter. Filed as an issue at the maintainer's request, with the two commands and their result counts.
- **`jobnet-search detail` returning 404 on external ads.** That URL class (`/find-job/`) was fixed for *search* in #557 (merged 2026-10-15, already in this fork's base). `detail` keeps falling back to the search summary **by design**, and #550 (in review) marks that fallback on stdout. The observation was accurate and is being handled upstream - nothing to change here.
- **Finding 10 (`jobbank-search` blocked by Cloudflare).** Documented behaviour in that skill's `SKILL.md` and README: the CLI reports the block and `/scrape` falls back to WebSearch. Keeping the portal `enabled: true` is therefore correct, not a misconfiguration.

Do **not** re-report the second and third items as open findings, and keep the `--region us` issue as the one open upstream thread from this file.
