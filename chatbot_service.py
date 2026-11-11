"""
CYBERPRO AI ASSISTANT - CRM & CONVERSATIONAL INTELLIGENCE SERVICE
Intelligent Customer Profiling, Multi-Turn State Machine & Lead Management
Supports:
  1. Automated Entity & Intent Extraction (Name, Phone, Email, Location, Level, Background, Goal)
  2. Multi-Turn Adaptive Question & Answer Progression (Never interrogates, never repeats)
  3. Dynamic Lead Scoring & CRM Database Synchronization
  4. Google Gemini API / OpenAI API / Built-In CYBERPRO Neural Inference Engine
"""

import json
import os
import re
import time
import sqlite3
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional

import runtime_paths
import universal_ai_engine

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = runtime_paths.resolve_db_path(ROOT)
KNOWLEDGE_FILE = DATA_DIR / "cyberpro_knowledge.json"
CONFIG_FILE = DATA_DIR / "chatbot_config.json"

_rate_limits: Dict[str, List[float]] = {}


def get_db():
    db_file = runtime_paths.resolve_db_path(ROOT)
    con = sqlite3.connect(db_file, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def load_knowledge_base() -> Dict[str, Any]:
    try:
        kb_path = runtime_paths.resolve_data_read_path(ROOT, "cyberpro_knowledge.json")
        if kb_path.is_file():
            with open(kb_path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print("Error loading knowledge base:", e)
    return {}


def load_config() -> Dict[str, Any]:
    defaults = {
        "enabled": True,
        "provider": "auto",
        "gemini_api_key": os.environ.get("GEMINI_API_KEY", ""),
        "gemini_model": os.environ.get("GEMINI_MODEL", "gemini-1.5-flash"),
        "openai_api_key": os.environ.get("OPENAI_API_KEY", ""),
        "openai_model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        "temperature": 0.4,
        "max_tokens": 1000,
        "system_name": "CYBERPRO AI ASSISTANT",
        "rate_limit_per_minute": 25,
        "max_input_length": 1500
    }
    try:
        cfg_path = runtime_paths.resolve_data_read_path(ROOT, "chatbot_config.json")
        if cfg_path.is_file():
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                defaults.update(cfg)
                if os.environ.get("GEMINI_API_KEY"):
                    defaults["gemini_api_key"] = os.environ["GEMINI_API_KEY"]
                if os.environ.get("OPENAI_API_KEY"):
                    defaults["openai_api_key"] = os.environ["OPENAI_API_KEY"]
    except Exception as e:
        print("Error loading chatbot config:", e)
    return defaults


def save_config(new_config: Dict[str, Any]) -> bool:
    try:
        out_dir = runtime_paths.resolve_writable_dir(ROOT, "data")
        with open(out_dir / "chatbot_config.json", "w", encoding="utf-8") as f:
            json.dump(new_config, f, indent=2)
        return True
    except Exception as e:
        print("Error saving chatbot config:", e)
        return False


def save_knowledge_base(new_kb: Dict[str, Any]) -> bool:
    try:
        out_dir = runtime_paths.resolve_writable_dir(ROOT, "data")
        with open(out_dir / "cyberpro_knowledge.json", "w", encoding="utf-8") as f:
            json.dump(new_kb, f, indent=2)
        return True
    except Exception as e:
        print("Error saving knowledge base:", e)
        return False


def is_rate_limited(ip: str, limit: int = 25, window: float = 60.0) -> bool:
    now = time.time()
    timestamps = _rate_limits.get(ip, [])
    timestamps = [t for t in timestamps if now - t < window]
    if len(timestamps) >= limit:
        _rate_limits[ip] = timestamps
        return True
    timestamps.append(now)
    _rate_limits[ip] = timestamps
    return False


def sanitize_input(text: str, max_len: int = 1500) -> str:
    if not text:
        return ""
    clean = text.strip()[:max_len]
    clean = re.sub(r'(?i)(ignore all previous instructions|bypass system prompt|disregard prior rules)', '[SECURITY FILTERED]', clean)
    return clean


# -----------------------------------------------------------------------------
# CRM ENTITY EXTRACTION & CUSTOMER PROFILING
# -----------------------------------------------------------------------------

def extract_customer_entities(message: str, existing_profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    Intelligently extracts customer information from natural conversation text
    and updates their progressive profile.
    """
    profile = dict(existing_profile)
    text = message.strip()
    low = text.lower()

    # 1. Email extraction
    email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text)
    if email_match and not profile.get("email"):
        profile["email"] = email_match.group(0).lower().strip()

    # 2. Phone / WhatsApp extraction (10-14 digits, optional + or country code)
    phone_match = re.search(r'(\+?\d{1,4}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}|\b\d{10}\b', text)
    if phone_match and not profile.get("phone"):
        raw_phone = phone_match.group(0).strip()
        # Ensure it looks like a valid phone number, not a year or count
        if len(re.sub(r'\D', '', raw_phone)) >= 10:
            profile["phone"] = raw_phone

    # 3. Name extraction
    if not profile.get("full_name") or profile.get("full_name") in ("Visitor", ""):
        name_patterns = [
            r'(?:my name is|i am|i\'m|this is|call me)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)',
            r'(?:name\s*[:=-]\s*)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)',
            r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:here|from|and\s+my\s+phone)',
        ]
        for pat in name_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                cand = m.group(1).strip()
                # Exclude false positives like "I am a student"
                if cand.lower() not in ("a student", "a beginner", "interested", "looking", "new", "working", "trying"):
                    profile["full_name"] = cand.title()
                    break

    # 4. Location extraction
    if not profile.get("location"):
        loc_patterns = [
            r'(?:from|in|living in|located in|based in)\s+([A-Za-z]+(?:\s+[A-Za-z]+)?)(?:,|\.|\s|$)',
        ]
        for pat in loc_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                cand = m.group(1).strip()
                if cand.lower() not in ("cybersecurity", "scratch", "beginning", "college", "school", "office", "home", "india", "usa"):
                    profile["location"] = cand.title()
                    break

    # 5. Knowledge Level detection
    if not profile.get("knowledge_level"):
        if any(k in low for k in ["beginner", "zero knowledge", "no coding", "new to cyber", "fresher", "start from scratch", "basic"]):
            profile["knowledge_level"] = "Beginner"
        elif any(k in low for k in ["intermediate", "know some networking", "ceh", "learning for 6 months", "moderate"]):
            profile["knowledge_level"] = "Intermediate"
        elif any(k in low for k in ["experienced", "working in soc", "penetration tester", "security analyst", "advanced"]):
            profile["knowledge_level"] = "Experienced"

    # 6. Background detection
    if not profile.get("background"):
        if any(k in low for k in ["college student", "university", "btech", "bca", "mtech", "student", "final year", "engineering"]):
            profile["background"] = "College Student"
        elif any(k in low for k in ["working professional", "working in it", "software engineer", "sysadmin", "it support", "developer"]):
            profile["background"] = "Working Professional"
        elif any(k in low for k in ["switch career", "non-it", "different field", "career changer", "transition"]):
            profile["background"] = "Career Switcher"
        elif any(k in low for k in ["self-taught", "independent", "freelancer", "hobbyist"]):
            profile["background"] = "Independent Learner"

    # 7. Learning Goal detection
    if not profile.get("learning_goal"):
        if any(k in low for k in ["job", "placement", "career", "get hired", "interview"]):
            profile["learning_goal"] = "Career & Placement"
        elif any(k in low for k in ["certificate", "certified", "exam", "certification"]):
            profile["learning_goal"] = "Certification"
        elif any(k in low for k in ["practical skills", "hands on", "learn tools", "offensive security", "bug bounty"]):
            profile["learning_goal"] = "Practical Skills & Bug Bounty"

    # 8. Interested Course / Track
    if not profile.get("interested_course") or profile.get("interested_course") == "General":
        if any(k in low for k in ["ccce", "flagship", "6 month", "certified cybersecurity expert"]):
            profile["interested_course"] = "CCCE Flagship Program"
        elif any(k in low for k in ["ethical hacking", "penetration testing", "red team", "bug bounty"]):
            profile["interested_course"] = "Ethical Hacking & Pentesting"
        elif any(k in low for k in ["soc", "blue team", "security operations", "splunk", "incident response"]):
            profile["interested_course"] = "SOC Analyst & Blue Team"
        elif any(k in low for k in ["corporate", "enterprise", "workforce", "training for team"]):
            profile["interested_course"] = "Corporate Training"

    # 9. Preferred Schedule
    if not profile.get("preferred_schedule"):
        if "weekend" in low:
            profile["preferred_schedule"] = "Weekend Batch"
        elif any(k in low for k in ["evening", "after office", "night"]):
            profile["preferred_schedule"] = "Evening Batch"

    # Update requirements notes if message has substantial user query
    if len(text) > 15 and not profile.get("requirements"):
        profile["requirements"] = text[:300]
    elif len(text) > 15 and profile.get("requirements") and text not in profile["requirements"]:
        profile["requirements"] = (profile["requirements"] + " | " + text[:150])[:500]

    return profile


def calculate_lead_score(profile: Dict[str, Any]) -> int:
    score = 30
    if profile.get("phone"):
        score += 25
    if profile.get("email"):
        score += 15
    if profile.get("full_name") and profile.get("full_name") != "Visitor":
        score += 10
    if profile.get("knowledge_level"):
        score += 5
    if profile.get("background"):
        score += 5
    if profile.get("learning_goal"):
        score += 5
    if profile.get("interested_course") and "CCCE" in profile.get("interested_course", ""):
        score += 5
    return min(100, score)


# -----------------------------------------------------------------------------
# DATABASE CRM SYNCHRONIZATION
# -----------------------------------------------------------------------------

def sync_crm_lead(session_id: str, profile: Dict[str, Any], history: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    Saves or updates the customer enquiry record in SQLite.
    Performs automatic deduplication by email or phone.
    """
    now = int(time.time())
    score = calculate_lead_score(profile)
    profile["lead_score"] = score

    email = (profile.get("email") or "").strip().lower()
    phone = (profile.get("phone") or "").strip()
    name = (profile.get("full_name") or "Visitor").strip()

    # Determine status
    current_status = profile.get("status", "new")
    if phone or "call" in profile.get("requirements", "").lower() or "whatsapp" in profile.get("requirements", "").lower():
        if current_status == "new":
            current_status = "awaiting_contact"
    if profile.get("background") == "College Student" and current_status == "new":
        current_status = "interested_student"
    profile["status"] = current_status

    # Generate multi-sentence summary
    summary_parts = []
    if name != "Visitor":
        summary_parts.append(f"Customer: {name}")
    if profile.get("location"):
        summary_parts.append(f"Location: {profile['location']}")
    if profile.get("knowledge_level"):
        summary_parts.append(f"Level: {profile['knowledge_level']}")
    if profile.get("background"):
        summary_parts.append(f"Background: {profile['background']}")
    if profile.get("learning_goal"):
        summary_parts.append(f"Goal: {profile['learning_goal']}")
    if profile.get("interested_course"):
        summary_parts.append(f"Interest: {profile['interested_course']}")
    if profile.get("preferred_schedule"):
        summary_parts.append(f"Schedule: {profile['preferred_schedule']}")
    summary = " | ".join(summary_parts) if summary_parts else "General inquiry"
    profile["conversation_summary"] = summary

    history_json = json.dumps(history[-16:])

    with get_db() as con:
        # Check if record already exists by session_id, or by email, or by phone
        row = None
        if email:
            row = con.execute("SELECT id FROM customer_enquiries WHERE email=? AND email != ''", (email,)).fetchone()
        if not row and phone:
            row = con.execute("SELECT id FROM customer_enquiries WHERE phone=? AND phone != ''", (phone,)).fetchone()
        if not row and session_id:
            row = con.execute("SELECT id FROM customer_enquiries WHERE session_id=?", (session_id,)).fetchone()

        if row:
            enquiry_id = row["id"]
            con.execute("""
                UPDATE customer_enquiries SET
                    full_name = CASE WHEN ? != 'Visitor' AND ? != '' THEN ? ELSE full_name END,
                    email = CASE WHEN ? != '' THEN ? ELSE email END,
                    phone = CASE WHEN ? != '' THEN ? ELSE phone END,
                    location = CASE WHEN ? != '' THEN ? ELSE location END,
                    interested_course = CASE WHEN ? != '' THEN ? ELSE interested_course END,
                    knowledge_level = CASE WHEN ? != '' THEN ? ELSE knowledge_level END,
                    background = CASE WHEN ? != '' THEN ? ELSE background END,
                    learning_goal = CASE WHEN ? != '' THEN ? ELSE learning_goal END,
                    preferred_schedule = CASE WHEN ? != '' THEN ? ELSE preferred_schedule END,
                    requirements = ?,
                    conversation_summary = ?,
                    conversation_history = ?,
                    status = ?,
                    lead_score = ?,
                    updated_at = ?
                WHERE id = ?
            """, (
                name, name, name,
                email, email,
                phone, phone,
                profile.get("location", ""), profile.get("location", ""),
                profile.get("interested_course", ""), profile.get("interested_course", ""),
                profile.get("knowledge_level", ""), profile.get("knowledge_level", ""),
                profile.get("background", ""), profile.get("background", ""),
                profile.get("learning_goal", ""), profile.get("learning_goal", ""),
                profile.get("preferred_schedule", ""), profile.get("preferred_schedule", ""),
                profile.get("requirements", ""),
                summary,
                history_json,
                current_status,
                score,
                now,
                enquiry_id
            ))
            profile["id"] = enquiry_id
        else:
            cur = con.execute("""
                INSERT INTO customer_enquiries (
                    session_id, full_name, email, phone, location,
                    interested_course, knowledge_level, background, learning_goal,
                    preferred_schedule, requirements, conversation_summary,
                    conversation_history, status, lead_score, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session_id, name, email, phone, profile.get("location", ""),
                profile.get("interested_course", "CCCE Flagship"), profile.get("knowledge_level", ""),
                profile.get("background", ""), profile.get("learning_goal", ""),
                profile.get("preferred_schedule", ""), profile.get("requirements", ""),
                summary, history_json, current_status, score, now, now
            ))
            profile["id"] = cur.lastrowid

        # Also sync to legacy leads table for backward compatibility
        if (email or phone) and (name != "Visitor"):
            con.execute("""
                INSERT OR IGNORE INTO leads (name, phone, email, goal, role, program, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                name, phone, email, profile.get("requirements", "")[:300],
                profile.get("background", "student"), profile.get("interested_course", "CCCE"), now
            ))

    return profile


# -----------------------------------------------------------------------------
# ADAPTIVE MULTI-TURN CONVERSATION PROGRESSION
# -----------------------------------------------------------------------------

def _recent_assistant_text(history: List[Dict[str, str]]) -> str:
    """Concatenates recent assistant messages for anti-repetition and context checks."""
    return " ".join(
        str(m.get("content", "")).lower()
        for m in history[-8:]
        if isinstance(m, dict) and m.get("role") == "assistant"
    )


def _last_assistant_message(history: List[Dict[str, str]]) -> str:
    """Returns the most recent assistant message text, if any."""
    for m in reversed(history):
        if isinstance(m, dict) and m.get("role") == "assistant":
            return str(m.get("content", ""))
    return ""


def determine_next_follow_up(profile: Dict[str, Any], user_msg: str, history: Optional[List[Dict[str, str]]] = None) -> Optional[str]:
    """
    Intelligently inspects what information has already been collected and discussed,
    returning the next logical discovery question while never repeating past prompts.
    """
    low = user_msg.lower()
    prior_ai = _recent_assistant_text(history or [])

    # Never append a follow-up if visitor supplied contact info
    if bool(re.search(r'[\w\.-]+@[\w\.-]+\.\w+', user_msg)) or bool(re.search(r'(\+?\d{1,4}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}|\b\d{10}\b', user_msg)):
        return None

    candidates = []
    if not profile.get("knowledge_level") and any(k in low for k in ["learn", "ethical hacking", "course", "ccce", "start", "join", "study", "syllabus"]):
        candidates.append("Are you completely new to cybersecurity, or do you already have some technical or networking background?")
    if not profile.get("background") and (profile.get("knowledge_level") or any(k in low for k in ["beginner", "zero", "experienced", "intermediate"])):
        candidates.append("Are you currently a college student, working professional, or looking to switch careers into tech?")
    if not profile.get("learning_goal") and (profile.get("background") or any(k in low for k in ["student", "working", "engineer", "college"])):
        candidates.append("What is your primary goal—are you preparing for job placements, building practical hands-on skills, or aiming for international certifications?")
    if not profile.get("preferred_schedule") and any(k in low for k in ["batch", "timing", "schedule", "class", "enroll", "fee", "cost"]):
        candidates.append("We offer flexible evening and weekend batches for CCCE Live Cohort 03. Which schedule works best for your routine?")
    if not (profile.get("phone") or profile.get("email")) and any(k in low for k in ["enroll", "syllabus", "admission", "register", "brochure", "fee", "cost"]):
        candidates.append("Our admissions counselor can share the complete 27-module syllabus PDF, batch schedule, and scholarship options. What is your name and WhatsApp number so we can send that over to you?")

    for q in candidates:
        # Skip questions whose key phrase was already asked in recent assistant turns
        probe = q[:42].lower()
        if probe not in prior_ai:
            return q

    return None


AI_MODES: Dict[str, Dict[str, str]] = {
    "cyberpro": {
        "label": "CyberPro",
        "desc": "Strictly CyberPro website, Student LMS, classes & courses",
        "directive": "Respond STRICTLY about CYBERPRO Defense Academy ONLY: the CYBERPRO website pages, Student LMS (/lms), Dashboard, Live Class, Recorded Courses, Labs, Quizzes, Cyber Range, Certificates, Refer & Earn, CCCE 27-module curriculum, admissions, and Founder Parth Rathod. Politely decline unrelated outside topics and suggest switching to Worldwide, Ethical Hacking, or Code mode.",
    },
    "ethical_hacking": {
        "label": "Ethical Hacking",
        "desc": "Strictly ethical hacking, defense & authorized labs",
        "directive": "Think and respond STRICTLY as an ethical hacker and cybersecurity defender. Focus exclusively on authorized penetration testing methodology, OWASP Top 10, network/system security, vulnerability analysis, defense hardening, SIEM triage, and lab exercises. Decline illegal attacks and decline unrelated off-topic non-security trivia.",
    },
    "worldwide": {
        "label": "Worldwide",
        "desc": "World knowledge, science, math, quotes & general Q&A",
        "directive": "Act as a versatile universal AI assistant answering worldwide general knowledge, science, history, geography, comparisons, math, quotes, writing, coding, and CYBERPRO academy queries accurately.",
    },
    "code": {
        "label": "Code",
        "desc": "Programming, debugging, Python/JS/SQL/Bash & code",
        "directive": "Think and respond STRICTLY as a senior software engineer and secure coding mentor. Focus exclusively on writing, explaining, debugging, and optimizing code (Python, JavaScript, SQL, Bash, HTML/CSS, APIs, algorithms, and OWASP coding safety). Politely decline unrelated non-coding topics.",
    },
}


def normalize_ai_mode(raw_mode: str) -> str:
    key = re.sub(r"[^a-z0-9_]+", "_", str(raw_mode or "").strip().lower()).strip("_")
    aliases = {
        "world": "worldwide",
        "world_wide": "worldwide",
        "worldwide_question": "worldwide",
        "global": "worldwide",
        "general": "worldwide",
        "neural": "worldwide",
        "neural_core": "worldwide",
        "neural_core_4_2": "worldwide",
        "learn": "worldwide",
        "ethical": "ethical_hacking",
        "hacking": "ethical_hacking",
        "hacker": "ethical_hacking",
        "pentest": "ethical_hacking",
        "pentesting": "ethical_hacking",
        "security": "ethical_hacking",
        "soc": "ethical_hacking",
        "soc_defense_4_0": "ethical_hacking",
        "defend": "ethical_hacking",
        "cyber_pro": "cyberpro",
        "cyberpro_ai": "cyberpro",
        "ccce": "cyberpro",
        "ccce_mentor_pro": "cyberpro",
        "mentor": "cyberpro",
        "lms": "cyberpro",
        "labs": "cyberpro",
        "coding": "code",
        "programming": "code",
        "dev": "code",
        "owasp": "code",
        "owasp_auditor": "code",
        "auditor": "code",
    }
    key = aliases.get(key, key)
    return key if key in AI_MODES else "worldwide"


def build_system_prompt(kb: Dict[str, Any], profile: Dict[str, Any], mode: str = "worldwide") -> str:
    active_mode = normalize_ai_mode(mode)
    mode_meta = AI_MODES[active_mode]
    company = kb.get("company", {})
    contact = kb.get("contact", {})
    phone = contact.get("phone", "+91 76668762325")

    known_facts = []
    if profile.get("full_name") and profile["full_name"] != "Visitor":
        known_facts.append(f"Customer Name: {profile['full_name']}")
    if profile.get("knowledge_level"):
        known_facts.append(f"Skill Level: {profile['knowledge_level']}")
    if profile.get("background"):
        known_facts.append(f"Background: {profile['background']}")
    if profile.get("learning_goal"):
        known_facts.append(f"Learning Goal: {profile['learning_goal']}")
    if profile.get("location"):
        known_facts.append(f"Location: {profile['location']}")
    if profile.get("phone"):
        known_facts.append(f"WhatsApp/Phone: {profile['phone']}")
    if profile.get("email"):
        known_facts.append(f"Email: {profile['email']}")

    profile_context = "\n".join(known_facts) if known_facts else "No prior details known yet."

    scope_rules = {
        "cyberpro": (
            "STRICT CYBERPRO-ONLY SCOPE:\n"
            "1. ONLY answer questions directly related to CYBERPRO Defense Academy, its website pages, Student LMS (/lms), Student Dashboard, Live Classes, Recorded Courses, Class Schedule, Labs, Cyber Range, CCCE curriculum, admissions, policies, and Founder Parth Rathod.\n"
            "2. If asked about anything outside CYBERPRO (world trivia, celebrities, unrelated code, etc.), DO NOT answer it — politely explain that CyberPro mode handles CYBERPRO platform & academy topics only, and advise switching to Worldwide, Ethical Hacking, or Code mode."
        ),
        "ethical_hacking": (
            "STRICT ETHICAL-HACKING-ONLY SCOPE:\n"
            "1. ONLY think and answer about Ethical Hacking, authorized penetration testing methodology, defensive cybersecurity, OWASP Top 10, Nmap/Wireshark/Burp Suite/Metasploit/Splunk, vulnerability remediation, threat modeling, and lab defense.\n"
            "2. NEVER assist with illegal/unauthorized hacking. If asked completely unrelated non-security questions, politely stay within Ethical Hacking mode and suggest switching to Worldwide, Code, or CyberPro mode."
        ),
        "code": (
            "STRICT CODE-ONLY SCOPE:\n"
            "1. ONLY answer programming, software engineering, debugging, algorithms, SQL/APIs, HTML/CSS/JS, Python/Bash, and secure coding questions.\n"
            "2. If asked unrelated non-coding trivia or academy admin questions, politely explain Code mode is dedicated to programming only and suggest Worldwide or CyberPro mode."
        ),
        "worldwide": (
            "WORLDWIDE UNIVERSAL ASSISTANT SCOPE:\n"
            "1. Answer ANY question on ANY topic worldwide accurately (science, history, geography, math, quotes, comparisons, writing, coding, cybersecurity).\n"
            "2. Use verified official CYBERPRO facts below when the visitor asks about CYBERPRO, CCCE, or Student LMS."
        ),
    }[active_mode]

    prompt = f"""You are CYBERPRO AI ({mode_meta['label']}).
Founder & Owner of CYBERPRO: {company.get('founder_and_owner', 'Parth Rathod')}.
Official CYBERPRO Contact / WhatsApp Admissions: {phone}.
Official CYBERPRO Support Email: {contact.get('support_email', 'support@cyberpro.com')}.
ACTIVE MODE: {mode_meta['label']} — {mode_meta['directive']}

{scope_rules}

CURRENT USER PROFILE KNOWN:
{profile_context}

VERIFIED CYBERPRO WEBSITE & STUDENT LMS FACTS:
• Homepage (/): Hero 3D Threat Globe, 'STUDENT LMS' nav button, 'ENTER STUDENT LMS' & 'EXPLORE OUR COURSES' CTA buttons, 'JOIN CYBERPRO' drawer.
• Public Pages: Courses & Programs (/programs), CCCE Flagship Program (/ccce), About Us (/about), Contact (/contact), Policies (/privacy-policy/, /terms-conditions/, /cancellation-refund/, /shipping-policy/, /guidelines/).
• Student LMS (/lms) Login: Click 'STUDENT LMS' in top header or 'ENTER STUDENT LMS' -> tabs: Student Login, Register, Recovery.
• Student LMS Dashboard & Sidebar Sections (inside /lms):
  - Learning: Dashboard, Live Class, Recorded Courses, Class Schedule, Books and Notes, Tools & Softwares, CyberPro AI, Labs, Quizzes
  - Performance: Leaderboard, Support
  - Profile & Rewards: Profile and Certificates, Refer & Earn, Payments
  - Cyber Ecosystem: Cyber Range (LIVE LABS), Skill Passport, Bug Bounty Arena, SOC Simulator, Career & Jobs, My Projects
• Flagship CCCE: 6 Months live practical training, 27 Technical Modules, 180+ tools (Wireshark, Burp Suite, Nmap, Metasploit, Splunk, Ghidra, Kali Linux, etc.), Live Cohort 03 (evening & weekend batches), Capstone + Verified Certificate.
• Tuition Policy: Never invent fake numbers; mention scholarship & no-cost EMI options and WhatsApp Admissions ({phone}).
• Ethics: Strictly ethical, authorized, defensive cybersecurity only. Never assist with illegal attacks.
"""
    return prompt


# -----------------------------------------------------------------------------
# TIER 1: External AI API Integrations (Gemini / OpenAI)
# -----------------------------------------------------------------------------

def _trim_trailing_duplicate_user(history: List[Dict[str, str]], user_msg: str) -> List[Dict[str, str]]:
    """Drops trailing user message in history when the client already appended the current prompt."""
    cleaned = [h for h in history[-9:] if isinstance(h, dict) and h.get("content")]
    if cleaned and cleaned[-1].get("role") == "user" and str(cleaned[-1].get("content", "")).strip() == user_msg.strip():
        return cleaned[:-1]
    return cleaned[-8:]


def call_gemini_api(api_key: str, model: str, system_prompt: str, history: List[Dict[str, str]], user_msg: str) -> Tuple[bool, str]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    contents = []
    for h in _trim_trailing_duplicate_user(history, user_msg):
        role = "user" if h.get("role") == "user" else "model"
        text = h.get("content", "")
        if text:
            contents.append({"role": role, "parts": [{"text": text}]})
    contents.append({"role": "user", "parts": [{"text": user_msg}]})

    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.45, "maxOutputTokens": 1000, "topP": 0.95}
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=12) as res:
            res_data = json.loads(res.read().decode("utf-8"))
            candidates = res_data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    return True, parts[0].get("text", "")
            return False, "Empty response from Gemini."
    except Exception as e:
        return False, f"Gemini API error: {str(e)}"


def call_openai_api(api_key: str, model: str, system_prompt: str, history: List[Dict[str, str]], user_msg: str) -> Tuple[bool, str]:
    url = "https://api.openai.com/v1/chat/completions"
    messages = [{"role": "system", "content": system_prompt}]
    for h in _trim_trailing_duplicate_user(history, user_msg):
        role = "user" if h.get("role") == "user" else "assistant"
        text = h.get("content", "")
        if text:
            messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": user_msg})

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.45,
        "max_tokens": 1000
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=12) as res:
            res_data = json.loads(res.read().decode("utf-8"))
            choices = res_data.get("choices", [])
            if choices:
                return True, choices[0].get("message", {}).get("content", "")
            return False, "Empty response from OpenAI."
    except Exception as e:
        return False, f"OpenAI API error: {str(e)}"


