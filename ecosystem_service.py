"""
CYBERPRO ADVANCED CYBERSECURITY ECOSYSTEM SERVICE
Encapsulates complete business logic, validations, database interactions,
and integrations for:
1. Cloud Cyber Range (Lab execution & terminal emulation)
2. CyberPro Skill Passport (Portfolio, 3-tier skills, public verification & QR)
3. Bug Bounty Training Arena (Challenges, vulnerable testbeds & reports)
4. SOC Analyst Simulator (SIEM telemetry, investigations & automatic debrief)
5. Employer Hiring Portal (Job postings, candidate discovery & applications)
6. Project Evaluation System (Project submissions & 6-criteria rubric grading)
"""

import sqlite3
import json
import os
import re
import time
import secrets
from datetime import datetime, timedelta
from pathlib import Path
import runtime_paths

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = str(runtime_paths.resolve_db_path(Path(ROOT_DIR)))

def get_db():
    con = sqlite3.connect(runtime_paths.resolve_db_path(Path(ROOT_DIR)))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON;")
    return con

# =============================================================================
# 1. CLOUD CYBER RANGE MODULE
# =============================================================================

def get_cyber_range_labs(student_id=None, category=None):
    con = get_db()
    query = "SELECT * FROM cyber_range_labs WHERE is_active=1"
    params = []
    if category and category != 'All':
        query += " AND category = ?"
        params.append(category)
    query += " ORDER BY id ASC"
    rows = con.execute(query, params).fetchall()

    labs = []
    for r in rows:
        lab = dict(r)
        lab['objectives'] = json.loads(lab['objectives_json'] or '[]')
        lab['hints'] = json.loads(lab['hints_json'] or '[]')
        del lab['flag'] # Never expose flag directly to client
        del lab['initial_fs_json']

        # Check student session status if student_id provided
        if student_id:
            sess = con.execute("""
            SELECT id, session_token, status, started_at, expires_at, completed_at, score_awarded
            FROM cyber_range_sessions 
            WHERE student_id=? AND lab_id=? 
            ORDER BY id DESC LIMIT 1
            """, (student_id, lab['id'])).fetchone()

            if sess:
                lab['student_status'] = sess['status']
                lab['session_token'] = sess['session_token']
                lab['expires_at'] = sess['expires_at']
                lab['score_awarded'] = sess['score_awarded']
                # Check expiration
                if sess['status'] == 'running':
                    exp_dt = datetime.fromisoformat(sess['expires_at'].replace('Z', ''))
                    if datetime.utcnow() > exp_dt:
                        con.execute("UPDATE cyber_range_sessions SET status='expired' WHERE id=?", (sess['id'],))
                        con.commit()
                        lab['student_status'] = 'expired'
            else:
                lab['student_status'] = 'not_started'
                lab['session_token'] = None
                lab['expires_at'] = None
                lab['score_awarded'] = 0
        else:
            lab['student_status'] = 'not_started'

        labs.append(lab)
    con.close()
    return labs

def start_cyber_range_lab(student_id, lab_id):
    con = get_db()
    lab = con.execute("SELECT * FROM cyber_range_labs WHERE id=? AND is_active=1", (lab_id,)).fetchone()
    if not lab:
        con.close()
        return {'success': False, 'error': 'Lab not found or inactive.'}

    # Expire any previous running sessions for this student on other labs
    con.execute("""
    UPDATE cyber_range_sessions SET status='stopped' 
    WHERE student_id=? AND status='running' AND lab_id!=?
    """, (student_id, lab_id))

    # Check existing active session for this lab
    existing = con.execute("""
    SELECT * FROM cyber_range_sessions WHERE student_id=? AND lab_id=? AND status='running'
    """, (student_id, lab_id)).fetchone()

    if existing:
        exp_dt = datetime.fromisoformat(existing['expires_at'].replace('Z', ''))
        if datetime.utcnow() < exp_dt:
            con.close()
            return {
                'success': True,
                'message': 'Resumed active lab session.',
                'session_token': existing['session_token'],
                'expires_at': existing['expires_at'],
                'lab_title': lab['title'],
                'category': lab['category'],
                'duration_minutes': lab['duration_minutes']
            }
        else:
            con.execute("UPDATE cyber_range_sessions SET status='expired' WHERE id=?", (existing['id'],))

    # Create new session
    token = f"CR-SESS-{secrets.token_hex(8)}"
    duration = lab['duration_minutes'] or 60
    now = datetime.utcnow()
    expires_at = (now + timedelta(minutes=duration)).isoformat() + 'Z' 

    con.execute("""
    INSERT INTO cyber_range_sessions 
    (session_token, student_id, lab_id, status, started_at, expires_at, fs_state_json)
    VALUES (?, ?, ?, 'running', ?, ?, ?)
    """, (token, student_id, lab_id, now.isoformat(), expires_at, lab['initial_fs_json'] or '{}'))
    con.commit()
    con.close()

    return {
        'success': True,
        'message': f"Lab '{lab['title']}' started successfully.",
        'session_token': token,
        'expires_at': expires_at,
        'duration_minutes': duration,
        'lab_title': lab['title'],
        'category': lab['category'],
        'objectives': json.loads(lab['objectives_json'] or '[]'),
        'hints': json.loads(lab['hints_json'] or '[]')
    }

def stop_cyber_range_lab(student_id, session_token):
    con = get_db()
    sess = con.execute("SELECT * FROM cyber_range_sessions WHERE session_token=? AND student_id=?", (session_token, student_id)).fetchone()
    if not sess:
        con.close()
        return {'success': False, 'error': 'Session not found.'}

    con.execute("UPDATE cyber_range_sessions SET status='stopped' WHERE id=?", (sess['id'],))
    con.commit()
    con.close()
    return {'success': True, 'message': 'Lab session stopped.'}

