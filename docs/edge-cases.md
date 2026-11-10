# Edge cases

Known traps, grouped by area. Every review finding of a new kind gets added here (one line, plus the task or PR).

## Multi-user isolation
- Config loads once per process at import (`prefilter.py`, `validate_resume.py`, `finalize.py`): a worker serving
  two users would apply the first user's rules to the second. Pass a per-user config object (task 2.3).
- Timezone is fixed to America/New_York; it becomes per user.
- Any module-level cache, global or memoized value keyed without `user_id` can leak between users.

## Goals and scoring
- Still in code, not config: "US locations or remote US only" and "3+ years" in `prompts/dream.md`, and the
  Career internship rule in `prefilter.fit_career`.
- Goal ids `career` and `bridge` are hard-coded in scripts; goals must be data (task 6.4).
- Prefilter drops are final and never re-scored when preferences change; store sub-scores and re-rank instead.

## Sources and verification
- Verification is the funnel's bottleneck: 74 of 158 judged postings were lost because no employer page was
  confirmed or verify slots ran out. ATS feeds fix this (phase 5).
- Aggregator listings with no ATS original must stay labeled "unconfirmed" with the source named.

## Location
- Location is US-only and list-based; geocoding replaces it (task 6.2).

## Pay parsing (Phase 1; most become moot with Haiku extraction, task 6.1)
- Labeled ranges without `$` or `k`, e.g. "Salary range: 95,000 - 110,000".
- Weekly or monthly pay read as yearly.
- A sign-on bonus before the range ("$5,000 sign-on bonus, $70k-$90k") is taken as the pay.
- Fixed in Phase 1, keep covered: 401(k) noise, non-USD amounts, k-ranges, hourly vs yearly.

## Start dates (fixed in Phase 1, keep covered)
- Start-date window bounded by the sentence; month abbreviations; past-year dates need a backstop.

## Resume fact-checker
- The watched-tools list is tech-only, so invented non-tech skills pass (task 8.1).
- Allowed years are hard-coded 2022-2027 (task 8.1).

## Leak check
- Crashes on staged binary files.
- `--all` run from a subfolder scans only that folder; always run from the repo root.
- Misses phones with a parenthesized area code and no space before the exchange.

## Tooling
- CI pins `anthropic<1` because tests import `httpx`; update the test to the newer SDK and drop the pin.