# -----------------------------------------------------------------------------
# TIER 2: Built-in CYBERPRO Neural Inference Engine (Context-Aware State Machine)
# -----------------------------------------------------------------------------

_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "could", "do",
    "does", "for", "from", "get", "give", "how", "i", "in", "is", "it", "me",
    "my", "of", "on", "or", "our", "please", "tell", "that", "the", "this",
    "to", "us", "want", "we", "what", "when", "where", "which", "who", "why",
    "will", "with", "would", "you", "your", "about", "explain", "know"
}

_CONCEPT_ANSWERS: List[Tuple[Tuple[str, ...], str]] = [
    (
        ("phases of ethical hacking", "ethical hacking phase", "ptes", "footprinting", "methodology", "kill chain", "cyber kill chain"),
        "**5 Phases of Ethical Hacking (Authorized Methodology):** ⚔️\n\n"
        "1. **Reconnaissance:** Passive OSINT (`WHOIS`, DNS records, certificate transparency, public subdomains) and approved active footprinting.\n"
        "2. **Scanning & Enumeration:** Port/service discovery (`Nmap`), banner grabbing, and mapping open services/endpoints.\n"
        "3. **Gaining Access (Controlled Verification):** Safely verifying exploitable flaws (SQLi, IDOR, misconfigurations) strictly within authorized lab scope.\n"
        "4. **Post-Exploitation & Privilege Path Audit:** Evaluating privilege escalation paths and lateral exposure without disrupting systems.\n"
        "5. **Reporting & Remediation:** Documenting reproduction steps, CVSS severity, root-cause fixes, and re-testing patches."
    ),
    (
        ("reconnaissance", "recon", "osint", "information gathering", "subdomain", "whois"),
        "**Reconnaissance & OSINT in Ethical Hacking:** 🔎\n\n"
        "Reconnaissance maps target attack surfaces before active testing:\n"
        "• **Passive Recon:** Certificate Transparency logs (`crt.sh`), DNS/WHOIS records, ASN ownership, and public code/metadata hygiene.\n"
        "• **Active Enumeration (Authorized Only):** Live subdomain probing, HTTP technology fingerprinting, and port discovery.\n"
        "• **Defense:** Minimize exposed staging subdomains, purge API keys/secrets from public repos, and monitor DNS anomalies."
    ),
    (
        ("sql injection", "sqli", "parameterized", "prepared statement", "union select"),
        "**SQL Injection (SQLi) — Attack & Remediation:** 💉\n\n"
        "SQL Injection occurs when untrusted input is concatenated into a SQL string, altering query syntax and exposing or tampering with database rows.\n"
        "• **Authorized Lab Detection:** Observe error responses, boolean condition behavior (`1=1` vs `1=2`), or time delays in safe targets (`Burp Repeater`).\n"
        "• **Permanent Fix:** Always use parameterized queries / prepared statements (e.g., `SELECT * FROM users WHERE email = ?` with bound arguments), least-privilege DB accounts, and strict input typing."
    ),
    (
        ("xss", "cross site scripting", "cross-site scripting", "csp", "content security policy"),
        "**Cross-Site Scripting (XSS) — Types & Defense:** 🛡️\n\n"
        "XSS executes untrusted JavaScript inside another user's browser across three main variants:\n"
        "• **Stored XSS:** Malicious payload saved in DB and served to visitors.\n"
        "• **Reflected XSS:** Payload echoed directly from request parameters/URLs.\n"
        "• **DOM XSS:** Client-side JS writes untrusted source data (`location.search`) into dangerous sinks (`innerHTML`).\n"
        "• **Defense:** Context-aware HTML escaping, using `textContent` instead of `innerHTML`, `HttpOnly` session cookies, and a strict Content-Security-Policy (`CSP`)."
    ),
    (
        ("csrf", "cross site request forgery", "samesite"),
        "**Cross-Site Request Forgery (CSRF) — Mechanic & Mitigation:** 🔐\n\n"
        "CSRF tricks an authenticated browser into sending an unintended state-changing request because browsers automatically include cookies.\n"
        "• **Prevention:** Use unguessable per-session anti-CSRF synchronizer tokens verified server-side, set cookies with `SameSite=Lax` or `SameSite=Strict`, and require re-authentication for sensitive actions."
    ),
    (
        ("ssrf", "server side request forgery", "metadata", "169.254"),
        "**Server-Side Request Forgery (SSRF) — Impact & Hardening:** 🌐\n\n"
        "SSRF abuses a server feature that fetches user-supplied URLs to pivot into internal services, localhost ports, or cloud metadata endpoints (`169.254.169.254`).\n"
        "• **Defense:** Enforce strict destination domain/IP allowlists, block private RFC1918/link-local ranges after DNS resolution, disable HTTP redirects in internal fetchers, and enforce cloud `IMDSv2`."
    ),
    (
        ("idor", "broken access control", "access control", "bola", "authorization bypass"),
        "**IDOR & Broken Access Control (#1 OWASP Top 10):** 🔓\n\n"
        "Insecure Direct Object Reference (IDOR) occurs when changing an identifier (`/api/invoice/1042` → `1041`) exposes another user's record because authorization wasn't verified.\n"
        "• **Fix:** Enforce object-level ownership/role checks on the server (`WHERE id = ? AND user_id = current_user.id`) for every API call—never rely on hidden UI fields or client checks."
    ),
    (
        ("burp suite", "burp", "proxy intercept", "repeater", "intruder"),
        "**Burp Suite for Authorized Web Testing:** 🦊\n\n"
        "Burp Suite sits as an HTTPS intercepting proxy between your browser and authorized web labs:\n"
        "• **Proxy & HTTP History:** Inspect every request header, JSON payload, cookie flag, and server response.\n"
        "• **Repeater:** Modify parameters/methods manually to verify validation, SQLi/XSS escaping, and broken access controls.\n"
        "• **Decoder / Comparer:** Analyze Base64/JWT payloads and diff response lengths safely."
    ),
    (
        ("owasp", "owasp top 10", "web security", "web app", "http headers", "hsts", "xxe", "ssti", "command injection", "lfi", "directory traversal"),
        "**OWASP Top 10 Web Application Security Audit:** 🌐\n\n"
        "Modern ethical web assessments systematically verify critical OWASP categories:\n"
        "• **A01 Broken Access Control (IDOR), A02 Cryptographic Failures, A03 Injection (SQLi/XSS/Cmd Injection), A05 Security Misconfiguration (`HSTS`, `CSP`, `X-Frame-Options`), and A10 SSRF.**\n"
        "• Intercept flows via **Burp Suite** in approved labs and remediate at root cause with server-side validation and secure defaults."
    ),
    (
        ("nmap", "port scan", "port scanning", "tcp scan", "syn scan", "enumeration"),
        "**Nmap Network Scanning & Port Enumeration:** 🛰️\n\n"
        "Nmap maps live hosts, open ports, and service versions inside authorized networks:\n"
        "• `nmap -sS -sV -sC <target>`: Stealth TCP SYN scan + service/version detection + safe default NSE scripts.\n"
        "• Port states (`open`, `closed`, `filtered`) expose reachable daemons and firewall rules.\n"
        "• **Hardening:** Close unneeded ports, enforce host firewalls, and restrict admin ports (SSH/RDP/DB) behind VPN/allowlists."
    ),
    (
        ("wireshark", "packet analysis", "packet capture", "pcap", "packet sniffing"),
        "**Wireshark Packet Capture & Traffic Analysis:** 🦈\n\n"
        "Wireshark dissects live network frames down to L2–L7 payloads:\n"
        "• **Practical Filters:** `http`, `dns`, `tcp.flags.syn == 1 && tcp.flags.ack == 0`, `tls.handshake`, `arp`.\n"
        "• **Security Uses:** Spot cleartext credentials/tokens, suspicious C2 beaconing, DNS tunneling, TCP SYN floods, or ARP spoofing anomalies."
    ),
    (
        ("arp spoofing", "mitm", "man in the middle", "dns spoofing", "sniffing", "tcp/ip", "osi"),
        "**MITM, ARP/DNS Spoofing & Network Layer Defense:** 📡\n\n"
        "In local segments, Man-in-the-Middle attacks poison ARP tables or DNS answers to intercept traffic.\n"
        "• **Detection & Defense:** Enable Dynamic ARP Inspection (DAI) & DHCP Snooping on switches, use encrypted protocols exclusively (`TLS 1.3`, `SSH`, `HTTPS` + `HSTS`), and deploy DNSSEC."
    ),
    (
        ("soc", "siem", "splunk", "blue team", "triage", "playbook", "logs", "wazuh"),
        "**SOC Analyst, SIEM Telemetry & Alert Triage:** 🛡️\n\n"
        "SOC defense correlates endpoint, firewall, and auth logs in platforms like **Splunk** or **Wazuh**:\n"
        "• **Detection Signals:** Impossible travel logins, brute-force spikes, encoded PowerShell execution, and rare outbound connections.\n"
        "• **Workflow:** Verify true vs. false positive → Correlate entity/IP timeline → Isolate compromised host/account → Document containment & tune correlation rules."
    ),
    (
        ("incident response", "dfir", "forensics", "digital forensics"),
        "**Incident Response (IR) & Digital Forensics:** 🚨\n\n"
        "Effective IR follows the NIST PICERL lifecycle:\n"
        "1. **Preparation** → 2. **Identification & Triage** (preserve volatile memory, logs, hashes before rebooting!) → 3. **Containment** (network isolate without tipping off threat actors) → 4. **Eradication** → 5. **Recovery** → 6. **Lessons Learned**."
    ),
    (
        ("privilege escalation", "privesc", "suid", "sudo", "lateral movement"),
        "**Privilege Escalation Paths & Hardening:** ⬆️\n\n"
        "Privilege escalation checks how low-privilege sessions could gain root/SYSTEM access in labs:\n"
        "• **Linux Vectors:** Misconfigured `sudo -l` rules, risky `SUID` binaries, writable cron scripts/service paths, and leaked secrets.\n"
        "• **Windows Vectors:** Unquoted service paths, weak service ACLs, stored credentials, and `SeImpersonate` token abuse.\n"
        "• **Fix:** Enforce least privilege, audit `sudo`/SUID permissions, harden service directories, and patch kernels/packages."
    ),
    (
        ("active directory", "kerberoasting", "bloodhound", "responder", "kerberos", "ldap", "smb"),
        "**Active Directory Security & Attack Path Defense:** 🏢\n\n"
        "AD assessments audit domain privilege graphs (`BloodHound`) and authentication weaknesses:\n"
        "• **Common Risks:** Kerberoasting weak SPN service passwords, AS-REP Roasting, LLMNR/NBT-NS poisoning (`Responder`), unconstrained delegation, and disabled SMB signing.\n"
        "• **Defense:** Enforce 24+ char random gMSA passwords, disable LLMNR/NBT-NS, require SMB signing + LDAP signing, and adopt tiered admin isolation."
    ),
    (
        ("metasploit", "kali", "kali linux", "gobuster", "ffuf", "hydra", "sqlmap", "nikto", "hashcat", "john", "bug bounty"),
        "**Ethical Pentesting Tooling (Kali Linux & Bug Bounty Workflow):** 🧰\n\n"
        "Inside authorized sandboxes and bug bounty programs, practitioners pair manual understanding with focused tools:\n"
        "• **Discovery:** `Nmap`, `ffuf`/`gobuster` (authorized paths), `Burp Suite` proxy.\n"
        "• **Verification:** Controlled PoCs with `Metasploit`/custom scripts strictly within scope (verify without destructive payloads).\n"
        "• **Auth/Hash Audits:** Password strength audits with `Hashcat` / `John` only against authorized test dumps."
    ),
    (
        ("jwt", "oauth", "session hijacking", "cookie", "bcrypt", "scrypt", "argon2", "hashing", "salting", "mfa", "password"),
        "**Authentication, JWT & Cryptographic Session Defense:** 🔐\n\n"
        "Secure identity design prevents account takeover and session forging:\n"
        "• **Password Hashing:** Always use slow salted KDFs (`Argon2id`, `scrypt`, or `bcrypt`) — never MD5, SHA-1, or unsalted SHA-256.\n"
        "• **JWT Security:** Pin allowed signing algorithms server-side (reject `alg: none` & asymmetric/symmetric confusion), validate `exp`/`iss`/`aud`.\n"
        "• **Cookies:** Enforce `HttpOnly; Secure; SameSite=Lax` + MFA on sensitive actions."
    ),
    (
        ("phishing", "social engineering", "spf", "dkim", "dmarc"),
        "**Phishing & Social Engineering Defense:** 🎣\n\n"
        "Social engineering targets human trust rather than software bugs.\n"
        "• **Technical Controls:** Enforce `SPF`, `DKIM`, and `DMARC (p=reject)` email authentication, hardware FIDO2/WebAuthn MFA (phishing-resistant!), link sandboxing, and domain-spoofing alerts."
    ),
    (
        ("malware", "ransomware", "trojan", "reverse engineering", "ghidra"),
        "**Malware, Ransomware & Static/Dynamic Analysis:** 🧬\n\n"
        "Analyze suspicious binaries ONLY inside air-gapped/snapshot VMs (`Ghidra`, strings, hashes, Sysinternals Procmon).\n"
        "• **Ransomware Resilience:** Immutable offline backups (3-2-1 rule), EDR behavioral blocking, macro/script restrictions, and least-privilege network shares."
    ),
    (
        ("cloud", "aws", "azure", "iam"),
        "**Cloud Security & IAM Hardening (AWS / Azure):** ☁️\n\n"
        "Cloud security prevents misconfigurations that expose data externally:\n"
        "• Enforce least-privilege IAM roles (avoid `*` wildcards), block public object buckets by default, require `IMDSv2` against SSRF metadata theft, and log all `CloudTrail` / Activity events."
    ),
    (
        ("mitre", "cve", "cvss", "cia triad", "zero trust", "firewall", "vpn", "cryptography", "encryption", "ddos", "tls", "defense"),
        "**Security Architecture Frameworks (Zero Trust, CIA Triad & MITRE ATT&CK):** 🏛️\n\n"
        "• **CIA Triad:** Protect **Confidentiality** (TLS/AES encryption, ACLs), **Integrity** (HMAC/hashes, parameterized queries), and **Availability** (redundancy, DDoS rate-limiting).\n"
        "• **Zero Trust:** Never trust implicitly by network location — authenticate & authorize every request continuously.\n"
        "• **MITRE ATT&CK & CVSS:** Map attacker tactics/techniques for detection coverage and prioritize patching using CVSS + exploitability."
    ),
]


