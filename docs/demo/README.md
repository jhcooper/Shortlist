# Shortlist demo (fictional candidate)

The candidate, "Daniel Reyes," is fictional: name, school, employers, contact details, resume numbers, and application history (companies marked "(demo)") and the locations of those applications. The job listings, companies, and pay in today's picks and checks are real, from a run on 2026-11-06, except the top pick's employer, renamed to the fictional "Riverside Regional Health."

- `tracker-demo.html`: the tracker, fully offline. Open it in a browser. Confirm, follow-up, stage, notes, and "Was this job open?" all work but reset on reload. The tailored resume downloads as .docx.
- `digest-email.html`: the morning email for this run.
- `run/`: what each step produced: raw results, dropped (with reasons), shortlisted, verified, judged.
- `config/`: the fictional candidate's profile, preferences, sources and base resumes, in the format the engine reads. The run itself used a private config with the same region and target roles; details that would identify the author (exact locations, employer lists, dream employers) are replaced here.
