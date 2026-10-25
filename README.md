# HRMS Automated Intelligence — Recruitment MVP

This workspace contains the first implementation of the recruitment MVP described in [TECHNICAL_DOCUMENTATION.md](./TECHNICAL_DOCUMENTATION.md) and [PROJECT_STRUCTURE_AND_FEATURES.md](./PROJECT_STRUCTURE_AND_FEATURES.md). It follows the documented FastAPI and React/Vite structure and uses MongoDB for backend persistence. The backend currently focuses on private HR recruitment workflows; it does not replace or implement the other HR modules listed in the architecture documents.

## MVP workflows

- **HR account access:** Signup uses email and password and creates recruiter-role accounts only; administrator accounts can only be configured through the backend bootstrap settings. Passwords use Argon2 hashing, API access uses expiring JWTs, and HR routes require a valid bearer token.
- **Google Forms intake:** An installable Google Apps Script trigger reads a candidate's uploaded file from Drive and sends its bytes to a secret-protected backend endpoint. Applications require an active job and are deduplicated by normalized email and job.
- **Public job applications:** HR can create and publish roles with job criteria, deadlines, skill requirements, mandatory qualifications, custom questions, and a configurable 100-point ATS scoring model. Published roles get a public candidate form; the server enforces job status and opening/deadline times.
- **Resume review:** PDF, DOCX, and UTF-8 TXT resumes (5 MB maximum by default) are stored under a private, non-static directory. The parser extracts only text-backed email, phone, known skill terms, experience phrases, and education lines. Candidate name and job title come from the submitted form; parsing does not guess them. Applications start in `needs_review`; HR can review/edit details before advancing.
- **Semantic ATS matching:** When OpenAI-compatible embeddings and Pinecone are configured, background processing embeds privacy-filtered resume chunks into one shared Pinecone index, retrieves criterion-specific evidence, and stores a hybrid, weighted assessment in MongoDB. Without both services, applications remain saved with matching marked `pending`; no keyword-only score is presented as semantic.
- **Pipeline and interviews:** Search and filter applications, inspect and download resumes as an authenticated HR user, create job roles, and schedule/edit/cancel/complete interviews. Round-based Google Calendar events send invitations only after HR confirms the scheduling plan.
- **Recruitment assistant:** A configured OpenAI-compatible chat model can call bounded MongoDB, semantic-search, schedule-plan, and email-draft tools. Tool actions never bypass backend authorization; scheduling requires explicit HR confirmation and email tools only save drafts.
- **Google Forms guide:** The frontend includes the required form field list, a configurable Apps Script example, secret handling guidance, trigger setup, and a test workflow.

## Local setup (Windows PowerShell)

### 1. Backend

From the repository root:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
if (-not (Test-Path ..\.env)) { Copy-Item ..\.env.example ..\.env }
```

Edit the repository-root `.env`:

1. Set `JWT_SECRET_KEY` to the output of `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
2. Set `MONGODB_URI` to your local MongoDB connection (`mongodb://localhost:27017`) or MongoDB Atlas URI, and set `MONGODB_DATABASE` (default `peopleflow`). Do not commit credentials included in an Atlas URI.
3. Set both `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD` to create the first administrator. Use a unique password with at least 12 characters. The bootstrap account is created once and later environment changes do not reset its password.
4. Set `GOOGLE_FORMS_INTEGRATION_SECRET` to a separate random value of at least 32 characters to enable the intake endpoint. Leave it unset/blank to keep the integration disabled.
5. To enable actual semantic matching, set `EMBEDDING_API_KEY` and `PINECONE_API_KEY`; set the Pinecone serverless region/cloud and ensure the configured index name is available. `EMBEDDING_MODEL` and `EMBEDDING_DIMENSIONS` must match the Pinecone index dimensions. A Pinecone index is created at first processing if one does not exist. Leave either API key unset to keep applications pending rather than returning fabricated matches.
6. To enable the interactive recruitment assistant, set `AGENT_API_KEY` and `AGENT_MODEL`. For an OpenAI-compatible provider, set `AGENT_API_BASE_URL` to its documented API endpoint. This key is separate from `EMBEDDING_API_KEY`; without it the assistant returns a clear service-disabled response instead of pretending to be an AI agent. Candidate details and tool results used in a conversation are sent to the configured model provider, so configure a provider approved for your data.
7. Set `PUBLIC_APPLICATION_URL` to the frontend public apply URL root (default `http://localhost:5173/apply`).
8. Keep `RESUME_STORAGE_DIR` outside any web-served/public folder. For local development the default is `backend/private_uploads`.

