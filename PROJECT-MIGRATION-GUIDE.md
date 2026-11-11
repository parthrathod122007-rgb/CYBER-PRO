# CYBERPRO — PROJECT MIGRATION GUIDE FOR EMERGENT AI

> **Target Platform:** Emergent AI (and other autonomous AI agent environments)  
> **Source Platform:** Google Antigravity 2.0  
> **Project:** CYBERPRO Defense Academy, Student LMS & AI Assistant CRM  
> **Author/Founder:** Parth Rathod (`parthrathod122007@gmail.com` / `+91 76668762325`)  
> **Status:** Production-Ready, 100% Functioning & Fully Verified  

---

## 1. CRITICAL DIRECTIVES FOR EMERGENT AI

When continuing development on Emergent AI, follow these strict principles:

1. **DO NOT REDESIGN OR SIMPLIFY:**
   Preserve the cybernetic obsidian glassmorphism aesthetic (`#070a08`), matrix green neon glowing accents (`#39ff14`), cyan laser highlights (`#00f5a0`), and dark futuristic typography.
2. **MAINTAIN ZERO-TRUST SEPARATION:**
   The Student LMS (`private/lms.html`) and the Admin CRM (`private/admin-crm.html`) are stored in `private/` and are **strictly authentication-gated**. Never move them to `public/` and never serve them without validating the session token via `user_for_token(self.token())`.
3. **DO NOT REPLACE THE BACKEND:**
   `server.py` is a zero-dependency, ultra-fast Python HTTP server engineered specifically for this platform. It handles session cookies, rate-limiting, Cloudflare Turnstile verification, CRM endpoints, and static asset streaming. Do not attempt to replace it with an unconfigured generic framework.
4. **RESPECT THE ROBOT & RADAR ASSETS:**
   The homepage features an interactive 3D robot head-turn animation driven by cursor tracking across 30 frame slices in `public/assets/head_frames/`, alongside a canvas radar scanner. Do not delete or disconnect these assets.

---

## 2. PROJECT REPOSITORY STRUCTURE

```
CYBERPRO-PROJECT/
├── server.py                   # Main HTTP Server, API Router & Session Guard
├── chatbot_service.py          # AI Chatbot Engine, Entity Discovery & CRM Sync
├── manage_users.py             # CLI tool to manage accounts in SQLite
├── requirements.txt            # Python dependencies (Standard Library + Optional AI)
├── package.json                # Project descriptor & npm script runner
├── Procfile                    # Deployment command: web: python server.py
├── Dockerfile                  # Production container configuration
├── docker-compose.yml          # Container composition definition
├── .env.example                # Safe environment variable template
├── README.md                   # Complete system manual
├── PROJECT-MIGRATION-GUIDE.md  # This document
│
├── data/
│   ├── cyberpro.db             # SQLite Database (active users, sessions, leads, CRM)
│   ├── schema.sql              # Clean SQL dump of all tables, indexes & seed data
│   ├── chatbot_config.json     # Chatbot multi-provider configuration
│   └── cyberpro_knowledge.json # Official CYBERPRO curriculum & academy facts
│
├── public/                     # Publicly Accessible Files (Served at root /)
│   ├── index.html              # Academy homepage with interactive robot & radar
│   ├── about.html              # About CYBERPRO, mission, founder Parth Rathod
│   ├── ccce.html               # Flagship CCCE Master Curriculum (27 Modules, 180+ Tools)
│   ├── programs.html           # Learning tracks and certification paths
│   ├── contact.html            # Contact & Admissions inquiries
│   └── assets/
│       ├── cyber-chatbot.js    # AI Assistant client controller & session tracking
│       ├── cyber-chatbot.css   # Chatbot obsidian glassmorphic styling
│       ├── motion.js           # Interactive animations and HUD telemetry
│       ├── site-auth.js        # Cloudflare Turnstile login modal controller
│       ├── cyberpro-guardian-video.mp4 # Video guardian asset
│       └── head_frames/        # 30 image slices for interactive robot head turning
│
├── private/                    # Protected Templates (Served only to authenticated users)
│   ├── lms.html                # Student Learning Management System (Access: /lms)
│   └── admin-crm.html          # Admin Lead Intelligence Dashboard (Access: /admin/crm)
│
└── tests/                      # Automated Verification Test Suites
    ├── test_crm.py             # CRM API, discovery, lead scoring & CSV export tests
    ├── test_chatbot.py         # AI Assistant knowledge base & ethical guardrails tests
    ├── test_turnstile.py       # Bot defense, Turnstile token verification & auth tests
    └── test_e2e_browser.py     # Browser user journey tests
```

---

## 3. COMPLETE ROUTE & API MAP

### Public Web Routes
- `GET /` → Serves `public/index.html` (Homepage)
- `GET /about` → Serves `public/about.html`
- `GET /ccce` → Serves `public/ccce.html`
- `GET /programs` → Serves `public/programs.html`
- `GET /contact` → Serves `public/contact.html`
- `GET /assets/*` → Serves static media, scripts, and stylesheets

