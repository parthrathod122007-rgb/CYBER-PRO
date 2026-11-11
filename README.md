# CYBERPRO — Zero-Trust Cybersecurity Defense Academy, Student LMS & AI CRM

A full-stack cybersecurity education platform, interactive Three.js web experience, role-gated Student Learning Management System (LMS), Administrative Portal & Lead CRM, and AI Assistant built with Python 3.10+ (`http.server` + SQLite) and Vanilla JavaScript/HTML/CSS.

---

## 1. Platform Highlights

- **Futuristic Public Website (`public/`)**:
  - Home (`/`), About (`/about`), Programs (`/programs`), Flagship 27-Module CCCE Course (`/ccce`), Contact & Admissions (`/contact`), Skill Passport (`/passport`), and legal/policy pages (`/privacy-policy/`, `/terms-conditions/`, `/cancellation-refund/`, `/shipping-policy/`, `/guidelines/`).
  - Interactive Three.js 3D Planetary Earth Globe (`#view-globe`), Holographic Radar Scanner (`#view-radar`), cursor-tracked 30-frame Cyber Guardian (`public/assets/head_frames/`), WebGL particle/shader universe (`cyber-webgl-universe.js`), liquid-glass 3D splash/header, and VisionOS-style translucent obsidian & neon-green (`#39ff14`) UI.
- **Zero-Trust Auth & Cloudflare Turnstile (`server.py`)**:
  - Per-user salted `hashlib.scrypt` password hashing, `HttpOnly; SameSite=Lax` session cookies backed by SQLite `sessions`, brute-force lockout protection, server-side Cloudflare Turnstile validation (`/api/turnstile-config`, `/api/login`, `/api/register`, `/api/forgot-password`), and strict `public/` vs `private/` isolation.
- **Authenticated Student LMS (`/lms` — `private/lms.html`)**:
  - Live classes & attendance checks, recorded lecture player, class schedule, study material library, tools showcase, CyberPro AI study mentor workspace, interactive labs/quizzes, XP leaderboard, Refer & Earn dashboard (`referral_service.py`), Cloud Cyber Range, Skill Passport, Bug Bounty Arena, SOC Simulator, and Career Portal (`ecosystem_service.py`).
- **Admin Portal & Lead Intelligence CRM (`/admin`, `/admin/crm` — `private/admin.html`, `private/admin-crm.html`)**:
  - Complete academy management (`admin_service.py`, `admin_handlers.py`): student enrollments, EMI/payments, live/recorded lecture publishing, attendance & security logs, quiz/certificate governance, database snapshots, CSV exports, and PPTX/HTML report generation (`report_service.py`).
- **CYBERPRO AI Assistant (`chatbot_service.py`, `universal_ai_engine.py`, `public/assets/cyber-chatbot.js`)**:
  - Built-in local knowledge & universal Q&A engine with optional external Google Gemini (`GEMINI_API_KEY`) or OpenAI (`OPENAI_API_KEY`) acceleration, strict ethical cybersecurity guardrails, progressive multi-turn lead discovery, and CRM enquiry syncing.

---

## 2. Repository Structure