Start the API:

```powershell
uvicorn app.main:app --reload
```

The API connects to MongoDB on startup, verifies connectivity, and creates required unique/search indexes before accepting requests. The API runs at `http://localhost:8000`; interactive API documentation is at `http://localhost:8000/docs`. Run these commands from `backend`; configuration is loaded from the repository-root `.env`.

For MongoDB Atlas, set `MONGODB_URI` to the cluster's SRV connection string and add the backend's IP to the Atlas network access list. Keep this URI in the local `.env`; never commit database credentials.

### 2. Frontend

In another PowerShell window, from the repository root:

```powershell
cd frontend
Copy-Item .env.example .env.local
npm install
npm run dev
```

Open the Vite URL (normally `http://localhost:5173`). `VITE_API_URL` must point to the backend's `/api/v1` base URL. The API's `FRONTEND_ORIGIN` must match the frontend origin.

### 3. First sign-in

Sign in with the bootstrap administrator values from the repository-root `.env`. HR teammates can create recruiter accounts using their email and password from the signup screen. Signup always assigns the recruiter role; administrator access is only created through the backend bootstrap settings.

Create one or more active roles under **Recruitment → Open a role** before configuring the Google Form's Job title choices.

Create a role from **Recruitment** and save it as a draft. Add a job description or responsibilities and publish it to generate a shareable `/apply/<token>` URL. Public applications accept PDF or DOCX resumes and are validated against opening dates, deadlines, and required custom questions. The existing Google Forms integration remains available for organizations that need their Drive-based submission flow.

### Configure semantic matching

Semantic processing requires an embedding provider API key and Pinecone credentials:

- `EMBEDDING_API_KEY`: key for the OpenAI embeddings API used with `EMBEDDING_MODEL` (default `text-embedding-3-small`).
- `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`, `EMBEDDING_MODEL_VERSION`: model/dimension/version used for both job criteria and resume chunks.
- `PINECONE_API_KEY`, `PINECONE_INDEX_NAME`, `PINECONE_CLOUD`, `PINECONE_REGION`: Pinecone serverless index settings. The API creates one shared index with the configured dimension if it does not exist.
- `MATCHING_SCORING_VERSION`: records the scoring logic version alongside every result.

The local API uses FastAPI background tasks because this checkout does not include a durable worker/queue. Resume parsing and initial application storage are synchronous; embedding and matching are enqueued after the response. Configure a persistent task worker before deploying to a multi-worker/production environment. A failed/pending application can be retried from its HR candidate details page; resume chunk IDs are stable per application/model version so Pinecone upserts are idempotent. A model-version change creates a new vector ID/filter namespace and requires re-running matching.

Matching is decision support only: exact skills, experience, education and criterion-specific semantic evidence are combined using per-job weights that must total 100. Mandatory criteria that are not explicitly verified are flagged for HR; they do not automatically reject a person. Original resume text is not copied into MongoDB; Pinecone receives relevant chunks with emails, phone numbers, and the candidate name redacted.

### HR-approved candidate email