def reset_cyber_range_lab(student_id, lab_id):
    con = get_db()
    lab = con.execute("SELECT * FROM cyber_range_labs WHERE id=?", (lab_id,)).fetchone()
    if not lab:
        con.close()
        return {'success': False, 'error': 'Lab not found.'}

    con.execute("UPDATE cyber_range_sessions SET status='stopped' WHERE student_id=? AND lab_id=?", (student_id, lab_id))
    con.commit()
    con.close()
    return start_cyber_range_lab(student_id, lab_id)

def execute_cyber_range_terminal(student_id, session_token, command):
    con = get_db()
    sess = con.execute("""
    SELECT s.*, l.title as lab_title, l.flag as lab_flag, l.xp_reward, l.category
    FROM cyber_range_sessions s
    JOIN cyber_range_labs l ON s.lab_id = l.id
    WHERE s.session_token=? AND s.student_id=?
    """, (session_token, student_id)).fetchone()

    if not sess:
        con.close()
        return {'output': 'Session expired or not found. Please restart the lab.', 'error': True}

    if sess['status'] != 'running':
        con.close()
        return {'output': f"Lab is currently {sess['status']}. Start or restart to access terminal.", 'error': True}

    cmd = (command or '').strip()
    history = json.loads(sess['command_history_json'] or '[]')
    history.append(cmd)

    fs = json.loads(sess['fs_state_json'] or '{}')
    cwd = sess['current_directory'] or '/home/student'

    output = ""
    prompt = f"cadet@cyberpro-range:{cwd}$ "

    # Command interpretation
    parts = cmd.split()
    base_cmd = parts[0].lower() if parts else ""
    args = parts[1:] if len(parts) > 1 else []

    if not cmd:
        output = ""
    elif base_cmd == "clear":
        output = "\033[2J\033[H"
    elif base_cmd == "pwd":
        output = cwd
    elif base_cmd == "whoami":
        output = "cadet (uid=1001, gid=1001)"
    elif base_cmd == "id":
        output = "uid=1001(cadet) gid=1001(cadet) groups=1001(cadet),27(sudo-restricted)"
    elif base_cmd == "hostname":
        output = "cyberpro-lab-target-01"
    elif base_cmd == "uname":
        output = "Linux cyberpro-lab-target-01 6.6.14-cyberpro-hardened-amd64 #1 SMP PREEMPT_DYNAMIC x86_64 GNU/Linux"
    elif base_cmd == "ls":
        # list cwd contents
        curr_files = fs.get(cwd, {})
        if not curr_files:
            output = ""
        else:
            lines = []
            for name, content in curr_files.items():
                if name.endswith('.txt') or name.endswith('.json') or name.endswith('.pcap'):
                    lines.append(f"-rw-r--r-- 1 cadet cadet  {len(content)} Nov 09 21:00 {name}")
                else:
                    lines.append(f"-rwsr-xr-x 1 root  root   48192 Nov 09 21:00 \033[31m{name}\033[0m")
            output = "\n".join(lines)
    elif base_cmd == "cat":
        if not args:
            output = "cat: missing file operand"
        else:
            filename = args[0]
            curr_files = fs.get(cwd, {})
            # check root files if target
            if filename.startswith("/root/") and "suid" in sess['lab_title'].lower():
                output = sess['lab_flag']
            elif filename in curr_files:
                output = curr_files[filename]
            else:
                output = f"cat: {filename}: No such file or directory"
    elif base_cmd == "help":
        output = (
            "CyberPro Linux Terminal v4.2 [Isolated Range Pod]\n"
            "Supported commands:\n"
            "  ls, pwd, cd, cat, grep, find, whoami, id, hostname, uname, ps, ping,\n"
            "  nmap, nc, curl, aircrack-ng, volatility, submit_flag, help, clear\n\n"
            "Submit challenge flag using: submit_flag CYBERPRO{...}"
        )
    elif base_cmd == "submit_flag":
        if not args:
            output = "Usage: submit_flag CYBERPRO{your_flag_token}"
        else:
            submitted = args[0].strip()
            if submitted == sess['lab_flag']:
                con.execute("""
                UPDATE cyber_range_sessions 
                SET status='completed', completed_at=?, flag_submitted=?, score_awarded=?
                WHERE id=?
                """, (datetime.utcnow().isoformat(), submitted, sess['xp_reward'], sess['id']))

                # Award Skill to Skill Passport
                con.execute("""
                INSERT OR REPLACE INTO skill_passport_skills 
                (student_id, skill_name, category, level, source_module, source_reference_id)
                VALUES (?, ?, ?, 'practiced', 'cyber_range', ?)
                """, (student_id, sess['lab_title'], sess['category'], str(sess['lab_id'])))

                con.commit()
                output = (
                    f"\033[32m[✓] FLAG ACCEPTED! CHALLENGE SOLVED!\033[0m\n"
                    f"Earned +{sess['xp_reward']} XP Points!\n"
                    f"Practical lab achievement recorded in your CYBERPRO SKILL PASSPORT."
                )
            else:
                output = "\033[31m[✗] INCORRECT FLAG. Double check your findings and try again.\033[0m"
    elif base_cmd == "find":
        if "/usr/local/bin" in cmd or "-u=s" in cmd or "-perm" in cmd:
            output = (
                "/usr/bin/passwd\n"
                "/usr/bin/sudo\n"
                "/usr/local/bin/backup_agent\n"
                "/usr/bin/chfn"
            )
        else:
            output = "./notes.txt\n./target_info.json"
    elif base_cmd == "ps":
        output = (
            "PID   TTY          TIME CMD\n"
            "    1 ?        00:00:01 systemd\n"
            " 1042 ?        00:00:00 sshd\n"
            " 4120 ?        00:00:02 backup_agent (SUID: root)\n"
            " 6802 pts/0    00:00:00 bash\n"
            " 7120 pts/0    00:00:00 ps"
        )
    elif base_cmd == "ping":
        target = args[0] if args else "127.0.0.1"
        output = (
            f"PING {target} ({target}) 56(84) bytes of data.\n"
            f"64 bytes from {target}: icmp_seq=1 ttl=64 time=0.412 ms\n"
            f"64 bytes from {target}: icmp_seq=2 ttl=64 time=0.388 ms\n"
            f"--- {target} ping statistics ---\n"
            f"2 packets transmitted, 2 received, 0% packet loss, time 1001ms"
        )
    elif base_cmd == "nmap":
        target = args[-1] if args else "10.10.20.10"
        output = (
            f"Starting Nmap 7.94 ( https://nmap.org ) at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"Nmap scan report for {target}\n"
            f"Host is up (0.0012s latency).\n"
            f"PORT     STATE SERVICE     VERSION\n"
            f"22/tcp   open  ssh         OpenSSH 8.9p1 Ubuntu (protocol 2.0)\n"
            f"80/tcp   open  http        Apache httpd 2.4.52\n"
            f"8080/tcp open  http-proxy  Squid http proxy 5.7\n"
            f"9999/tcp open  abyss?      CyberPro Admin Management Protocol v2\n"
            f"Service Info: OS: Linux; CPE: cpe:/o:linux:linux_kernel\n\n"
            f"Nmap done: 1 IP address (1 host up) scanned in 2.14 seconds"
        )
    elif base_cmd in ["nc", "curl"]:
        if "9999" in cmd or "10.10.20.10:9999" in cmd or "portal" in cmd:
            output = (
                "CONNECTED TO CYBERPRO MAINTENANCE SERVICE [PORT 9999]\n"
                "AUTHENTICATION BYPASS DETECTED.\n"
                f"FLAG: {sess['lab_flag']}"
            )
        else:
            output = "HTTP/1.1 200 OK\nServer: CyberPro-Edge/1.0\nContent-Type: text/plain\n\nWelcome to Target Perimeter Service."
    elif base_cmd == "aircrack-ng":
        output = (
            "Reading packets, please wait...\n"
            "Opening wifi_corp.pcap\n"
            "Read 1420 packets.\n\n"
            "   #  BSSID              ESSID                     Encryption\n"
            "   1  00:14:6C:7E:40:80  CYBERPRO_CORP_WIFI        WPA (1 handshake)\n\n"
            "Choosing first network as target.\n"
            "Opening wordlist cyberpro_wordlist.txt\n"
            "KEY FOUND! [ CyberProDefense@2026 ]\n\n"
            f"Master Secret Key Recovered. Operational Flag: {sess['lab_flag']}"
        )
    elif base_cmd == "volatility":
        output = (
            "Volatility Foundation Volatility Framework 2.6.1\n"
            "Determining profile based on KDBG search...\n"
            "Suggested Profile: Win10x64_19041\n\n"
            "PID: 4912 | Process: svchost.exe | Injected DLL: beacon_x64.dll\n"
            "C2 Callback IP: 198.51.100.44:443\n"
            f"Decrypted Memory Flag Token: {sess['lab_flag']}"
        )
    elif base_cmd in ["/usr/local/bin/backup_agent", "backup_agent"]:
        output = (
            f"[backup_agent] Running automated tar backup routine as UID 0 (root)...\n"
            f"[backup_agent] SUID bit active. Root escalation verified.\n"
            f"FLAG: {sess['lab_flag']}"
        )
    else:
        output = f"bash: {base_cmd}: command not found. Type 'help' for available commands."

    # Update command history in session
    con.execute("""
    UPDATE cyber_range_sessions 
    SET command_history_json=?
    WHERE id=?
    """, (json.dumps(history[-50:]), sess['id']))
    con.commit()
    con.close()

    return {'output': output, 'cwd': cwd, 'prompt': prompt}

