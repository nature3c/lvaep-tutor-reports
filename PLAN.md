# LVAEP Tutor Session Reporting — Build Spec

Replaces the paper "Student Monthly Attendance & Achievement Form – FY 2026-2027"
(one sheet per student per fiscal year: a day(1–31) × month(Jul–Jun) grid of hours,
absence codes TA/SA/H, an A–E achievements checklist, and a "Stopped" box).

## Core design principle
**Store facts, derive views.** The paper form mixes data entry with reporting.
We store one record per session; the grid, monthly totals and staff reports are
all computed from those records, so nothing is summed by hand or goes stale.

## Stack (fixed — do not change)
- Python 3.14, Django 5.2 (already installed in `.venv`; no network access — do not pip install anything).
- SQLite locally; Postgres in production via `DATABASE_URL` (`dj-database-url`).
- `whitenoise` for static files, `gunicorn` for serving. Deploy target: Render.
- Server-rendered Django templates + one hand-written CSS file. No JS framework,
  no build step, no CDN assets. Tiny vanilla JS only where it clearly helps.
- Single Django project `config/`, single app `reports/`.

## Data model (`reports/models.py`)
- **User** — Django's built-in user. Tutors are normal users; staff have `is_staff=True`.
- **Site** — `name` (unique). Tutoring locations (e.g. "Bloomfield Public Library").
- **Student** — `first_name`, `last_name`, `notes` (blank ok), `created_at`.
- **Assignment** — a tutor–student pairing (pairings change during a term, so this is its own entity).
  `tutor` FK User, `student` FK Student, `site` FK Site, `meeting_days` (comma-separated weekday
  ints 0=Mon..6=Sun, e.g. "1,3"; provide helper `meeting_weekdays()` → list[int]),
  `meeting_time` (CharField, free text e.g. "6–7:30pm"), `start_date`,
  `status` (ACTIVE / STOPPED), `stopped_on` (null), `stopped_reason` (blank).
  Constraint: at most one ACTIVE assignment per (tutor, student).
- **Session** — `assignment` FK, `date`, `hours` Decimal(4,2), `status` choices:
  HELD ("Held"), TA ("Tutor absent"), SA ("Student absent"), H ("Holiday"); `notes` blank ok;
  `created_by` FK User; `created_at`, `updated_at`.
  Rules (enforce in `clean()` AND DB `CheckConstraint`s where possible):
  - HELD ⇒ 0.25 ≤ hours ≤ 8 and hours is a multiple of 0.25.
  - TA/SA/H ⇒ hours == 0. (Absences are explicit records, never "a blank box".)
  - `date` not in the future; `date` ≥ assignment.start_date; not after `stopped_on` if stopped.
  - UniqueConstraint (assignment, date) — one record per pairing per day.
  - Cannot be created/edited/deleted if its month is locked (see MonthLock).
- **Goal** — the achievements catalog. `category` (A Economic, B Educational, C Family,
  D Societal/Community, E Other), `text`, `is_reportable` (the asterisked items on the form —
  assumed to be funder-reported outcomes), `order`. Seed via a **data migration** with exactly:
  - A: *Enter Employment; *Retain Employment; Leave public assistance
  - B: Achieve work-based project learner goal; *Enter Occupational Skills Training Program; *Enter Postsecondary Education; *Obtain High School Diploma
  - C: Help more frequently with school; Increase contact with child(ren)'s teachers; More involvement in child(ren)'s school activities; Purchase books or magazines; Read to child(ren); Visit the library (with/for child(ren))
  - D: *Obtain citizenship; Achieve civics skills; Increase involvement in community activities; Vote or register to vote
  (* = is_reportable=True). "E. Other" is handled by StudentGoal with a custom text (below).
- **StudentGoal** — `student` FK, `goal` FK (null for custom "Other"), `custom_text` (blank),
  `attained_on` date, `recorded_by` FK User, `created_at`. Unique (student, goal) when goal is not null.
- **MonthLock** — `year`, `month`, `locked_by`, `locked_at`. Unique (year, month).
  Staff lock a month once reported, so reports never silently change afterward.

Put fiscal-year helpers in `reports/utils.py`: FY runs Jul 1 – Jun 30; `fiscal_year_for(date)` →
start year (2026 for FY 2026-2027); `fy_months(start_year)` → list of (year, month) Jul..Jun;
`is_locked(date)`.

## Access control
- Login required everywhere (Django auth, username + password). Staff create tutor accounts in
  Django admin. (Magic-link email login is a documented future improvement, not in scope.)
- Tutors can only see/modify sessions, students and goals reachable through **their own**
  assignments. Any other id → 404 (not 403, to avoid leaking existence). Centralise this in a
  helper like `get_assignment_for_user(user, pk)` and use it everywhere.
- Staff (`is_staff`) can view everything and access `/staff/…` pages; non-staff get 403 there.
- Staff pages also available: Django admin at `/admin/` with sensible `list_display`,
  `list_filter`, `search_fields` for every model.