### Protected Application Routes
- `GET /lms` → Served only if user has a valid session token; otherwise redirects to `/?login=1`. Serves `private/lms.html`.
- `GET /admin/crm` (or `/admin/leads`) → Served only if user has an active session with role `admin` or `superuser`; otherwise redirects to `/?login=1`. Serves `private/admin-crm.html`.

### Authentication APIs
- `GET /api/turnstile-config` → Returns `{ "enabled": bool, "sitekey": str }`.
- `POST /api/login` → Validates credentials (`email`, `password`, `cf-turnstile-response`). Sets secure `cp_session` cookie.
- `POST /api/register` → Registers new student with Cloudflare Turnstile verification.
- `POST /api/forgot-password` → Initiates password reset flow with Turnstile verification.
- `POST /api/logout` → Revokes session token server-side and clears cookie.
- `GET /api/me` → Returns logged-in user details (`{ "id", "email", "name", "role" }`).

### Chatbot & CRM APIs
- `POST /api/chat` → Processes visitor message with dynamic progressive discovery. Returns `{ "ok": true, "reply": str, "lead_captured": bool }`. Automatically extracts lead data and syncs to `customer_enquiries`.
- `POST /api/chat/lead` → Direct lead capture endpoint.
- `GET /api/chat/config` → Returns public chatbot configuration (API keys are never exposed).
- `GET /api/admin/crm/enquiries` → Returns list of customer inquiries with stats (`{ "enquiries": [...], "stats": {...} }`).
- `GET /api/admin/crm/enquiry/<id>` → Returns individual customer dossier and complete message transcript JSON.
- `POST /api/admin/crm/enquiry/<id>/update` → Updates lead status and counselor admin notes.
- `GET /api/admin/crm/export-csv` → Downloads `cyberpro_customer_enquiries.csv`.

---

## 4. AUTHENTICATION & TURNSTILE BOT DEFENSE

1. **How It Works**:
   - Every login, registration, and password recovery form requires a Cloudflare Turnstile token (`cf-turnstile-response`).
   - The server validates this token against `https://challenges.cloudflare.com/turnstile/v0/siteverify`.
2. **Local / Development Testing**:
   - In development mode (`.env` contains test dummy keys or secret ends in `00AA`), dummy tokens like `XXXX.DUMMY.TOKEN.XXXX` automatically pass verification.
3. **Production Mode**:
   - In production, set real Cloudflare Turnstile keys in `.env` or system environment variables.

---

## 5. CREATING INITIAL ACCOUNTS LOCALLY

There are no default hardcoded passwords in this repository. After cloning, initialize local administrator or student accounts using the CLI utility:

```bash
python manage_users.py add admin@example.com --name "Administrator" --role admin
python manage_users.py add student@example.com --name "Student Name" --role student
```

Alternatively, students can register directly through the web portal at `/?login=1`.

---

## 6. HOW TO CONTINUE DEVELOPMENT ON EMERGENT AI

### Step 1: Start the Server
```bash
python server.py
# Or using npm
npm start
```
The server listens on `http://127.0.0.1:8000` (or `0.0.0.0:8000` if configured).

### Step 2: Verify Existing Tests Pass
Run the verification test suites to ensure the environment is healthy:
```bash
python -m unittest tests/test_crm.py
python -m unittest tests/test_chatbot.py
python -m unittest tests/test_turnstile.py
```
All tests must report `OK`.

### Step 3: Modifying Frontend Pages
- Edit HTML files in `public/` directly. No build step is required.
- Global styles are located in `public/assets/site-pages.css`, `public/assets/cyber-interactions.css`, and Tailwind CDN.
- Chatbot visual customizations are in `public/assets/cyber-chatbot.css`.

### Step 4: Modifying the LMS
- The LMS template is located at `private/lms.html`.
- It connects to `/api/me` and `/api/logout`.
- Any new LMS student features (e.g. database persistence for quiz scores, course enrollment) should be added to `server.py` as authenticated `/api/student/*` endpoints.

### Step 5: Connecting Cloud AI to Chatbot (Optional)
By default, the AI assistant runs locally with its built-in rule-based and neural knowledge engine. If you want to connect external LLMs:
- **Gemini**: Add `GEMINI_API_KEY=your_key` to `.env`.
- **OpenAI**: Add `OPENAI_API_KEY=your_key` to `.env`.
`chatbot_service.py` will automatically switch to the external model with local fallback protection.

---

## 7. EXPORT VERIFICATION CHECKLIST

- [x] All HTML, CSS, and JS files preserved with original neon cybersecurity styling.
- [x] All 30 robot rotation frames preserved in `public/assets/head_frames/`.
- [x] Background video assets preserved in `public/assets/`.
- [x] Clean database schema and public catalog seed (`data/schema.sql`) included (runtime `data/cyberpro.db` is auto-created and excluded from Git).
- [x] Zero hardcoded passwords, API keys, or machine paths in repository files.
- [x] Cloudflare Turnstile human defense active on all auth routes.
- [x] Student LMS and Admin CRM templates secured in `private/`.
- [x] All unit test suites pass (CRM: 6/6, Chatbot: 7/7, Turnstile: 11/11).
- [x] Comprehensive documentation and deployment templates included.