def submit_cyber_range_flag(student_id, lab_id, flag):
    con = get_db()
    lab = con.execute("SELECT * FROM cyber_range_labs WHERE id=?", (lab_id,)).fetchone()
    if not lab:
        con.close()
        return {'success': False, 'error': 'Lab not found.'}

    clean_flag = (flag or '').strip()
    if clean_flag != lab['flag']:
        con.close()
        return {'success': False, 'error': 'Incorrect flag token. Verify your findings and try again.'}

    # Find active session
    sess = con.execute("""
    SELECT * FROM cyber_range_sessions 
    WHERE student_id=? AND lab_id=? 
    ORDER BY id DESC LIMIT 1
    """, (student_id, lab_id)).fetchone()

    now = datetime.utcnow().isoformat()
    if sess:
        con.execute("""
        UPDATE cyber_range_sessions 
        SET status='completed', completed_at=?, flag_submitted=?, score_awarded=?
        WHERE id=?
        """, (now, clean_flag, lab['xp_reward'], sess['id']))
    else:
        token = f"CR-SESS-{secrets.token_hex(8)}"
        con.execute("""
        INSERT INTO cyber_range_sessions 
        (session_token, student_id, lab_id, status, started_at, expires_at, completed_at, flag_submitted, score_awarded)
        VALUES (?, ?, ?, 'completed', ?, ?, ?, ?, ?)
        """, (token, student_id, lab_id, now, now, now, clean_flag, lab['xp_reward']))

    # Link to Skill Passport
    con.execute("""
    INSERT OR REPLACE INTO skill_passport_skills 
    (student_id, skill_name, category, level, source_module, source_reference_id)
    VALUES (?, ?, ?, 'practiced', 'cyber_range', ?)
    """, (student_id, lab['title'], lab['category'], str(lab_id)))

    con.commit()
    con.close()
    return {
        'success': True,
        'message': f"Congratulations! Lab completed successfully. +{lab['xp_reward']} XP awarded!",
        'xp_reward': lab['xp_reward']
    }

