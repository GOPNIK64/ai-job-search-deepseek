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