def _normalize_cyber_msg(user_msg: str) -> str:
    norm = re.sub(r"\s+", " ", re.sub(r"[^\w\s@.+:/-]", " ", user_msg.lower())).strip()
    typos = {
        "dashbord": "dashboard",
        "dasboard": "dashboard",
        "dashbaord": "dashboard",
        "cyyberpro": "cyberpro",
        "cyyber": "cyber",
        "cyber pro": "cyberpro",
        "shepping": "shipping",
        "instructer": "instructor",
        "registion": "registration",
        "pasword": "password",
        "passwrd": "password",
        "syallabus": "syllabus",
        "sylabus": "syllabus",
        "internshp": "internship",
        "certifcate": "certificate",
        "certificte": "certificate",
        "recordd": "recorded",
        "recorings": "recordings",
        "recoding": "recording",
        "cours": "course",
        "admision": "admission",
    }
    for bad, good in typos.items():
        if bad in norm:
            norm = norm.replace(bad, good)
    return norm


def _has_any_term(norm_msg: str, terms: Any) -> bool:
    """Matches multi-word phrases safely and single tokens on word boundaries."""
    return _count_term_hits(norm_msg, terms) > 0


def _count_term_hits(norm_msg: str, terms: Any) -> int:
    hits = 0
    for term in terms:
        if " " in term or "/" in term or "-" in term:
            if term in norm_msg:
                hits += 2 if " " in term else 1
        else:
            if re.search(rf"\b{re.escape(term)}\b", norm_msg):
                hits += 1
    return hits