# =============================================================================
# 2. CYBERPRO SKILL PASSPORT MODULE
# =============================================================================

def get_student_skill_passport(student_id=None, passport_token=None, is_public=False):
    con = get_db()

    # Resolve student
    if passport_token:
        settings = con.execute("SELECT * FROM student_portfolio_settings WHERE passport_token=?", (passport_token,)).fetchone()
        if not settings:
            con.close()
            return {'success': False, 'error': 'Invalid passport token or portfolio not found.'}
        student_id = settings['student_id']
    else:
        settings = con.execute("SELECT * FROM student_portfolio_settings WHERE student_id=?", (student_id,)).fetchone()
        if not settings:
            token = f"CP-PASS-{student_id}-{secrets.token_hex(4).upper()}"
            con.execute("INSERT INTO student_portfolio_settings (student_id, passport_token) VALUES (?, ?)", (student_id, token))
            con.commit()
            settings = con.execute("SELECT * FROM student_portfolio_settings WHERE student_id=?", (student_id,)).fetchone()

    if is_public and not settings['is_public']:
        con.close()
        return {'success': False, 'error': 'This portfolio is marked as private by the student.'}

    # Fetch User & Profile
    student = con.execute("""
    SELECT u.id, u.name, u.email, sp.student_id, sp.photo_url, sp.qualification, sp.city
    FROM users u
    JOIN student_profiles sp ON u.id = sp.user_id
    WHERE u.id=?
    """, (student_id,)).fetchone()

    if not student:
        con.close()
        return {'success': False, 'error': 'Student not found.'}

    # Enrolled courses
    courses = con.execute("""
    SELECT c.id, c.name, c.code, ce.access_status as status, 100 as progress_percent
    FROM course_enrollments ce
    JOIN courses c ON ce.course_id = c.id
    WHERE ce.user_id=?
    """, (student_id,)).fetchall()

    # Certificates
    certificates = con.execute("""
    SELECT id, course_name, issued_at as issue_date, grade, certificate_code as verification_code
    FROM certificates
    WHERE user_id=? AND status != 'revoked'
    ORDER BY id DESC
    """, (student_id,)).fetchall()

    # 3-Tier Skills
    skills = con.execute("""
    SELECT id, skill_name, category, level, source_module, verified_at
    FROM skill_passport_skills
    WHERE student_id=?
    ORDER BY level DESC, category ASC
    """, (student_id,)).fetchall()

    # Cyber Range Labs completed
    completed_labs = con.execute("""
    SELECT s.lab_id, l.title, l.category, l.difficulty, s.completed_at, s.score_awarded
    FROM cyber_range_sessions s
    JOIN cyber_range_labs l ON s.lab_id = l.id
    WHERE s.student_id=? AND s.status='completed'
    ORDER BY s.completed_at DESC
    """, (student_id,)).fetchall()

    # Bug Bounty accepted reports
    bounties = con.execute("""
    SELECT r.id, r.title, r.category, r.severity, r.points_awarded, r.reviewed_at
    FROM bug_bounty_reports r
    WHERE r.student_id=? AND r.status='approved'
    ORDER BY r.reviewed_at DESC
    """, (student_id,)).fetchall()

    # Approved Projects
    projects = con.execute("""
    SELECT p.id, p.title, p.category, p.description, p.repository_url, p.demo_video_url, pr.total_marks, pr.grade_letter
    FROM student_projects p
    JOIN project_reviews pr ON p.id = pr.project_id
    WHERE p.student_id=? AND p.status='approved'
    ORDER BY p.updated_at DESC
    """, (student_id,)).fetchall()

    # SOC Simulations evaluated
    soc_sims = con.execute("""
    SELECT i.id, s.title, s.attack_type, i.accuracy_score, i.completed_at
    FROM soc_investigations i
    JOIN soc_scenarios s ON i.scenario_id = s.id
    WHERE i.student_id=? AND i.status='evaluated'
    ORDER BY i.completed_at DESC
    """, (student_id,)).fetchall()

    # Total XP Calculation
    total_xp = (
        sum(r['score_awarded'] or 0 for r in completed_labs) +
        sum(r['points_awarded'] or 0 for r in bounties) +
        sum((r['accuracy_score'] or 0) * 2 for r in soc_sims) +
        sum((r['total_marks'] or 0) * 3 for r in projects) + 250
    )

    con.close()

    return {
        'success': True,
        'student': dict(student),
        'settings': dict(settings),
        'total_xp': total_xp,
        'badge': 'CyberPro Elite Specialist' if total_xp > 1500 else ('CyberPro Defensive Analyst' if total_xp > 600 else 'CyberPro Cadet'),
        'courses': [dict(c) for c in courses],
        'certificates': [dict(c) for c in certificates] if settings['show_certificates'] else [],
        'skills': [dict(s) for s in skills],
        'skills_summary': {
            'learning': [dict(s) for s in skills if s['level'] == 'learning'],
            'practiced': [dict(s) for s in skills if s['level'] == 'practiced'],
            'verified': [dict(s) for s in skills if s['level'] == 'verified']
        },
        'completed_labs': [dict(l) for l in completed_labs],
        'bounties': [dict(b) for b in bounties],
        'projects': [dict(p) for p in projects] if settings['show_projects'] else [],
        'soc_sims': [dict(s) for s in soc_sims]
    }