```text
CYBERPRO/
├── api/
│   └── index.py                  # Vercel Python Serverless Function entrypoint
├── runtime_paths.py              # Local vs serverless (/tmp) writable path resolver
├── vercel.json                   # Vercel CDN + Python rewrite & bundle configuration
├── server.py                     # HTTP server, zero-trust auth, API & route dispatcher
├── admin_handlers.py             # Authenticated admin GET/POST API route handlers
├── admin_service.py              # Admin CRM, LMS management, analytics & backup logic
├── chatbot_service.py            # 4-Mode AI assistant, guardrails & lead capture
├── ecosystem_service.py          # Cyber Range, Skill Passport, Bug Bounty, SOC & Careers
├── referral_service.py           # Refer & Earn codes, tiers & reward workflows
├── report_service.py             # Printable HTML/PDF & PPTX executive report builder
├── targets_service.py            # Target query / helper service
├── universal_ai_engine.py        # Built-in offline/multi-topic AI inference engine
├── manage_users.py               # CLI tool for creating & managing user accounts
│
├── public/                       # Static public frontend assets & pages (Vercel CDN)
│   ├── index.html                # Main academy landing page & Student LMS modal
│   ├── staff-login.html          # Dedicated Admin & Instructor login gateway
│   ├── about.html / about/       # Academy overview & mission
│   ├── programs.html / programs/ # Training tracks
│   ├── ccce.html / ccce/         # CCCE flagship 27-module curriculum explorer
│   ├── contact.html / contact/   # Admissions desk & inquiry channels
│   ├── passport.html             # Public Skill Passport viewer
│   ├── site-pages.css            # Shared subpage & policy styles
│   ├── cyberpro-signature.js     # Brand UI animations & polish
│   ├── legal-page.js             # Policy navigation & interactions
│   ├── shipping-policy/          # Shipping Policy (contains Admin & Instructor Login link below founder badge)
│   ├── privacy-policy/           # Additional legal pages (terms, cancellation-refund, guidelines)
│   └── assets/                   # JS, CSS, Three.js modules, shaders, videos, logos, head_frames/
│
├── private/                      # Auth-gated HTML portals (never served statically)
│   ├── lms.html                  # Student LMS application
│   ├── admin.html                # Full Academy Admin Dashboard
│   └── admin-crm.html            # Standalone Admin CRM Lead Console
│
├── data/                         # Seed schemas, config & public knowledge base
│   ├── schema.sql                # Clean 53-table schema + non-PII curriculum catalog seeds
│   ├── chatbot_config.json       # Default chatbot runtime config (no API keys stored)
│   ├── cyberpro_knowledge.json   # Official curriculum & academy knowledge base
│   ├── countries.geo.json        # GeoJSON boundaries used by the 3D globe
│   └── reports/                  # Presentation deck assets (.pptx)
│
├── scripts/                      # Database migration & 3D asset build helpers
│   ├── apply_globe_india_upgrade.py
│   ├── build_cyber_globe_assets.py
│   ├── migrate_ecosystem_modules.py
│   ├── migrate_referral_system.py
│   └── sync_master_package.py
│
├── tests/                        # Automated unittest & smoke test suites
│   ├── test_chatbot.py           # AI assistant config, guardrails, leads & admin ACL tests
│   ├── test_crm.py               # CRM enquiries, multi-turn discovery, dossier & CSV tests
│   ├── test_turnstile.py         # Cloudflare Turnstile verification, replay & role tests
│   ├── test_api.py               # Standalone API security script
│   └── test_e2e_browser.py       # Optional Playwright end-to-end browser verification
│
├── uploads/                      # Runtime upload destination (ignored via .gitignore)
├── .env.example                  # Environment variable template (safe placeholders only)
├── .gitignore                    # Excludes secrets, .env, .db runtime files, caches & uploads
├── requirements.txt              # Python package dependencies
├── package.json                  # Project metadata & script runner shortcuts
├── Dockerfile                    # Production container definition
├── docker-compose.yml            # Containerized local deployment
├── Procfile                      # Process entry point (web: python server.py)
├── start.bat                     # Windows quick-start launcher
└── PROJECT-MIGRATION-GUIDE.md    # Technical architecture & developer handover guide
```

---

## 3. Prerequisites & Installation

1. **Install Python 3.10+** (`python --version`).
2. **Clone or open the `CYBERPRO/` folder**:
   ```bash
   cd CYBERPRO
   ```
3. **Create and activate a virtual environment (recommended)**:
   - Windows (PowerShell):
     ```powershell
     python -m venv .venv
     .\.venv\Scripts\Activate.ps1
     ```
   - macOS / Linux:
     ```bash
     python3 -m venv .venv
     source .venv/bin/activate
     ```
4. **Install Python dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
5. **Configure environment variables**:
   - Windows (PowerShell):
     ```powershell
     Copy-Item .env.example .env
     ```
   - macOS / Linux:
     ```bash
     cp .env.example .env
     ```
   Edit `.env` to add your Cloudflare Turnstile keys and optional `GEMINI_API_KEY` / `OPENAI_API_KEY`. The defaults in `.env.example` work immediately for local development (Turnstile sandbox test keys + built-in local AI engine).

---

## 4. Database Initialization & User Setup

When you start `server.py` for the first time, it automatically creates `data/cyberpro.db` and loads all tables and public curriculum catalog data from `data/schema.sql`. There are **no default hardcoded passwords** in the repository.

Create your first administrator or student account using `manage_users.py`:

```bash
# Create an administrator account (prompts securely for password, minimum 8 chars)
python manage_users.py add admin@example.com --name "Academy Admin" --role admin

# Create a student account
python manage_users.py add student@example.com --name "Demo Student" --role student

# List, disable, enable, reset password, or delete users
python manage_users.py list
python manage_users.py passwd student@example.com
```

