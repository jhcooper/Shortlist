# Shortlist

Shortlist is an assistant that finds jobs worth applying to instead of sending a flood of alerts.
For each job it picks, it explains why the role fits.
It also drafts a tailored resume for the roles worth pursuing.

## Status

Being rebuilt from a single-user pipeline into a multi-user web app (FastAPI, Supabase/Postgres, Next.js + TypeScript).
`docs/legacy-run-procedure.md` describes the old pipeline; it is documentation only.

## Running the tests today

```sh
pip install "anthropic<1" pyyaml
for t in tests/test_*.py; do CONFIG_DIR=config.example python3 "$t"; done
python3 scripts/leak_check.py --all
```

`tests/test_telemetry_sql.py` needs Postgres 16 binaries and skips without them.

Secret scan with gitleaks:

```sh
bash scripts/install_gitleaks.sh
.tools/gitleaks dir --config .gitleaks.toml --redact --no-banner .
```

## History

Developed in a private repo that held personal data. Personal config was moved out and this copy was published without history.

## License

MIT, see `LICENSE`.