def _match_best_concept(norm_msg: str) -> Optional[str]:
    """Selects the highest-specificity security concept answer rather than first broad hit."""
    best_score = 0
    best_ans = None
    for keywords, explanation in _CONCEPT_ANSWERS:
        score = _count_term_hits(norm_msg, keywords)
        if score > best_score:
            best_score = score
            best_ans = explanation
    return best_ans if best_score > 0 else None


def _match_kb_faq(norm_msg: str, kb: Dict[str, Any]) -> Optional[str]:
    """Scores visitor query against dynamic FAQs loaded from cyberpro_knowledge.json."""
    tokens = {t for t in re.findall(r"[a-z0-9]{2,}", norm_msg) if t not in _STOP_WORDS and t != "cyberpro"}
    if not tokens:
        return None
    best_score = 0
    best_answer = None
    for faq in kb.get("faqs", []):
        q = str(faq.get("question", "")).lower()
        a = str(faq.get("answer", "")).strip()
        if not a:
            continue
        q_tokens = {t for t in re.findall(r"[a-z0-9]{2,}", q) if t not in _STOP_WORDS and t != "cyberpro"}
        overlap = len(tokens & q_tokens)
        if overlap > best_score:
            best_score = overlap
            best_answer = a
    min_req = 1 if len(tokens) <= 2 else 2
    return best_answer if best_score >= min_req else None


