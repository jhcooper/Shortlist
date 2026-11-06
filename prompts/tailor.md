# Tailoring a resume (coordinating session)

Start from `{{base_resumes}}[<base>]` (base named in the pick's `tailor.base`). Copy it to
`out/docs/<cid>.json` and change ONLY:
- `summary`: rewrite for this role and company, 45–65 words, using only facts already in the base resumes
  (education and the jobs in them). Name the company once if natural.
- Bullet order within each job, following `tailor.notes`.
- Light rewording of a bullet to mirror the posting's language, only where the base bullet already says the same
  thing. Never add an action, tool, number, scope or outcome. "pulled, cleaned, and formatted" stays that; it
  never becomes "validated" or "deployed".
- Skills: reorder, drop, or add from the approved list only (`skills.approved` in `{{config_dir}}/profile.yaml`).
  Skill-group labels may change ("Data & Analytics").
- You may drop a weaker bullet to fit one page; never drop a job's header line.
Keep every job's org, loc, title and dates exactly. Max 5 bullets per job. Keep every number exactly as written.
Base-specific fitting tips (which bullet to trim, numbers to keep) are in `resume_bases.<base>` of
`{{config_dir}}/profile.yaml`; read them before you start.

Then run `python3 build_doc.py resume <base> ../out/docs/<cid>.json "../out/docs/<Company> – <Role> – Resume.docx"`.
It rejects new facts and checks the page count; fix exactly what it reports.