Candidate emails are prepared as saved drafts on the candidate review page. HR must review the recipient, subject, and full message and explicitly approve sending. Configure `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, and `SMTP_STARTTLS` in the private repository-root `.env` to enable delivery. Leave `SMTP_HOST` or `SMTP_FROM_EMAIL` blank to disable sending; approval then returns a visible configuration error and does not record the message as sent. A send attempt is protected against duplicate sends for the same application and email type. Delivery is recorded as sent only after the SMTP server accepts the message; this does not guarantee inbox delivery.

### Multi-round interviews and Google Calendar

In **Interviews**, configure rounds for each job: name/order, duration, required interviewer email addresses, prerequisites, working hours, slot interval, and meeting link/location. A prerequisite is eligible only after its interview is completed with an explicitly recorded `Passed` outcome. Completed interviews without an outcome remain pending evaluation. Failure or hold outcomes never advance automatically.

The scheduling assistant accepts an HR instruction and prepares a candidate/slot plan. It checks the configured host and required interviewer calendars, HRMS bookings, progression outcomes, and the requested window. **Nothing is booked until HR reviews the plan and presses Confirm.** Availability is checked again immediately before creating each event. Google Calendar sends attendee invitations only after confirmation; booking results and failures are recorded in HRMS and audit logs. Candidate calendar availability is intentionally not checked, per the product instruction; candidates receive the confirmed event invitation. The default scheduling window remains seven days (maximum 30 days); this honors the prior scheduling policy rather than the attached prompt's proposed three-day window.

The conversational assistant uses the configured chat model with a fixed tool allowlist. Search, semantic-evidence lookup, schedule-plan creation, and email drafting are performed by backend tools with server-side validation. Scheduling plans are persisted and can only be booked through the separate explicit confirmation action. “Next round” is inferred only when eligible candidates point to one common configured round; otherwise the workflow asks HR to clarify. HR may authorize a progression override on a candidate profile by selecting a round, recording a reason, and explicitly confirming; the override is audited and does not alter the original outcome, rejection, or hiring decision.

Candidate email drafts can be listed, edited, previewed, and deleted while unsent. Interview invitation drafts require a scheduled interview and use its persisted time, timezone, round, and location; they are not sent until HR approves. Sent emails become read-only history. An explicit SMTP recipient rejection can be retried once per attempt through a new approved record. An ambiguous SMTP failure is marked unknown and is not retried because SMTP does not guarantee exactly-once delivery.

To enable real Google Calendar reads and bookings:

1. In Google Cloud Console, create or select a project, enable the **Google Calendar API**, and create a service account. Restrict access to the minimum required.
2. Create a service-account JSON key only if your deployment requires key-file authentication. Store it outside the repository in a protected location and set `GOOGLE_CALENDAR_SERVICE_ACCOUNT_FILE` to its absolute path. Never commit this file or paste its contents into `.env.example`.
3. Share the host calendar identified by `GOOGLE_CALENDAR_HOST_ID` with the service-account email as an editor. Share each required interviewer calendar with it as a reader. If a round requires HR attendance, set `GOOGLE_CALENDAR_HR_ID` to that HR user's calendar email/ID and share it as a reader. Google Workspace policies may prevent external service-account sharing.
4. Set `GOOGLE_CALENDAR_HOST_ID` to the actual ID of the writable shared host calendar. Do not use `primary` unless the service account itself has a writable primary calendar. In each job's round configuration, use interviewer email/calendar IDs the service account can read. HR-required rounds cannot be scheduled until `GOOGLE_CALENDAR_HR_ID` is configured.
5. Install backend requirements and restart the API. A missing key file or calendar permissions are shown as scheduling failures; HRMS will not claim events were booked when Calendar is unavailable.

#### Interview automation workflow

1. **Plan** (Interviews page or chatbot, e.g. "Schedule interviews for the 4 shortlisted candidates for Technical Round 2"). The job, round, candidate names/count and eligibility (recorded `Passed` outcomes for the round's prerequisites) are resolved; interviewer, HR and HRMS calendars are checked within the round's working hours. Candidate calendars are not checked (not supported). Nothing is booked or emailed yet, and a newer plan for the same job/round supersedes older unconfirmed ones.
2. **Confirm.** Calendar events are created, each booked candidate's `pipeline_status` becomes `Shortlisted for <round>` (stage `interview`), unselected candidates are untouched, and the shortlisting email to the candidate plus a notification to each interviewer are sent.
3. **Track.** Your Schedule shows candidate, job, round, date/time, interviewer, meeting link, interview status, pipeline status and email status from MongoDB, refreshed after every booking, on tab focus and every minute. Recording an outcome updates the status (`Passed`, `Not selected after`, `On hold after`) and makes the candidate eligible for the next round.
4. **Retry.** Emails are logged before sending with deterministic idempotency keys, so confirming twice or pressing *Retry sending emails* never sends a message twice. If SMTP is not configured the booking still succeeds and the UI says the emails were not sent.

### Email-based interview automation (current workflow)

Interviews are scheduled and confirmed **by email; no Google Calendar events or invitations are created**. Interviewer availability comes from HRMS bookings and, optionally, a *read-only* Google Calendar connection (service account); without it the plan says availability was checked against HRMS bookings only.

1. **Plan** in the Interviews page or the chatbot. HR must **confirm** before anything is saved or emailed.
2. **Confirm** saves the interview, marks the candidate `Shortlisted for <round>`, generates a temporary meeting link (if a provider is configured), and emails the candidate (congratulations, passed previous round when applicable, date/time/timezone, instructions, link) and the interviewer (candidate name/email/phone, job title and description, resume attached plus a secure resume link, round, time, and a **unique evaluation link**).
3. The interviewer opens `/evaluate/<token>` (no login), reviews the candidate and resume, and submits **Passed / Failed / On Hold** with feedback. Tokens are unguessable, stored only as hashes, expire (`EVALUATION_LINK_EXPIRY_DAYS`), cover one interview and are revoked when the interview is cancelled or rescheduled. The first final outcome wins; On Hold can be resolved later; a pass makes the candidate eligible for the next configured round. Sending an email never marks anyone Passed.
4. A later round needs a **recorded Passed outcome** in its prerequisite rounds (configured prerequisites, otherwise the immediately preceding round). Wording in a chat instruction never counts as a pass.
5. Emails are logged before sending with idempotency keys; failures keep the schedule and can be retried safely.

**Meeting links.** `MEETING_PROVIDER=zoom` (server-to-server OAuth app: `ZOOM_ACCOUNT_ID`, `ZOOM_CLIENT_ID`, `ZOOM_CLIENT_SECRET`; waiting room on) or `webhook` (your service returns `{"join_url": "https://...", "meeting_id": "..."}` for `create`/`delete` actions). Links are unique per interview, recorded with an expiry (`MEETING_LINK_EXPIRY_HOURS` after the interview ends; overdue links are deleted at the provider when schedules are listed) and deleted at the provider on cancel/reschedule. With `none`, HRMS reports that no link could be generated and never fabricates a URL.

### Resume shortlisting

Each application gets an explainable assessment (`/recruitment/jobs/{id}/shortlist`, or the **Shortlist resumes** button): required/preferred skill evidence (exact, synonym/abbreviation, spelling variant, semantic embedding, quote-verified LLM), relevant projects/internships, responsibility alignment, experience, a configurable weighted score, and a verdict. `not_evidenced` (no evidence found) is kept distinct from `contradicted` (the resume says the candidate lacks it). A candidate is never marked not eligible just for different wording. Weights, thresholds, the semantic threshold, the default limit and extra synonyms are `.env` settings (see `.env.example`).

### LLM provider

`LLM_PROVIDER` (`openai`, `openai_compatible`, `anthropic`), `LLM_MODEL`, `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_TEMPERATURE` select the provider; adapters in `app/services/llm/` hide provider differences, and invalid settings stop startup with a clear message. The assistant answers HR tasks only and declines other requests without calling the model.

#### Troubleshooting "Google Calendar ... calendar was not found" (HTTP 404) / "access denied" (403)

Open **Interviews → Plan interview rounds → Check Google Calendar connection**. It tests the host calendar, the optional HR calendar, and every interviewer configured for the job's rounds, and names the exact calendar that fails plus the service-account email to share it with. Typical causes:

- `GOOGLE_CALENDAR_HOST_ID` is empty or `primary` (service accounts have no usable `primary`). Set it to the ID of a real calendar — for a normal Google account this is the calendar owner's Gmail address (Calendar → Settings and sharing → Integrate calendar → Calendar ID).
- That calendar (or an interviewer's calendar listed in the round) has not been shared with the service-account email (`client_email` in the JSON key) with **Make changes to events**.
- The Google Calendar API is not enabled in the service account's Cloud project.
- Google rejects attendee invitations from a service account unless Google Workspace domain-wide delegation is configured (`forbiddenForServiceAccounts`); on a personal Gmail account events cannot invite the candidate automatically.

Restart the backend after changing `.env`.

Google Calendar invitations are generated by the Calendar API with `sendUpdates=all`; there is no separate SMTP interview invitation. Calendar availability must be readable for every configured participant, and event creation requires write access to the host calendar. Rescheduling a Google Calendar event rechecks the newly selected time and is audited; if external calendar access is unavailable, resolve the reported issue before relying on the HRMS record as a calendar booking.

## Google Forms and Drive integration

### Required form questions

The provided script uses these titles by default (change `CONFIG` in the script if your form uses different titles):

1. `Candidate name` — short answer, required
2. `Email address` — short answer with email validation, required
3. `Phone number` — short answer, optional
4. `Job title` — dropdown, required; value must exactly match an active HRMS job
5. `Resume` — Google Forms file-upload question; PDF, DOCX, or TXT, maximum 5 MB

### Configure and install the trigger

1. Deploy the backend on a trusted HTTPS origin and configure its `GOOGLE_FORMS_INTEGRATION_SECRET` in the backend environment. Restart/redeploy the backend after changing environment values.
2. Sign in to the HRMS and open **Application intake**. Copy the Apps Script example. Replace `CONFIG.endpoint` with `https://YOUR-DOMAIN/api/v1/recruitment/google-forms/applications` and update the question titles to match the form.
3. Open the form's linked Apps Script project (or create one at `script.google.com`) and add the script. In **Project Settings → Script properties**, set `HRMS_INTEGRATION_SECRET` to the same secret as the backend. Do not put this secret in the form, frontend, or a public repository.
4. In **Triggers**, add an installable trigger for `onFormSubmit` with event source **From form** and event type **On form submit**. Authorize the script as the trusted form owner; it needs Drive access for the uploaded file and permission to call the HRMS HTTPS endpoint.
5. Submit a test application with a small supported resume. The script uses `DriveApp.getFileById()` to read the Drive upload and sends its bytes as base64 over HTTPS to the protected API. Confirm the API returns success and the application appears in **Needs review**. Check Apps Script **Executions** and backend logs for failures; the script fails visibly on non-2xx responses.

