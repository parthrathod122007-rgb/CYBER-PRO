"""
CYBERPRO REFER & EARN SERVICE
Comprehensive backend business logic for Student LMS & Admin LMS referral system.
Zero-trust security, real-time database transactions, full audit trail.
"""

import sqlite3
import re
import os
import json
import csv
import io
from datetime import datetime
from pathlib import Path
import runtime_paths

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = str(runtime_paths.resolve_db_path(Path(ROOT_DIR)))

RESERVED_CODES = {
    'ADMIN', 'ADMINISTRATOR', 'ROOT', 'SUPERADMIN', 'NULL', 'UNDEFINED',
    'SYSTEM', 'API', 'CYBERPRO', 'HACKER', 'TEST', 'MOD', 'STAFF', 'OWNER',
    'SUPPORT', 'CYDENIUM', 'LOGIN', 'REGISTER', 'AUTH', 'DEFAULT', 'GLOBAL'
}

PROFANITY_WORDS = {
    'ABUSE', 'SCAM', 'FRAUD', 'FAKE', 'HACK', 'CRACK', 'SPAM', 'BOT',
    'EXPLOIT', 'MALWARE', 'PHISH', 'ATTACK'
}

def get_db():
    conn = sqlite3.connect(runtime_paths.resolve_db_path(Path(ROOT_DIR)))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def get_setting(key, default=None):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT value FROM referral_settings WHERE key = ?", (key,))
        row = c.fetchone()
        return row['value'] if row else default

def set_setting(key, value):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO referral_settings (key, value, updated_at) 
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
        """, (key, str(value)))
        conn.commit()

def log_audit(admin_id, action, details="", ip_address="127.0.0.1"):
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO referral_audit_logs (admin_id, action, details, ip_address)
                VALUES (?, ?, ?, ?)
            """, (admin_id, action, str(details), ip_address))
            conn.commit()
    except Exception as e:
        print(f"[REFERRAL AUDIT LOG ERROR] {e}")

# ==============================================================================
# VALIDATION ENGINE
# ==============================================================================