def update_student_portfolio_settings(student_id, data):
    con = get_db()
    curr = con.execute("SELECT * FROM student_portfolio_settings WHERE student_id=?", (student_id,)).fetchone()
    if not curr:
        token = f"CP-PASS-{student_id}-{secrets.token_hex(4).upper()}"
        con.execute("INSERT INTO student_portfolio_settings (student_id, passport_token) VALUES (?, ?)", (student_id, token))
        curr = con.execute("SELECT * FROM student_portfolio_settings WHERE student_id=?", (student_id,)).fetchone()

    is_pub = (1 if data.get('is_public') else 0) if 'is_public' in data else curr['is_public']
    sh_grd = (1 if data.get('show_grades') else 0) if 'show_grades' in data else curr['show_grades']
    sh_crt = (1 if data.get('show_certificates') else 0) if 'show_certificates' in data else curr['show_certificates']
    sh_prj = (1 if data.get('show_projects') else 0) if 'show_projects' in data else curr['show_projects']
    al_emp = (1 if data.get('allow_employer_discovery') else 0) if 'allow_employer_discovery' in data else curr['allow_employer_discovery']
    bio = data.get('bio', curr['bio'] or '') if 'bio' in data else (curr['bio'] or '')
    gh = data.get('github_url', curr['github_url'] or '') if 'github_url' in data else (curr['github_url'] or '')
    li = data.get('linkedin_url', curr['linkedin_url'] or '') if 'linkedin_url' in data else (curr['linkedin_url'] or '')
    head = data.get('headline', curr['headline'] or '') if 'headline' in data else (curr['headline'] or '')

    con.execute("""
    UPDATE student_portfolio_settings 
    SET is_public=?, show_grades=?, show_certificates=?, show_projects=?, allow_employer_discovery=?,
        bio=?, github_url=?, linkedin_url=?, headline=?, updated_at=CURRENT_TIMESTAMP
    WHERE student_id=?
    """, (is_pub, sh_grd, sh_crt, sh_prj, al_emp, bio, gh, li, head, student_id))
    con.commit()
    con.close()
    return {'success': True, 'message': 'Portfolio privacy and profile updated.'}

def verify_student_skill_by_instructor(student_id, skill_name, instructor_id):
    con = get_db()
    con.execute("""
    UPDATE skill_passport_skills
    SET level='verified', verified_by_user_id=?, verified_at=CURRENT_TIMESTAMP
    WHERE student_id=? AND skill_name=?
    """, (instructor_id, student_id, skill_name))
    con.commit()
    con.close()
    return {'success': True, 'message': f"Skill '{skill_name}' officially verified."}

# =============================================================================
# 3. BUG BOUNTY TRAINING ARENA MODULE
# =============================================================================

def get_bug_bounty_challenges(student_id=None, category=None):
    con = get_db()
    query = "SELECT * FROM bug_bounty_challenges WHERE is_active=1"
    params = []
    if category and category != 'All':
        query += " AND category = ?"
        params.append(category)
    query += " ORDER BY points ASC"
    rows = con.execute(query, params).fetchall()

    challenges = []
    for r in rows:
        ch = dict(r)
        del ch['flag'] # Strip internal validation flag
        if student_id:
            sub = con.execute("""
            SELECT id, status, points_awarded, submitted_at, reviewed_at, instructor_feedback
            FROM bug_bounty_reports 
            WHERE student_id=? AND challenge_id=? 
            ORDER BY id DESC LIMIT 1
            """, (student_id, ch['id'])).fetchone()
            ch['submission'] = dict(sub) if sub else None
            ch['solved'] = sub['status'] == 'approved' if sub else False
        else:
            ch['submission'] = None
            ch['solved'] = False
        challenges.append(ch)
    con.close()
    return challenges