The trigger must run with an account that can access the uploaded Drive files. If the Google Workspace policy does not permit Apps Script Drive access or external URL fetch, the integration cannot transfer files until that policy is addressed. Keep job dropdown options in sync with active HRMS roles. The endpoint is disabled when no integration secret is configured; arbitrary anonymous application submission is not supported.

## API and data notes

- API prefix: `/api/v1`
- Authentication: `POST /auth/login`, `POST /auth/signup` (email/password; creates recruiter role only), and `GET /auth/me`
- Jobs and candidate applications: `/recruitment/jobs`, `/recruitment/applications`, `/recruitment/dashboard/summary`
- Public job/apply routes: `GET /recruitment/public/jobs/{public_token}` and `POST /recruitment/public/jobs/{public_token}/applications`
- Job lifecycle: `PATCH /recruitment/jobs/{job_id}`, `POST /recruitment/jobs/{job_id}/publish`, `/close`, and `/archive`
- Semantic matching: `/recruitment/applications/{id}/rerun-matching`, `/match-details`, `/recruitment/jobs/{id}/matching-status`, and `/recruitment/jobs/{id}/semantic-search`
- Resume download: `/recruitment/applications/{application_id}/resume` (bearer token required)
- Google intake: `POST /recruitment/google-forms/applications` with `X-Integration-Key`
- Interviews: `/interviews` (create/list), `/interviews/{id}` (edit/status update)
- Candidate details are stored separately from applications, with unique MongoDB indexes for email and candidate/job applications.
- Interview instants are persisted in UTC; the IANA timezone is saved separately and returned with the schedule.
- MongoDB connection settings are `MONGODB_URI` and `MONGODB_DATABASE`. The backend validates the connection and ensures indexes at startup; it does not use SQLite, SQLAlchemy, or Alembic.
- Resume metadata and a SHA-256 digest are stored in the database; raw files remain in private storage, not under frontend assets or a static route. Back up that storage directory securely alongside the database.
- The parser is deterministic and dependency-based, not an LLM parser. It does not infer candidate names, education credentials, or other details absent from resume text. Scanned/image-only PDFs and malformed documents receive a visible parsing error and do not create an application.
- The AI assistant requires a configured model provider (`AGENT_API_KEY` and `AGENT_MODEL`); semantic search additionally requires embedding and Pinecone configuration. SMTP sending and real Google Calendar operations also require valid external configuration and permissions. No durable background queue exists in this checkout; resume processing uses FastAPI background tasks.

## Tests and build

From `backend` with the virtual environment active:

```powershell
pytest
```

From `frontend`:

```powershell
npm test
npm run build
```

Backend tests use an in-memory MongoDB-compatible test database and cover authentication, recruiter-only signup, Google intake secret and payload validation, private resume storage/download, duplicate applications, resume parsing, and interview validation/status transitions. Frontend tests cover signup fields, API auth-header handling, and visible API errors.

## Current limitations

- Email notices and calendar invitations are not implemented.
- Resume parsing is a deterministic text parser with a small skills vocabulary; it does not provide semantic extraction, OCR, or an AI fallback.
- The frontend intake guide is administrator-facing; candidate applications enter through the configured Google Form integration.
- Only the recruitment MVP is implemented. Employee, payroll, onboarding, performance, compliance, analytics, agent, and other modules in the broader technical documentation remain out of scope.
#   A u t o m a t e d - H R - I n t e l l i g a n c e  
 