# KSP Sahay

**AI Crime Intelligence Assistant for Karnataka State Police**

A conversational AI platform that lets investigators query crime data in plain language — any language — and surfaces patterns, criminal networks, hotspots, and predictive insights that would otherwise take hours of manual cross-referencing. Beyond the chatbot, it's grown into a full operational suite: FIR registration, chargesheet drafting, custody/bail tracking, BNSS deadline compliance, offender risk scoring, and a real, queryable audit trail — all scoped to each officer's actual jurisdiction and rank.

---

## 1. Project Overview

### Project Name
**KSP Sahay** ("Sahay" — Kannada/Hindi for "assistance") — an AI Crime Intelligence Assistant built for Karnataka State Police.

### Problem Statement
Investigators sitting on a large, structured crime database (FIRs, accused, victims, arrests, chargesheets, financial records) have no fast way to ask it a real question. Getting an answer today means knowing the right table, the right join, and often the right officer to ask — a natural-language question like *"is this part of a pattern?"* or *"which district has the most murder cases this year?"* has no direct path to an answer. Meanwhile, real signal already sits in the data — repeat-offender patterns, gang networks, seasonal crime spikes, socio-demographic correlations, upcoming BNSS deadlines — that nobody has time to go looking for manually.

### Solution
KSP Sahay is a chatbot-first layer over the department's live crime database: an investigator types (or speaks) a question in English, Hindi, Kannada, or any other language, and gets a grounded, cited answer — pulled from real case records, not a language model's guess. Around that core, the platform has grown into a full operational suite — FIR registration and amendment, AI-assisted chargesheet drafting, a criminal-network graph, a 2D/3D crime map, custody and BNSS-deadline tracking, offender risk scoring, and a real audit trail — all enforcing the same jurisdiction/rank scoping a live deployment would require, not a demo shortcut.

---

## 2. Tech Stack & Architecture

### Technologies Used