def submit_bug_bounty_report(student_id, data):
    challenge_id = data.get('challenge_id')
    title = (data.get('title') or '').strip()
    category = (data.get('category') or '').strip()
    severity = (data.get('severity') or 'Medium').strip()
    cvss = float(data.get('cvss_score') or 5.0)
    desc = (data.get('description') or '').strip()
    steps = (data.get('reproduction_steps') or '').strip()
    impact = (data.get('impact_analysis') or '').strip()
    remediation = (data.get('recommended_remediation') or '').strip()
    evidence_text = data.get('evidence_text', '')
    evidence_attachment_url = data.get('evidence_attachment_url', '')

    if not title or not desc or not steps or not impact or not remediation:
        return {'success': False, 'error': 'All report sections (Title, Description, Steps, Impact, Remediation) are compulsory.'}

    con = get_db()
    ch = con.execute("SELECT * FROM bug_bounty_challenges WHERE id=?", (challenge_id,)).fetchone()
    if not ch:
        con.close()
        return {'success': False, 'error': 'Challenge not found.'}

    # Auto-award if user provided the flag in evidence_text
    auto_approve = ch['flag'] and (ch['flag'] in evidence_text or ch['flag'] in steps)
    status = 'approved' if auto_approve else 'submitted'
    points = ch['points'] if auto_approve else 0
    feedback = "Automated Flag PoC verification successful. Full points awarded!" if auto_approve else None

    cur = con.cursor()
    cur.execute("""
    INSERT INTO bug_bounty_reports 
    (student_id, challenge_id, title, category, severity, cvss_score, description, 
     reproduction_steps, evidence_text, evidence_attachment_url, impact_analysis, 
     recommended_remediation, status, points_awarded, instructor_feedback, reviewed_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        student_id, challenge_id, title, category, severity, cvss, desc,
        steps, evidence_text, evidence_attachment_url, impact, remediation,
        status, points, feedback, datetime.utcnow().isoformat() if auto_approve else None
    ))
    report_id = cur.lastrowid

    if auto_approve:
        # Link to Skill Passport
        con.execute("""
        INSERT OR REPLACE INTO skill_passport_skills 
        (student_id, skill_name, category, level, source_module, source_reference_id)
        VALUES (?, ?, 'Application Security', 'practiced', 'bug_bounty', ?)
        """, (student_id, f"Vulnerability Discovery: {category}", str(report_id)))

    con.commit()
    con.close()

    return {
        'success': True,
        'message': 'Vulnerability report submitted to instructor review queue.' if not auto_approve else 'PoC Verified! Bug Bounty report approved and points awarded!',
        'report_id': report_id,
        'auto_approved': auto_approve,
        'points_awarded': points
    }

def review_bug_bounty_report(report_id, reviewer_id, status, points, feedback):
    con = get_db()
    rep = con.execute("SELECT * FROM bug_bounty_reports WHERE id=?", (report_id,)).fetchone()
    if not rep:
        con.close()
        return {'success': False, 'error': 'Report not found.'}

    con.execute("""
    UPDATE bug_bounty_reports
    SET status=?, points_awarded=?, instructor_feedback=?, reviewed_by_user_id=?, reviewed_at=CURRENT_TIMESTAMP
    WHERE id=?
    """, (status, points, feedback, reviewer_id, report_id))

    if status == 'approved':
        con.execute("""
        INSERT OR REPLACE INTO skill_passport_skills 
        (student_id, skill_name, category, level, source_module, source_reference_id, verified_by_user_id, verified_at)
        VALUES (?, ?, 'Application Security', 'verified', 'bug_bounty', ?, ?, CURRENT_TIMESTAMP)
        """, (rep['student_id'], f"Vulnerability Discovery: {rep['category']}", str(report_id), reviewer_id))

    con.commit()
    con.close()
    return {'success': True, 'message': f"Report #{report_id} updated to {status}."}

# =============================================================================
# 4. SOC ANALYST SIMULATOR MODULE
# =============================================================================

def get_soc_scenarios(student_id=None):
    con = get_db()
    rows = con.execute("SELECT * FROM soc_scenarios WHERE is_active=1 ORDER BY id ASC").fetchall()
    scenarios = []
    for r in rows:
        sc = dict(r)
        sc['initial_alerts'] = json.loads(sc['initial_alerts_json'] or '[]')
        sc['logs'] = json.loads(sc['logs_json'] or '[]')
        sc['correct_containment_actions'] = json.loads(sc['correct_containment_actions_json'] or '[]')

        if student_id:
            inv = con.execute("""
            SELECT id, status, accuracy_score, student_classification, student_severity, 
                   completed_at, feedback_json, time_taken_seconds
            FROM soc_investigations
            WHERE student_id=? AND scenario_id=?
            ORDER BY id DESC LIMIT 1
            """, (student_id, sc['id'])).fetchone()
            sc['investigation'] = dict(inv) if inv else None
        else:
            sc['investigation'] = None

        del sc['post_incident_debrief'] # Only reveal after submission
        del sc['correct_remediation']
        del sc['correct_classification']
        scenarios.append(sc)
    con.close()
    return scenarios

def submit_soc_investigation(student_id, scenario_id, data):
    con = get_db()
    sc = con.execute("SELECT * FROM soc_scenarios WHERE id=?", (scenario_id,)).fetchone()
    if not sc:
        con.close()
        return {'success': False, 'error': 'Scenario not found.'}

    student_classification = (data.get('classification') or '').strip()
    student_severity = (data.get('severity') or '').strip().upper()
    pinned_evidence = data.get('pinned_evidence') or []
    containment_actions = data.get('containment_actions') or []
    root_cause = (data.get('root_cause') or '').strip()
    remediation = (data.get('remediation') or '').strip()
    time_taken = int(data.get('time_taken_seconds') or 120)

    # Automatic Evaluation Scoring:
    # 1. Classification (25 pts)
    # 2. Severity match (15 pts)
    # 3. Containment actions selection (30 pts)
    # 4. Evidence collection & analysis (30 pts)
    score = 0
    feedback_notes = []

    # 1. Classification check
    correct_cls = sc['correct_classification'].lower()
    if any(k in student_classification.lower() for k in correct_cls.split() if len(k) > 3):
        score += 25
        feedback_notes.append("Threat classification correctly identified the attack pattern.")
    else:
        score += 10
        feedback_notes.append(f"Threat classification partially aligned. Standard classification: {sc['correct_classification']}")

    # 2. Severity check
    if student_severity == sc['severity']:
        score += 15
        feedback_notes.append(f"Severity correctly assessed as {sc['severity']}.")
    else:
        score += 5
        feedback_notes.append(f"Severity calibrated to {sc['severity']} based on enterprise impact.")

    # 3. Containment actions check
    correct_actions = json.loads(sc['correct_containment_actions_json'] or '[]')
    if containment_actions:
        matches = sum(1 for a in containment_actions if any(ca.lower() in a.lower() or a.lower() in ca.lower() for ca in correct_actions))
        action_score = min(30, int((matches / max(1, len(correct_actions))) * 30))
        score += action_score
        feedback_notes.append(f"Containment checklist scored {action_score}/30 points.")
    else:
        feedback_notes.append("No active containment measures selected.")

    # 4. Evidence & Remediation quality
    if len(pinned_evidence) >= 2:
        score += 15
        feedback_notes.append(f"Corroborated {len(pinned_evidence)} critical IOC indicators as evidence.")
    elif len(pinned_evidence) == 1:
        score += 8
    else:
        score += 0

    if len(root_cause) > 20 and len(remediation) > 20:
        score += 15
        feedback_notes.append("Thorough root-cause analysis and actionable remediation advice.")
    else:
        score += 5

    accuracy_score = min(100, max(20, score))

    # Persist investigation
    con.execute("""
    INSERT INTO soc_investigations 
    (student_id, scenario_id, status, completed_at, pinned_evidence_json, 
     selected_containment_actions_json, student_classification, student_severity, 
     root_cause_analysis, remediation_recommendation, accuracy_score, feedback_json, time_taken_seconds)
    VALUES (?, ?, 'evaluated', CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        student_id, scenario_id, json.dumps(pinned_evidence), json.dumps(containment_actions),
        student_classification, student_severity, root_cause, remediation, accuracy_score,
        json.dumps(feedback_notes), time_taken
    ))

    # Link to Skill Passport
    con.execute("""
    INSERT OR REPLACE INTO skill_passport_skills 
    (student_id, skill_name, category, level, source_module, source_reference_id)
    VALUES (?, ?, 'Defensive Security & SOC', ?, 'soc_simulator', ?)
    """, (student_id, f"SOC Triaging: {sc['attack_type']}", 'verified' if accuracy_score >= 80 else 'practiced', str(scenario_id)))

    con.commit()
    con.close()

    return {
        'success': True,
        'accuracy_score': accuracy_score,
        'grade': 'Excellent' if accuracy_score >= 85 else ('Satisfactory' if accuracy_score >= 60 else 'Needs Review'),
        'feedback_notes': feedback_notes,
        'correct_classification': sc['correct_classification'],
        'correct_remediation': sc['correct_remediation'],
        'debrief': sc['post_incident_debrief']
    }