def _mode_enrichment(active_mode: str) -> str:
    if active_mode == "ethical_hacking":
        return "\n\n**Ethical Hacking Focus:** Test strictly inside authorized lab targets, document root cause cleanly, and verify defensive remediation."
    if active_mode == "cyberpro":
        return "\n\n**CyberPro Academy Reference:** All live classes, recorded lectures, hands-on labs, and quizzes for this live inside your **Student LMS (`/lms`)** under our 27-module CCCE track."
    if active_mode == "code":
        return "\n\n**Secure Code Audit Check:** Validate all inputs with strict types/schemas, bind SQL parameters, encode HTML outputs cleanly, and avoid dynamic `eval()`."
    return ""


def _answer_cyberpro_query(
    norm: str,
    user_msg: str,
    history: List[Dict[str, str]],
    kb: Dict[str, Any],
    profile: Dict[str, Any],
    active_mode: str,
) -> Optional[str]:
    """Uses weighted intent scoring so CyberPro questions get exact, non-repetitive answers."""
    prior_history = _trim_trailing_duplicate_user(history, user_msg)
    prior_ai = _recent_assistant_text(prior_history)
    last_ai = _last_assistant_message(prior_history).lower()

    company = kb.get("company", {})
    owner_name = company.get("founder_and_owner", "Parth Rathod")
    company_name = company.get("name", "CYBERPRO Defense Academy")
    established = company.get("established", "2026")
    contact = kb.get("contact", {})
    phone = contact.get("phone", "+91 76668762325")
    wa_url = contact.get("whatsapp_chat_url", f"https://wa.me/{phone.replace('+', '').replace(' ', '')}")
    tg_chat_url = contact.get("telegram_chat_url", "https://t.me/+917666872325")
    tg_comm_url = contact.get("telegram_community_url", tg_chat_url)
    support_email = contact.get("support_email", "support@cyberpro.com")
    admissions_email = contact.get("admissions_email", "admissions@cyberpro.com")

    next_question = determine_next_follow_up(profile, user_msg, prior_history)
    follow_up_suffix = f"\n\n**Quick question:** {next_question}" if next_question else ""

    # Define granular intents with weighted terms (bare 'cyberpro' never dominates specific intents)
    intent_specs = [
        ("dashboard", (
            "dashboard", "student lms", "lms overview", "lms panel", "where is lms",
            "lms features", "what is inside lms", "student portal", "portal"
        ), 3),
        ("live_class", (
            "live class", "live classes", "live session", "live lecture", "attend class",
            "join class", "class link", "online class", "class schedule", "timetable",
            "timing", "timings", "batch", "weekend", "evening", "cohort"
        ), 4),
        ("recorded_courses", (
            "recorded", "recording", "recordings", "replay", "missed class",
            "video library", "watch later", "lifetime access", "recorded courses"
        ), 4),
        ("lms_labs_study", (
            "labs", "lab", "cyber range", "soc simulator", "bug bounty arena",
            "quizzes", "quiz", "books", "notes", "cheat sheet", "study material",
            "tools", "softwares", "180 tools", "practical labs"
        ), 3),
        ("lms_login_recovery", (
            "login", "log in", "sign in", "register", "signup", "sign up",
            "create account", "password", "forgot", "recovery", "cloudflare",
            "turnstile", "reset password", "cannot login"
        ), 4),
        ("refer_rewards_billing", (
            "refer", "refer and earn", "referral", "earn cash", "rewards",
            "leaderboard", "xp", "points", "payments", "billing", "skill passport", "my projects"
        ), 4),
        ("internship", (
            "internship", "intern", "capstone", "mini project", "projects", "practical experience"
        ), 4),
        ("ccce_syllabus", (
            "ccce", "syllabus", "curriculum", "module", "modules", "5 phases",
            "five phases", "phases", "duration", "how long", "6 month", "3 month", "27", "180"
        ), 4),
        ("courses_beginner", (
            "what course", "which course", "all courses", "courses offer", "courses",
            "course", "programs", "program", "tracks", "beginner", "beginners",
            "fresher", "no coding", "zero background", "coding required", "who can join"
        ), 2),
        ("certificates_career", (
            "certificate", "certification", "credential", "verify certificate", "verify",
            "placement", "job", "jobs", "career", "interview", "resume", "hire"
        ), 4),
        ("fees_pricing", (
            "fee", "fees", "cost", "price", "pricing", "tuition", "how much",
            "scholarship", "discount", "installment", "emi", "payment"
        ), 5),
        ("enrollment", (
            "enroll", "enrol", "admission", "admissions", "join cyberpro", "how to apply", "apply"
        ), 3),
        ("website_pages", (
            "shipping", "shipping policy", "privacy", "privacy policy", "terms",
            "refund", "cancellation", "guidelines", "about page", "contact page", "legal", "pages"
        ), 4),
        ("contact_support", (
            "contact", "whatsapp", "telegram", "phone", "mobile", "number",
            "email", "mail", "reach", "channel", "support", "helpline", "support hours"
        ), 3),
        ("founder_owner", (
            "owner", "founder", "founded", "ceo", "director", "parth", "parth rathod",
            "who owns", "who created", "who made", "who runs", "who started", "established",
            "what is cyberpro", "about cyberpro", "about academy", "about company", "who are you"
        ), 2),
    ]

    # Guard against mistaking "write email" or external company founder questions for CyberPro
    external_org_query = bool(re.search(r"\b(?:owner|founder|ceo|director|founded|started|created)\s+(?:of|at|for)\s+(?!cyberpro\b|this\b|academy\b|website\b)[a-z0-9]", norm))
    non_contact_task = any(k in norm for k in ("write an email", "write email", "draft email", "how does whatsapp", "what is whatsapp", "what is telegram", "prime number", "random number"))

    best_intent = None
    best_score = 0
    for intent_name, terms, base_weight in intent_specs:
        if intent_name == "founder_owner" and external_org_query:
            continue
        if intent_name == "contact_support" and non_contact_task:
            continue
        if intent_name == "courses_beginner" and active_mode == "ethical_hacking":
            continue
        hits = _count_term_hits(norm, terms)
        if hits:
            score = hits * base_weight
            if score > best_score:
                best_score = score
                best_intent = intent_name

    # Also treat standalone "where is X" in cyberpro mode when asking about LMS
    if not best_intent and active_mode == "cyberpro" and _has_any_term(norm, ["lms", "where is", "website", "menu"]):
        best_intent = "website_pages" if "website" in norm or "menu" in norm else "dashboard"

    if best_intent == "dashboard":
        lms = kb.get("lms_instructions", {})
        return (
            f"**Where is the Dashboard ({lms.get('name', 'CYBERPRO Student LMS')})?** 💻\n\n"
            "Your **Student Dashboard** is located inside the **CYBERPRO Student LMS** ([/lms](/lms)).\n\n"
            "**How to Open It:**\n"
            "1. Click **STUDENT LMS** in the top navigation bar (or **ENTER STUDENT LMS** in the hero).\n"
            "2. Sign in under **Student Login** (or **Register** / **Recovery**) after passing the Cloudflare check.\n"
            "3. Inside the left sidebar, click **Dashboard** under **Learning**.\n\n"
            "**Everything Inside Student LMS:**\n"
            "• **Learning:** **Dashboard**, **Live Class** (live sessions), **Recorded Courses** (replays), **Class Schedule**, **Books and Notes**, **Tools & Softwares**, **CyberPro AI**, **Labs**, and **Quizzes**.\n"
            "• **Performance & Rewards:** **Leaderboard**, **Profile and Certificates**, **Refer & Earn**, **Payments**, and **Support**.\n"
            "• **Cyber Ecosystem:** **Cyber Range**, **Skill Passport**, **Bug Bounty Arena**, **SOC Simulator**, **Career & Jobs**, and **My Projects**."
        )

    if best_intent == "live_class":
        return (
            "**Where Live Classes & Batches Happen (Student LMS → Live Class):** 🎥\n\n"
            "• **Where to Join:** Log into **Student LMS (`/lms`)** via **STUDENT LMS** in the top bar and open **Learning → Live Class**.\n"
            "• **Timings Calendar:** Check your upcoming sessions anytime inside **Learning → Class Schedule**.\n"
            "• **Batch Schedule (Live Cohort 03):** Flexible **evening** and **weekend** live batches designed for college students and working professionals.\n"
            f"• **Want to Join a Live Batch?** Click **JOIN CYBERPRO** in the header or message **[WhatsApp ({phone})]({wa_url})**."
        )

    if best_intent == "recorded_courses":
        return (
            "**Recorded Classes & Lifetime Replay Library:** 🎬\n\n"
            "Every live mentor session is recorded and uploaded for review inside your **Student LMS (`/lms`)**:\n"
            "• **Where to Watch:** Open **Student LMS → Learning → Recorded Courses**.\n"
            "• **Access Policy:** CCCE learners get lifetime access to lecture recordings, study guides (**Books and Notes**), and lab walkthroughs so you never fall behind if you miss a live class."
        )

    if best_intent == "lms_labs_study":
        return (
            "**Hands-On Labs, Cyber Range, Books, Tools & Quizzes in Student LMS (`/lms`):** 🧪\n\n"
            "Once logged into **Student LMS**, open these left-sidebar sections directly:\n"
            "• **Labs & Cyber Range (`LIVE LABS`):** Practice safe guided hacking and defense targets in the cloud.\n"
            "• **SOC Simulator & Bug Bounty Arena:** Drill real SIEM alert triage and vulnerability hunting workflows.\n"
            "• **Books and Notes & Tools & Softwares:** Access downloadable PDFs, cheat sheets, and the **180+ tools** stack (`Nmap`, `Wireshark`, `Burp Suite`, `Metasploit`, `Splunk`, `Ghidra`, `Kali Linux`).\n"
            "• **Quizzes:** Test module readiness and earn XP."
        )

    if best_intent == "lms_login_recovery":
        return (
            "**How to Login, Register or Reset Your Student LMS Password:** 🔑\n\n"
            "1. Click **STUDENT LMS** in the top navigation bar (or **ENTER STUDENT LMS** in the hero / **LMS Login** on subpages).\n"
            "2. Complete the Cloudflare human verification check.\n"
            "3. Choose your tab inside the login modal:\n"
            "   • **Student Login:** Sign in with your registered student email & password.\n"
            "   • **Register:** Create a new student account.\n"
            "   • **Recovery:** Reset your forgotten student password.\n\n"
            f"Still locked out? Email **{support_email}** or message **[WhatsApp ({phone})]({wa_url})**."
        )

    if best_intent == "refer_rewards_billing":
        return (
            "**Leaderboard, Refer & Earn, Payments & Projects inside Student LMS (`/lms`):** 🏆\n\n"
            "• **Leaderboard:** Track your cohort rank and lab/quiz XP under **Performance → Leaderboard**.\n"
            "• **Refer & Earn:** Get and share your personal referral code and view reward status under **Profile & Rewards → Refer & Earn**.\n"
            "• **Payments:** View billing/payment info under **Profile & Rewards → Payments**.\n"
            "• **Skill Passport & My Projects:** Showcase completed capstone audits and skills under **Cyber Ecosystem**."
        )

    if best_intent == "internship":
        return (
            "**CYBERPRO Internship, Capstone & Real Projects:** 🚀\n\n"
            "• The flagship **CCCE** track pairs practical instructor training with **My Projects** capstone assessments, real-world pentest & remediation report writing, and **Internship** readiness.\n"
            "• Learners graduate with verified hands-on deliverables stored in their **Skill Passport** (`/lms`) plus mock technical interviews.\n"
            f"• Ask admissions on **[WhatsApp ({phone})]({wa_url})** for upcoming internship cohort timelines."
        )

    if best_intent == "ccce_syllabus":
        return (
            "**CyberPro Certified Cybersecurity Expert (CCCE) — Complete Overview:** 📚\n\n"
            "• **Duration & Structure:** **6 Months Live Practical Training** • **27 Deep-Dive Technical Modules** • **180+ Industry Tools** (see [/ccce](/ccce)).\n"
            "• **5 Progressive Phases (`ccce` Page):**\n"
            "  1. **Foundation:** Networking (TCP/IP, OSI, DNS), Linux CLI, Bash & SOC 101\n"
            "  2. **Practice:** Guided virtual labs, packet analysis (`Wireshark`), scanning (`Nmap`)\n"
            "  3. **Attack:** Web app testing (`OWASP Top 10`, SQLi, XSS, **Burp Suite**)\n"
            "  4. **Defend:** SIEM correlation (**Splunk**), Incident Response & Active Directory hardening\n"
            "  5. **Professional:** Cloud security (AWS/Azure), capstone audits, internship & career prep\n\n"
            "You can explore module folders directly on [/ccce](/ccce)!" + follow_up_suffix
        )

    if best_intent == "courses_beginner":
        return (
            "**CYBERPRO Courses & Beginner Path (Zero Coding Required):** 💡\n\n"
            "Start with our flagship **CCCE (CyberPro Certified Cybersecurity Expert)** pathway on [/programs](/programs) and [/ccce](/ccce):\n\n"
            "• **Built for Beginners:** No prior coding or hacking background needed—we build networking, Linux, and security scripting up from scratch.\n"
            "• **Working Professionals & Freshers:** Includes **27 Modules**, **180+ tools**, **Live Class** + **Recorded Courses**, hands-on cloud labs, mini projects, and weekend practice quizzes."
            + follow_up_suffix
        )

    if best_intent == "certificates_career":
        return (
            "**Verified CCCE Certificate & Career Support:** 🎓\n\n"
            "• **Official Certificate:** Completing labs and capstone projects awards the **CyberPro Certified Cybersecurity Expert (CCCE)** credential, accessible in **Student LMS → Profile and Certificates** and verifiable via cryptographic record ID on our website.\n"
            "• **Career Preparation:** Includes **Career & Jobs** opportunities, **Skill Passport** portfolio, resume review, and mock interviews for **SOC Analyst**, **Junior Pentester**, and **Security Engineer** tracks."
        )

    if best_intent == "fees_pricing":
        return (
            "**CYBERPRO Fees, Scholarships & No-Cost EMI:** 💳\n\n"
            "Tuition varies by cohort schedule, early-bird wave, and merit scholarship eligibility (with flexible monthly **No-Cost EMI** options).\n\n"
            f"To give you accurate current figures rather than guessing, our admissions team sends the official fee sheet directly on **[WhatsApp ({phone})]({wa_url})** (or {admissions_email})."
            + follow_up_suffix
        )

    if best_intent == "enrollment":
        return (
            "**How to Enroll in CYBERPRO Live Cohort 03:** 📝\n\n"
            "1. Review the curriculum on [/ccce](/ccce) or [/programs](/programs).\n"
            "2. Click **JOIN CYBERPRO →** in the top navigation bar to submit your enrollment enquiry, OR\n"
            f"3. Message Admissions directly on **[WhatsApp ({phone})]({wa_url})** (or [/contact](/contact)) to check upcoming evening/weekend seats and scholarships."
            + follow_up_suffix
        )

    if best_intent == "website_pages":
        return (
            "**CYBERPRO Website Navigation & Legal Pages Map:** 🧭\n\n"
            "• **Home:** [/](/)\n"
            "• **Programs / Courses:** [/programs](/programs) • **Flagship CCCE:** [/ccce](/ccce)\n"
            "• **About Us:** [/about](/about) • **Contact Desk:** [/contact](/contact)\n"
            "• **Student LMS & Dashboard:** Click **STUDENT LMS** in top bar (`/lms`)\n"
            "• **Legal Pages (Header 'Legal' menu & Footer):** [Privacy Policy](/privacy-policy/), [Terms](/terms-conditions/), [Refund Policy](/cancellation-refund/), [Shipping Policy](/shipping-policy/), [Guidelines](/guidelines/)"
        )

    if best_intent == "contact_support":
        return (
            "**Contact CYBERPRO Support & Admissions:** 📞\n\n"
            f"• **WhatsApp & Phone:** [{phone}]({wa_url})\n"
            f"• **Telegram Advisory:** [Chat on Telegram]({tg_chat_url}) • [Community Channel]({tg_comm_url})\n"
            f"• **Admissions Email:** {admissions_email}\n"
            f"• **Student Support Email:** {support_email}\n"
            f"• **Availability:** {contact.get('support_hours', '24/7 AI Assistance • Admissions desk responds within 1-2 hours')}"
        )

    if best_intent == "founder_owner":
        if _has_any_term(norm, ["owner", "founder", "ceo", "director", "parth", "who owns", "who created", "who made", "who runs", "who started"]):
            return (
                f"**{owner_name}** is the Founder & Owner of **{company_name}** (established {established}). 🛡️\n\n"
                f"Under **{owner_name}**'s leadership, CYBERPRO builds practical, lab-first cybersecurity training featuring **Live Class** mentorship, **27 CCCE modules**, and **180+ enterprise tools**.\n\n"
                f"• **Direct WhatsApp:** [{phone}]({wa_url})\n"
                f"• **Admissions Email:** {admissions_email}"
            )
        return (
            f"**About {company_name}:** 🛡️\n\n"
            f"Founded by **{owner_name}** (established {established}), CYBERPRO trains aspiring ethical hackers and SOC defenders through live instructor mentoring, hands-on virtual labs (**180+ tools**), our 27-module **CCCE** flagship program, and the interactive **Student LMS (`/lms`)**."
        )

    # Dynamic KB FAQ fallback
    faq_answer = _match_kb_faq(norm, kb)
    if faq_answer and faq_answer[:40].lower() not in prior_ai:
        return faq_answer

    # Contextual Follow-Up Replies
    if prior_history and _has_any_term(norm, [
        "yes", "yeah", "yep", "sure", "ok", "okay", "tell me more", "more", "details", "go ahead", "next"
    ]):
        if "background" in last_ai or "college student" in last_ai:
            return (
                "Got it! Whether you're in college or working full-time, our **evening & weekend batches** happen in **Student LMS → Live Class**, and every lecture stays recorded under **Recorded Courses**.\n\n"
                f"Would you like **fees/scholarships**, the **CCCE syllabus**, or WhatsApp guidance at **[{phone}]({wa_url})**?"
            )
        if "syllabus" in last_ai or "ccce" in last_ai or "phase" in last_ai:
            return (
                "Here's how learners progress inside the **CCCE** track:\n\n"
                "• **Phase 1–2 (Foundation & Practice):** Networking, Linux CLI, Wireshark & Nmap labs.\n"
                "• **Phase 3–4 (Attack & Defend):** OWASP Top 10, Burp Suite, Splunk SIEM & Active Directory hardening.\n"
                "• **Phase 5 (Professional):** Cloud security (AWS/Azure), capstone project audits, internship & career prep."
            )
        if "dashboard" in last_ai or "student lms" in last_ai:
            return (
                "Inside **Student LMS (`/lms`)**, use the left sidebar to switch directly between **Dashboard**, **Live Class**, **Recorded Courses**, **Labs**, **Quizzes**, **Books and Notes**, **Leaderboard**, and **Refer & Earn**."
            )

    return None