## Tutor pages (mobile-first; this is the most important UX)
1. **Dashboard `/`** — one card per ACTIVE assignment: student name, site, meeting days/time.
   Each card has an inline **quick-log form**: date (default today), status (default Held),
   hours (default = that pairing's most recent HELD hours, else 1.5), optional notes. Submitting
   takes one tap. Show hours logged this month on the card.
   **"Did these sessions happen?" prompts**: for each active assignment, list scheduled meeting
   days (from `meeting_days`) in the last 14 days (≥ start_date, not locked, ≤ today) that have
   no Session record, each with one-tap buttons "Held (Xh)", "Student absent", "Tutor absent",
   "Holiday". This replaces filling in the form from memory at month-end.
2. **Student record `/assignments/<id>/`** — the paper form, digitised: a read-only FY grid
   (rows days 1–31, columns Jul–Jun) showing hours or TA/SA/H codes, grey-out invalid dates
   (e.g. Feb 30), monthly totals row, FY total. FY selector (default current FY). Below it, a list
   of this FY's sessions with edit/delete links (disabled for locked months).
3. **Edit session `/sessions/<id>/edit/`**, **delete** via POST with confirm.
4. **Achievements `/assignments/<id>/goals/`** — checklist grouped A–E; reportable goals marked.
   Checking a goal records `attained_on` (default today, editable) and who recorded it;
   unchecking removes it. Add custom "Other" goals with free text.
5. **Stop tutoring `/assignments/<id>/stop/`** — requires a reason (required field) and date.
   Sets status STOPPED, then emails all staff users (use `send_mail`; console email backend
   unless `EMAIL_HOST` env var is set) and flashes a confirmation. Replaces "notify the office ASAP".

## Staff pages
1. **Monthly report `/staff/reports/?month=YYYY-MM`** (default: previous month if today ≤ 7th,
   else current month). Month picker. Show:
   - Summary tiles: total hours, sessions held, students served (≥1 HELD), absences by type.
   - Table by **site**, table by **tutor**, table by **student** (hours, held count, TA, SA, H).
   - **Missing reports**: ACTIVE assignments (started on/before month end) with **zero** Session
     records in that month — tutor name, student, site. This is the key time-saver: staff stop
     chasing paper.
   - **Recently stopped**: assignments stopped in that month with reasons.
   - **Goals attained** in that month, flagging reportable ones.
   - Buttons: **Export CSV** (one row per session in month: date, site, tutor, student, status,
     hours, notes), **Lock month** / **Unlock month**.
   All aggregation via ORM queries (`annotate`/`aggregate`), not Python loops over sessions.
2. **Printable form `/staff/assignments/<id>/print/?fy=2026`** — a print-optimised page that
   mirrors the original paper layout (header with tutor/student/site/days/times, FY grid,
   achievements checklist with ticks, stopped box). Keeps continuity with the old workflow.
   Staff can reach it from the report tables and from a **staff assignments list
   `/staff/assignments/`** (filter by site / status / tutor search).

## Look & feel
- Clean, accessible, no framework. One `static/reports/app.css`. System font stack, good contrast,
  large tap targets (≥44px), works at 360px wide. Nav: tutors see "My students"; staff also see
  "Reports", "Assignments", "Admin". `@media print` rules for the printable form and report.
- Flash messages for every action. Form errors shown inline.
- Login page shows demo credentials (this is a public demo) when `DEMO_MODE=1`.

## Demo data — `python manage.py seed_demo`
Idempotent (do nothing if any Student exists unless `--reset`). Creates: 3 sites;
staff user `staff` / `demo1234`; 6 tutors `tutor1`..`tutor6` / `demo1234` with realistic names;
~15 students with realistic, diverse names; ~16 assignments (some tutors have 2–3 students,
one assignment STOPPED with a reason); realistic sessions from 2026-07-01 up to today
(following each pairing's meeting days, ~85% held with 1–2h, some SA/TA, July 4 as H),
deliberately leaving a few gaps so dashboard prompts and "missing reports" have content;
a handful of attained goals; July 2026 locked. Use a fixed random seed.

## Settings / deploy
- `config/settings.py` reads env: `SECRET_KEY` (dev fallback only when DEBUG), `DEBUG`
  (default False), `ALLOWED_HOSTS` (comma list; also append `RENDER_EXTERNAL_HOSTNAME` if set),
  `CSRF_TRUSTED_ORIGINS`, `DATABASE_URL` (default sqlite), `DEMO_MODE`, `EMAIL_*`.
  Secure cookie / SSL-redirect settings when not DEBUG. `TIME_ZONE = "America/New_York"`.
- `requirements.txt` pinned to the versions in `.venv` (`pip freeze`).
- `build.sh`: install requirements, `collectstatic --noinput`, `migrate`, `seed_demo`.
- `render.yaml`: one free web service (gunicorn `config.wsgi`) + one free Postgres database,
  wiring `DATABASE_URL`, generating `SECRET_KEY`, `DEMO_MODE=1`, `PYTHON_VERSION=3.13.x` compatible.
- `.gitignore` (venv, db.sqlite3, staticfiles, __pycache__, .env).

## Tests (`reports/tests/`) — must all pass with `python manage.py test`
- Session validation: HELD hours bounds & quarter-hour steps; absences must be 0h; future date
  rejected; duplicate (assignment, date) rejected; before start / after stop rejected.
- Month lock blocks create/edit/delete for tutors.
- Tutor A gets 404 for tutor B's assignment, session edit, goals page, stop page.
- Non-staff gets 403 on `/staff/reports/`.
- Report totals & missing-reports computed correctly for a small fixture.
- Dashboard prompts list an unlogged scheduled day and omit logged ones.
- FY helpers (Jun 30 vs Jul 1 boundary).
- CSV export content and headers.

## README.md
Short: what it is, the design principle, run locally (venv, migrate, seed_demo, runserver),
run tests, deploy to Render, demo logins. Keep it concise.

## Out of scope
Payments, magic-link login, SMS, multi-tenant, i18n.