# =============================================================================
# 5. EMPLOYER HIRING PORTAL MODULE
# =============================================================================

def get_career_opportunities(student_id=None):
    con = get_db()
    jobs = con.execute("""
    SELECT j.*, e.company_name, e.industry, e.website, e.contact_person
    FROM employer_jobs j
    JOIN employer_profiles e ON j.employer_id = e.id
    WHERE j.is_active=1
    ORDER BY j.id DESC
    """).fetchall()

    opportunities = []
    for r in jobs:
        jb = dict(r)
        jb['required_skills'] = json.loads(jb['required_skills_json'] or '[]')
        if student_id:
            app = con.execute("""
            SELECT id, status, applied_at, interview_details_json, employer_feedback
            FROM job_applications
            WHERE student_id=? AND job_id=?
            """, (student_id, jb['id'])).fetchone()
            jb['application'] = dict(app) if app else None
        else:
            jb['application'] = None
        opportunities.append(jb)

    # Consent setting
    consent = 0
    if student_id:
        row = con.execute("SELECT allow_employer_discovery FROM student_portfolio_settings WHERE student_id=?", (student_id,)).fetchone()
        consent = row['allow_employer_discovery'] if row else 0

    con.close()
    return {'opportunities': opportunities, 'employer_discovery_consent': bool(consent)}

def apply_for_job(student_id, job_id, cover_letter, resume_url=None):
    con = get_db()
    job = con.execute("SELECT * FROM employer_jobs WHERE id=? AND is_active=1", (job_id,)).fetchone()
    if not job:
        con.close()
        return {'success': False, 'error': 'Job posting not found or inactive.'}

    # Fetch student's passport token
    p = con.execute("SELECT passport_token FROM student_portfolio_settings WHERE student_id=?", (student_id,)).fetchone()
    token = p['passport_token'] if p else None

    try:
        con.execute("""
        INSERT INTO job_applications (job_id, student_id, cover_letter, resume_url, passport_token, status)
        VALUES (?, ?, ?, ?, ?, 'applied')
        ON CONFLICT(job_id, student_id) DO UPDATE SET
            cover_letter=excluded.cover_letter,
            resume_url=excluded.resume_url,
            passport_token=excluded.passport_token,
            updated_at=CURRENT_TIMESTAMP
        """, (job_id, student_id, cover_letter, resume_url, token))
        con.commit()
    except Exception as e:
        con.close()
        return {'success': False, 'error': str(e)}

    con.close()
    return {'success': True, 'message': 'Application submitted successfully to hiring team!'}

def get_employer_candidate_pool(search_query='', skill_filter=''):
    con = get_db()
    query = """
    SELECT u.id, u.name, u.email, sp.student_id, sp.city, sp.qualification,
           sps.passport_token, sps.headline, sps.bio, sps.github_url, sps.linkedin_url
    FROM users u
    JOIN student_profiles sp ON u.id = sp.user_id
    JOIN student_portfolio_settings sps ON u.id = sps.student_id
    WHERE sps.allow_employer_discovery = 1 AND u.role='student'
    """
    params = []
    if search_query:
        query += " AND (u.name LIKE ? OR sp.city LIKE ? OR sps.headline LIKE ?)"
        q = f"%{search_query}%"
        params.extend([q, q, q])

    rows = con.execute(query, params).fetchall()
    candidates = []
    for r in rows:
        cand = dict(r)
        # Fetch verified skills
        sk = con.execute("""
        SELECT skill_name, category, level FROM skill_passport_skills 
        WHERE student_id=? AND level IN ('practiced', 'verified')
        """, (cand['id'],)).fetchall()
        cand['skills'] = [dict(s) for s in sk]

        if skill_filter and skill_filter != 'All':
            if not any(skill_filter.lower() in s['skill_name'].lower() for s in cand['skills']):
                continue

        # Fetch approved projects
        proj = con.execute("""
        SELECT title, category, repository_url FROM student_projects 
        WHERE student_id=? AND status='approved'
        """, (cand['id'],)).fetchall()
        cand['projects'] = [dict(p) for p in proj]

        candidates.append(cand)

    con.close()
    return candidates

