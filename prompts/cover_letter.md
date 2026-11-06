# Cover letter (coordinating session; only for rows with request_cover_letter true)

Read the row (company, role, posting_url, why, my_notes) and the posting (WebSearch the company and role, then WebFetch the matching result; direct posting_url fetches are refused).
Write `out/docs/cl-<row_id>.json`:
`{"date": "<Month D, YYYY>", "recipient": ["Hiring Team", "<Company>"], "salutation": "Dear Hiring Team,",
  "paragraphs": ["<why this company and role, specific to the posting>", "<the 1-2 most relevant experiences from
  the base resumes, with their real numbers>", "<what the candidate would bring + availability ({{graduation}} graduation;
  short-term roles: available now, per the goal's minimum hours)>", "<short close>"]}`
The letter is written in the candidate's own voice (first person, signed with their name by the builder).
250–350 words total. Facts only from `{{base_resumes}}` and the row's `my_notes` (the candidate's own words).
No invented enthusiasm claims about the company beyond what the posting says. Plain, warm, direct.
Build: `python3 build_doc.py cover ../out/docs/cl-<row_id>.json "../out/docs/<Company> – <Role> – Cover Letter.docx"`.