def validate_custom_code(code, current_user_id=None):
    """
    Validates a student custom referral code.
    Rules:
    - Min length: default 4
    - Max length: default 20
    - Allowed characters: Letters, numbers, and @, !, #, $, %, *, &
    - Case-insensitive uniqueness across automatic_code and custom_code
    - Prohibited / reserved keywords check
    """
    if not code or not isinstance(code, str):
        return False, "Referral code cannot be empty."

    code_clean = code.strip()
    min_len = int(get_setting('min_code_length', 4))
    max_len = int(get_setting('max_code_length', 20))

    if len(code_clean) < min_len or len(code_clean) > max_len:
        return False, f"Referral code must be between {min_len} and {max_len} characters long."

    # Pattern: Letters, numbers, and @, !, #, $, %, *, &
    pattern = r'^[A-Za-z0-9@!#$%*&]+$'
    if not re.match(pattern, code_clean):
        return False, "Code can only contain letters, numbers, and symbols (@, !, #, $, %, *, &)."

    # Must contain at least one alphanumeric character
    if not re.search(r'[A-Za-z0-9]', code_clean):
        return False, "Code must contain at least one letter or number."

    upper_code = code_clean.upper()

    # Reserved words check
    if upper_code in RESERVED_CODES:
        return False, f"The code '{code_clean}' is a reserved system keyword. Please choose another."

    # Profanity check
    for p in PROFANITY_WORDS:
        if p in upper_code:
            return False, "Inappropriate or prohibited words are not allowed in referral codes."

    # Uniqueness check in DB
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT user_id, active_code, automatic_code, custom_code 
            FROM student_referral_codes 
            WHERE (automatic_code = ? COLLATE NOCASE OR custom_code = ? COLLATE NOCASE OR active_code = ? COLLATE NOCASE)
        """, (code_clean, code_clean, code_clean))
        existing = c.fetchone()
        if existing:
            if current_user_id and existing['user_id'] == current_user_id:
                if existing['active_code'].upper() == upper_code:
                    return False, "This is already your active referral code."
            else:
                return False, "This referral code is already taken. Please try another code."

    return True, "Code is available."

# ==============================================================================
# STUDENT LMS LOGIC
# ==============================================================================

def ensure_student_referral_code(user_id):
    """Ensures the student has an active automatic referral code record."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM student_referral_codes WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        if row:
            return dict(row)

        # Look up student profile & user
        c.execute("""
            SELECT u.name, sp.student_id 
            FROM users u 
            LEFT JOIN student_profiles sp ON u.id = sp.user_id 
            WHERE u.id = ?
        """, (user_id,))
        u = c.fetchone()
        name = u['name'] if u and u['name'] else f"Student{user_id}"
        sid = u['student_id'] if u and u['student_id'] else f"CP-{user_id:05d}"

        # Generate automatic code
        clean_name = re.sub(r'[^a-zA-Z\s]', '', name).strip().upper()
        parts = clean_name.split()
        initials = (parts[0][:2] if len(parts) == 1 else "".join(p[0] for p in parts[:3])) or "CP"
        num = int(user_id)

        c.execute("SELECT automatic_code FROM student_referral_codes")
        existing_codes = {r[0].upper() for r in c.fetchall()}

        candidate = f"{initials}{num:03d}"
        if candidate.upper() in existing_codes:
            candidate = f"CP{num:04d}"
        if candidate.upper() in existing_codes:
            candidate = f"REF{num:05d}"

        c.execute("""
            INSERT INTO student_referral_codes (
                user_id, student_id_code, automatic_code, active_code, code_type, is_active
            ) VALUES (?, ?, ?, ?, 'automatic', 1)
        """, (user_id, sid, candidate, candidate))
        conn.commit()

        c.execute("SELECT * FROM student_referral_codes WHERE user_id = ?", (user_id,))
        return dict(c.fetchone())