def update_job_application_status(application_id, status, feedback=None, interview_details=None):
    con = get_db()
    con.execute("""
    UPDATE job_applications 
    SET status=?, employer_feedback=?, interview_details_json=?, updated_at=CURRENT_TIMESTAMP
    WHERE id=?
    """, (status, feedback, json.dumps(interview_details) if interview_details else None, application_id))
    con.commit()
    con.close()
    return {'success': True, 'message': f"Application #{application_id} updated to '{status}'."}

# =============================================================================
# 6. PROJECT EVALUATION SYSTEM MODULE
# =============================================================================

def get_student_projects(student_id=None):
    con = get_db()
    query = """
    SELECT p.*, c.name as course_name, u.name as student_name,
           pr.technical_score, pr.creativity_score, pr.security_score,
           pr.functionality_score, pr.documentation_score, pr.presentation_score,
           pr.total_marks, pr.grade_letter, pr.written_feedback, pr.decision,
           pr.reviewed_at
    FROM student_projects p
    LEFT JOIN courses c ON p.course_id = c.id
    JOIN users u ON p.student_id = u.id
    LEFT JOIN project_reviews pr ON p.id = pr.project_id
    """
    params = []
    if student_id:
        query += " WHERE p.student_id = ?"
        params.append(student_id)
    query += " ORDER BY p.id DESC"

    rows = con.execute(query, params).fetchall()
    con.close()
    return [dict(r) for r in rows]

def submit_student_project(student_id, data):
    title = (data.get('title') or '').strip()
    category = (data.get('category') or '').strip()
    course_id = (data.get('course_id') or 'ccce').strip()
    desc = (data.get('description') or '').strip()
    repo_url = (data.get('repository_url') or '').strip()
    doc_url = (data.get('documentation_url') or '').strip()
    demo_url = (data.get('demo_video_url') or '').strip()
    comments = (data.get('submission_comments') or '').strip()

    if not title or not category or not desc:
        return {'success': False, 'error': 'Title, Category, and Description are compulsory.'}

    con = get_db()
    cur = con.cursor()
    cur.execute("""
    INSERT INTO student_projects 
    (student_id, course_id, title, category, description, repository_url, documentation_url, demo_video_url, submission_comments, status)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'submitted')
    """, (student_id, course_id, title, category, desc, repo_url, doc_url, demo_url, comments))

    proj_id = cur.lastrowid
    con.commit()
    con.close()

    return {'success': True, 'message': 'Project submitted successfully for instructor evaluation!', 'project_id': proj_id}

def review_student_project(project_id, reviewer_id, rubric, written_feedback, decision):
    tech = min(20, max(0, int(rubric.get('technical_score') or 0)))
    creat = min(15, max(0, int(rubric.get('creativity_score') or 0)))
    sec = min(20, max(0, int(rubric.get('security_score') or 0)))
    func = min(20, max(0, int(rubric.get('functionality_score') or 0)))
    doc = min(15, max(0, int(rubric.get('documentation_score') or 0)))
    pres = min(10, max(0, int(rubric.get('presentation_score') or 0)))

    total = tech + creat + sec + func + doc + pres
    if total >= 90:
        grade = 'A+'
    elif total >= 80:
        grade = 'A'
    elif total >= 70:
        grade = 'B+'
    elif total >= 60:
        grade = 'B'
    elif total >= 50:
        grade = 'C'
    else:
        grade = 'F'

    con = get_db()
    proj = con.execute("SELECT * FROM student_projects WHERE id=?", (project_id,)).fetchone()
    if not proj:
        con.close()
        return {'success': False, 'error': 'Project not found.'}

    con.execute("DELETE FROM project_reviews WHERE project_id=?", (project_id,))
    con.execute("""
    INSERT INTO project_reviews 
    (project_id, reviewer_user_id, technical_score, creativity_score, security_score,
     functionality_score, documentation_score, presentation_score, total_marks, grade_letter, written_feedback, decision)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (project_id, reviewer_id, tech, creat, sec, func, doc, pres, total, grade, written_feedback, decision))

    con.execute("UPDATE student_projects SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (decision, project_id))

    if decision == 'approved':
        # Link verified skill to Skill Passport
        con.execute("""
        INSERT OR REPLACE INTO skill_passport_skills 
        (student_id, skill_name, category, level, source_module, source_reference_id, verified_by_user_id, verified_at)
        VALUES (?, ?, 'Defensive Security & SOC', 'verified', 'project', ?, ?, CURRENT_TIMESTAMP)
        """, (proj['student_id'], f"Project Mastery: {proj['title']}", str(project_id), reviewer_id))

    con.commit()
    con.close()

    return {
        'success': True,
        'message': f"Project #{project_id} evaluated with Grade {grade} ({total}/100) and marked as '{decision}'.",
        'total_marks': total,
        'grade_letter': grade
    }
