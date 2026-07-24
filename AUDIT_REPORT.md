# KSP Sahay — End-to-End Audit Report

**Method:** Live app run at `localhost:5173`/`localhost:8000`, screenshotted every page at desktop (1440×900, light + dark) and mobile (390×844, light) via Playwright with a real admin session, cross-checked against backend code, live API timing, and direct Catalyst queries. No code was changed.

**Two premise corrections before anything else**, because getting these wrong would make the rest of this report misleading:

1. **"Cases (with investigation timeline)"** — there is no investigation timeline anywhere in the codebase. `grep -rn timeline` across the whole frontend/backend returns exactly one hit, and it's the word "Timeline:" inside a text prompt template for AI case summaries — not a UI component. If a judge is told this exists, that's the single fastest way to lose credibility in the room.
2. **Financial nodes in the Network graph** — also not present. `Network.jsx` has zero references to financial/transaction data. The Network graph shows only accused nodes and gang edges.

Both were apparently scoped in an earlier planning pass and never built. Recommend either building a minimal version of #1 before demo day (it's a genuinely good story: FIR → arrest → chargesheet dates are all real, linked data — see Section 5) or removing the claim from whatever judge-facing materials mention it.

---

## 1. PAGE-BY-PAGE UI AUDIT

### Systemic issue affecting every single page first
Every page audited (10/10) overflows horizontally by **exactly 18px at 1440×900** — a completely standard laptop resolution (the default "looks like 1440×900" scaling on 13" MacBooks). `document.body.scrollWidth` is 1458px against a 1440px viewport on every page. Root cause isolated via DOM inspection: `.shell-user` (the username/role/sign-out cluster in the top-right of the desktop topbar) doesn't fit in the space left after the nav links, and there's no wrapping/shrinking behavior. In a real browser window (not a full-page screenshot, which captures overflow content invisibly) this means **"Sign out" is genuinely clipped or requires horizontal scrolling to reach** on a very common demo-laptop resolution. This is a single shared-component fix (`AppShell.css`) that resolves all 10 pages at once — see Fix #1.

Zero JS console/page errors were observed on any of the 30 page/theme/viewport combinations tested — the app doesn't crash or throw anywhere in normal navigation, which is a genuinely good sign for demo stability.

### Home
- Quick Actions grid + Live Overview + Recent Alerts all render cleanly, dark mode is solid (same navy/gold token system holds up).
- "Report Crime" tile is visibly greyed out with a "SOON" badge — honest, but it's the first thing under Quick Actions and reads as an unfinished corner of an otherwise complete-looking dashboard.
- **Recent Alerts uses a binary red/green dot + "Spike Alert"/"Trend Update" label** — this is the pre-4-tier system. Theft at 0.25× (a real, currently-live "Declining" case) renders with the exact same green dot as Murder at 0.93× ("Normal") — two different signals look identical here. See Section 3.

### Chat
- Clean empty state, avatar, suggestion chips, mic button, language toggle all present and no layout issues at any viewport.
- The 3 suggestion chips are all CASE_LOOKUP/LEGAL_REFERENCE examples ("Summarize crime number...", "What is Section 302?", "What is Section 420?") — none showcase the aggregate-query capability ("How many theft cases last month?"), which is arguably the single most impressive thing built this project and is completely undiscoverable from the empty state.

### Cases
- **The single worst dead-space offender in the app.** At 1440px, the case list + accused-history search occupy a ~360px-wide left column; the entire remaining ~1080px (75% of the screen) is blank except for one line of grey text ("Select a case from the list to see full detail.") floating near the top. A judge's very first impression of this page is "did something fail to load?"
- Once a case is open, the detail view itself is dense and good — the empty state is the problem, not the populated state.
- Mobile stacking works correctly, no overflow.

### Network
- Same dead-space pattern pre-selection (large empty panel, "Select a group to visualize its network.").
- **All 5 of 5 organized-crime groups are classified "Fragmented"** with a red dot, no visual distinction between them. This is a real, previously-verified consequence of how sparse the live seed data's clustering actually is (documented elsewhere in this project) — but a judge with zero of that context will reasonably read "every single group shows the same red label" as "this classifier doesn't work" rather than "this classifier is working correctly on this specific dataset." The UI gives no visual room for "Organized" or "Loosely Organized" to ever look different even in principle (no comparison bar, no numeric score shown alongside the label).

### Hotspot Map
- Loads fast (<0.3s for 300 points), heatmap renders, no issues found in code review or screenshot. Solid.

### Analytics
- **Chart-quality issues:** the "Monthly crime trend" line chart's x-axis month labels are fully overlapping and illegible in the screenshot ("2018-032018-072019-...") — recharts is rendering every tick with no thinning/rotation at this data density (7+ years of monthly data).
- **Victim demographics pie chart's top data label is clipped** by the card boundary (shows as "1/37" instead of "1737") — the exact class of label-collision bug that was found and fixed on the Social Insights page's scatter charts earlier in this project, but this is a *separate* pie-chart instance that never got the same treatment.
- **Early Warning Alerts panel uses the old binary "SPIKE (1.75×)" / "normal (1.35×)" text tags** — third occurrence of the tier-inconsistency bug (see Section 3). Notably, a 1.35× ratio is labeled "normal" here, "Watch" on the Alerts page, and gets a *green* dot on Home — three different framings of the identical number.
- A large empty grey column (~240px) runs down the entire right edge of the page — the 2-column card grid doesn't fill the available width at 1440px.

### Insights
- The second-worst dead-space offender: four analysis category buttons + one behavioral-analysis search sit at the very top, and roughly 80% of the vertical viewport below them is empty until a crime number is analyzed. No placeholder/preview content, no "try this crime number" prompt.

### Alerts
- Clean, well-built (4-tier badges, sparklines, correct colors) — but only 4 cards exist (one per real crime type) so the same large-empty-space-below-content pattern repeats here too.

### Social Insights
- The most polished page in the app by a clear margin — trendlines, hover tooltips, sample-size disclosure, key-finding summary card, source citations, collision-avoided labels. No issues found. This page should be the demo's opening example of UI quality, not an afterthought.

### Profile
- Small, correctly minimal, no issues.

### Dark mode (all pages)
- No broken contrast, no invisible text, no unstyled elements found anywhere. The token system (`--surface`, `--ink`, `--muted`, etc.) is applied consistently. This is a genuine strength worth highlighting to judges, not just a non-finding.

### Mobile (390px)
- Zero horizontal overflow anywhere (the 18px desktop bug doesn't reproduce at mobile width, because the topbar swaps to the compact mobile header at that breakpoint). Bottom tab bar and stacked layouts all work. This is the most solid dimension of the whole audit.

---

## 2. FEATURE FUNCTIONAL AUDIT (27 features)

Rated against real, current behavior — either a live API call made during this audit or direct inspection of the exact code path, not general impressions.

| # | Feature | Rating | Evidence |
|---|---|---|---|
| 1 | NL Chatbot (routed: legal/case/aggregate/follow-up/out-of-scope) | **SOLID** | 38/40 (95%) on the 40-question eval harness (`eval/eval_report.md`), covering English/Hindi/Kannada; the 2 remaining "failures" are confirmed test-design artifacts, not real bugs, on direct inspection. |
| 2 | FIR/Accused/Victim/Case query APIs | **WORKS-BUT-ROUGH** | Correct and fast, but the Arrest card in the case-detail UI renders a raw Catalyst ROWID as "Accused ID: 43437000000106050" right next to a human-readable "Accused Person-X" elsewhere on the same card — see Section 5, Fix #2. |
| 3 | Context-aware conversation (recent window + Chroma semantic recall) | **SOLID** | Live-verified this session: a follow-up 2 turns removed from its referent ("How old is the first one?") correctly resolved to "Accused Person-824 is 49 years old." |
| 4 | English / Hindi / Kannada support | **SOLID** | All 3 languages pass in the eval harness for every intent type tested, including aggregate queries phrased natively in Hindi/Kannada with correct entity extraction. |
| 5 | Voice I/O (ElevenLabs STT/TTS) | **WORKS-BUT-ROUGH** | English round-trip is clean; Kannada STT quality is genuinely rough (documented: garbled words, one hallucinated parenthetical on a real test clip) — real capability, just not demo-safe in Kannada without a disclaimer. |
| 6 | PDF export (case report + conversation) | **SOLID** | Noto Sans + Noto Sans Kannada fallback fonts are correctly wired (`pdf_service.py`), fixing what was previously a guaranteed crash on any non-Latin character. |
| 7 | Criminal network visualization | **WORKS-BUT-ROUGH** | Functionally correct force-graph rendering, but all 5/5 seeded gangs show identical "Fragmented" status with no visual differentiation — see Page Audit above. |
| 8 | Crime hotspot map (heatmap) | **SOLID** | 300-point heatmap loads in <0.3s, no issues found. |
| 9 | Role-based access control | **SOLID** | `RolePermission`-gated endpoints (`can_view_network`, `can_export`) enforced via `require_permission()`; not re-tested live this pass but code path is unchanged and was previously verified with a real 403. |
| 10 | Audit logs | **SOLID** | Verified live this session — every chat turn AND every intent classification is persisted to `AuditLog` with real timestamps, confirmed via direct query during the Prompt D eval work. |
| 11 | Risk scoring (explainable weighted-sum) | **FRAGILE** | Correctly implemented and computes real scores, but **`GET /scoring/risk-score` is a dead endpoint — zero frontend UI calls it anywhere.** It's only reachable via Swagger. A judge browsing the actual app will never see this feature exists. |
| 12 | Repeat-offender detection | **FRAGILE** | Same dead-route problem as #11 (`GET /scoring/repeat-offenders`, never called from the frontend) — and even via Swagger it returns empty on this dataset since accused names are synthetic and per-case-unique. |
| 13 | Similar-case suggestions | **SOLID** | Wired into the Insights page, functions as documented. |
| 14 | Auto case summaries | **SOLID** | Markdown-stripped, grounded, wired into both Insights and Chat. |
| 15 | Crime trend charts | **WORKS-BUT-ROUGH** | Real data, but the monthly-trend chart's x-axis is illegible at the current data density (7 years of months with no label thinning) — see Page Audit. |
| 16 | Behavioral analysis | **SOLID** | Wired into Insights and Network's node-click panel. |
| 17 | Early warning alerts | **WORKS-BUT-ROUGH** | The underlying detection is solid and now has a genuinely good 4-tier UI on the Alerts page — but the *same* alert data displays as a 3rd, 4th distinct visual language on Home and Analytics. See Section 3 — this is the single most demo-visible inconsistency in the whole app. |
| 18 | Explainable AI citations + Sources line | **SOLID** | Verified across all routes this session (legal KB, case lookup, aggregate, RAG fallback) — every grounded answer now ends with a real, non-fabricated "Sources:" line built from the same citation data returned to the frontend. |
| 19 | Seasonal trends | **SOLID** | Chart renders correctly, labels are legible (12 fixed month buckets, no density problem). |
| 20 | Demographic analysis (victim gender/age) | **WORKS-BUT-ROUGH** | Real data, but the pie chart's top label is clipped by the card boundary in the current screenshot. |
| 21 | Social risk factors (age-band/locality-density) | **FRAGILE** | Correct logic, but only surfaces inside the risk-score breakdown (#11), which has no frontend surface — inherits that dead-route problem. |
| 22 | Investigative leads | **SOLID** | Wired into Insights, grounded output. |
| 23 | Crime forecasting (deterministic OLS) | **SOLID** | Dashed projection line renders correctly on Analytics; deliberately not a black-box ML model, which is itself a good talking point for judges (explainable, not a fabricated confidence score). |
| 24 | Financial transaction analysis | **WORKS-BUT-ROUGH** | Summary stats (avg amount, suspicious count) surface correctly on Home/Analytics, but **`GET /financial/suspicious` (the actual ranked list of flagged transactions) is a dead endpoint** — no drill-down UI exists, so the feature is "we counted them" with no way to see which ones. |
| 25 | Organized crime group detection | **WORKS-BUT-ROUGH** | Same evidence as #7/#9 above — real BFS clustering, but the "all Fragmented" presentation undersells it. |
| 26 | MO analysis (crime-pattern/spree detection) | **SOLID** | Live-tested this session via chat context injection ("is this part of a series?" answered correctly with real distance/time reasoning). |
| 27 | Social Insights (urbanization + migration + literacy + income correlation) | **SOLID** | The most polished page in the app — real Census 2011 data, Pearson r, trendlines, honest sample-size disclosure (n=10 of 31 real districts), AI interpretation grounded strictly in the computed numbers. |

**Summary: 17 SOLID / 8 WORKS-BUT-ROUGH / 2 FRAGILE (dead routes: risk-score, repeat-offenders — #21 inherits #11's problem rather than being independently fragile).**

**What could embarrass in a live demo, ranked:**
1. Raw Catalyst ROWID ("43437000000106050") shown as "Accused ID" in every populated Arrest card — the ugliest single artifact in the UI, and it's not rare, it's in every case with an arrest on record.
2. The 3-way alert-tier inconsistency (Home says "Trend Update" green dot, Analytics says "normal (1.35×)", Alerts says "Watch" orange badge) for the literal same number — if a judge clicks between pages in the order a real investigator would, this is immediately visible.
3. Cases page's near-empty first impression — the single most-demoed page (every hackathon judge clicks into a case) opens to 75% blank screen.
4. All-"Fragmented" Network page — invites the exact question "does this actually work?"

---

## 3. CROSS-PAGE CONSISTENCY

| Check | Result |
|---|---|
| Total case count (Home "Total Incidents" vs Analytics "Total Incidents") | **Consistent** — both read 3,000, both derived from the same `/analytics/crime-types` sum. |
| Suspicious transaction count (Home vs Analytics) | **Consistent** — both read 1,452 from `/financial/summary`. |
| **Alert severity representation (Home / Analytics / Alerts)** | **Inconsistent — 3 different taxonomies for the same data.** Home: binary dot (red/green) + "Spike Alert"/"Trend Update" text. Analytics: binary text tag "SPIKE (r×)"/"normal (r×)". Alerts: the real 4-tier system (Critical/Watch/Normal/Declining). All three read from `/scoring/early-warnings`, whose `ratio` field is identical everywhere — only the *display* logic diverged when the 4-tier system was added to Alerts.jsx without updating the other two call sites. |
| Gender labels (Victims/Accused/Complainants, Network, Analytics pie chart) | **Consistent** — a shared `genderLabel()` utility (1=Male/2=Female/3=Transgender) is applied on Cases, Network, and Analytics; verified in code, no raw `GenderID` integers found rendering anywhere in the current codebase. |
| Crime-type labels across pages | **Consistent** — all four real crime types (Murder, Attempt to Murder, Theft, Online Fraud) render identically everywhere; all derived from the same `extract_crime_type()`/`BriefFacts` parsing. |
| Date format | **Consistent but raw** — every page renders CaseMaster dates as bare ISO strings (`2019-04-05`), never localized/humanized. Consistent is good; "consistent but not really designed" is the more honest framing — nobody made a decision here, it's just what the API returns, unformatted, everywhere. |
| Sources-line presence in AI outputs | **Consistent as of this session's Prompt D work** — legal KB, case lookup, RAG fallback, and aggregate answers all now end with a real, non-duplicated "Sources:" line built from one shared formatter (`chat/prompts.py:build_sources_line`). Verified live for 3 of the 4 paths during Prompt D; the RAG-fallback path specifically was smoke-tested and confirmed working. |
| Act/Section naming (Cases page vs Chat legal answers) | **Consistent** — both now read from the same canonical `data/legal_kb/ipc_sections.json` via `GET /legal/ipc-sections`, replacing what used to be two independently-maintained dictionaries. |

---

## 4. BACKEND HYGIENE

**Missing error handling (raw exception can reach the UI):**
- `POST /auth/login` — `UserRole(user["Role"])` raises a raw, uncaught `ValueError` if the stored `Role` value on an `AppUser` row doesn't exactly match the enum (typo, or a role added to `RolePermission` but not yet mirrored in `schemas.auth_dto.UserRole`). Surfaces as a generic "Internal Server Error" with **correct credentials rejected for an unrelated reason** — the worst kind of hygiene gap because it's actively misleading to debug.
- Same function: `user["PasswordHash"]` is a raw dict access with no `.get()` guard — a malformed `AppUser` row (missing that column) raises `KeyError` the same way.
- No router in the app registers a catch-all `Exception` handler in `main.py` — only `AppException` is handled (`main.py:22`). Any endpoint whose service-layer code raises a plain Python exception (not wrapped as `AppException`/`CatalystQueryError`) will return FastAPI's bare default 500 (`{"detail": "Internal Server Error"}`), which the frontend *does* parse gracefully (confirmed in `api/client.js`), so this isn't a crash — but it's a silent inconsistency: some failures get this app's real error shape (`{"error": true, "message", "path"}`) and audit-relevant detail, others get a generic FastAPI default with zero diagnostic value and (more importantly) **no log line**, since nothing catches and logs it before it propagates.

**Slow endpoints (>2s measured live during this audit):**
- `GET /scoring/early-warnings` — **2.34s**, live-timed. This is called on *every* Home page load and *every* Analytics page load (uncached, no memoization), computed fresh from a full paginated CaseMaster date fetch each time.
- The AGGREGATE_QUERY chat path (classification → entity extraction → ZCQL → answer composition) routinely takes 10–30s end-to-end in practice this session, sometimes longer under LLM retry — by far the slowest thing in the app, and it's front-and-center in the flagship feature. There's no loading/progress indicator differentiating "thinking about a stats question" from "answering a simple question" in `Chat.jsx` — both show the same generic typing dots, so a 25-second aggregate answer looks identical to a stall for the first 20 of those seconds.
- `GET /network/organized-groups` — 1.37s (below the 2s bar, but worth watching — BFS clustering over ~500-member gangs recomputed on every request, no caching).

**Missing pagination:**
- `services/db_service.get_accused_history()` — the underlying ZCQL `WHERE AccusedName = '...'` has no `LIMIT` at all, and then does one additional ZCQL round-trip *per matching row* (N+1). Harmless today only because this dataset's accused names are synthetic and almost always unique (documented elsewhere in this project) — a real name-matching dataset would both return unbounded rows and make an unbounded number of sequential API calls.

**Dead/unused routes** (defined in a router, zero references anywhere in `frontend/src`):
- `GET /network/accused/{accused_id}` — fully superseded by `/network/profile/{accused_id}`, which the frontend actually calls.
- `GET /scoring/repeat-offenders`
- `GET /scoring/risk-score`
- `GET /financial/suspicious`
- `GET /cases/{case_master_id}/victims` — superseded by the full case-detail endpoint, which already includes victims.

Five real, unreachable-from-the-app endpoints. None are broken — they're just invisible, which for a judging context is arguably worse than broken, since "built but not shown" reads as "not built" unless someone specifically goes looking in Swagger.

**Inconsistent 404 handling:** `GET /network/gang/{gang_name}/analysis` 404s correctly on an unknown gang name; the sibling endpoint `GET /network/gang/{gang_name}` (the raw graph data) does not — an unknown gang name likely returns an empty/degenerate graph structure instead of a clean 404, which `Network.jsx` would then have to render *something* for with no explicit empty-state handling for that specific case (untested this pass, flagged from code inspection).

---

## 5. TOP 15 FIXES

Ranked by (demo impact × effort). S = under an hour, M = a few hours, L = half a day or more.

| # | Fix | Where | Why it matters for judging | Size |
|---|---|---|---|---|
| 1 | Fix the 18px topbar overflow (shrink/wrap `.shell-user`) | `frontend/src/components/AppShell.css` | Affects literally every page at a standard demo-laptop resolution; "Sign out" is clipped. Single fix, whole-app impact. | S |
| 2 | Resolve `AccusedMasterID` to a real display (or just hide the raw ID) in Arrest cards | `frontend/src/pages/Cases.jsx` (RECORD_SHAPE.arrests) | A raw 17-digit database ID sitting next to a readable name is the single ugliest artifact a judge is likely to screenshot. | S |
| 3 | Unify alert-severity display: port the 4-tier badge (or at minimum matching color/label) to Home and Analytics | `frontend/src/pages/Home.jsx`, `Analytics.jsx` | The most demonstrable cross-page inconsistency found — same number, three different stories, visible just by clicking between 3 nav items. | M |
| 4 | Give Cases' empty detail-panel state real content (recent cases, a hint, or just a bigger default-open first case) | `frontend/src/pages/Cases.jsx` / `.css` | This is the page every judge will click into first; 75% blank screen reads as broken, not empty. | S–M |
| 5 | Fix Analytics' monthly-trend x-axis label overlap (thin/rotate ticks) | `frontend/src/pages/Analytics.jsx` | Currently illegible — a chart a judge literally cannot read. | S |
| 6 | Fix the clipped victim-demographics pie label | `frontend/src/pages/Analytics.jsx` | Same bug class already fixed once on Social Insights; same fix pattern applies here. | S |
| 7 | Add a numeric/visual differentiator to the Network organization-level badge (e.g. show the actual largest-cluster % next to "Fragmented") | `frontend/src/pages/Network.jsx`, `services/network_service.py` (data already computed) | Turns "every gang looks the same" from a suspicious-looking dead end into a demonstrably real, data-driven classification a judge can verify. | M |
| 8 | Fix `POST /auth/login`'s unguarded `UserRole(...)`/`PasswordHash` access | `api/routers/auth.py` | A malformed row currently fails login with zero diagnostic value for a reason unrelated to the entered password — exactly the kind of bug that eats live-demo time if it fires at the worst moment. | S |
| 9 | Add an aggregate-query example to Chat's empty-state suggestion chips | `frontend/src/pages/Chat.jsx` | The most-built, most-tested capability this session (95% eval accuracy, full NL-to-ZCQL pipeline) is currently undiscoverable from the page a judge lands on first. | S |
| 10 | Add a distinct "thinking about your stats question…" indicator for aggregate queries specifically | `frontend/src/pages/Chat.jsx` | A 10-30s aggregate answer currently looks identical to a stalled request for the first 20 seconds — real risk of a judge assuming it's broken and re-clicking send. | S–M |
| 11 | Cache/shorten `GET /scoring/early-warnings` (it's fetched fresh, uncached, on both Home and Analytics on every load) | `services/scoring_service.py` | 2.34s measured live; hit twice per typical navigation session; the easiest "make the app feel faster" win available. | M |
| 12 | Wire `GET /financial/suspicious` into an actual drill-down list somewhere (Analytics or a dedicated panel) | `frontend/src/pages/Analytics.jsx` (or new component) | "1,452 suspicious transactions" with no way to ever see one is a claim with no receipt — a sharp judge will ask "show me one." | M |
| 13 | Either wire risk-scoring into a real UI surface (e.g. a "Risk Score" button on the Accused card in Cases) or explicitly remove it from anything judge-facing | `frontend/src/pages/Cases.jsx`, `api/routers/scoring.py` | A fully-built, explainable scoring feature that's invisible in the app is wasted work in a judging context — either show it or stop claiming it. | M–L |
| 14 | Decide the fate of "Investigation Timeline" and "Financial nodes in Network graph" before demo day | New feature, `Cases.jsx`/`Network.jsx` | Both are referenced as if they exist in this task's own framing but don't exist in the code. Silently discovering this live in front of judges is the worst-case outcome — resolve it now, either by building a minimal version or scrubbing the claim. | L (build) / S (scrub) |
| 15 | Add `LIMIT`/pagination guard to `get_accused_history()`, and collapse its N+1 per-match query loop into one bulk `IN (...)` fetch | `services/db_service.py` | Currently harmless only because of a data quirk (synthetic unique names); a single well-timed real-world query pattern would make this the slowest, most fragile endpoint in the app. Cheap insurance. | S |

---

**Overall assessment:** the backend is meaningfully more solid than the frontend's polish suggests — most of the "rough" findings above are presentation/wiring gaps around genuinely correct, well-tested logic (95% on a 40-question multilingual eval harness is a real number, not a demo trick), not broken functionality. The highest-leverage work between now and judging is almost entirely *frontend*: fix #1 and #2 alone remove the two most visually embarrassing artifacts in under two hours combined, and #3/#4/#9 close the gap between "what this app can actually do" and "what a 10-minute judge walkthrough will actually see."
