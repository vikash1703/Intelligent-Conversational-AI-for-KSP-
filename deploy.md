# Deploying KSP Sahay to Zoho Catalyst

Two independent Catalyst resources, both in project **ksp-crime-ai** (Development environment):

| Piece | Catalyst product | Live URL |
|---|---|---|
| Backend (FastAPI) | AppSail `ksp-backend` | https://ksp-backend-50043202031.development.catalystappsail.in |
| Frontend (React/Vite) | Web Client Hosting `ksp-sahay` | https://ksp-crime-ai-60074316253.development.catalystserverless.in/app/index.html |

Prerequisites: `zcatalyst-cli` installed (already a devDependency — `npx catalyst` or the `catalyst` binary), logged in (`catalyst login`), project already linked in this directory (`.catalystrc` / `catalyst.json`). No further login/setup needed for a redeploy.

## Redeploying (the common case)

From the repo root:

```bash
# Backend only
catalyst deploy --only appsail:ksp-backend -ni

# Frontend only
catalyst deploy --only client -ni

# Both
catalyst deploy -ni
```

Each has a `predeploy` script (see `catalyst.json` / `app-config.json`) that runs automatically — you do **not** need to manually rebuild or re-vendor anything first:
- Backend's predeploy re-vendors Python dependencies into `./vendor_deps` (see below for why).
- Client's predeploy runs `npm run build` and copies `dist/index.html` → `dist/404.html` (SPA fallback, see below).

Both AppSail and the Client sit behind Catalyst's own edge/CDN — allow ~10–20s after a deploy completes before testing; the very first request can occasionally 503 while the container/edge warms up.

## Backend (AppSail) — key facts, don't relearn these the hard way