def _mode_boundary_reply(active_mode: str, user_msg: str, phone: str, wa_url: str) -> str:
    topic = user_msg.strip()[:70]
    if active_mode == "cyberpro":
        return (
            f"I'm currently in **CyberPro Mode**, so I help **strictly with CYBERPRO Defense Academy** (`{topic}` appears outside CYBERPRO topics).\n\n"
            "Try asking specifically:\n"
            "• **\"Where is the dashboard?\"** or **\"Where do live classes happen?\"**\n"
            "• **\"Where are recorded lectures, labs, books, or Refer & Earn?\"**\n"
            "• **\"What are the CCCE 5 phases, 27 modules, fees, or certificate details?\"**\n\n"
            "For outside knowledge, ethical hacking concepts, or programming, select **Ethical Hacking**, **Worldwide**, or **Code** below!"
        )
    if active_mode == "ethical_hacking":
        return (
            f"I'm currently in **Ethical Hacking Mode**, so my thinking stays **strictly on Ethical Hacking, authorized penetration testing, and cybersecurity defense** (`{topic}` is outside cybersecurity).\n\n"
            "Ask me specific topics like:\n"
            "• **\"Explain SQL Injection / XSS / IDOR / CSRF / SSRF\"**\n"
            "• **\"How does Nmap / Wireshark / Burp Suite work?\"**\n"
            "• **\"5 phases of ethical hacking\"**, **\"Privilege escalation\"**, **\"Active Directory defense\"**, or **\"SOC SIEM triage\"**\n\n"
            "Switch to **CyberPro**, **Worldwide**, or **Code** below for non-security questions!"
        )
    if active_mode == "code":
        return (
            f"I'm currently in **Code Mode**, so I focus **strictly on programming, algorithms, debugging, and secure coding** (`{topic}` doesn't look like a coding topic).\n\n"
            "Try asking:\n"
            "• *\"Write Python Fibonacci / factorial / recursion / binary search\"*\n"
            "• *\"Safe parameterized SQL query\"*, *\"SQL INNER vs LEFT JOIN\"*, or *\"JavaScript async fetch\"*\n"
            "• *\"Python list vs tuple\"*, *\"FastAPI endpoint\"*, or *\"Essential Git commands\"*\n\n"
            "Or switch to **CyberPro**, **Ethical Hacking**, or **Worldwide** below!"
        )
    return (
        f"Here's how I can help with **{topic}** (**Worldwide**):\n\n"
        "• **Worldwide Facts & Comparisons:** Ask *\"What is [any history, science, country, university, or tech topic]?\"* or *\"Compare X vs Y\"*\n"
        "• **Math, Quotes, Dates & Writing:** Ask calculations, unit conversions, quotes, or drafts\n"
        "• **Specialized Modes Below:** Switch anytime between **CyberPro**, **Ethical Hacking**, **Worldwide**, and **Code**!"
    )