| Layer | Technology |
|---|---|
| **Backend** | Python 3.14, FastAPI, Uvicorn |
| **Frontend** | React 19 + Vite, `react-router-dom`, Recharts (charts), Leaflet + `leaflet.heat` + `leaflet.markercluster` (2D hotspot map), **MapLibre GL JS** (3D extruded-district / heatmap / clustered-points view, with a real rotating globe projection), `react-force-graph-2d` (network graph) |
| **Map tiles** | Esri World Imagery + Boundaries/Places reference layer (free, no API key) — real satellite/hybrid basemap for both the 2D map and the 3D globe |
| **Database** | Zoho Catalyst Data Store, queried via ZCQL (Zoho's SQL-like query language) |
| **AI / LLM** | Multi-provider: **Zia** (Zoho Catalyst's hosted LLM + RAG/document retrieval), **Google Gemini** (primary for answer composition, any language), **Groq** (primary for fast intent classification) — automatic failover between all three via a per-provider circuit breaker |
| **Voice** | ElevenLabs (speech-to-text + text-to-speech) |
| **Auth** | JWT (`PyJWT`) + `bcrypt` password hashing, role-based access control (DGP/IGP/SP/Inspector/Admin) with jurisdiction (station/district) scoping enforced server-side |
| **Semantic memory** | `sentence-transformers` + `chromadb` (local, offline vector search for long-conversation recall) |
| **PDF export** | `fpdf2`, with bundled Noto Sans / Noto Sans Kannada fonts for Kannada-script reports (case reports, chargesheet drafts, conversation transcripts, hearing lists) |

### System Architecture

```
┌───────────────────────────────────────────────────────────────┐
│                     React Frontend (Vite)                     │
│  Chat · Cases · Network · Hotspot Map (2D/3D) · Analytics      │
│  Insights · Alerts · Social Insights · Offender Profiling      │
│  Financial Intelligence · Custody Registry · Shift Briefing    │
│  Compliance (BNSS) · Chargesheet Management · FIR Registration │
│  Investigation Tray · Data Quality Supervisor · Dataset Notes  │
│  Audit Logs · Profile / Settings                               │
└───────────────────────────┬─────────────────────────────────┘
                            │ REST (JSON) + JWT
                            ▼
┌───────────────────────────────────────────────────────────────┐
│                       FastAPI Backend                         │
│                                                                 │
│  ┌───────────────────────────────────────────────────────┐    │
│  │ Router Layer (17 routers, all under /api/v1)           │    │
│  │ auth · chat · cases · network · analytics · scoring     │    │
│  │ insights · financial · voice · translate · report       │    │
│  │ social · legal · quality · custody · compliance         │    │
│  │ chargesheet · audit                                     │    │
│  └───────────────────────┬───────────────────────────────┘    │
│                          ▼                                     │
│  ┌───────────────────────────────────────────────────────┐    │
│  │ Chat Pipeline                                           │    │
│  │ fast-path regex router                                  │    │
│  │ → intent classification                                 │    │
│  │ → entity extraction                                     │    │
│  │ → RAG grounding / ZCQL aggregation                      │    │
│  │ → answer composition                                    │    │
│  │ → translation                                           │    │
│  └───────┬─────────────────────────────┬──────────────────┘    │
│          ▼                             ▼                       │
│  ┌───────────────────┐        ┌──────────────────────┐         │
│  │ Multi-provider     │        │ Semantic Memory       │         │
│  │ LLM Failover       │        │ (Chroma, Local)       │         │
│  │ Groq → Gemini → Zia│        └──────────────────────┘         │
│  └─────────┬───────────┘                                       │
│            │      Every decision, generation, and provider     │
│            │      failover is written to AuditLog — see the    │
│            │      Audit Logs page for a real, filterable read. │
└────────────┼─────────────────────────────────────────────────┘
             ▼
┌───────────────────────────────────────────────────────────────┐
│              Zoho Catalyst Data Store (ZCQL)                  │
│                                                                 │
│ • CaseMaster · Accused · Victim · ComplainantDetails            │
│ • ArrestSurrender · ChargesheetDetails · ActSectionDetails      │
│ • CriminalNetwork · FinancialTransaction · Unit (stations)      │
│ • District · CaseStatusMaster · AppUser · RolePermission        │
│ • AuditLog · ChatHistory                                        │
└───────────────────────────────────────────────────────────────┘
```

**Request flow for a chat message:**
`User types/speaks → React sends question → FastAPI /chat/message → regex fast-path (skip AI for obvious patterns) or LLM intent classification (Groq, any language auto-detected) → routed to case lookup / aggregate ZCQL query / legal knowledge base → answer composed (Gemini) with real data injected as grounding → translated back to the question's own language if needed → citations + audit log entry attached → JSON response → rendered in chat with a "via <provider>" transparency badge.`

Every LLM call in the system carries an explicit time budget and automatically fails over to the next provider in its chain if the primary is slow, rate-limited, or down — never a silent hang, never a raw 500. Every officer-facing write action (FIR registration/amendment, chargesheet draft generation) and every AI decision (intent classification, provider failover, language preference) is persisted to `AuditLog` and readable back through the **Audit Logs** page (Admin/DGP only).

---

## 3. Setup & Installation (How to Run)

### Prerequisites
- **Python 3.14+** (a `venv` is used — see below)
- **Node.js 18+** and npm (for the frontend)
- A **Zoho Catalyst** project with the Data Store schema provisioned (CaseMaster, Accused, Victim, AppUser, ChatHistory, AuditLog, RolePermission, CriminalNetwork, FinancialTransaction, Unit, District, ArrestSurrender, ChargesheetDetails, etc.)
- API keys: Zoho Catalyst OAuth credentials, and (optional but recommended) **Groq** and **Gemini** API keys for the fast AI path — both have free tiers
- (Optional) **ElevenLabs** API key for voice input/output

### Installation Steps

```bash
# 1. Clone the repository
git clone <repository-url>
cd ksp-backend

# 2. Backend — create a virtual environment and install dependencies
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 3. Configure environment variables (see below)
cp .env.example .env
# edit .env with real credentials

# 4. Run the backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 5. Frontend — in a separate terminal
cd frontend
npm install
npm run dev
# opens on http://localhost:5173
```

The backend API is now live at `http://localhost:8000` (interactive Swagger docs at `http://localhost:8000/docs`), and the frontend at `http://localhost:5173`.

### Deploying to Zoho Catalyst

The frontend and backend deploy separately as Catalyst-managed apps (Web Client Hosting + AppSail). See **`deploy.md`** for the full walkthrough, including two real, live-verified gotchas worth knowing up front:

- **Catalyst rejects certain environment variable names as "reserved keywords"** on AppSail deploy — both the standard Zoho OAuth trio (`ZOHO_CLIENT_ID`/`ZOHO_CLIENT_SECRET`/`ZOHO_REFRESH_TOKEN`) and anything with a `CATALYST_` prefix. This is why the env vars below are named `ZIA_OAUTH_*` and `APP_CATALYST_ENV` instead of the more obvious names — only the env var **string key** is renamed; the Python attribute names in `core/config.py` are unchanged.
- **Catalyst's managed Python runtime does not run `pip install`** — dependencies must be hand-vendored into `./vendor_deps` via a `predeploy` script (see `deploy.md`).

### Environment Variables

All configuration lives in `.env` at the project root (never commit this file — see `.env.example` for the full annotated template). Key variables:

| Variable | Purpose |
|---|---|
| `ZOHO_ORG_ID`, `ZOHO_PROJECT_ID` | Catalyst project identifiers |
| `ZIA_OAUTH_CLIENT_ID`, `ZIA_OAUTH_CLIENT_SECRET`, `ZIA_OAUTH_REFRESH_TOKEN` | OAuth credentials for the Catalyst Data API (named off the `ZOHO_` prefix, not `ZOHO_CLIENT_ID`/etc. — Catalyst AppSail's deploy rejects those exact names as reserved keywords) |
| `ZOHO_RAG_ENDPOINT`, `ZOHO_DOCUMENT_IDS`, `ZIA_TRANSLATE_ENDPOINT` | Zia's hosted RAG/translation endpoints |
| `APP_CATALYST_ENV` | `Development` or `Production` — which Catalyst environment to query (named off `CATALYST_ENVIRONMENT`, also rejected as a reserved AppSail keyword) |
| `JWT_SECRET_KEY`, `JWT_ALGORITHM`, `JWT_EXPIRE_MINUTES` | App-level auth token signing |
| `GROQ_API_KEY`, `GROQ_MODEL` | Fast classification provider (free tier at console.groq.com) |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Primary answer-composition provider (free tier at aistudio.google.com) |
| `ELEVENLABS_API_KEY` | Voice transcription + speech synthesis |
| `APP_USER_TABLE`, `AUDIT_LOG_TABLE`, `CHAT_HISTORY_TABLE` | Configurable Catalyst table names |

Every AI provider key is optional independently — the app degrades gracefully (falling back toward Zia-only behavior) if `GROQ_API_KEY`/`GEMINI_API_KEY` are left unset, rather than failing to start.

No API key is required for the map basemap — the 2D/3D Hotspot Map tiles come from Esri's free World Imagery + Boundaries/Places services, not a paid provider.

---

## 4. Features & Usage

### Key Features

1. **Natural-language chatbot, any language** — the assistant auto-detects the language of *every* message (verified working across English, Hindi, Kannada, Spanish, and French) and replies in that same language, without the user ever needing to say "answer in X." UI chrome language and chat reply language are independent — switching languages mid-chat never silently changes the whole app's language.
2. **Context-aware, multi-turn conversation** — follow-up questions ("How old is the first one?") are resolved against real conversation history, including a semantic-memory layer that can recall a fact from many turns earlier in a long session. Streaming responses, citations with expandable sources, a visible reasoning-path trace, and per-message voice playback.
3. **Criminal network visualization** — an interactive force-directed graph of gang membership and cross-case connections, with organized-crime-group detection (Organized / Loosely Organized / Fragmented) computed from real connection density.
4. **2D + 3D Hotspot Map** — Population-weighted choropleth (real crime-rate-per-lakh), Heatmap, clustered Points, and a Forecast layer (honestly labeled — this dataset's real trend slope is ~0), each also renderable as a real 3D scene: extruded district bars, a GPU heatmap, or clustered points, all on an actual rotating 3D globe (MapLibre GL). District → police-station drill-down with real per-station case counts.
5. **FIR Registration & Amendment** — a real, validated FIR intake form (auto-generated crime number, AI-assisted brief-facts drafting) with a full amendment audit trail; role-gated to Inspector/SP.
6. **AI-Assisted Chargesheet Drafting** — generates a structured 7-section chargesheet draft PDF from a case's real record, with a batch manager (pending/filed tabs, bulk generation + ZIP export) and its own audit trail.
7. **Custody Registry & BNSS Compliance** — every real arrest record with bail/custody status, upcoming-hearing tracking, and BNSS chargesheet-filing deadline monitoring (Shift Briefing surfaces "Cases Needing Attention" — deadlines ≤7 days or pending >90 days).
8. **Offender risk scoring & repeat-offender detection** — a transparent, weighted score (every point traces back to a named factor, never a black-box estimate), plus a real, name-matched repeat-offenders list.
9. **Early-warning spike detection & social/demographic correlations** — crime types currently spiking vs. their own real baseline, and correlation analysis against socio-demographic indicators.
10. **Explainable, audited AI** — every answer carries a citation (which case record or document grounded it) and discloses which AI provider actually produced it; every query, classification decision, FIR write, chargesheet generation, and provider failover is written to a real audit trail, readable end-to-end from the **Audit Logs** page.
11. **Role-based, jurisdiction-scoped access** — five ranks (DGP/IGP/SP/Inspector/Admin) with real, server-enforced permission gates (network visibility, export rights, FIR registration) and station/district-level data scoping matching Karnataka Police's actual rank hierarchy — an Inspector never sees another station's cases, not just a hidden UI element.
12. **Honest data-limitation disclosure** — a dedicated Dataset Notes page and a Data Quality Supervisor documenting exactly which fields are real vs. computed for demonstration, and what a production deployment would additionally need — framed as engineering transparency, not apology.

### Full Page Map

| Page | Route | Purpose |
|---|---|---|
| Home | `/home` | Quick actions, live overview, recent alerts |
| Chat | `/chat` | The core conversational assistant |
| Cases | `/cases` | Case search + full case detail |
| Network | `/network` | Criminal network graph |
| Hotspot Map | `/map` | 2D/3D crime map (population/heat/points/forecast) |
| Analytics | `/analytics` | Trends, seasonal patterns, forecast, case-outcome flow |
| Insights | `/insights` | Case summaries, MO/series detection, behavioral analysis |
| Alerts | `/alerts` | Early-warning spike/decline dashboard |
| Social Insights | `/social-insights` | Socio-demographic correlation analysis |
| Offender Profiling | `/offender-profiling` | Risk-score lookup, repeat offenders |
| Financial Intelligence | `/financial-intelligence` | Suspicious-transaction analysis |
| Custody Registry | `/custody` | Arrests, bail status, hearings |
| Shift Briefing | `/briefing` | Start-of-shift summary, cases needing attention |
| Compliance | `/compliance` | BNSS chargesheet-filing deadline tracking |
| Chargesheet Management | `/chargesheet` | Batch chargesheet draft generation |
| Register FIR | `/fir/register` | New FIR intake (Inspector/SP/Admin) |
| Investigation Tray | `/tray` | Pinned-case comparison workspace |
| Data Quality Supervisor | `/data-quality` | Dataset-wide completeness/quality audit |
| Dataset Notes | `/dataset-notes` | Real-vs-simulated data disclosure |
| Audit Logs | `/audit-logs` | Real, filterable read of the AuditLog table (Admin/DGP) |
| Profile / Settings | `/profile` | Account, theme, language, default page |

### Demo & Usage

Sample prompts that work end-to-end against the live dataset:

```
"What is Section 302 of the IPC?"
"Summarize crime number 100091036201900002"
"Who is the accused in crime number 100091034202400001?"
"How old is the first one?"                    ← follow-up, same conversation
"Which district has the most murder cases?"
"Kolar mein kitne cases hue?"                    ← Hindi
"ಕೋಲಾರದಲ್ಲಿ ಎಷ್ಟು ಕೊಲೆ ಪ್ರಕರಣಗಳಿವೆ?"                ← Kannada
"¿Qué es la sección 302?"                        ← auto-detected Spanish
"Is this part of a pattern?"                     ← crime-series / MO detection
```

A typed question can also be recorded as voice (mic button in the chat input), and any assistant reply can be played back as speech via the speaker icon on its bubble. A full conversation can be exported as a PDF from the chat sidebar. Test accounts for all 5 ranks are available as one-click "quick access" cards on the login screen (pre-fills the username and focuses the password field — the real password is never auto-filled).

---

## 5. API Documentation

Base URL: `http://localhost:8000/api/v1`. All endpoints except `/auth/login` require `Authorization: Bearer <JWT>`. Full interactive documentation (with live "Try it out") is auto-generated at **`/docs`**.

### Auth
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/auth/login` | `{username, password}` → `{access_token, role, username}` |

### Chat
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/chat/message` | Main chat endpoint. Body: `{question, session_id?, crime_no?}` → `{answer, session_id, citations, intent, provider_used, fallback_reason, translated_answer, response_language, latency_ms, ...}` |
| `POST` | `/chat/stream` | Same pipeline, server-sent-events streaming response |
| `POST` | `/chat/feedback` | Thumbs up/down on an answer |
| `POST` | `/chat/language-preference/clear` | Clear a session's "sticky" reply-language override |
| `GET` | `/chat/sessions` | List a user's chat sessions |
| `GET` | `/chat/sessions/search` | Search sessions by content |
| `GET` | `/chat/sessions/{session_id}/messages` | Full transcript of one session |
| `DELETE` | `/chat/sessions/{session_id}` | Delete a session |

### Cases (incl. FIR Registration & Chargesheet Drafts)
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/cases/filter-options` | Real, jurisdiction-scoped crime types/statuses/stations for filter dropdowns |
| `GET` | `/cases/register/preview-crime-no` | Preview the crime number a new FIR would get |
| `POST` | `/cases/register/ai-assist-brief-facts` | AI-drafted brief-facts text for a new FIR |
| `POST` | `/cases/register` | Register a new FIR (Inspector/SP/Admin) |
| `PATCH` | `/cases/{crime_no}/amend` | Amend an existing FIR (full audit trail) |
| `GET` | `/cases/search/count` | Total matching rows for a search filter set |
| `GET` | `/cases/search` | Filterable case search (crime type, district, station, status, date range...) |
| `GET` | `/cases/accused/search` | Search accused by (partial) name |
| `GET` | `/cases/accused/history` | An accused person's case history |
| `GET` | `/cases/{crime_no}` | Full case detail — victims, accused, arrests, chargesheets |
| `GET` | `/cases/{crime_no}/timeline` | Chronological investigation timeline |
| `POST` | `/cases/{crime_no}/chargesheet-draft` | Generate a structured chargesheet draft |
| `POST` | `/cases/{crime_no}/chargesheet-draft/pdf` | Chargesheet draft as a downloadable PDF |

### Network
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/network/organized-groups` | All gangs, classified by organization level |
| `GET` | `/network/gang/{gang_name}` | Full node/edge graph for one gang |
| `GET` | `/network/gang/{gang_name}/analysis` | Organization-level analysis for one gang |
| `GET` | `/network/accused/{accused_id}` | One accused person's network connections |
| `GET` | `/network/profile/{accused_id}` | Resolve a clicked node to a real person's profile |

### Analytics / Scoring / Insights
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/analytics/hotspots` | GPS points for the crime map (district/station/crime-type/date filters) |
| `GET` | `/analytics/summary`, `/analytics/crime-types` | Dataset totals, crime-type distribution |
| `GET` | `/analytics/trends`, `/analytics/seasonal` | Monthly / seasonal crime trend series |
| `GET` | `/analytics/forecast` | Statewide crime-count projection (OLS regression) |
| `GET` | `/analytics/hotspot-forecast` | Per-district projected case count (Hotspot Map's Forecast layer) |
| `GET` | `/analytics/case-outcome-flow` | Crime type → status → chargesheet outcome flow |
| `GET` | `/analytics/demographics/victims` | Victim gender/age demographics |
| `GET` | `/scoring/risk-score?name=X` | Offender risk score with factor breakdown |
| `GET` | `/scoring/risk-score/weights` | The exact point values the score is built from |
| `GET` | `/scoring/repeat-offenders` | Accused appearing in 2+ cases, real name-matched |
| `GET` | `/scoring/early-warnings` | Crime types currently spiking vs. their own baseline |
| `GET` | `/scoring/early-warnings/districts` | District-level breakdown of a spike |
| `GET` | `/insights/case-summary/{crime_no}` | AI-generated case summary |
| `GET` | `/insights/behavioral-analysis?name=X` | AI-generated behavioral pattern summary |
| `GET` | `/insights/mo-analysis/{crime_no}` | Crime-pattern / possible-series detection |
| `GET` | `/insights/similar-cases/{crime_no}`, `/insights/investigative-leads/{crime_no}` | Similar-case matching, AI leads |

### Financial / Social / Legal
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/financial/summary`, `/financial/suspicious` | Transaction totals, flagged-suspicious list |
| `GET` | `/financial/monthly-summary` | Transactions over time |
| `GET` | `/financial/export` | Financial Intelligence report PDF |
| `GET` | `/social/correlations`, `/social/district-crime-rates` | Socio-demographic correlation analysis |
| `GET` | `/legal/ipc-sections` | IPC/BNS section reference lookup |

### Custody, Compliance & Chargesheet Management
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/custody/summary`, `/custody/list` | Custody Registry totals + full arrest list |
| `GET` | `/custody/upcoming-hearings`, `/custody/bnss-deadlines` | Hearing/deadline tracking |
| `GET` | `/custody/export-hearings` | Hearing list PDF |
| `GET` | `/compliance/chargesheet-deadlines` | BNSS filing-deadline compliance view |
| `GET` | `/chargesheet/summary`, `/chargesheet/pending`, `/chargesheet/filed` | Chargesheet Management page data |
| `POST` | `/chargesheet/batch-generate` | Bulk-generate chargesheet drafts, ZIP download |

### Data Quality & Audit
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/quality/summary`, `/quality/drilldown` | Dataset-wide completeness/quality findings |
| `GET` | `/audit` | Real, filterable, newest-first read of the AuditLog table (Admin/DGP only) |

### Voice, Translate, Report
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/voice/transcribe` | Audio file → `{text, detected_language}` |
| `POST` | `/voice/speak` | `{text, language_code}` → MP3 audio |
| `POST` | `/translate/` | `{text, source_lang, target_lang}` → `{translated_text}` |
| `GET` | `/report/conversation/{session_id}` | Export a chat session as PDF |
| `GET` | `/report/tray-comparison` | Investigation Tray comparison PDF |
| `POST` | `/report/generate` | Case report PDF |

All error responses share one shape: `{"error": true, "message": "...", "path": "..."}`.

---
