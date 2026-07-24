# KSP Sahay

**AI Crime Intelligence Assistant for Karnataka State Police**

A conversational AI platform that lets investigators query crime data in plain language — any language — and surfaces patterns, criminal networks, and predictive insights that would otherwise take hours of manual cross-referencing.

---

## 1. Project Overview

### Project Name
**KSP Sahay** ("Sahay" — Kannada/Hindi for "assistance") — an AI Crime Intelligence Assistant built for Karnataka State Police.

### Problem Statement
Investigators sitting on a large, structured crime database (FIRs, accused, victims, arrests, chargesheets, financial records) have no fast way to ask it a real question. Getting an answer today means knowing the right table, the right join, and often the right officer to ask — a natural-language question like *"is this part of a pattern?"* or *"which district has the most murder cases this year?"* has no direct path to an answer. Meanwhile, real signal already sits in the data — repeat-offender patterns, gang networks, seasonal crime spikes, socio-demographic correlations — that nobody has time to go looking for manually.

### Solution
KSP Sahay is a chatbot-first layer over the department's live crime database: an investigator types (or speaks) a question in English, Hindi, Kannada, or any other language, and gets a grounded, cited answer — pulled from real case records, not a language model's guess. Beyond Q&A, it proactively surfaces criminal network structure, crime hotspots, early-warning spikes, and offender risk scores, so the platform's value isn't limited to "answering what's asked" — it's built to flag what an investigator didn't think to ask.

---

## 2. Tech Stack & Architecture

### Technologies Used