def _finalize_non_repeating_reply(candidate: str, prior_history: List[Dict[str, str]], active_mode: str, user_msg: str, phone: str, wa_url: str) -> str:
    last_raw = _last_assistant_message(prior_history).strip()
    if last_raw and candidate.strip() == last_raw:
        if active_mode == "cyberpro":
            return (
                f"To make sure I give you the exact CYBERPRO detail you want on *\"{user_msg.strip()[:60]}\"* — which area should we inspect next?\n\n"
                "• **Dashboard & LMS Navigation:** Ask *\"Where is live class / recordings / labs / quizzes / Refer & Earn?\"*\n"
                "• **Curriculum & Program:** Ask *\"CCCE 5 phases & 27 modules\"*, *\"Internship\"*, or *\"Certificates\"*\n"
                f"• **Admissions & Login:** Ask *\"Fees & EMI\"*, *\"How to login/recover password\"*, or WhatsApp **[{phone}]({wa_url})**"
            )
        if active_mode == "ethical_hacking":
            return (
                f"Let's go deeper on *\"{user_msg.strip()[:60]}\"* in **Ethical Hacking Mode** — tell me the exact technique, tool, or vulnerability (for example **SQLi**, **XSS**, **IDOR**, **Nmap**, **Burp Suite**, **Wireshark**, **Privilege Escalation**, or **SOC triage**), and I'll break down both how testers verify it safely in labs and how defenders patch it."
            )
        if active_mode == "code":
            return (
                f"Let's zero in on the exact code you need for *\"{user_msg.strip()[:60]}\"* — tell me the language (**Python**, **JavaScript**, **SQL**, **Bash**, **HTML/CSS**) and goal, and I'll write or explain a clean implementation."
            )
    return candidate


