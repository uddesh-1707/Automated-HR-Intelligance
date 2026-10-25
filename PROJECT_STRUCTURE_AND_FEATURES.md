# Project Structure and Features

This document summarizes the repository's main folders and the features represented in its frontend and backend source.

## Folder Structure

```text
HRMS_AI_POWERED/
├── backend/
│   ├── app/
│   │   ├── agents/                 # HR agents, agent routing, and agent tools
│   │   │   └── tools/
│   │   ├── api/
│   │   │   └── v1/                 # Version 1 REST API route modules
│   │   ├── core/                   # Configuration, database, security, and memory
│   │   ├── db/
│   │   │   └── crud/               # Database operations
│   │   ├── models/                 # MongoDB collections and indexes
│   │   ├── schemas/                # Request/response schemas
│   │   ├── scripts/                # Seed and query scripts
│   │   ├── services/               # AI, RAG, MCP, vector store, and memory services
│   │   │   ├── mcp_tools/
│   │   │   └── memory/
│   │   ├── tasks/                  # Background agent and document tasks
│   │   ├── celery_app.py
│   │   └── main.py                 # FastAPI application entry point
│   ├── tests/
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── electron/                   # Electron desktop application entry points
│   ├── src/
│   │   ├── components/             # Shared UI, layout, charts, and chatbot
│   │   ├── context/                # Authentication and application state
│   │   ├── hooks/
│   │   ├── pages/                  # Login and HR module screens
│   │   ├── services/               # API clients and workflow services
│   │   ├── App.jsx                 # Application routes
│   │   └── main.jsx                # Frontend entry point
│   ├── index.html
│   ├── Dockerfile
│   └── package.json
├── images/                         # Product and UI screenshots
├── install/                        # Installation resources
├── docker-compose.yml              # Local multi-service deployment
├── start-local.ps1                 # Local startup script
├── README.md                       # Setup and usage guide
└── Project and feature documentation (*.md)
```

### Main Frontend Screens

The frontend routes to these screens:

- `LoginPage` — sign-in entry.
- `DashboardPage` — HR dashboard.
- `RecruitmentPage` — recruitment workspace.
- `EmployeesListPage` and `EmployeeProfilePage` — employee directory and employee details.
- `PerformancePage` — performance management.
- `AnalyticsPage` — workforce analytics.
- `LearningPage` — learning and development.
- `PayrollPage` — payroll.
- `OnboardingPage` — onboarding.
- `CompliancePage` — compliance.
- `Settings` — application settings.

The protected HR screens use a shared layout with navigation/header and a global chatbot modal.

## Feature List

### HR management

- **Employee management:** Browse employees, view employee profiles, search and filter employee data, and perform employee record operations through the API and CRUD layer.
- **Recruitment and candidates:** Manage candidates and their pipeline stages; backend modules also include resume handling and candidate search.
- **Recruitment MVP additions:** Published job posts, public application forms, criteria-based semantic matching when OpenAI embeddings and Pinecone are configured, score evidence, and manual HR review.
- **Multi-round interviews:** Configure rounds per job, record outcomes, enforce passed prerequisites, plan/reschedule with Google Calendar availability, explicitly confirm bookings, preserve history, and record audited HR progression overrides.
- **Recruitment communications:** Save, edit, preview, and delete unsent candidate email drafts; send only after HR approval; invitation drafts use persisted scheduled-interview details.
- **Onboarding:** Dedicated onboarding screen and API module for new-hire processes.
- **Performance management:** Dedicated performance screen and API module for performance information.
- **Learning and development:** Dedicated learning screen and API module for learning activities.
- **Payroll:** Dedicated payroll screen and API module for payroll information.
- **Compliance:** Dedicated compliance screen and API module for compliance information.
- **Dashboard and analytics:** HR dashboard, workforce analytics, metric cards, and chart components.

### AI-assisted capabilities

- **Recruitment assistant:** When an OpenAI-compatible model is configured, the assistant uses bounded tools for MongoDB candidate lookup, Pinecone evidence search, saved schedule proposals, and email drafts. It fails clearly when no model is configured; the language model cannot directly write arbitrary database records.
- **Specialized HR agents:** Agent modules cover employee operations, recruitment/job posting, onboarding, performance, learning, payroll, analytics, engagement, and compliance; supervisor/coordinator modules support request routing and coordination.
- **Database-backed AI actions:** Employee and candidate assistant tools call CRUD operations rather than writing directly to the database.
- **Resume and document processing:** Backend modules provide resume endpoints, document processing, and background document tasks.
- **RAG and semantic retrieval:** Retrieval-augmented generation, embeddings, vector storage, and document-related routes/services are present.
- **Agent memory:** Short-term, working, semantic, and long-term memory modules support context across interactions.
- **Calendar and email integrations:** Google Calendar events/invitations are optional and require a configured service account plus shared calendar permissions. SMTP candidate email is separately optional and HR-approved.

### Platform and operations

- **Authentication and route protection:** Email/password signup creates recruiter-role accounts; administrator accounts are configured through backend bootstrap settings. Authentication APIs and frontend auth context protect the HR application screens.
- **REST API and API documentation:** FastAPI exposes versioned HR routes and interactive API documentation.
- **Database access:** The recruitment MVP uses MongoDB through PyMongo. Collection indexes are ensured at API startup; no relational migration engine is used.
- **Background processing:** Celery app and task modules support agent and document jobs.
- **Desktop and web UI:** React/Vite frontend can run in a browser; Electron files provide a desktop-app target.
- **Containerized local stack:** The broader architecture describes PostgreSQL, Redis, Qdrant, and Celery; the recruitment MVP connects to MongoDB using `MONGODB_URI` and `MONGODB_DATABASE`.

## Notes

- This is a source-tree inventory, not a verification that every feature is complete or enabled in a running environment.
- Some feature areas are represented by backend APIs/services even when there is no separate frontend page for them.
- The README describes nine AI agents; the current source tree contains multiple specialized agent modules. This document names the represented areas rather than relying on a single agent count.
- The current recruitment MVP is narrower than the broader target architecture: it uses MongoDB/PyMongo and does not yet implement the other listed HR modules.
- Semantic matching requires separately configured embedding and Pinecone credentials; with missing services, applications remain stored and matching is marked pending.
- Interview scheduling checks HR host and required interviewer calendars; candidate calendar availability is intentionally not checked. Calendar events are only created after HR confirms a plan.
- The existing scheduling window defaults to seven days and is capped at 30 days; the three-day window in the attached specification was not adopted because the prior scheduling policy was retained.
- AI-assisted email content remains a draft until an authorized HR user approves it. SMTP failures are visible; only an explicit recipient rejection can be retried through a new audited attempt. Ambiguous outcomes are not retried because SMTP does not guarantee exactly-once delivery.
- The current repository does not contain a durable task queue implementation; resume processing uses FastAPI background tasks.
