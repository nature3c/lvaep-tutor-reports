# LVAEP tutor session reporting

A mobile-friendly replacement for the FY attendance and achievement form. **Store facts, derive views:** each session is recorded once; annual grids, monthly totals, and staff reports are calculated from those records.

## Run locally

Python 3.14 and dependencies are already available in `.venv`. No installation is needed in this workspace.

```sh
export DEBUG=1 DEMO_MODE=1
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_demo
.venv/bin/python manage.py runserver
```

Open http://127.0.0.1:8000. Demo logins: `staff` or `tutor1`–`tutor6`, all with password `demo1234`. The demo staff account is a superuser so the admin can manage tutors and pairings. Data starts July 2026; July is locked. Seeding does nothing when students exist; `seed_demo --reset` replaces reporting data. Use demo accounts only for the public demo.

For a separate installation, create a virtualenv and install `requirements.txt`. Environment variables are documented in `.env.example` (export them in your shell; the application does not automatically load dotenv files). SQLite is the local default. Production requires a `SECRET_KEY`; `DEBUG` defaults to false and enables HTTPS redirects and secure cookies when false.

## Verify

```sh
export DEBUG=1
.venv/bin/python manage.py check
.venv/bin/python manage.py test
```

Tests cover session and database validation, lock protections, tutor isolation, workflows, reports, CSV, fiscal years, and repeatable demo seeding.

## Use and deploy

Tutors quick-log sessions from **My students**, revisit scheduled days, maintain dated achievements, and notify staff when tutoring stops. Staff use **Reports** to find missing submissions, export CSV, and lock completed months. **Assignments** links to printable annual forms. Add users, students, sites, and pairings in **Admin**. Staff accounts need the appropriate Django model permissions (or superuser access) to administer data.

Create a Render Blueprint from this repository using `render.yaml`: it declares a free web service and free PostgreSQL database, a generated secret, Python 3.13, and the public demo. `build.sh` installs pinned dependencies, collects static files, migrates, and seeds idempotently. `RENDER_EXTERNAL_HOSTNAME` is added to allowed hosts and CSRF origins. Configure `EMAIL_HOST` and the `EMAIL_*` variables for SMTP; without them, notifications are printed to the console. Disable `DEMO_MODE` and replace demo data/accounts before using real records. Render provisioning and plan availability require validation in your Render account; deployment was not performed here.

Month locks protect session creation, editing, deletion, and moving dates, for staff as well as tutors. They also protect dated achievements and prevent changing a locked pairing’s tutor, student, or site. Unlock a month explicitly to correct its records. Use model methods/forms for fact changes; direct SQL and ORM bulk updates bypass model validation and must not be used for session/date changes.

Future improvement: magic-link email login. Payments, SMS, multi-tenant support, and i18n are outside scope.