**Catalyst's "Catalyst-managed runtime" for Python does NOT run `pip install` for you.** This isn't documented anywhere on docs.catalyst.zoho.com (confirmed by reading the CLI's own source, `node_modules/zcatalyst-cli/lib/deploy/features/appsail/pack.js`) — it just zips whatever's in the source directory and uploads it. Every dependency has to be vendored by hand:

```bash
python3 -m pip install --upgrade --target ./vendor_deps \
  --platform manylinux2014_x86_64 --python-version 3.12 \
  --implementation cp --abi cp312 --only-binary=:all: \
  -r requirements-deploy.txt
```

This is wired up as `app-config.json`'s `scripts.predeploy`, so it happens automatically on every `catalyst deploy`. The startup `command` sets `PYTHONPATH=./vendor_deps` so Python finds them:

```
sh -c "PYTHONPATH=./vendor_deps python3 -m uvicorn main:app --host 0.0.0.0 --port $X_ZOHO_CATALYST_LISTEN_PORT"
```

**`requirements-deploy.txt` is a deliberate subset of `requirements.txt`** — it excludes `chromadb`/`sentence-transformers`, which pull in `torch` (400–800MB for the linux/cp312 target). That's too large to hand-vendor for this deploy path. `services/vector_memory_service.py` guards the import (`try/except ImportError` → `_AVAILABLE = False`) so the app boots fine without them; it just means **semantic long-conversation chat recall is disabled in this deployment**. Chat still works fully via `ChatHistory`'s recent-turn window. If you ever want semantic memory back in prod, this is a Docker/custom-runtime AppSail problem, not a managed-runtime one — don't just add the packages back to `requirements-deploy.txt` and expect it to work.

**`.catalystignore`** (repo root, same directory as `app-config.json`) excludes `venv/`, `node_modules/`, `frontend/`, `chroma_data/`, `__pycache__/`, `temp_reports/`, `tests/`, `eval/`, `scripts/`, `rag_documents/`, `data/cache/`, `.claude/`, `.env`, and some docs/tooling files from the upload. This is also undocumented (found by reading `pack.js`) — glob-per-line, minimatch syntax, matched against paths relative to the source dir.

**Secrets live in `app-config.json`'s `env_variables`, not `.env`.** `.env` is explicitly excluded from the upload — nothing in it ever reaches Catalyst. `core/config.py`'s `os.getenv(...)` calls read whatever Catalyst injects into the process environment at runtime, same as they'd read a real `.env` locally. If you rotate a credential, update it in **both** `.env` (local dev) and `app-config.json` (deploy), then redeploy.

**Valid `--stack` values** (from a CLI validation-error probe, not docs): `python_3_13/3_12/3_11/3_10/3_9`, `node24/22/20/18/16/14/12/10`, `java25/21/17/11/8`. Deployed on `python_3_12` — local dev venv is Python 3.14, which Catalyst doesn't offer.

**`memory` is set to 2048 MB** in `app-config.json` (default is 256, too small once FastAPI/pydantic/etc. are loaded).

## Frontend (Web Client Hosting) — key facts

**Served under `/app/`, not domain root.** `vite.config.js` sets `base: '/app/'` for production builds (dev server stays at `/`), and `main.jsx`'s `<BrowserRouter basename={import.meta.env.PROD ? '/app' : '/'}>` matches it. If you ever see broken asset URLs (404s for `/assets/...` instead of `/app/assets/...`) after a build change, check these two haven't regressed.

**Requires a `client-package.json` in the deployed source dir** (`frontend/dist/client-package.json`) — a Catalyst-specific marker file, *not* `package.json`. Must have `name` and `version` (semver) or the deploy 400s. It's generated by copying `frontend/public/client-package.json` — Vite copies everything in `public/` into `dist/` verbatim, so it just travels along with every build automatically.

**SPA deep-link fallback**: hitting a client-side route directly (e.g. `/app/login` typed in the address bar, or a page refresh mid-session) 404s at Catalyst's static host by default — it doesn't know to fall back to `index.html` for React Router to take over. Fixed the standard static-host way: the predeploy script copies `dist/index.html` → `dist/404.html` after every build. Catalyst serves that file (with a `404` status code, which browsers render fine on a normal navigation) for any unmatched path, and React Router then reads the real URL client-side. Don't rename/remove `404.html` without understanding this.

**`VITE_API_BASE`** (`frontend/.env.production`) points the built app at the live backend's `/api/v1`. If the backend URL ever changes, update this file and redeploy the client.

## CORS — the one that took the longest to find

Two real bugs, both fixed:

1. **Our own middleware ordering.** `CORSMiddleware` must be the *last* one added in `main.py` (making it outermost) — a `BaseHTTPMiddleware` subclass (`AuditLogMiddleware`) added after it wraps it instead of the other way around, which is a known Starlette interaction that can duplicate `Access-Control-*` response headers. Fixed by reordering (`AuditLogMiddleware` first, `CORSMiddleware` last) — correct regardless of the next point, don't undo it.

2. **Catalyst's own AppSail edge auto-trusts CORS for origins belonging to the same Catalyst project.** Empirically confirmed (not documented anywhere): a request from `ksp-crime-ai-60074316253.development.catalystserverless.in` (our own deployed Client) gets `Access-Control-Allow-Origin`/`Access-Control-Allow-Credentials` injected by Catalyst's edge *automatically*, independent of our app. When our own `CORSMiddleware` **also** added the identical header for that same origin (via `ALLOWED_ORIGINS`), the response carried the header twice (once title-case from our app, once lowercase from the edge) — a genuine CORS spec violation. `curl` doesn't care and shows both as fine; real browsers reject the response outright with a bare, unhelpful `TypeError: Failed to fetch` (no CORS-specific message logged). This is what "backend and frontend aren't talking" actually was.

   **Fix**: `ALLOWED_ORIGINS` in `app-config.json` is deliberately left **empty**. Don't add the Catalyst Client's own URL to it — the platform already handles that origin for you, and adding it back reintroduces the duplicate-header bug. `ALLOWED_ORIGINS` still exists in the code (`main.py`) for a genuinely *external* frontend origin (a custom domain not hosted on this same Catalyst project) if one is ever added — that case doesn't get the platform's automatic trust and does need the app-level allowlist.

If you ever see "Failed to fetch" again after a change here, check for duplicate `Access-Control-*` headers first (`curl -sD - -H "Origin: <frontend-url>" <backend-url>/` and look for the header appearing twice, in different casing) before assuming it's a connectivity problem — `curl` alone will not reveal this class of bug.

## API Gateway must stay disabled

Catalyst's API Gateway, if enabled on the project, intercepts **all** non-asset paths on the project's default domain and returns a generic `INVALID_URL`/"Invalid API" 404 for anything without a registered gateway route — including the web client's own `/app/index.html`. Nothing in this project uses API Gateway (the backend has its own separate AppSail subdomain). It's disabled via `catalyst.json`'s `apig.enabled: false`. Don't re-enable it unless you're also prepared to register explicit routes for the web client.

## Known live limitations (not deploy bugs, just current state)

- **No semantic long-conversation chat recall** — see the `requirements-deploy.txt` note above.
- **Real `/auth/login` doesn't work** — the `AppUser` Catalyst Data Store table still doesn't exist (a pre-existing gap unrelated to deployment). Use the Login page's "Developer option" to paste a locally-minted JWT (`core.security.create_access_token`) instead.
- **`chroma_data/` and `temp_reports/` are ephemeral** on the AppSail container — recreated on cold start, wiped on redeploy. Not persistent storage; nothing currently depends on them surviving a redeploy.