def built_in_inference_engine(user_msg: str, history: List[Dict[str, str]], kb: Dict[str, Any], profile: Dict[str, Any], mode: str = "worldwide") -> str:
    active_mode = normalize_ai_mode(mode)
    norm = _normalize_cyber_msg(user_msg)
    prior_history = _trim_trailing_duplicate_user(history, user_msg)
    prior_ai = _recent_assistant_text(prior_history)

    company = kb.get("company", {})
    owner_name = company.get("founder_and_owner", "Parth Rathod")
    contact = kb.get("contact", {})
    phone = contact.get("phone", "+91 76668762325")
    wa_url = contact.get("whatsapp_chat_url", f"https://wa.me/{phone.replace('+', '').replace(' ', '')}")
    tg_chat_url = contact.get("telegram_chat_url", "https://t.me/+917666872325")

    # 1. Ethical / Illegal Hacking Guardrail (Always highest priority safety check across all modes)
    illegal_triggers = [
        "hack wifi", "hack instagram", "hack facebook", "hack whatsapp", "hack snapchat",
        "hack phone", "hack account", "ddos script", "steal password", "crack account",
        "bypass otp", "phishing kit", "keylogger", "ransomware attack"
    ]
    if any(trigger in norm for trigger in illegal_triggers):
        return (
            "**Notice:** CYBERPRO Defense Academy strictly adheres to ethical hacking, authorized security testing, and legal compliance.\n\n"
            "We do **not** assist with unauthorized penetration or malicious attacks against personal accounts or systems.\n\n"
            "Instead, we train you in **defensive security, vulnerability remediation, and authorized penetration testing** to legally protect systems and build verified careers.\n\n"
            "Would you like to explore legitimate defensive methodologies or our **CCCE program**?"
        )

    # 2. Lead / Contact Details Provided by Visitor (Phone or Email)
    has_email = bool(re.search(r"[\w\.-]+@[\w\.-]+\.\w+", user_msg))
    has_phone = bool(re.search(r"(\+?\d{1,4}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}|\b\d{10}\b", user_msg))
    if has_email or has_phone:
        name_str = f" {profile['full_name']}" if profile.get("full_name") and profile["full_name"] != "Visitor" else ""
        return (
            f"Thank you{name_str}! 🛡️ Your details have been securely recorded in our admissions system.\n\n"
            f"Senior counselor **{owner_name}** and our admissions desk will reach out to you on WhatsApp ({profile.get('phone', phone)}) "
            f"with the complete **CCCE syllabus PDF**, cohort batch schedule, and scholarship details.\n\n"
            f"For immediate assistance, you can also chat directly right now: **[WhatsApp ({phone})]({wa_url})**."
        )

    # 3. Human Advisor / Call Back Request
    if any(k in norm for k in [
        "talk to human", "speak to real person", "real person", "call me",
        "speak to advisor", "speak to counselor", "talk to admissions", "human support"
    ]):
        if profile.get("phone") or profile.get("email"):
            return (
                f"I've alerted our admissions desk for you! 📞\n\n"
                f"A team member will reach out at **{profile.get('phone', phone)}**.\n\n"
                f"You can also message **{owner_name}** directly here: **[Chat on WhatsApp]({wa_url})** or **[Telegram]({tg_chat_url})**."
            )
        return (
            "I'd be happy to connect you with a human advisor! 📞\n\n"
            "Share your **name** and **WhatsApp number** here, or message us directly anytime:\n"
            f"• **WhatsApp Admissions:** [{phone}]({wa_url})\n"
            f"• **Telegram Advisory:** [Chat on Telegram]({tg_chat_url})"
        )

    # =========================================================================
    # MODE-STRICT ROUTING BRANCHES
    # =========================================================================

    # A. STRICT CYBERPRO MODE (CYBERPRO website, Student LMS, Dashboard, Live Class, CCCE ONLY)
    if active_mode == "cyberpro":
        cp_ans = _answer_cyberpro_query(norm, user_msg, history, kb, profile, active_mode)
        if cp_ans:
            return _finalize_non_repeating_reply(cp_ans, prior_history, active_mode, user_msg, phone, wa_url)
        if _has_any_term(norm, ["hi", "hello", "hey", "hii", "hlo", "namaste", "good morning", "good afternoon", "good evening"]):
            return (
                "Hello! 👋 I'm in **CyberPro Mode** — strictly focused on **CYBERPRO Defense Academy**.\n\n"
                "Ask me anything specifically about:\n"
                "• **Dashboard & Live Class:** Where the Dashboard, Live Class, Recorded Courses, Labs & Quizzes are in Student LMS (`/lms`)\n"
                "• **CCCE Program:** 5 phases, 27 modules, 180+ tools, internship & certificates\n"
                "• **Admissions & Website:** Fees/EMI, Student Login/Recovery, Contact, and Founder **Parth Rathod**"
            )
        if _has_any_term(norm, ["thanks", "thank you", "thx", "bye", "goodbye", "helpful", "nice"]):
            return f"You're very welcome! 🛡️ Reach our team anytime on **[WhatsApp ({phone})]({wa_url})** or open **STUDENT LMS** from the top menu."
        return _finalize_non_repeating_reply(_mode_boundary_reply("cyberpro", user_msg, phone, wa_url), prior_history, active_mode, user_msg, phone, wa_url)

    # B. STRICT ETHICAL HACKING MODE (Ethical Hacking & Security Defense Thinking ONLY)
    if active_mode == "ethical_hacking":
        if _has_any_term(norm, [
            "learn ethical hacking", "how to learn ethical hacking", "start ethical hacking",
            "become ethical hacker", "ethical hacking roadmap", "ethical hacking", "how should i start"
        ]) and not _match_best_concept(norm):
            roadmap = (
                "**Ethical Hacking Mindset & Practical Study Roadmap (Authorized Only):** ⚔️\n\n"
                "1. **Networking & Linux Core:** Master TCP/IP, OSI, DNS, HTTP/TLS, packet capture (**Wireshark**), and Linux CLI + Bash.\n"
                "2. **Reconnaissance & Scanning Methodology:** Learn OSINT, target surface enumeration, and **Nmap** service fingerprinting strictly in authorized lab scopes.\n"
                "3. **Web App Security (OWASP Top 10):** Practice SQLi, XSS, IDOR, CSRF, and SSRF verification with **Burp Suite** alongside server-side remediation.\n"
                "4. **System & Active Directory Pentesting:** Study privilege escalation paths, Kerberos/AD hardening, and safe exploitation validation.\n"
                "5. **Defensive Reporting & Blue Team:** Document CVSS severity, root-cause patches, and SIEM detection indicators (`Splunk`)."
            )
            return _finalize_non_repeating_reply(roadmap, prior_history, active_mode, user_msg, phone, wa_url)

        concept_ans = _match_best_concept(norm)
        if concept_ans:
            return _finalize_non_repeating_reply(concept_ans + _mode_enrichment("ethical_hacking"), prior_history, active_mode, user_msg, phone, wa_url)

        sec_code = universal_ai_engine.try_coding_assistant(user_msg, "ethical_hacking")
        if sec_code:
            return _finalize_non_repeating_reply(sec_code, prior_history, active_mode, user_msg, phone, wa_url)

        sec_world = universal_ai_engine.try_world_knowledge(user_msg, "ethical_hacking", mode_enrichment=_mode_enrichment("ethical_hacking"))
        if sec_world:
            return _finalize_non_repeating_reply(sec_world, prior_history, active_mode, user_msg, phone, wa_url)

        if _has_any_term(norm, ["hi", "hello", "hey", "hii", "hlo", "namaste", "good morning", "good afternoon", "good evening"]):
            return (
                "Hello! ⚔️ I'm in **Ethical Hacking Mode** — thinking strictly from an ethical hacking, authorized pentesting, and defensive security perspective.\n\n"
                "Ask about **SQLi**, **XSS**, **CSRF**, **SSRF**, **IDOR**, **Nmap**, **Wireshark**, **Burp Suite**, **Privilege Escalation**, **Active Directory**, or **SOC triage**!"
            )
        if _has_any_term(norm, ["thanks", "thank you", "thx", "bye", "goodbye", "helpful", "nice"]):
            return "Glad that helped! ⚔️ Always test ethically, stay strictly inside authorized lab scope, and verify patches thoroughly."
        return _finalize_non_repeating_reply(_mode_boundary_reply("ethical_hacking", user_msg, phone, wa_url), prior_history, active_mode, user_msg, phone, wa_url)

    # C. STRICT CODE MODE (Programming, Debugging, Algorithms & Secure Code ONLY)
    if active_mode == "code":
        code_res = universal_ai_engine.try_coding_assistant(user_msg, "code")
        if code_res:
            return _finalize_non_repeating_reply(code_res, prior_history, active_mode, user_msg, phone, wa_url)

        math_res = universal_ai_engine.try_math_or_units(user_msg)
        if math_res:
            return _finalize_non_repeating_reply(math_res, prior_history, active_mode, user_msg, phone, wa_url)

        code_world = universal_ai_engine.try_world_knowledge(user_msg, "code", mode_enrichment=_mode_enrichment("code"))
        if code_world:
            return _finalize_non_repeating_reply(code_world, prior_history, active_mode, user_msg, phone, wa_url)

        if _has_any_term(norm, ["sql injection", "sqli", "xss", "owasp", "jwt", "hashing", "scrypt", "bcrypt", "csp", "cors"]):
            sec_concept = _match_best_concept(norm)
            if sec_concept:
                return _finalize_non_repeating_reply(sec_concept + _mode_enrichment("code"), prior_history, active_mode, user_msg, phone, wa_url)

        if _has_any_term(norm, ["hi", "hello", "hey", "hii", "hlo", "namaste"]):
            return (
                "Hello! </> I'm in **Code Mode** — ready for Python, JavaScript, SQL, Bash, HTML/CSS, algorithms, Git, Docker, and secure coding reviews.\n\n"
                "Ask me to write a function, explain a pattern, or audit code safety!"
            )
        if _has_any_term(norm, ["thanks", "thank you", "thx", "bye", "goodbye", "helpful", "nice"]):
            return "Happy coding! </> Always validate inputs, handle edge-case errors cleanly, and test after every change."
        return _finalize_non_repeating_reply(_mode_boundary_reply("code", user_msg, phone, wa_url), prior_history, active_mode, user_msg, phone, wa_url)

    # D. WORLDWIDE MODE (Universal Q&A + CyberPro + Coding + Ethical Concepts)
    for helper in (
        universal_ai_engine.try_datetime_query,
        universal_ai_engine.try_math_or_units,
        universal_ai_engine.try_quotes_query,
        universal_ai_engine.try_writing_assistant,
        universal_ai_engine.try_comparison_query,
    ):
        u_res = helper(user_msg)
        if u_res:
            return _finalize_non_repeating_reply(u_res, prior_history, active_mode, user_msg, phone, wa_url)

    code_res = universal_ai_engine.try_coding_assistant(user_msg, active_mode)
    if code_res:
        return _finalize_non_repeating_reply(code_res, prior_history, active_mode, user_msg, phone, wa_url)

    # Official CyberPro queries when visitor asks about CyberPro / CCCE / Student LMS in Worldwide mode
    cp_ans = _answer_cyberpro_query(norm, user_msg, history, kb, profile, active_mode)
    if cp_ans:
        return _finalize_non_repeating_reply(cp_ans, prior_history, active_mode, user_msg, phone, wa_url)

    concept_ans = _match_best_concept(norm)
    if concept_ans:
        return _finalize_non_repeating_reply(
            concept_ans + "\n\nWant to explore more security concepts in **Ethical Hacking** mode or practice in CCCE hands-on labs?",
            prior_history,
            active_mode,
            user_msg,
            phone,
            wa_url,
        )

    world_res = universal_ai_engine.try_world_knowledge(user_msg, active_mode, mode_enrichment="")
    if world_res:
        return _finalize_non_repeating_reply(world_res, prior_history, active_mode, user_msg, phone, wa_url)

    if _has_any_term(norm, ["hi", "hello", "hey", "hii", "hlo", "namaste", "good morning", "good afternoon", "good evening", "how are you"]):
        if "happy to help" in prior_ai or "hello" in prior_ai:
            return (
                "Hello again! 👋 Choose any option below (**CyberPro**, **Ethical Hacking**, **Worldwide**, or **Code**) or ask your exact question!"
            )
        return (
            "Hello! 👋 I'm **CyberPro AI**.\n\n"
            "Switch modes anytime using the options below:\n"
            "• **CyberPro:** Strictly CYBERPRO website, Student LMS Dashboard, Live Classes & CCCE\n"
            "• **Ethical Hacking:** Strictly ethical hacking methodology, OWASP & cyber defense\n"
            "• **Worldwide:** General knowledge, science, history, math, quotes & global Q&A\n"
            "• **Code:** Python, JavaScript, SQL, Bash, algorithms & debugging\n\n"
            "How can I help you today?"
        )

    if _has_any_term(norm, ["thanks", "thank you", "thx", "bye", "goodbye", "helpful", "nice"]):
        return (
            f"You're very welcome! 🛡️ Feel free to message anytime—or reach CYBERPRO Admissions on **[WhatsApp ({phone})]({wa_url})**."
        )

    if _has_any_term(norm, ["joke", "funny"]):
        return "Why did the firewall go to therapy? Because it had too many unresolved ports and trust issues! 🛡️ Ask any worldwide question, coding challenge, or CYBERPRO topic next!"

    fallback_primary = _mode_boundary_reply("worldwide", user_msg, phone, wa_url)
    if "worldwide facts" not in prior_ai:
        return fallback_primary

    return (
        f"Let's make sure you get the exact answer you need right away! 💬\n\n"
        f"Try selecting **CyberPro**, **Ethical Hacking**, **Worldwide**, or **Code** below and asking 2–5 clear keywords — or reach CYBERPRO Admissions on **[WhatsApp ({phone})]({wa_url})** ({admissions_email})."
    )


# -----------------------------------------------------------------------------
# MASTER CHAT DISPATCHER WITH CRM SYNCHRONIZATION
# -----------------------------------------------------------------------------

def process_chat_message(user_msg: str, history: List[Dict[str, str]], client_ip: str, session_id: str = "", mode: str = "worldwide") -> Dict[str, Any]:
    cfg = load_config()
    active_mode = normalize_ai_mode(mode)
    mode_meta = AI_MODES[active_mode]

    if not cfg.get("enabled", True):
        return {
            "ok": False,
            "error": "The CYBERPRO AI Assistant is temporarily undergoing routine maintenance. Please contact admissions on WhatsApp at +91 76668762325.",
            "status": "maintenance"
        }

    # 1. Rate Limiting Check
    rate_limit = int(cfg.get("rate_limit_per_minute", 25))
    if is_rate_limited(client_ip, limit=rate_limit):
        return {
            "ok": False,
            "error": "You are sending messages too quickly. Please pause for a few seconds before trying again.",
            "status": "rate_limited"
        }

    # 2. Input Sanitization
    max_len = int(cfg.get("max_input_length", 1500))
    clean_msg = sanitize_input(user_msg, max_len)
    if not clean_msg:
        return {
            "ok": False,
            "error": "Message cannot be empty.",
            "status": "empty_input"
        }

    # 3. Load or Retrieve Customer CRM Profile
    profile = {}
    if session_id:
        with get_db() as con:
            row = con.execute("SELECT * FROM customer_enquiries WHERE session_id=?", (session_id,)).fetchone()
            if row:
                profile = dict(row)

    # 4. Extract Entities & Progressive Profiling
    profile = extract_customer_entities(clean_msg, profile)

    kb = load_knowledge_base()
    system_prompt = build_system_prompt(kb, profile, mode=active_mode)

    provider = cfg.get("provider", "auto").lower()
    gemini_key = cfg.get("gemini_api_key", "").strip()
    openai_key = cfg.get("openai_api_key", "").strip()

    reply_text = ""
    used_provider = "built-in"

    # Attempt Gemini API if configured
    if (provider in ("auto", "gemini")) and gemini_key:
        model = cfg.get("gemini_model", "gemini-1.5-flash")
        success, res = call_gemini_api(gemini_key, model, system_prompt, history, clean_msg)
        if success and res.strip():
            reply_text = res.strip()
            used_provider = f"gemini ({model})"

    # Attempt OpenAI API if configured
    if not reply_text and (provider in ("auto", "openai")) and openai_key:
        model = cfg.get("openai_model", "gpt-4o-mini")
        success, res = call_openai_api(openai_key, model, system_prompt, history, clean_msg)
        if success and res.strip():
            reply_text = res.strip()
            used_provider = f"openai ({model})"

    # Fallback to Built-in Contextual Inference Engine
    if not reply_text:
        reply_text = built_in_inference_engine(clean_msg, history, kb, profile, mode=active_mode)
        used_provider = "cyberpro-neural-crm"

    # 5. Synchronize with Database CRM
    updated_history = list(history)
    updated_history.append({"role": "user", "content": clean_msg})
    updated_history.append({"role": "assistant", "content": reply_text})

    try:
        profile = sync_crm_lead(session_id or f"sess_{int(time.time()*1000)}", profile, updated_history)
    except Exception as e:
        print("CRM sync error:", e)

    return {
        "ok": True,
        "reply": reply_text,
        "provider": used_provider,
        "mode": active_mode,
        "mode_label": mode_meta["label"],
        "timestamp": int(time.time()),
        "lead_captured": bool(profile.get("phone") or profile.get("email")),
        "lead_score": profile.get("lead_score", 50),
        "customer_id": profile.get("id")
    }