def get_student_referral_dashboard(user_id):
    """
    Returns complete student referral dashboard data:
    1. Active Referral Code & details
    2. Total People Referred
    3. Successful Referrals
    4. Pending Referrals
    5. Rejected Referrals
    6. Total Rewards Earned
    7. Available Rewards
    8. Referral History
    """
    code_data = ensure_student_referral_code(user_id)

    with get_db() as conn:
        c = conn.cursor()

        # Stats from referral_records
        c.execute("""
            SELECT 
                COUNT(*) as total_referred,
                SUM(CASE WHEN referral_status = 'successful' THEN 1 ELSE 0 END) as successful_referrals,
                SUM(CASE WHEN referral_status = 'pending' THEN 1 ELSE 0 END) as pending_referrals,
                SUM(CASE WHEN referral_status = 'rejected' THEN 1 ELSE 0 END) as rejected_referrals
            FROM referral_records 
            WHERE referring_user_id = ?
        """, (user_id,))
        stats_row = c.fetchone()

        total_referred = stats_row['total_referred'] or 0
        successful_referrals = stats_row['successful_referrals'] or 0
        pending_referrals = stats_row['pending_referrals'] or 0
        rejected_referrals = stats_row['rejected_referrals'] or 0

        # Rewards calculations
        c.execute("""
            SELECT 
                SUM(CASE WHEN status IN ('approved', 'paid') THEN amount ELSE 0 END) as total_earned,
                SUM(CASE WHEN status = 'approved' THEN amount ELSE 0 END) as available_balance,
                SUM(CASE WHEN status = 'paid' THEN amount ELSE 0 END) as total_paid,
                SUM(CASE WHEN status = 'pending' THEN amount ELSE 0 END) as pending_rewards
            FROM referral_rewards 
            WHERE user_id = ?
        """, (user_id,))
        rew_row = c.fetchone()

        total_earned = float(rew_row['total_earned'] or 0.0)
        available_balance = float(rew_row['available_balance'] or 0.0)
        total_paid = float(rew_row['total_paid'] or 0.0)
        pending_rewards = float(rew_row['pending_rewards'] or 0.0)

        # Referral History with referred student details
        c.execute("""
            SELECT 
                rr.id,
                rr.referred_user_id,
                u.name as referred_name,
                u.email as referred_email,
                sp.student_id as referred_student_id,
                sp.mobile_number as referred_mobile,
                COALESCE(rr.course_name, c.name, 'Flagship Track') as course_name,
                rr.referral_code_used,
                rr.code_type,
                rr.referral_status,
                rr.rejection_reason,
                rr.reward_status,
                rr.reward_amount,
                rr.reward_type,
                rr.created_at,
                rr.approved_at,
                rr.paid_at
            FROM referral_records rr
            JOIN users u ON rr.referred_user_id = u.id
            LEFT JOIN student_profiles sp ON u.id = sp.user_id
            LEFT JOIN courses c ON rr.course_id = c.id
            WHERE rr.referring_user_id = ?
            ORDER BY rr.created_at DESC
        """, (user_id,))
        history = [dict(r) for r in c.fetchall()]

    return {
        "success": True,
        "code_details": code_data,
        "stats": {
            "my_referral_code": code_data['active_code'],
            "code_type": code_data['code_type'],
            "automatic_code": code_data['automatic_code'],
            "custom_code": code_data['custom_code'],
            "is_active": bool(code_data['is_active']),
            "allow_custom_changes": bool(code_data['allow_custom_changes']),
            "total_referred": total_referred,
            "successful_referrals": successful_referrals,
            "pending_referrals": pending_referrals,
            "rejected_referrals": rejected_referrals,
            "total_rewards_earned": total_earned,
            "available_rewards": available_balance,
            "paid_rewards": total_paid,
            "pending_rewards": pending_rewards
        },
        "history": history
    }

