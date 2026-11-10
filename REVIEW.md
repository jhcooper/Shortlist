# REVIEW.md — reviewer guide

## Review prompt
You are reviewing one pull request for Shortlist. You did not write it. Read `CLAUDE.md`, the task prompt and
`docs/edge-cases.md`, then the full diff and any file it touches.

1. Does the PR do what the task asked, and only that? Note scope creep and missing pieces.
2. Run the full test suite and the leak check yourself (commands in `CLAUDE.md`). Report the exact results.
3. Try to break it: walk every case in `docs/edge-cases.md` that touches the changed code, plus your own.
4. Go through the checklist below. Skip nothing; write "n/a" with a reason when an item does not apply.
5. Report findings as a numbered list, each with file:line, severity (blocker / should-fix / nit), what is wrong,
   and a concrete fix or failing input. No praise, no restating the diff.
6. Do not push commits. Findings go back to the builder.
7. Any finding of a kind not yet in `docs/edge-cases.md` is flagged "new edge case" so it gets added there.

## Checklist

### Row-level security
- [ ] Every new table has RLS enabled and forced in the same migration.
- [ ] Per-user tables: policies filter on `user_id = auth.uid()` for every operation they allow.
- [ ] Shared tables (`postings`, `companies`, ATS boards): users read only; writes by the service role only.
- [ ] No grants to `anon` unless the task says so. Views use `security_invoker`.
- [ ] A test proves user A cannot read or write user B's rows.
- [ ] Applied migrations are not edited; changes come as new migration files.

### Secrets
- [ ] No keys, tokens or passwords in code, tests, fixtures, logs, telemetry or error messages.
- [ ] Stored secrets use envelope encryption (AES-256-GCM, TOKEN_KEK-wrapped data key, user id + purpose as AAD).
- [ ] The service-role key and TOKEN_KEK are never reachable from the browser or the web bundle.
- [ ] New secret types are covered by `.gitleaks.toml`; `.env.example` stays empty.

### Personal data
- [ ] Fixtures use only the fictional Alex Rivera data, example domains and 555-01xx phones.
- [ ] No user's data can reach another user (shared tables, caches, logs, module-level state).
- [ ] Config and profile are passed per user/run, never loaded once at import.
- [ ] Commit author is a noreply address.

### Prompt injection and model output
- [ ] Posting, email and web text is delimited as data in prompts and never acted on as instructions.
- [ ] Model output is schema-validated; only allowlisted fields are written.
- [ ] Email recipients come from the user record only. No new code path sends mail or applies to jobs.
- [ ] URLs, SQL, shell and file paths are never built from model or posting text.
- [ ] Resumes pass the fact-checker before they are saved or sent.
- [ ] Model calls go through the telemetry wrapper (budget guard applies).

### Test quality
- [ ] Every changed behavior has a test that fails without the change.
- [ ] Edge cases from the task and `docs/edge-cases.md` are covered, including empty, malformed and hostile input.
- [ ] No existing test was weakened, skipped or deleted.
- [ ] Tests are deterministic: no network, no wall-clock dependence, no order dependence.
- [ ] Assertions check values, not just "no exception".
