# REVIEW.md — reviewer guide

## Review prompt
You are reviewing PR #<n> in this repo. You did not write it.
Read CLAUDE.md, the task in the PR description, the full diff, and the files it touches. Run the tests yourself.
Report, most severe first:
- bugs, each with a concrete input that fails
- security or privacy problems: row-level security, secrets, personal data, prompt injection
- changes outside the task's scope
- missing or weak tests
- anything the author would struggle to explain in an interview
For each: file, line, what is wrong, and a suggested fix. Don't edit code.
End with one verdict: merge, merge after fixes, or don't merge.

Use the checklist below while reviewing, and check the changed code against `docs/edge-cases.md`. Mark any
finding of a kind not yet in that file "new edge case".

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