def create_or_update_custom_code(user_id, custom_code):
    """
    Saves a student's custom referral code.
    Preserves all previous referral records and linkages.
    """
    valid, msg = validate_custom_code(custom_code, current_user_id=user_id)
    if not valid:
        return {"success": False, "error": msg}

    code_clean = custom_code.strip()

    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT allow_custom_changes, automatic_code FROM student_referral_codes WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        if not row:
            ensure_student_referral_code(user_id)
        elif not row['allow_custom_changes']:
            return {"success": False, "error": "Custom code modifications are currently disabled by administration."}

        c.execute("""
            UPDATE student_referral_codes 
            SET custom_code = ?, active_code = ?, code_type = 'custom', updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, (code_clean, code_clean, user_id))
        conn.commit()

    log_audit(None, "STUDENT_SET_CUSTOM_CODE", f"User {user_id} set custom code: {code_clean}")
    return {
        "success": True,
        "message": f"Custom referral code '{code_clean}' successfully activated!",
        "active_code": code_clean,
        "code_type": "custom"
    }

def reset_to_automatic_code(user_id):
    """Reverts student active code to automatic code."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT automatic_code FROM student_referral_codes WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        if not row:
            return {"success": False, "error": "Student referral record not found."}

        auto_code = row['automatic_code']
        c.execute("""
            UPDATE student_referral_codes 
            SET active_code = automatic_code, code_type = 'automatic', updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, (user_id,))
        conn.commit()

    log_audit(None, "STUDENT_RESET_AUTO_CODE", f"User {user_id} reverted to automatic code: {auto_code}")
    return {
        "success": True,
        "message": "Switched back to automatic referral code.",
        "active_code": auto_code,
        "code_type": "automatic"
    }

# ==============================================================================
# REGISTRATION & TRACKING ENGINE
# ==============================================================================

def process_referral_registration(new_user_id, referral_code, course_id=None, source="direct_link"):
    """
    Called upon new student registration.
    - Validates code
    - Prevents self-referrals
    - Prevents duplicate referral attribution
    - Connects referring and referred students
    - Records referral record & reward record
    - Updates usage count in student_referral_codes
    """
    if not referral_code or not str(referral_code).strip():
        return {"success": False, "error": "No referral code provided."}

    code_clean = str(referral_code).strip()

    with get_db() as conn:
        c = conn.cursor()

        # Find owner of referral code (matches active, custom, or automatic)
        c.execute("""
            SELECT user_id, active_code, automatic_code, custom_code, code_type, is_active
            FROM student_referral_codes
            WHERE (active_code = ? COLLATE NOCASE OR custom_code = ? COLLATE NOCASE OR automatic_code = ? COLLATE NOCASE)
        """, (code_clean, code_clean, code_clean))
        owner = c.fetchone()

        if not owner:
            return {"success": False, "error": f"Invalid or non-existent referral code '{code_clean}'."}

        if not owner['is_active']:
            return {"success": False, "error": f"The referral code '{code_clean}' is currently inactive."}

        referring_user_id = owner['user_id']

        # Prevent Self-Referral
        if int(referring_user_id) == int(new_user_id):
            return {"success": False, "error": "Self-referral is strictly prohibited. You cannot use your own referral code."}

        # Prevent Duplicate Referral Attribution
        c.execute("SELECT id FROM referral_records WHERE referred_user_id = ?", (new_user_id,))
        if c.fetchone():
            return {"success": False, "error": "This student account has already been attributed to an existing referral."}

        # Resolve course details and reward settings
        course_name = "CCCE Flagship Program"
        if course_id:
            c.execute("SELECT name FROM courses WHERE id = ?", (course_id,))
            crow = c.fetchone()
            if crow:
                course_name = crow['name']

        # Determine reward amount based on course rules
        reward_amount = float(get_setting('default_reward_amount', 500))
        reward_type = get_setting('default_reward_type', 'cash')
        rules_json = get_setting('referral_rules_json')
        if rules_json and course_id:
            try:
                rules = json.loads(rules_json)
                if course_id in rules:
                    reward_amount = float(rules[course_id].get('reward_amount', reward_amount))
                    reward_type = rules[course_id].get('reward_type', reward_type)
            except Exception:
                pass

        # Referral status defaults to 'pending'
        referral_status = 'pending'
        reward_status = 'pending'

        # Insert referral_records
        c.execute("""
            INSERT INTO referral_records (
                referring_user_id, referred_user_id, referral_code_used, code_type,
                referral_source, course_id, course_name, referral_status,
                reward_status, reward_type, reward_amount
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            referring_user_id, new_user_id, code_clean, owner['code_type'],
            source, course_id, course_name, referral_status,
            reward_status, reward_type, reward_amount
        ))
        record_id = c.lastrowid

        # Insert referral_rewards
        c.execute("""
            INSERT INTO referral_rewards (
                referral_record_id, user_id, reward_type, amount, status
            ) VALUES (?, ?, ?, ?, ?)
        """, (record_id, referring_user_id, reward_type, reward_amount, reward_status))

        # Update usage count on code
        c.execute("""
            UPDATE student_referral_codes
            SET usage_count = usage_count + 1, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, (referring_user_id,))

        conn.commit()

    log_audit(None, "REFERRAL_REGISTRATION_LINKED",
              f"Student {new_user_id} registered using code '{code_clean}' by student {referring_user_id}")

    return {
        "success": True,
        "message": "Referral tracked and attributed successfully.",
        "record_id": record_id,
        "referring_user_id": referring_user_id
    }

# ==============================================================================
# ADMIN LMS LOGIC
# ==============================================================================

def get_admin_referral_overview():
    """Returns all high-level KPI telemetry for Super Admin Dashboard."""
    with get_db() as conn:
        c = conn.cursor()

        # Code stats
        c.execute("""
            SELECT 
                COUNT(*) as total_codes,
                SUM(CASE WHEN code_type = 'automatic' THEN 1 ELSE 0 END) as total_automatic,
                SUM(CASE WHEN code_type = 'custom' THEN 1 ELSE 0 END) as total_custom,
                SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) as total_active_codes
            FROM student_referral_codes
        """)
        code_counts = c.fetchone()

        # Referral record stats
        c.execute("""
            SELECT 
                COUNT(*) as total_referrals,
                SUM(CASE WHEN referral_status = 'successful' THEN 1 ELSE 0 END) as total_successful,
                SUM(CASE WHEN referral_status = 'pending' THEN 1 ELSE 0 END) as total_pending,
                SUM(CASE WHEN referral_status = 'rejected' THEN 1 ELSE 0 END) as total_rejected,
                SUM(CASE WHEN reward_status = 'approved' THEN 1 ELSE 0 END) as total_rewards_approved,
                SUM(CASE WHEN reward_status = 'paid' THEN 1 ELSE 0 END) as total_rewards_paid,
                SUM(CASE WHEN reward_status = 'paid' THEN reward_amount ELSE 0 END) as total_payout_amount,
                SUM(CASE WHEN reward_status = 'approved' THEN reward_amount ELSE 0 END) as total_pending_payout_amount
            FROM referral_records
        """)
        ref_counts = c.fetchone()

        # Top 5 referrers
        c.execute("""
            SELECT 
                u.id as user_id,
                u.name as student_name,
                sp.student_id,
                src.active_code,
                src.code_type,
                COUNT(rr.id) as total_referred,
                SUM(CASE WHEN rr.referral_status = 'successful' THEN 1 ELSE 0 END) as successful_referrals,
                COALESCE(SUM(CASE WHEN rr.reward_status IN ('approved', 'paid') THEN rr.reward_amount ELSE 0 END), 0) as rewards_earned
            FROM student_referral_codes src
            JOIN users u ON src.user_id = u.id
            LEFT JOIN student_profiles sp ON u.id = sp.user_id
            LEFT JOIN referral_records rr ON src.user_id = rr.referring_user_id
            GROUP BY src.user_id
            HAVING successful_referrals > 0 OR total_referred > 0
            ORDER BY successful_referrals DESC, total_referred DESC
            LIMIT 5
        """)
        top_referrers = [dict(r) for r in c.fetchall()]

    return {
        "success": True,
        "kpi": {
            "total_codes_generated": code_counts['total_codes'] or 0,
            "total_automatic_codes": code_counts['total_automatic'] or 0,
            "total_custom_codes": code_counts['total_custom'] or 0,
            "total_active_codes": code_counts['total_active_codes'] or 0,
            "total_referrals_received": ref_counts['total_referrals'] or 0,
            "total_successful_referrals": ref_counts['total_successful'] or 0,
            "total_pending_referrals": ref_counts['total_pending'] or 0,
            "total_rejected_referrals": ref_counts['total_rejected'] or 0,
            "total_rewards_approved": ref_counts['total_rewards_approved'] or 0,
            "total_rewards_paid": ref_counts['total_rewards_paid'] or 0,
            "total_payout_amount": float(ref_counts['total_payout_amount'] or 0.0),
            "total_pending_payout_amount": float(ref_counts['total_pending_payout_amount'] or 0.0)
        },
        "top_referrers": top_referrers
    }

def get_admin_referral_records(filters=None):
    """
    Returns filtered and searched referral records with full student metadata.
    """
    filters = filters or {}
    search = filters.get('search', '').strip()
    status_filter = filters.get('referral_status', '').strip()
    reward_filter = filters.get('reward_status', '').strip()
    code_type_filter = filters.get('code_type', '').strip()
    course_filter = filters.get('course_id', '').strip()

    sql = """
        SELECT 
            rr.id,
            rr.referring_user_id,
            u_ref.name as referring_name,
            u_ref.email as referring_email,
            sp_ref.student_id as referring_student_id,
            sp_ref.mobile_number as referring_mobile,
            rr.referral_code_used,
            rr.code_type,
            rr.referred_user_id,
            u_red.name as referred_name,
            u_red.email as referred_email,
            sp_red.student_id as referred_student_id,
            sp_red.mobile_number as referred_mobile,
            rr.course_id,
            COALESCE(rr.course_name, c.name, 'CCCE Flagship Program') as course_name,
            rr.referral_status,
            rr.rejection_reason,
            rr.reward_status,
            rr.reward_type,
            rr.reward_amount,
            rr.payout_reference,
            rr.created_at,
            rr.approved_at,
            rr.paid_at
        FROM referral_records rr
        JOIN users u_ref ON rr.referring_user_id = u_ref.id
        LEFT JOIN student_profiles sp_ref ON u_ref.id = sp_ref.user_id
        JOIN users u_red ON rr.referred_user_id = u_red.id
        LEFT JOIN student_profiles sp_red ON u_red.id = sp_red.user_id
        LEFT JOIN courses c ON rr.course_id = c.id
        WHERE 1=1
    """
    params = []

    if search:
        s_wild = f"%{search}%"
        sql += """ AND (
            u_ref.name LIKE ? OR sp_ref.student_id LIKE ? OR u_ref.email LIKE ? OR
            u_red.name LIKE ? OR sp_red.student_id LIKE ? OR u_red.email LIKE ? OR
            rr.referral_code_used LIKE ?
        )"""
        params.extend([s_wild] * 7)

    if status_filter:
        sql += " AND rr.referral_status = ?"
        params.append(status_filter)

    if reward_filter:
        sql += " AND rr.reward_status = ?"
        params.append(reward_filter)

    if code_type_filter:
        sql += " AND rr.code_type = ?"
        params.append(code_type_filter)

    if course_filter:
        sql += " AND rr.course_id = ?"
        params.append(course_filter)

    sql += " ORDER BY rr.created_at DESC"

    with get_db() as conn:
        c = conn.cursor()
        c.execute(sql, params)
        records = [dict(r) for r in c.fetchall()]

    return {"success": True, "total": len(records), "records": records}

def get_admin_referral_codes(filters=None):
    """Returns list of student referral codes for Code Management subsection."""
    filters = filters or {}
    search = filters.get('search', '').strip()
    code_type = filters.get('code_type', '').strip()
    is_active = filters.get('is_active')

    sql = """
        SELECT 
            src.id,
            src.user_id,
            u.name as student_name,
            u.email as student_email,
            sp.student_id,
            sp.mobile_number,
            src.automatic_code,
            src.custom_code,
            src.active_code,
            src.code_type,
            src.is_active,
            src.usage_count,
            src.successful_count,
            src.allow_custom_changes,
            src.created_at,
            src.updated_at
        FROM student_referral_codes src
        JOIN users u ON src.user_id = u.id
        LEFT JOIN student_profiles sp ON u.id = sp.user_id
        WHERE 1=1
    """
    params = []

    if search:
        s_wild = f"%{search}%"
        sql += """ AND (
            u.name LIKE ? OR sp.student_id LIKE ? OR u.email LIKE ? OR
            src.active_code LIKE ? OR src.automatic_code LIKE ? OR src.custom_code LIKE ?
        )"""
        params.extend([s_wild] * 6)

    if code_type:
        sql += " AND src.code_type = ?"
        params.append(code_type)

    if is_active is not None and is_active != '':
        sql += " AND src.is_active = ?"
        params.append(1 if is_active in (True, '1', 1) else 0)

    sql += " ORDER BY src.usage_count DESC, src.created_at DESC"

    with get_db() as conn:
        c = conn.cursor()
        c.execute(sql, params)
        codes = [dict(r) for r in c.fetchall()]

    return {"success": True, "total": len(codes), "codes": codes}

def toggle_code_active_status(code_id, admin_id=None):
    """Toggles active/inactive state of a referral code."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id, is_active, active_code, user_id FROM student_referral_codes WHERE id = ?", (code_id,))
        row = c.fetchone()
        if not row:
            return {"success": False, "error": "Referral code not found."}

        new_status = 0 if row['is_active'] else 1
        c.execute("UPDATE student_referral_codes SET is_active = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (new_status, code_id))
        conn.commit()

    action_text = "ACTIVATED" if new_status else "DEACTIVATED"
    log_audit(admin_id, f"CODE_{action_text}", f"Referral code {row['active_code']} (ID {code_id}) set to is_active={new_status}")

    return {
        "success": True,
        "is_active": bool(new_status),
        "message": f"Referral code successfully {action_text.lower()}."
    }

def update_referral_record_status(record_id, new_status, reason="", admin_id=None):
    """
    Admin action to approve/reject referral record.
    If marked 'successful': increments student's successful_count.
    If marked 'rejected': stores reason and voids pending reward.
    """
    if new_status not in ('pending', 'successful', 'rejected'):
        return {"success": False, "error": "Invalid referral status."}

    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM referral_records WHERE id = ?", (record_id,))
        row = c.fetchone()
        if not row:
            return {"success": False, "error": "Referral record not found."}

        old_status = row['referral_status']
        referring_user_id = row['referring_user_id']

        now_iso = datetime.now().isoformat()
        approved_at = now_iso if new_status == 'successful' else None

        # Determine reward status update
        reward_status_update = row['reward_status']
        if new_status == 'successful' and row['reward_status'] == 'pending':
            reward_status_update = 'approved'
        elif new_status == 'rejected':
            reward_status_update = 'rejected'

        c.execute("""
            UPDATE referral_records
            SET referral_status = ?, rejection_reason = ?, reward_status = ?,
                approved_by = ?, approved_at = COALESCE(approved_at, ?), updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (new_status, reason if new_status == 'rejected' else None, reward_status_update, admin_id, approved_at, record_id))

        # Update referral_rewards
        c.execute("""
            UPDATE referral_rewards
            SET status = ?, approved_by = ?, approved_at = COALESCE(approved_at, ?)
            WHERE referral_record_id = ?
        """, (reward_status_update, admin_id, approved_at, record_id))

        # Update student_referral_codes successful_count
        c.execute("""
            UPDATE student_referral_codes
            SET successful_count = (
                SELECT COUNT(*) FROM referral_records 
                WHERE referring_user_id = ? AND referral_status = 'successful'
            ), updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, (referring_user_id, referring_user_id))

        conn.commit()

    log_audit(admin_id, "UPDATE_REFERRAL_STATUS",
              f"Record {record_id} changed from {old_status} to {new_status}. Reason: {reason}")

    return {
        "success": True,
        "message": f"Referral record #{record_id} updated to '{new_status}'.",
        "referral_status": new_status,
        "reward_status": reward_status_update
    }

def update_reward_payout_status(record_id, new_reward_status, payout_reference=None, notes=None, admin_id=None):
    """
    Admin updates reward status: 'pending', 'approved', 'paid', 'rejected'.
    """
    if new_reward_status not in ('pending', 'approved', 'paid', 'rejected'):
        return {"success": False, "error": "Invalid reward status."}

    now_iso = datetime.now().isoformat()

    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM referral_records WHERE id = ?", (record_id,))
        row = c.fetchone()
        if not row:
            return {"success": False, "error": "Referral record not found."}

        paid_at = now_iso if new_reward_status == 'paid' else row['paid_at']
        approved_at = now_iso if new_reward_status in ('approved', 'paid') and not row['approved_at'] else row['approved_at']
        ref_no = payout_reference or (f"PAY-REF-{int(datetime.now().timestamp())}" if new_reward_status == 'paid' else row['payout_reference'])

        c.execute("""
            UPDATE referral_records
            SET reward_status = ?, payout_reference = ?, reward_notes = ?,
                paid_at = ?, approved_at = ?, approved_by = COALESCE(approved_by, ?), updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (new_reward_status, ref_no, notes, paid_at, approved_at, admin_id, record_id))

        c.execute("""
            UPDATE referral_rewards
            SET status = ?, payout_reference = ?, notes = ?,
                paid_at = ?, approved_at = ?, approved_by = COALESCE(approved_by, ?)
            WHERE referral_record_id = ?
        """, (new_reward_status, ref_no, notes, paid_at, approved_at, admin_id, record_id))

        conn.commit()

    log_audit(admin_id, "UPDATE_REWARD_PAYOUT",
              f"Record {record_id} reward set to '{new_reward_status}'. Ref: {ref_no}")

    return {
        "success": True,
        "message": f"Reward status updated to '{new_reward_status}'.",
        "reward_status": new_reward_status,
        "payout_reference": ref_no
    }

# ==============================================================================
# LEADERBOARD & EXPORT ENGINE
# ==============================================================================

def get_referral_leaderboard(limit=25):
    """
    Returns rankings of top referrers based on successful referrals count.
    Used by both Student LMS and Admin LMS.
    """
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT 
                src.user_id,
                u.name as student_name,
                sp.student_id,
                src.active_code,
                src.code_type,
                src.usage_count as total_referred,
                COUNT(CASE WHEN rr.referral_status = 'successful' THEN 1 END) as successful_referrals,
                COALESCE(SUM(CASE WHEN rr.reward_status IN ('approved', 'paid') THEN rr.reward_amount ELSE 0 END), 0) as rewards_earned
            FROM student_referral_codes src
            JOIN users u ON src.user_id = u.id
            LEFT JOIN student_profiles sp ON u.id = sp.user_id
            LEFT JOIN referral_records rr ON src.user_id = rr.referring_user_id
            GROUP BY src.user_id
            ORDER BY successful_referrals DESC, total_referred DESC, src.created_at ASC
            LIMIT ?
        """, (limit,))
        rows = c.fetchall()

    leaderboard = []
    for rank, r in enumerate(rows, start=1):
        d = dict(r)
        d['rank'] = rank
        leaderboard.append(d)

    return {"success": True, "leaderboard": leaderboard}

def generate_referrals_csv():
    """Generates an RFC 4180 compliant CSV stream of all referral records."""
    res = get_admin_referral_records()
    records = res.get('records', [])

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Record ID",
        "Referring Student",
        "Referring Student ID",
        "Referring Email",
        "Referral Code Used",
        "Code Type",
        "Referred Student",
        "Referred Student ID",
        "Referred Email",
        "Referred Mobile",
        "Enrolled Course",
        "Referral Status",
        "Rejection Reason",
        "Reward Status",
        "Reward Type",
        "Reward Amount (INR)",
        "Payout Reference",
        "Registration Date",
        "Approved At",
        "Paid At"
    ])

    for r in records:
        writer.writerow([
            r.get('id', ''),
            r.get('referring_name', ''),
            r.get('referring_student_id', ''),
            r.get('referring_email', ''),
            r.get('referral_code_used', ''),
            r.get('code_type', ''),
            r.get('referred_name', ''),
            r.get('referred_student_id', ''),
            r.get('referred_email', ''),
            r.get('referred_mobile', ''),
            r.get('course_name', ''),
            r.get('referral_status', ''),
            r.get('rejection_reason', '') or '',
            r.get('reward_status', ''),
            r.get('reward_type', ''),
            f"{float(r.get('reward_amount') or 0):.2f}",
            r.get('payout_reference', '') or '',
            r.get('created_at', ''),
            r.get('approved_at', '') or '',
            r.get('paid_at', '') or ''
        ])

    return output.getvalue()