Students can also sign up directly via the registration tab inside the portal login modal (`/?login=1`).

---

## 5. Running the Application

Start the server locally:

```bash
python server.py
```

Or on Windows double-click `start.bat`. By default the application listens at:

- **Public Website**: `http://127.0.0.1:8000/`
- **Student LMS** (requires student/admin login): `http://127.0.0.1:8000/lms`
- **Admin Dashboard** (requires admin/superuser login): `http://127.0.0.1:8000/admin`
- **Admin CRM Console** (requires admin/superuser login): `http://127.0.0.1:8000/admin/crm`
- **Staff Portal (Admin & Instructor Login)**: `http://127.0.0.1:8000/staff-login` (also linked exclusively beneath `Founder / Owner: Parth Rathod` on `/shipping-policy/`)

### Deploying to GitHub & Vercel (Public Live URL)

1. **Upload the `CYBERPRO/` folder to GitHub**:
   - **Option A — Git CLI**:
     ```bash
     cd CYBERPRO
     git init
     git branch -M main
     git add .
     git commit -m "Deploy-ready CYBERPRO website, Student LMS & Vercel backend"
     git remote add origin https://github.com/<your-username>/<your-repo-name>.git
     git push -u origin main
     ```
   - **Option B — GitHub Desktop**: Choose **File → Add Local Repository**, select the `CYBERPRO` folder, publish/push to GitHub.

2. **Import & Deploy in Vercel**:
   1. Open [https://vercel.com/new](https://vercel.com/new) and click **Import Git Repository**.
   2. Paste or select your GitHub repository URL (`https://github.com/<your-username>/<your-repo-name>`).
   3. Keep **Framework Preset** as `Other` (`vercel.json` sets `"framework": null`, `"outputDirectory": "public"`, and maps `/api/*`, `/lms`, `/admin`, `/healthz`, `/uploads/*` to `api/index.py`).
   4. *(Optional)* Expand **Environment Variables** and add:
      - `CLOUDFLARE_TURNSTILE_SITE_KEY` & `CLOUDFLARE_TURNSTILE_SECRET_KEY` (your live domain Turnstile keys; leave unset or use defaults for sandbox testing)
      - `CYBERPRO_ADMIN_EMAIL` & `CYBERPRO_ADMIN_PASSWORD` (auto-creates an administrator account on cold start)
      - `CYBERPRO_INSTRUCTOR_EMAIL` & `CYBERPRO_INSTRUCTOR_PASSWORD` (optional instructor login)
      - `GEMINI_API_KEY` or `OPENAI_API_KEY` (optional external AI; works offline out of the box)
   5. Click **Deploy** — Vercel gives you a public live `https://<project>.vercel.app` URL.

> **Database Note for Vercel**: On Vercel serverless containers, SQLite automatically runs in writable `/tmp/cyberpro/cyberpro.db` (seeded from `data/schema.sql`), so public pages, the 4-mode AI assistant, student registration, Cyber Range, Bug Bounty Arena, and SOC Simulator work out of the box. Because Vercel serverless containers recycle `/tmp` between cold starts, use Docker (`docker compose up --build`) or a persistent VPS/Cloud SQLite volume if you require permanent database storage across container restarts.

### Running with Docker

```bash
docker compose up --build
```

---

## 6. Running Automated Tests

All unit test suites run cleanly against isolated temporary databases and ephemeral local servers—no pre-running server or database edits required:

```bash
# Run all unittest suites (Chatbot, CRM, Turnstile)
python -m unittest discover -s tests -p "test_*.py" -v

# Run individual suites
python -m unittest tests/test_chatbot.py -v
python -m unittest tests/test_crm.py -v
python -m unittest tests/test_turnstile.py -v
```

*(Note: `tests/test_e2e_browser.py` additionally requires `playwright` and Chromium installed: `pip install playwright && playwright install chromium`.)*

---

## 7. Security Before Pushing to GitHub

- `.gitignore` automatically excludes `.env`, `*.db` / `*.db-wal` / `*.db-shm`, `data/backups/`, `data/*.log`, uploaded media in `uploads/`, `__pycache__/`, virtual environments (`.venv/`, `venv/`), and `node_modules/`.
- Always keep real Turnstile secret keys, LLM API keys (`GEMINI_API_KEY`, `OPENAI_API_KEY`), SMTP credentials, and production passwords solely in `.env` or environment secrets—never commit them.