| Layer | Technology |
|---|---|
| **Backend** | Python 3.14, FastAPI, Uvicorn |
| **Frontend** | React 19 + Vite, `react-router-dom`, Recharts (charts), Leaflet + `leaflet.heat` (hotspot map), `react-force-graph-2d` (network graph) |
| **Database** | Zoho Catalyst Data Store, queried via ZCQL (Zoho's SQL-like query language) |
| **AI / LLM** | Multi-provider: **Zia** (Zoho Catalyst's hosted LLM + RAG/document retrieval), **Google Gemini** (primary for answer composition, any language), **Groq** (primary for fast intent classification) — automatic failover between all three via a per-provider circuit breaker |
| **Voice** | ElevenLabs (speech-to-text + text-to-speech) |
| **Auth** | JWT (`PyJWT`) + `bcrypt` password hashing, role-based access control |
| **Semantic memory** | `sentence-transformers` + `chromadb` (local, offline vector search for long-conversation recall) |
| **PDF export** | `fpdf2`, with bundled Noto Sans / Noto Sans Kannada fonts for Kannada-script reports |

### System Architecture

```
┌─────────────────────────────────────────────┐
│            React Frontend (Vite)            │
│  Chat · Cases · Network · Map · Analytics   │
│  Insights · Alerts · Social Insights        │
└───────────────────┬─────────────────────────┘
                    │ REST (JSON) + JWT
                    ▼
┌─────────────────────────────────────────────┐
│             FastAPI Backend                 │
│                                             │
│  ┌───────────────────────────────────────┐  │
│  │ Router Layer                          │  │
│  │ auth · chat · cases · network         │  │
│  │ analytics · scoring · insights        │  │
│  │ voice · translate · report · social   │  │
│  └───────────────┬───────────────────────┘  │
│                  ▼                          │
│  ┌───────────────────────────────────────┐  │
│  │ Chat Pipeline                         │  │
│  │ fast-path regex router                │  │
│  │ → intent classification               │  │
│  │ → entity extraction                   │  │
│  │ → RAG grounding / ZCQL aggregation    │  │
│  │ → answer composition                  │  │
│  │ → translation                         │  │
│  └───────┬─────────────────┬─────────────┘  │
│          ▼                 ▼                │
│  ┌───────────────┐   ┌──────────────────┐   │
│  │ Multi-provider│   │ Semantic Memory  │   │
│  │ LLM Failover  │   │ (Chroma, Local)  │   │
│  │ Groq → Gemini │   └──────────────────┘   │
│  │ → Zia         │                          │
│  └───────┬───────┘                          │
└──────────┼──────────────────────────────────┘
           ▼
┌─────────────────────────────────────────────┐
│      Zoho Catalyst Data Store (ZCQL)        │
│                                             │
│ • CaseMaster                                │
│ • Accused                                   │
│ • Victim                                    │
│ • Arrest                                    │
│ • Chargesheet                               │
│ • CriminalNetwork                           │
│ • AuditLog                                  │
│ • FinancialTransaction                      │
│ • RolePermission                            │
│ • ...                                       │
└─────────────────────────────────────────────┘
```
**Request flow for a chat message:**
`User types/speaks → React sends question → FastAPI /chat/message → regex fast-path (skip AI for obvious patterns) or LLM intent classification (Groq, any language auto-detected) → routed to case lookup / aggregate ZCQL query / legal knowledge base → answer composed (Gemini) with real data injected as grounding → translated back to the question's own language if needed → citations + audit log entry attached → JSON response → rendered in chat with a "via <provider>" transparency badge.`

Every LLM call in the system carries an explicit time budget and automatically fails over to the next provider in its chain if the primary is slow, rate-limited, or down — never a silent hang, never a raw 500.

---

## 3. Setup & Installation (How to Run)

### Prerequisites
- **Python 3.14+** (a `venv` is used — see below)
- **Node.js 18+** and npm (for the frontend)
- A **Zoho Catalyst** project with the Data Store schema provisioned (CaseMaster, Accused, Victim, AppUser, ChatHistory, AuditLog, RolePermission, CriminalNetwork, FinancialTransaction, etc.)
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

### Environment Variables

All configuration lives in `.env` at the project root (never commit this file — see `.env.example` for the full annotated template). Key variables:

| Variable | Purpose |
|---|---|
| `ZOHO_ORG_ID`, `ZOHO_PROJECT_ID` | Catalyst project identifiers |
| `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_REFRESH_TOKEN` | OAuth credentials for the Catalyst Data API |
| `ZOHO_RAG_ENDPOINT`, `ZOHO_DOCUMENT_IDS`, `ZIA_TRANSLATE_ENDPOINT` | Zia's hosted RAG/translation endpoints |
| `CATALYST_ENVIRONMENT` | `Development` or `Production` — which Catalyst environment to query |
| `JWT_SECRET_KEY`, `JWT_ALGORITHM`, `JWT_EXPIRE_MINUTES` | App-level auth token signing |
| `GROQ_API_KEY`, `GROQ_MODEL` | Fast classification provider (free tier at console.groq.com) |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Primary answer-composition provider (free tier at aistudio.google.com) |
| `ELEVENLABS_API_KEY` | Voice transcription + speech synthesis |
| `APP_USER_TABLE`, `AUDIT_LOG_TABLE`, `CHAT_HISTORY_TABLE` | Configurable Catalyst table names |

Every AI provider key is optional independently — the app degrades gracefully (falling back toward Zia-only behavior) if `GROQ_API_KEY`/`GEMINI_API_KEY` are left unset, rather than failing to start.

---

## 4. Features & Usage

### Key Features

1. **Natural-language chatbot, any language** — not just English + Kannada as originally scoped: the assistant auto-detects the language of *every* message (verified working across English, Hindi, Kannada, Spanish, and French) and replies in that same language, without the user ever needing to say "answer in X."
2. **Context-aware, multi-turn conversation** — follow-up questions ("How old is the first one?") are resolved against real conversation history, including a semantic-memory layer that can recall a fact from many turns earlier in a long session.
3. **Criminal network visualization** — an interactive force-directed graph of gang membership and cross-case connections, with organized-crime-group detection (Organized / Loosely Organized / Fragmented) computed from real connection density.
4. **Explainable, audited AI** — every answer carries a citation (which case record or document grounded it) and discloses which AI provider actually produced it; every query, classification decision, and provider failover is written to an audit trail.
5. **Role-based secure access** — five ranks (DGP/IGP/SP/Inspector/Admin) with real, enforced permission gates (network visibility, export rights), matching Karnataka Police's actual rank hierarchy.

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

A typed question can also be recorded as voice (mic button in the chat input), and any assistant reply can be played back as speech via the speaker icon on its bubble. A full conversation can be exported as a PDF from the chat sidebar.

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
| `POST` | `/chat/feedback` | Thumbs up/down on an answer |
| `GET` | `/chat/sessions` | List a user's chat sessions |
| `GET` | `/chat/sessions/{session_id}/messages` | Full transcript of one session |
| `DELETE` | `/chat/sessions/{session_id}` | Delete a session |

### Cases
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/cases/search` | Filterable case search (crime type, district, date range, status...) |
| `GET` | `/cases/{crime_no}` | Full case detail — victims, accused, arrests, chargesheets |
| `GET` | `/cases/{crime_no}/timeline` | Chronological investigation timeline |
| `GET` | `/cases/accused/history` | An accused person's case history |

### Network
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/network/organized-groups` | All gangs, classified by organization level |
| `GET` | `/network/gang/{gang_name}` | Full node/edge graph for one gang |
| `GET` | `/network/profile/{accused_id}` | Resolve a clicked node to a real person's profile |

### Analytics / Scoring / Insights
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/analytics/hotspots` | GPS points for the crime heatmap |
| `GET` | `/analytics/trends`, `/analytics/seasonal` | Monthly / seasonal crime trend series |
| `GET` | `/analytics/forecast` | 6-month crime-count projection |
| `GET` | `/scoring/risk-score?name=X` | Offender risk score with factor breakdown |
| `GET` | `/scoring/early-warnings` | Crime types currently spiking vs. their own baseline |
| `GET` | `/insights/mo-analysis/{crime_no}` | Crime-pattern / possible-series detection |
| `GET` | `/insights/behavioral-analysis?name=X` | AI-generated behavioral pattern summary |

### Voice, Translate, Report
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/voice/transcribe` | Audio file → `{text, detected_language}` |
| `POST` | `/voice/speak` | `{text, language_code}` → MP3 audio |
| `POST` | `/translate/` | `{text, source_lang, target_lang}` → `{translated_text}` |
| `GET` | `/report/conversation/{session_id}` | Export a chat session as PDF |

All error responses share one shape: `{"error": true, "message": "...", "path": "..."}`.

---
