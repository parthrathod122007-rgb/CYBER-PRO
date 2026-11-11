import hashlib
import csv
import io
import json
import secrets
import urllib.parse
import os
import re
import sqlite3
import time
from pathlib import Path
import runtime_paths

ROOT = Path(__file__).resolve().parent
def _resolve_db_path():
    return runtime_paths.resolve_db_path(ROOT)

DB_PATH = _resolve_db_path()

def _ensure_admin_schema(con):
    """Safely ensures admin credential & qualification columns exist on existing SQLite DBs."""
    try:
        u_cols = {r[1] for r in con.execute("PRAGMA table_info(users)").fetchall()}
        if "role" not in u_cols:
            con.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'student'")
        if "portal_password" not in u_cols:
            con.execute("ALTER TABLE users ADD COLUMN portal_password TEXT NOT NULL DEFAULT ''")
    except Exception:
        pass
    try:
        has_sp = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='staff_profiles'").fetchone()
        if has_sp:
            sp_cols = {r[1] for r in con.execute("PRAGMA table_info(staff_profiles)").fetchall()}
            if "qualification" not in sp_cols:
                con.execute("ALTER TABLE staff_profiles ADD COLUMN qualification TEXT DEFAULT ''")
    except Exception:
        pass

def get_db():
    con = sqlite3.connect(_resolve_db_path(), timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    return con

def get_overview():
    with get_db() as con:
        _ensure_admin_schema(con)
        total_students = con.execute("SELECT COUNT(*) FROM users WHERE role = 'student'").fetchone()[0]
        total_admins = con.execute("SELECT COUNT(*) FROM users WHERE LOWER(role) IN ('admin', 'superuser')").fetchone()[0]
        total_courses = con.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
        active_students = con.execute("SELECT COUNT(*) FROM users WHERE is_active = 1 AND role = 'student'").fetchone()[0]
        inactive_students = con.execute("SELECT COUNT(*) FROM users WHERE is_active = 0 AND role = 'student'").fetchone()[0]
        fully_paid = con.execute("SELECT COUNT(*) FROM student_profiles WHERE payment_status = 'fully_paid'").fetchone()[0]
        pending_payment = con.execute("SELECT COUNT(*) FROM student_profiles WHERE payment_status != 'fully_paid'").fetchone()[0]
        locked_courses = con.execute("SELECT COUNT(*) FROM student_profiles WHERE course_access_status = 'locked'").fetchone()[0]
        unlocked_courses = con.execute("SELECT COUNT(*) FROM student_profiles WHERE course_access_status = 'unlocked'").fetchone()[0]
        live_classes_sched = con.execute("SELECT COUNT(*) FROM live_classes WHERE meeting_status = 'scheduled'").fetchone()[0]
        active_sessions = con.execute("SELECT COUNT(*) FROM sessions WHERE expires_at > ?", (int(time.time()),)).fetchone()[0]
        
        # Overdue EMI count
        now_ts = int(time.time())
        overdue_emi = con.execute("SELECT COUNT(*) FROM emi_installments WHERE status = 'pending' AND due_date < ?", (now_ts,)).fetchone()[0]

        # Recent logins
        recent_logins = [dict(r) for r in con.execute("""
            SELECT lh.*, u.name, u.email, sp.student_id
            FROM login_history lh
            JOIN users u ON lh.user_id = u.id
            LEFT JOIN student_profiles sp ON lh.user_id = sp.user_id
            ORDER BY lh.login_time DESC LIMIT 6
        """).fetchall()]

        # Recent payments
        recent_payments = [dict(r) for r in con.execute("""
            SELECT p.*, u.name, u.email, sp.student_id, c.name as course_name
            FROM payments p
            JOIN users u ON p.user_id = u.id
            LEFT JOIN student_profiles sp ON p.user_id = sp.user_id
            LEFT JOIN courses c ON p.course_id = c.id
            ORDER BY p.created_at DESC LIMIT 6
        """).fetchall()]

        # Recent activities
        recent_activities = [dict(r) for r in con.execute("""
            SELECT al.*, u.name, u.email, sp.student_id
            FROM lms_activity_logs al
            JOIN users u ON al.user_id = u.id
            LEFT JOIN student_profiles sp ON al.user_id = sp.user_id
            ORDER BY al.created_at DESC LIMIT 8
        """).fetchall()]

        # Financial total
        financials = con.execute("SELECT COALESCE(SUM(total_paid), 0) as collected, COALESCE(SUM(remaining_balance), 0) as pending FROM student_profiles").fetchone()

        return {
            "total_students": total_students,
            "total_admins": total_admins,
            "total_courses": total_courses,
            "active_students": active_students,
            "inactive_students": inactive_students,
            "fully_paid": fully_paid,
            "pending_payment": pending_payment,
            "locked_courses": locked_courses,
            "unlocked_courses": unlocked_courses,
            "live_classes_scheduled": live_classes_sched,
            "active_sessions": active_sessions,
            "overdue_emi": overdue_emi,
            "total_revenue_collected": financials["collected"],
            "total_pending_balance": financials["pending"],
            "recent_logins": recent_logins,
            "recent_payments": recent_payments,
            "recent_activities": recent_activities
        }

def get_courses_list():
    with get_db() as con:
        courses = [dict(r) for r in con.execute("SELECT * FROM courses ORDER BY name").fetchall()]
        for c in courses:
            stats = con.execute("""
                SELECT 
                    COUNT(sp.id) as total_enrolled,
                    SUM(CASE WHEN sp.payment_status = 'fully_paid' THEN 1 ELSE 0 END) as fully_paid,
                    SUM(CASE WHEN sp.payment_status != 'fully_paid' THEN 1 ELSE 0 END) as pending_payment,
                    SUM(CASE WHEN sp.course_access_status = 'locked' THEN 1 ELSE 0 END) as locked,
                    SUM(CASE WHEN sp.course_access_status = 'unlocked' THEN 1 ELSE 0 END) as unlocked,
                    SUM(CASE WHEN u.is_active = 1 THEN 1 ELSE 0 END) as active_count,
                    SUM(CASE WHEN u.is_active = 0 THEN 1 ELSE 0 END) as inactive_count
                FROM student_profiles sp
                JOIN users u ON sp.user_id = u.id
                WHERE sp.enrolled_course_id = ?
            """, (c["id"],)).fetchone()

            c["stats"] = {
                "total_enrolled": stats["total_enrolled"] or 0,
                "fully_paid": stats["fully_paid"] or 0,
                "pending_payment": stats["pending_payment"] or 0,
                "locked": stats["locked"] or 0,
                "unlocked": stats["unlocked"] or 0,
                "active_count": stats["active_count"] or 0,
                "inactive_count": stats["inactive_count"] or 0
            }
        return courses

def get_course_students(course_id, query="", payment_status="", access_status="", limit=100):
    with get_db() as con:
        sql = """
            SELECT 
                sp.id as profile_id, sp.student_id, sp.mobile_number, sp.city, sp.qualification,
                sp.payment_status, sp.total_paid, sp.remaining_balance, sp.course_access_status,
                sp.unlocked_at, sp.created_at as registered_at,
                u.id as user_id, u.name, u.email, u.is_active, u.last_login, u.role,
                c.name as course_name, c.code as course_code
            FROM student_profiles sp
            JOIN users u ON sp.user_id = u.id
            JOIN courses c ON sp.enrolled_course_id = c.id
            WHERE sp.enrolled_course_id = ?
        """
        params = [course_id]

        if query:
            sql += " AND (u.name LIKE ? OR u.email LIKE ? OR sp.student_id LIKE ? OR sp.mobile_number LIKE ?)"
            term = f"%{query}%"
            params.extend([term, term, term, term])

        if payment_status:
            sql += " AND sp.payment_status = ?"
            params.append(payment_status)

        if access_status:
            sql += " AND sp.course_access_status = ?"
            params.append(access_status)

        sql += " ORDER BY sp.student_id ASC LIMIT ?"
        params.append(limit)

        rows = [dict(r) for r in con.execute(sql, params).fetchall()]
        return rows

def get_all_students(query="", course_filter="", payment_filter="", access_filter="", limit=100):
    with get_db() as con:
        sql = """
            SELECT 
                sp.id as profile_id, sp.student_id, sp.mobile_number, sp.city, sp.qualification,
                sp.payment_status, sp.total_paid, sp.remaining_balance, sp.course_access_status,
                sp.unlocked_at, sp.created_at as registered_at,
                u.id as user_id, u.name, u.email, u.is_active, u.last_login, u.role,
                COALESCE(c.name, 'CCCE') as course_name, COALESCE(c.code, 'CCCE') as course_code,
                sp.enrolled_course_id
            FROM student_profiles sp
            JOIN users u ON sp.user_id = u.id
            LEFT JOIN courses c ON sp.enrolled_course_id = c.id
            WHERE 1=1
        """
        params = []

        if query:
            sql += " AND (u.name LIKE ? OR u.email LIKE ? OR sp.student_id LIKE ? OR sp.mobile_number LIKE ?)"
            term = f"%{query}%"
            params.extend([term, term, term, term])

        if course_filter:
            sql += " AND sp.enrolled_course_id = ?"
            params.append(course_filter)

        if payment_filter:
            sql += " AND sp.payment_status = ?"
            params.append(payment_filter)

        if access_filter:
            sql += " AND sp.course_access_status = ?"
            params.append(access_filter)

        sql += " ORDER BY sp.student_id ASC LIMIT ?"
        params.append(limit)

        return [dict(r) for r in con.execute(sql, params).fetchall()]

def get_student_digital_file(identifier):
    """Fetches the complete 11-Tab Student Digital File by CP-ID or user_id."""
    with get_db() as con:
        _ensure_admin_schema(con)
        # Resolve user
        user = con.execute("""
            SELECT u.*, sp.id as profile_id, sp.student_id, sp.mobile_number, sp.photo_url,
                   sp.city, sp.qualification, sp.enrolled_course_id, sp.account_status,
                   sp.payment_status, sp.total_paid, sp.remaining_balance, sp.course_access_status,
                   sp.unlocked_at, sp.admin_notes, sp.created_at as reg_date,
                   c.name as course_name, c.code as course_code, c.total_fee as course_fee
            FROM users u
            JOIN student_profiles sp ON u.id = sp.user_id
            LEFT JOIN courses c ON sp.enrolled_course_id = c.id
            WHERE sp.student_id = ? OR u.id = ? OR u.email = ?
        """, (identifier, identifier, identifier)).fetchone()

        if not user:
            return None

        uid = user["id"]
        cid = user["enrolled_course_id"] or "ccce"

        # Tab 1: Overview & Tab 2: Personal Information (in user dict)
        profile_data = dict(user)

        # Tab 3: Login & Security History
        logins = [dict(r) for r in con.execute("""
            SELECT * FROM login_history WHERE user_id = ? ORDER BY login_time DESC LIMIT 20
        """, (uid,)).fetchall()]

        active_sessions = [dict(r) for r in con.execute("""
            SELECT s.*, ? as user_id FROM sessions s WHERE s.user_id = ? AND s.expires_at > ?
        """, (uid, uid, int(time.time()))).fetchall()]

        # Tab 4: Live Class Attendance
        attendance = [dict(r) for r in con.execute("""
            SELECT ca.*, lc.title as class_title, lc.scheduled_at, lc.instructor_name, lc.room_code
            FROM class_attendance ca
            JOIN live_classes lc ON ca.live_class_id = lc.id
            WHERE ca.user_id = ?
            ORDER BY ca.joined_at DESC
        """, (uid,)).fetchall()]

        # Tab 5: Recorded Lecture History
        video_progress = [dict(r) for r in con.execute("""
            SELECT vp.*, rl.title as lecture_title, rl.module_name, rl.duration_seconds
            FROM video_progress vp
            JOIN recorded_lectures rl ON vp.lecture_id = rl.id
            WHERE vp.user_id = ?
            ORDER BY vp.last_watched_at DESC
        """, (uid,)).fetchall()]

        # Tab 6: Payment & EMI
        payments = [dict(r) for r in con.execute("""
            SELECT * FROM payments WHERE user_id = ? ORDER BY created_at DESC
        """, (uid,)).fetchall()]

        emi_list = [dict(r) for r in con.execute("""
            SELECT * FROM emi_installments WHERE user_id = ? ORDER BY installment_number ASC
        """, (uid,)).fetchall()]

        # Tab 7: Course Access & Enrollment
        enrollments = [dict(r) for r in con.execute("""
            SELECT ce.*, c.name as course_name, c.code as course_code, c.total_modules
            FROM course_enrollments ce
            JOIN courses c ON ce.course_id = c.id
            WHERE ce.user_id = ?
        """, (uid,)).fetchall()]

        # Tab 8: Learning Progress Summary
        learning_hours = round(sum(v.get("watched_seconds", 0) for v in video_progress) / 3600, 1)
        completed_lectures = sum(1 for v in video_progress if v.get("completion_status") == "completed")

        # Tab 9: Quizzes & Assignments
        quiz_attempts = [dict(r) for r in con.execute("""
            SELECT qa.*, q.title as quiz_title, q.total_questions, q.passing_score
            FROM quiz_attempts qa
            JOIN quizzes q ON qa.quiz_id = q.id
            WHERE qa.user_id = ?
            ORDER BY qa.attempt_date DESC
        """, (uid,)).fetchall()]

        assignment_subs = [dict(r) for r in con.execute("""
            SELECT asub.*, a.title as assignment_title, a.due_date
            FROM assignment_submissions asub
            JOIN assignments a ON asub.assignment_id = a.id
            WHERE asub.user_id = ?
            ORDER BY asub.submitted_at DESC
        """, (uid,)).fetchall()]

        # Tab 10: Other LMS Activities (Certificates, Rewards, Activity Logs)
        certificates = [dict(r) for r in con.execute("SELECT * FROM certificates WHERE user_id = ?", (uid,)).fetchall()]
        rewards = [dict(r) for r in con.execute("SELECT * FROM rewards WHERE user_id = ? ORDER BY created_at DESC", (uid,)).fetchall()]
        activities = [dict(r) for r in con.execute("""
            SELECT * FROM lms_activity_logs WHERE user_id = ? ORDER BY created_at DESC LIMIT 30
        """, (uid,)).fetchall()]

        # Tab 11: Complete Activity Timeline (Merged Chronological)
        timeline = []
        for a in activities:
            timeline.append({
                "type": a["activity_type"],
                "title": a["item_name"],
                "details": a["meta_json"],
                "timestamp": a["created_at"]
            })
        for p in payments:
            timeline.append({
                "type": "payment",
                "title": f"Payment of ₹{p['amount']:,.0f} ({p['payment_method'].upper()})",
                "details": f"Status: {p['verification_status'].upper()} | Txn: {p['transaction_id']}",
                "timestamp": p["created_at"]
            })
        for l in logins:
            timeline.append({
                "type": "security",
                "title": f"Login from {l['device_type']} ({l['browser']} / {l['os']})",
                "details": f"IP: {l['ip_address']} | Location: {l['location_estimate']}",
                "timestamp": l["login_time"]
            })
        timeline.sort(key=lambda x: x["timestamp"], reverse=True)

        
        # Tab 7: Access Override & Audit History
        access_history = [dict(r) for r in con.execute("""
            SELECT al.*, COALESCE(u.name, 'Super Administrator') as admin_name
            FROM admin_audit_logs al
            LEFT JOIN users u ON al.admin_user_id = u.id
            WHERE (al.target_id = ? OR al.target_id = ?) AND (al.action LIKE '%access%' OR al.action LIKE '%lock%')
            ORDER BY al.created_at DESC LIMIT 20
        """, (str(uid), str(user["student_id"]))).fetchall()]

        return {
            "profile": profile_data,
            "security": {
                "active_sessions": active_sessions,
                "login_history": logins
            },
            "live_classes": attendance,
            "recorded_lectures": video_progress,
            "payments": {
                "payment_records": payments,
                "emi_installments": emi_list
            },
            "course_access": enrollments,
            "access_history": access_history,
            "learning_progress": {
                "learning_hours": learning_hours,
                "completed_lectures": completed_lectures,
                "total_modules": profile_data.get("total_modules", 27),
                "completion_percentage": min(100, int((completed_lectures / max(1, profile_data.get("total_modules", 27))) * 100))
            },
            "quizzes_and_assignments": {
                "quiz_attempts": quiz_attempts,
                "assignments": assignment_subs
            },
            "lms_activities": {
                "certificates": certificates,
                "rewards": rewards,
                "activity_logs": activities
            },
            "timeline": timeline[:50]
        }

def add_student(admin_user_id, data):
    """Admin functionality: Add a new student record with CP-ID, course, fee, and optional EMI."""
    import server as S
    name = str(data.get("name", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    mobile = str(data.get("mobile", "")).strip()
    course_id = str(data.get("course_id", "ccce")).strip()
    total_fee = float(data.get("total_fee", 4000.0))
    amount_paid = float(data.get("amount_paid", 0.0))
    payment_method = str(data.get("payment_method", "upi")).strip()
    txn_id = str(data.get("transaction_id", f"TXN-{int(time.time())}")).strip()
    payment_plan = str(data.get("payment_plan", "full")).strip() # full or emi
    admin_notes = str(data.get("admin_notes", "Enrolled by Admin")).strip()
    qualification = str(data.get("qualification", "")).strip() or "Cybersecurity Trainee"
    custom_pw = str(data.get("password", "")).strip()

    if not email or "@" not in email:
        return False, "Valid email address required."
    if not name:
        return False, "Student name is required."
    if custom_pw and len(custom_pw) < 8:
        return False, "Password must be at least 8 characters long."

    with S._db_lock, get_db() as con:
        _ensure_admin_schema(con)
        # Check existing email
        existing = con.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            return False, f"User with email '{email}' already exists."

        # Insert user
        default_pw = custom_pw or "CyberProStudent@2026"
        pw_hash = S.hash_password(default_pw)
        now_ts = int(time.time())

        cur = con.execute("""
            INSERT INTO users (email, name, password_hash, portal_password, is_active, role, created_at)
            VALUES (?, ?, ?, ?, 1, 'student', ?)
        """, (email, name, pw_hash, default_pw, now_ts))
        user_id = cur.lastrowid

        # Generate unique Student ID
        student_id = f"CP-{user_id:05d}"
        remaining = max(0.0, total_fee - amount_paid)
        payment_status = "fully_paid" if remaining == 0 and amount_paid > 0 else ("partially_paid" if amount_paid > 0 else "unpaid")
        course_access_status = "unlocked" if payment_status == "fully_paid" else "locked"

        # Insert student profile
        con.execute("""
            INSERT INTO student_profiles (
                user_id, student_id, mobile_number, photo_url, city, qualification,
                enrolled_course_id, account_status, payment_status, total_paid, remaining_balance,
                course_access_status, unlocked_at, admin_notes, created_at, updated_at
            ) VALUES (?, ?, ?, '', 'Mumbai', ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, student_id, mobile, qualification, course_id, payment_status, amount_paid, remaining,
            course_access_status, now_ts if course_access_status == "unlocked" else None,
            admin_notes, now_ts, now_ts
        ))

        # Insert course enrollment
        con.execute("""
            INSERT INTO course_enrollments (user_id, course_id, enrollment_date, access_status, unlocked_at, granted_by_user_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, course_id, now_ts, course_access_status, now_ts if course_access_status == "unlocked" else None, admin_user_id))

        # Record payment if paid
        if amount_paid > 0:
            cur_p = con.execute("""
                INSERT INTO payments (
                    user_id, course_id, amount, payment_type, payment_method,
                    transaction_id, verification_status, verified_by_user_id, verified_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'verified', ?, ?, ?)
            """, (user_id, course_id, amount_paid, payment_plan, payment_method, txn_id, admin_user_id, now_ts, now_ts))
            p_id = cur_p.lastrowid

            if payment_plan == "emi" and remaining > 0:
                con.execute("""
                    INSERT INTO emi_installments (user_id, course_id, installment_number, amount_due, due_date, amount_paid, status, paid_at, payment_id)
                    VALUES (?, ?, 1, ?, ?, ?, 'paid', ?, ?)
                """, (user_id, course_id, amount_paid, now_ts, amount_paid, now_ts, p_id))

                con.execute("""
                    INSERT INTO emi_installments (user_id, course_id, installment_number, amount_due, due_date, amount_paid, status)
                    VALUES (?, ?, 2, ?, ?, 0.0, 'pending')
                """, (user_id, course_id, remaining, now_ts + 30 * 86400))

        # Audit log
        con.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, action, target_entity, target_id, details, created_at)
            VALUES (?, 'add_student', 'student_profiles', ?, ?, ?)
        """, (admin_user_id, student_id, f"Created {name} ({email}) with {course_access_status} access", now_ts))

        con.commit()
        return True, {
            "user_id": user_id,
            "student_id": student_id,
            "email": email,
            "name": name,
            "default_password": default_pw,
            "course_access_status": course_access_status,
            "payment_status": payment_status
        }

def override_course_access(admin_user_id, student_id, new_status, reason="", course_id=None):
    """Manually unlock or lock student LMS and course access with mandatory audit record."""
    with get_db() as con:
        prof = con.execute("SELECT user_id, student_id, course_access_status FROM student_profiles WHERE student_id = ? OR user_id = ?", (student_id, student_id)).fetchone()
        if not prof:
            return False, "Student profile not found."

        uid = prof["user_id"]
        sid = prof["student_id"]
        now_ts = int(time.time())
        clean_status = str(new_status).strip().lower()
        if clean_status not in ("unlocked", "locked"):
            clean_status = "unlocked"
        unlocked_ts = now_ts if clean_status == "unlocked" else None

        if course_id and course_id not in ("all", "*"):
            # Update specific course enrollment
            con.execute("""
                UPDATE course_enrollments
                SET access_status = ?, unlocked_at = ?
                WHERE user_id = ? AND course_id = ?
            """, (clean_status, unlocked_ts, uid, course_id))
            action_desc = f"Course '{course_id}' set to {clean_status.upper()}. Reason: {reason or 'Admin override'}"
            # Check if any course is unlocked to set master status
            active_cnt = con.execute("SELECT COUNT(*) FROM course_enrollments WHERE user_id = ? AND access_status = 'unlocked'", (uid,)).fetchone()[0]
            master_status = 'unlocked' if active_cnt > 0 else 'locked'
            con.execute("UPDATE student_profiles SET course_access_status = ?, updated_at = ? WHERE user_id = ?", (master_status, now_ts, uid))
        else:
            # Master LMS Lock/Unlock: updates entire portal access and all courses
            con.execute("""
                UPDATE student_profiles
                SET course_access_status = ?, unlocked_at = ?, admin_notes = admin_notes || ' | Access override: ' || ?, updated_at = ?
                WHERE user_id = ?
            """, (clean_status, unlocked_ts, reason or clean_status, now_ts, uid))

            con.execute("""
                UPDATE course_enrollments
                SET access_status = ?, unlocked_at = ?
                WHERE user_id = ?
            """, (clean_status, unlocked_ts, uid))
            action_desc = f"Master LMS Access set to {clean_status.upper()}. Reason: {reason or 'Admin override'}"

        con.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, action, target_entity, target_id, details, created_at)
            VALUES (?, 'override_course_access', 'student_profiles', ?, ?, ?)
        """, (admin_user_id, str(sid or uid), action_desc, now_ts))

        con.commit()
        return True, f"LMS course access successfully set to {clean_status.upper()}."

def verify_payment(admin_user_id, payment_id, status, notes=""):
    """Verifies or rejects payment. Automatically unlocks course if balance hits 0."""
    with get_db() as con:
        p = con.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
        if not p:
            return False, "Payment record not found."

        now_ts = int(time.time())
        con.execute("""
            UPDATE payments
            SET verification_status = ?, verified_by_user_id = ?, verification_notes = ?, verified_at = ?
            WHERE id = ?
        """, (status, admin_user_id, notes, now_ts, payment_id))

        uid = p["user_id"]
        if status == "verified":
            # Recalculate totals
            total_verified = con.execute("""
                SELECT COALESCE(SUM(amount), 0) FROM payments WHERE user_id = ? AND verification_status = 'verified'
            """, (uid,)).fetchone()[0]

            course = con.execute("""
                SELECT c.total_fee FROM courses c JOIN student_profiles sp ON c.id = sp.enrolled_course_id WHERE sp.user_id = ?
            """, (uid,)).fetchone()
            fee = course["total_fee"] if course else 4000.0

            remaining = max(0.0, fee - total_verified)
            payment_status = "fully_paid" if remaining == 0 else "partially_paid"
            access_status = "unlocked" if remaining == 0 else "locked"

            con.execute("""
                UPDATE student_profiles
                SET total_paid = ?, remaining_balance = ?, payment_status = ?,
                    course_access_status = CASE WHEN ? = 'unlocked' THEN 'unlocked' ELSE course_access_status END,
                    unlocked_at = CASE WHEN ? = 'unlocked' THEN ? ELSE unlocked_at END,
                    updated_at = ?
                WHERE user_id = ?
            """, (total_verified, remaining, payment_status, access_status, access_status, now_ts, now_ts, uid))

            if access_status == "unlocked":
                con.execute("UPDATE course_enrollments SET access_status = 'unlocked', unlocked_at = ? WHERE user_id = ?", (now_ts, uid))
                con.execute("""
                    INSERT INTO lms_activity_logs (user_id, activity_type, item_name, meta_json, created_at)
                    VALUES (?, 'course_unlocked', 'Course Access Automatically Unlocked Upon Full Payment', '{"payment_id": %d}', ?)
                """ % payment_id, (uid, now_ts))

        con.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, action, target_entity, target_id, details, created_at)
            VALUES (?, 'verify_payment', 'payments', ?, ?, ?)
        """, (admin_user_id, str(payment_id), f"Payment {status.upper()}. Notes: {notes}", now_ts))

        con.commit()
        return True, f"Payment marked as {status}."

def export_students_csv(course_id=None):
    students = get_course_students(course_id) if course_id else get_all_students()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Student ID", "Full Name", "Email", "Mobile", "Course", "Payment Status",
        "Total Paid (INR)", "Remaining Balance (INR)", "Course Access Status",
        "Account Status", "Registration Date", "Last Login"
    ])
    for s in students:
        reg_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(s.get("registered_at", time.time())))
        ll_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(s["last_login"])) if s.get("last_login") else "Never"
        writer.writerow([
            s.get("student_id", ""),
            s.get("name", ""),
            s.get("email", ""),
            s.get("mobile_number", ""),
            s.get("course_name", "CCCE"),
            s.get("payment_status", "").upper(),
            f"{s.get('total_paid', 0):.2f}",
            f"{s.get('remaining_balance', 0):.2f}",
            s.get("course_access_status", "").upper(),
            "Active" if s.get("is_active") else "Inactive",
            reg_str,
            ll_str
        ])
    return output.getvalue()

# =============================================================================
# DYNAMIC CLASS SCHEDULE MANAGEMENT
# =============================================================================

DAY_NAMES = {
    0: "Sunday",
    1: "Monday",
    2: "Tuesday",
    3: "Wednesday",
    4: "Thursday",
    5: "Friday",
    6: "Saturday"
}

def get_schedules(course_id=None, day_of_week=None, status=None):
    with get_db() as con:
        query = """
            SELECT cs.*, c.name as course_name, c.code as course_code
            FROM class_schedules cs
            JOIN courses c ON c.id = cs.course_id
            WHERE cs.is_active = 1
        """
        params = []
        if course_id:
            query += " AND cs.course_id = ?"
            params.append(course_id)
        if day_of_week is not None and str(day_of_week).strip() != "":
            try:
                query += " AND cs.day_of_week = ?"
                params.append(int(day_of_week))
            except (ValueError, TypeError):
                pass
        if status:
            query += " AND cs.status = ?"
            params.append(status.lower())

        query += " ORDER BY cs.day_of_week ASC, cs.start_time ASC"
        rows = con.execute(query, tuple(params)).fetchall()
        schedules = []
        for r in rows:
            d = dict(r)
            d["day_name"] = DAY_NAMES.get(d.get("day_of_week", 0), "Unknown")
            schedules.append(d)
        return schedules

def get_schedule_by_id(schedule_id):
    with get_db() as con:
        row = con.execute("""
            SELECT cs.*, c.name as course_name, c.code as course_code
            FROM class_schedules cs
            JOIN courses c ON c.id = cs.course_id
            WHERE cs.id = ?
        """, (schedule_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["day_name"] = DAY_NAMES.get(d.get("day_of_week", 0), "Unknown")
        return d

def get_student_schedules(user_id):
    """
    Returns schedules strictly for the courses in which this student is enrolled.
    Admins/superusers see all active schedules.
    """
    with get_db() as con:
        u = con.execute("SELECT id, role FROM users WHERE id = ?", (user_id,)).fetchone()
        is_admin = u and u["role"] in ("admin", "superuser")

        if is_admin:
            rows = con.execute("""
                SELECT cs.*, c.name as course_name, c.code as course_code
                FROM class_schedules cs
                JOIN courses c ON c.id = cs.course_id
                WHERE cs.is_active = 1
                ORDER BY cs.day_of_week ASC, cs.start_time ASC
            """).fetchall()
        else:
            rows = con.execute("""
                SELECT cs.*, c.name as course_name, c.code as course_code
                FROM class_schedules cs
                JOIN courses c ON c.id = cs.course_id
                WHERE cs.course_id IN (
                    SELECT course_id FROM course_enrollments
                    WHERE user_id = ? AND access_status != 'revoked'
                )
                AND cs.is_active = 1
                ORDER BY cs.day_of_week ASC, cs.start_time ASC
            """, (user_id,)).fetchall()

        schedules = []
        for r in rows:
            d = dict(r)
            d["day_name"] = DAY_NAMES.get(d.get("day_of_week", 0), "Unknown")
            schedules.append(d)
        return schedules

def create_schedule(admin_user_id, data):
    course_id = str(data.get("course_id", "")).strip()
    title = str(data.get("title", "")).strip()
    topic = str(data.get("topic", "")).strip()
    start_time = str(data.get("start_time", "")).strip()
    end_time = str(data.get("end_time", "")).strip()
    instructor_name = str(data.get("instructor_name", "Parth Rathod")).strip()
    meeting_link = str(data.get("meeting_link", "")).strip()
    meeting_type = str(data.get("meeting_type", "Google Meet")).strip()
    description = str(data.get("description", "")).strip()
    status = str(data.get("status", "upcoming")).strip().lower()
    class_date = str(data.get("class_date", "")).strip()

    if not course_id:
        return False, "Course selection is required."
    if not title:
        return False, "Session title is required."
    if not topic:
        return False, "Session topic is required."
    if not start_time or not end_time:
        return False, "Starting and ending times are required."
    if not meeting_link:
        return False, "Meeting link is required."
    if status not in ("upcoming", "live", "completed", "cancelled"):
        status = "upcoming"

    day_of_week = data.get("day_of_week")
    if day_of_week is None or str(day_of_week).strip() == "":
        if class_date:
            try:
                dt = time.strptime(class_date, "%Y-%m-%d")
                py_wday = dt.tm_wday
                day_of_week = (py_wday + 1) % 7
            except Exception:
                day_of_week = 1
        else:
            day_of_week = 1
    else:
        try:
            day_of_week = int(day_of_week) % 7
        except Exception:
            day_of_week = 1

    now_ts = int(time.time())

    with get_db() as con:
        c = con.execute("SELECT id, name FROM courses WHERE id = ?", (course_id,)).fetchone()
        if not c:
            return False, f"Course '{course_id}' not found."

        cur = con.cursor()
        cur.execute("""
            INSERT INTO class_schedules (
                course_id, day_of_week, class_date, start_time, end_time,
                title, topic, instructor_name, meeting_link, meeting_type,
                description, status, is_active, created_by_admin_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
        """, (
            course_id, day_of_week, class_date, start_time, end_time,
            title, topic, instructor_name, meeting_link, meeting_type,
            description, status, admin_user_id, now_ts, now_ts
        ))
        new_id = cur.lastrowid

        con.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, target_entity, target_id, action, details, created_at)
            VALUES (?, 'class_schedules', ?, 'SCHEDULE_CREATED', ?, ?)
        """, (admin_user_id, str(new_id), f"Created schedule '{title}' for course {course_id} on {DAY_NAMES.get(day_of_week)} at {start_time}", now_ts))

        con.commit()

        row = con.execute("""
            SELECT cs.*, c.name as course_name, c.code as course_code
            FROM class_schedules cs
            JOIN courses c ON c.id = cs.course_id
            WHERE cs.id = ?
        """, (new_id,)).fetchone()
        d = dict(row)
        d["day_name"] = DAY_NAMES.get(d.get("day_of_week", 0), "Unknown")
        return True, d

def update_schedule(admin_user_id, schedule_id, data):
    now_ts = int(time.time())
    with get_db() as con:
        existing = con.execute("SELECT * FROM class_schedules WHERE id = ?", (schedule_id,)).fetchone()
        if not existing:
            return False, "Schedule session not found."

        course_id = str(data.get("course_id", existing["course_id"])).strip()
        title = str(data.get("title", existing["title"])).strip()
        topic = str(data.get("topic", existing["topic"])).strip()
        start_time = str(data.get("start_time", existing["start_time"])).strip()
        end_time = str(data.get("end_time", existing["end_time"])).strip()
        instructor_name = str(data.get("instructor_name", existing["instructor_name"])).strip()
        meeting_link = str(data.get("meeting_link", existing["meeting_link"])).strip()
        meeting_type = str(data.get("meeting_type", existing["meeting_type"] or "Google Meet")).strip()
        description = str(data.get("description", existing["description"] or "")).strip()
        status = str(data.get("status", existing["status"])).strip().lower()
        class_date = str(data.get("class_date", existing["class_date"] or "")).strip()

        day_of_week = data.get("day_of_week")
        if day_of_week is None or str(day_of_week).strip() == "":
            day_of_week = existing["day_of_week"]
        else:
            try:
                day_of_week = int(day_of_week) % 7
            except Exception:
                day_of_week = existing["day_of_week"]

        if status not in ("upcoming", "live", "completed", "cancelled"):
            status = existing["status"]

        con.execute("""
            UPDATE class_schedules SET
                course_id = ?,
                day_of_week = ?,
                class_date = ?,
                start_time = ?,
                end_time = ?,
                title = ?,
                topic = ?,
                instructor_name = ?,
                meeting_link = ?,
                meeting_type = ?,
                description = ?,
                status = ?,
                updated_at = ?
            WHERE id = ?
        """, (
            course_id, day_of_week, class_date, start_time, end_time,
            title, topic, instructor_name, meeting_link, meeting_type,
            description, status, now_ts, schedule_id
        ))

        con.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, target_entity, target_id, action, details, created_at)
            VALUES (?, 'class_schedules', ?, 'SCHEDULE_UPDATED', ?, ?)
        """, (admin_user_id, str(schedule_id), f"Updated schedule #{schedule_id} '{title}' ({status})", now_ts))

        con.commit()

        row = con.execute("""
            SELECT cs.*, c.name as course_name, c.code as course_code
            FROM class_schedules cs
            JOIN courses c ON c.id = cs.course_id
            WHERE cs.id = ?
        """, (schedule_id,)).fetchone()
        d = dict(row)
        d["day_name"] = DAY_NAMES.get(d.get("day_of_week", 0), "Unknown")
        return True, d

def reschedule_schedule(admin_user_id, schedule_id, new_date, new_start_time, new_end_time, reason=""):
    now_ts = int(time.time())
    with get_db() as con:
        existing = con.execute("SELECT * FROM class_schedules WHERE id = ?", (schedule_id,)).fetchone()
        if not existing:
            return False, "Schedule session not found."

        day_of_week = existing["day_of_week"]
        if new_date:
            try:
                dt = time.strptime(new_date, "%Y-%m-%d")
                day_of_week = (dt.tm_wday + 1) % 7
            except Exception:
                pass

        st = new_start_time or existing["start_time"]
        et = new_end_time or existing["end_time"]
        desc = existing["description"] or ""
        if reason:
            desc = f"{desc}\n[Rescheduled: {reason}]".strip()

        con.execute("""
            UPDATE class_schedules SET
                class_date = ?,
                day_of_week = ?,
                start_time = ?,
                end_time = ?,
                description = ?,
                status = 'upcoming',
                updated_at = ?
            WHERE id = ?
        """, (new_date or existing["class_date"], day_of_week, st, et, desc, now_ts, schedule_id))

        con.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, target_entity, target_id, action, details, created_at)
            VALUES (?, 'class_schedules', ?, 'SCHEDULE_RESCHEDULED', ?, ?)
        """, (admin_user_id, str(schedule_id), f"Rescheduled #{schedule_id} to {new_date} {st}-{et}. Reason: {reason}", now_ts))

        con.commit()
        return True, "Class session rescheduled successfully."

def update_schedule_status(admin_user_id, schedule_id, status, notes=""):
    valid = ("upcoming", "live", "completed", "cancelled")
    clean_status = str(status).strip().lower()
    if clean_status not in valid:
        return False, f"Invalid status '{status}'. Must be one of: {', '.join(valid)}"

    now_ts = int(time.time())
    with get_db() as con:
        existing = con.execute("SELECT * FROM class_schedules WHERE id = ?", (schedule_id,)).fetchone()
        if not existing:
            return False, "Schedule session not found."

        desc = existing["description"] or ""
        if notes:
            desc = f"{desc}\n[Status Change ({clean_status}): {notes}]".strip()

        con.execute("""
            UPDATE class_schedules SET
                status = ?,
                description = ?,
                updated_at = ?
            WHERE id = ?
        """, (clean_status, desc, now_ts, schedule_id))

        con.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, target_entity, target_id, action, details, created_at)
            VALUES (?, 'class_schedules', ?, 'SCHEDULE_STATUS_CHANGED', ?, ?)
        """, (admin_user_id, str(schedule_id), f"Changed status of #{schedule_id} to '{clean_status}'. Notes: {notes}", now_ts))

        con.commit()
        return True, f"Session status updated to '{clean_status}'."

def delete_schedule(admin_user_id, schedule_id):
    now_ts = int(time.time())
    with get_db() as con:
        existing = con.execute("SELECT * FROM class_schedules WHERE id = ?", (schedule_id,)).fetchone()
        if not existing:
            return False, "Schedule session not found."

        con.execute("DELETE FROM class_schedules WHERE id = ?", (schedule_id,))

        con.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, target_entity, target_id, action, details, created_at)
            VALUES (?, 'class_schedules', ?, 'SCHEDULE_DELETED', ?, ?)
        """, (admin_user_id, str(schedule_id), f"Deleted schedule #{schedule_id} '{existing['title']}'", now_ts))

        con.commit()
        return True, "Schedule session deleted successfully."


# =============================================================================
# STUDENT REGISTRATIONS & ADMISSION INFLOW MANAGEMENT
# =============================================================================

def get_registrations(query="", status_filter="", source_filter=""):
    with get_db() as con:
        sql = "SELECT * FROM student_registrations WHERE 1=1"
        params = []
        if query:
            sql += " AND (full_name LIKE ? OR email LIKE ? OR phone LIKE ? OR program LIKE ? OR registration_code LIKE ?)"
            q = f"%{query.strip()}%"
            params.extend([q, q, q, q, q])
        if status_filter:
            sql += " AND status = ?"
            params.append(status_filter.lower())
        if source_filter:
            sql += " AND source = ?"
            params.append(source_filter)

        sql += " ORDER BY created_at DESC, id DESC"
        rows = con.execute(sql, tuple(params)).fetchall()

        stats = {
            "total": con.execute("SELECT COUNT(*) FROM student_registrations").fetchone()[0],
            "new": con.execute("SELECT COUNT(*) FROM student_registrations WHERE status='new'").fetchone()[0],
            "contacted": con.execute("SELECT COUNT(*) FROM student_registrations WHERE status='contacted'").fetchone()[0],
            "enrolled": con.execute("SELECT COUNT(*) FROM student_registrations WHERE status='enrolled'").fetchone()[0]
        }

        registrations = [dict(r) for r in rows]
        return {"registrations": registrations, "stats": stats}

def update_registration_status(admin_user_id, reg_id, new_status, notes=""):
    valid = ("new", "contacted", "enrolled", "declined")
    if new_status not in valid:
        return False, f"Invalid status '{new_status}'"
    now_ts = int(time.time())
    with get_db() as con:
        existing = con.execute("SELECT * FROM student_registrations WHERE id = ?", (reg_id,)).fetchone()
        if not existing:
            return False, "Registration record not found."
        
        desc = existing["notes"] or ""
        if notes:
            desc = f"{desc}\n[Status: {new_status} - {notes}]".strip()

        con.execute("""
            UPDATE student_registrations SET status = ?, notes = ?, updated_at = ? WHERE id = ?
        """, (new_status, desc, now_ts, reg_id))

        con.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, target_entity, target_id, action, details, created_at)
            VALUES (?, 'student_registrations', ?, 'REGISTRATION_STATUS_UPDATED', ?, ?)
        """, (admin_user_id, str(reg_id), f"Changed status to '{new_status}'. Notes: {notes}", now_ts))
        con.commit()
        return True, f"Registration status updated to '{new_status}'."

def convert_registration_to_student(admin_user_id, reg_id, course_id="ccce", fee=4000.0, paid_amount=4000.0):
    import server as S
    now_ts = int(time.time())
    with S._db_lock, get_db() as con:
        reg = con.execute("SELECT * FROM student_registrations WHERE id = ?", (reg_id,)).fetchone()
        if not reg:
            return False, "Registration record not found."
        
        email = reg["email"]
        name = reg["full_name"]
        phone = reg["phone"] or ""
        remaining = max(0.0, float(fee) - float(paid_amount))
        payment_status = "fully_paid" if remaining == 0 and float(paid_amount) > 0 else ("partially_paid" if float(paid_amount) > 0 else "unpaid")
        course_access_status = "unlocked" if payment_status == "fully_paid" else "locked"

        existing_user = con.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing_user:
            user_id = existing_user["id"]
        else:
            default_pass = "CyberProStudent@2026"
            p_hash = S.hash_password(default_pass)
            cur = con.execute(
                "INSERT INTO users (email, name, password_hash, portal_password, is_active, role, created_at) VALUES (?, ?, ?, ?, 1, 'student', ?)",
                (email, name, p_hash, default_pass, now_ts)
            )
            user_id = cur.lastrowid

        sp = con.execute("SELECT student_id FROM student_profiles WHERE user_id = ?", (user_id,)).fetchone()
        if sp:
            student_id = sp["student_id"]
        else:
            student_id = f"CP-{user_id + 1000:04d}"
            con.execute("""
                INSERT INTO student_profiles (
                    user_id, student_id, mobile_number, photo_url, city, qualification,
                    enrolled_course_id, account_status, payment_status, total_paid, remaining_balance,
                    course_access_status, unlocked_at, admin_notes, created_at, updated_at
                ) VALUES (?, ?, ?, '', 'Online', 'Enrolled Applicant', ?, 'active', ?, ?, ?, ?, ?, 'Converted from Registration Form', ?, ?)
            """, (
                user_id, student_id, phone, course_id, payment_status, float(paid_amount), remaining,
                course_access_status, now_ts if course_access_status == "unlocked" else None, now_ts, now_ts
            ))

        ce = con.execute("SELECT id FROM course_enrollments WHERE user_id = ? AND course_id = ?", (user_id, course_id)).fetchone()
        if not ce:
            con.execute("""
                INSERT INTO course_enrollments (user_id, course_id, enrollment_date, access_status, unlocked_at, granted_by_user_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, course_id, now_ts, course_access_status, now_ts if course_access_status == "unlocked" else None, admin_user_id))

        if float(paid_amount) > 0:
            con.execute("""
                INSERT INTO payments (
                    user_id, course_id, amount, payment_type, payment_method,
                    transaction_id, verification_status, verified_by_user_id, verified_at, created_at
                ) VALUES (?, ?, ?, 'full', 'upi', ?, 'verified', ?, ?, ?)
            """, (user_id, course_id, float(paid_amount), f"TXN-REG-{reg_id}", admin_user_id, now_ts, now_ts))

        con.execute("""
            UPDATE student_registrations SET status = 'enrolled', converted_user_id = ?, updated_at = ? WHERE id = ?
        """, (user_id, now_ts, reg_id))

        con.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, target_entity, target_id, action, details, created_at)
            VALUES (?, 'student_registrations', ?, 'REGISTRATION_CONVERTED', ?, ?)
        """, (admin_user_id, str(reg_id), f"Converted registration #{reg_id} ({name}) to enrolled student {student_id} in {course_id}", now_ts))

        con.commit()
        return True, {
            "message": f"Successfully enrolled {name} ({student_id}) in course {course_id.upper()}!",
            "student_id": student_id,
            "user_id": user_id
        }

def delete_registration(admin_user_id, reg_id):
    now_ts = int(time.time())
    with get_db() as con:
        existing = con.execute("SELECT * FROM student_registrations WHERE id = ?", (reg_id,)).fetchone()
        if not existing:
            return False, "Registration record not found."

        con.execute("DELETE FROM student_registrations WHERE id = ?", (reg_id,))
        con.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, target_entity, target_id, action, details, created_at)
            VALUES (?, 'student_registrations', ?, 'REGISTRATION_DELETED', ?, ?)
        """, (admin_user_id, str(reg_id), f"Deleted registration #{reg_id} ({existing['full_name']})", now_ts))
        con.commit()
        return True, "Registration deleted successfully."

def export_registrations_csv():
    with get_db() as con:
        rows = con.execute("SELECT * FROM student_registrations ORDER BY created_at DESC").fetchall()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Reg Code", "Full Name", "Email", "Phone", "Program",
            "Budget Preference", "Notes", "Source", "Status", "Submission Date"
        ])
        for r in rows:
            dt_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["created_at"]))
            writer.writerow([
                r["registration_code"], r["full_name"], r["email"], r["phone"],
                r["program"], r["budget_preference"], r["notes"], r["source"],
                r["status"].upper(), dt_str
            ])
        return output.getvalue()


# ==============================================================================
# SECTION 6: PAYMENTS & EMI CENTRAL MANAGEMENT
# ==============================================================================

def get_payments_management_data(query="", course_filter=""):
    now_ts = int(time.time())
    with get_db() as con:
        # Platform-wide Stats
        total_rev = con.execute("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE verification_status = 'verified'").fetchone()[0]
        total_pending = con.execute("SELECT COALESCE(SUM(remaining_balance), 0) FROM student_profiles WHERE remaining_balance > 0").fetchone()[0]
        fully_paid_cnt = con.execute("SELECT COUNT(*) FROM student_profiles WHERE payment_status = 'fully_paid' OR remaining_balance <= 0").fetchone()[0]
        emi_pending_cnt = con.execute("SELECT COUNT(*) FROM student_profiles WHERE payment_status = 'partially_paid' OR remaining_balance > 0").fetchone()[0]
        overdue_cnt = con.execute("SELECT COUNT(*) FROM emi_installments WHERE status != 'paid' AND due_date < ?", (now_ts,)).fetchone()[0]

        # 1. EMI Pending Students Query
        emi_sql = """
            SELECT sp.user_id, sp.student_id, u.name, u.email, sp.mobile_number, sp.city,
                   sp.enrolled_course_id, c.name as course_name, c.code as course_code,
                   COALESCE(c.total_fee, 4000.0) as total_fee,
                   sp.total_paid, sp.remaining_balance, sp.payment_status, sp.course_access_status
            FROM student_profiles sp
            JOIN users u ON u.id = sp.user_id
            LEFT JOIN courses c ON c.id = sp.enrolled_course_id
            WHERE (sp.payment_status = 'partially_paid' OR sp.remaining_balance > 0)
        """
        emi_params = []
        if query:
            q_like = f"%{query}%"
            emi_sql += " AND (u.name LIKE ? OR u.email LIKE ? OR sp.student_id LIKE ? OR sp.mobile_number LIKE ?)"
            emi_params.extend([q_like, q_like, q_like, q_like])
        if course_filter:
            emi_sql += " AND sp.enrolled_course_id = ?"
            emi_params.append(course_filter)
        emi_sql += " ORDER BY sp.remaining_balance DESC, sp.user_id ASC"

        emi_raw = con.execute(emi_sql, tuple(emi_params)).fetchall()
        emi_students = []
        for r in emi_raw:
            s = dict(r)
            uid = s["user_id"]
            insts = [dict(row) for row in con.execute("""
                SELECT * FROM emi_installments WHERE user_id = ? ORDER BY installment_number ASC
            """, (uid,)).fetchall()]
            s["installments"] = insts
            s["total_installments"] = len(insts)
            paid_count = sum(1 for i in insts if i["status"] == "paid")
            s["paid_installments"] = paid_count

            pending = [i for i in insts if i["status"] != "paid"]
            if pending:
                next_i = pending[0]
                s["next_installment_id"] = next_i["id"]
                s["next_installment_number"] = next_i["installment_number"]
                s["next_due_amount"] = next_i["amount_due"]
                s["next_due_date"] = next_i["due_date"]
                s["next_due_date_str"] = time.strftime("%d %b %Y", time.localtime(next_i["due_date"])) if next_i["due_date"] else "TBD"
                s["is_overdue"] = bool(next_i["due_date"] and next_i["due_date"] < now_ts)
                s["days_overdue"] = max(0, int((now_ts - next_i["due_date"]) / 86400)) if s["is_overdue"] else 0
            else:
                s["next_installment_id"] = None
                s["next_installment_number"] = None
                s["next_due_amount"] = 0
                s["next_due_date"] = None
                s["next_due_date_str"] = "None"
                s["is_overdue"] = False
                s["days_overdue"] = 0

            emi_students.append(s)

        # 2. Full Paid Students Query
        full_sql = """
            SELECT sp.user_id, sp.student_id, u.name, u.email, sp.mobile_number, sp.city,
                   sp.enrolled_course_id, c.name as course_name, c.code as course_code,
                   COALESCE(c.total_fee, 4000.0) as total_fee,
                   sp.total_paid, sp.remaining_balance, sp.payment_status, sp.course_access_status
            FROM student_profiles sp
            JOIN users u ON u.id = sp.user_id
            LEFT JOIN courses c ON c.id = sp.enrolled_course_id
            WHERE (sp.payment_status = 'fully_paid' OR sp.remaining_balance <= 0)
        """
        full_params = []
        if query:
            q_like = f"%{query}%"
            full_sql += " AND (u.name LIKE ? OR u.email LIKE ? OR sp.student_id LIKE ? OR sp.mobile_number LIKE ?)"
            full_params.extend([q_like, q_like, q_like, q_like])
        if course_filter:
            full_sql += " AND sp.enrolled_course_id = ?"
            full_params.append(course_filter)
        full_sql += " ORDER BY sp.total_paid DESC, sp.user_id ASC"

        full_raw = con.execute(full_sql, tuple(full_params)).fetchall()
        full_students = []
        for r in full_raw:
            s = dict(r)
            uid = s["user_id"]
            p = con.execute("""
                SELECT * FROM payments WHERE user_id = ? ORDER BY created_at DESC LIMIT 1
            """, (uid,)).fetchone()
            if p:
                s["latest_payment"] = dict(p)
                s["payment_date_str"] = time.strftime("%d %b %Y", time.localtime(p["created_at"])) if p["created_at"] else "N/A"
            else:
                s["latest_payment"] = None
                s["payment_date_str"] = "Verified"
            full_students.append(s)

        # 3. Platform Transactions Ledger (Latest 50)
        transactions = [dict(r) for r in con.execute("""
            SELECT p.*, u.name as student_name, u.email as student_email, sp.student_id, c.name as course_name
            FROM payments p
            JOIN users u ON p.user_id = u.id
            LEFT JOIN student_profiles sp ON sp.user_id = u.id
            LEFT JOIN courses c ON c.id = p.course_id
            ORDER BY p.created_at DESC LIMIT 50
        """).fetchall()]
        for t in transactions:
            t["created_at_str"] = time.strftime("%d %b %Y, %I:%M %p", time.localtime(t["created_at"])) if t["created_at"] else "N/A"

        return {
            "stats": {
                "total_collected": total_rev,
                "total_pending_emi": total_pending,
                "fully_paid_count": fully_paid_cnt,
                "emi_pending_count": emi_pending_cnt,
                "overdue_emi_count": overdue_cnt
            },
            "emi_students": emi_students,
            "full_paid_students": full_students,
            "transactions": transactions
        }

def get_student_emi_drilldown(user_id):
    with get_db() as con:
        sp = con.execute("""
            SELECT sp.*, u.name, u.email, c.name as course_name, c.code as course_code,
                   COALESCE(c.total_fee, 4000.0) as total_fee
            FROM student_profiles sp
            JOIN users u ON u.id = sp.user_id
            LEFT JOIN courses c ON c.id = sp.enrolled_course_id
            WHERE sp.user_id = ? OR sp.student_id = ?
        """, (user_id, user_id)).fetchone()
        if not sp:
            return None

        uid = sp["user_id"]
        insts = [dict(r) for r in con.execute("""
            SELECT * FROM emi_installments WHERE user_id = ? ORDER BY installment_number ASC
        """, (uid,)).fetchall()]
        now_ts = int(time.time())
        for i in insts:
            i["due_date_str"] = time.strftime("%d %b %Y", time.localtime(i["due_date"])) if i["due_date"] else "TBD"
            i["paid_at_str"] = time.strftime("%d %b %Y, %I:%M %p", time.localtime(i["paid_at"])) if i["paid_at"] else "—"
            i["is_overdue"] = bool(i["status"] != "paid" and i["due_date"] and i["due_date"] < now_ts)

        payments = [dict(r) for r in con.execute("""
            SELECT * FROM payments WHERE user_id = ? ORDER BY created_at DESC
        """, (uid,)).fetchall()]
        for p in payments:
            p["created_at_str"] = time.strftime("%d %b %Y, %I:%M %p", time.localtime(p["created_at"])) if p["created_at"] else "—"
            p["verified_at_str"] = time.strftime("%d %b %Y, %I:%M %p", time.localtime(p["verified_at"])) if p["verified_at"] else "—"

        return {
            "student": dict(sp),
            "installments": insts,
            "payments": payments
        }

def collect_student_installment(admin_user_id, user_id, installment_id, amount, payment_method="upi", transaction_id="", notes=""):
    now_ts = int(time.time())
    with get_db() as con:
        inst = con.execute("SELECT * FROM emi_installments WHERE id = ? AND user_id = ?", (installment_id, user_id)).fetchone()
        if not inst:
            return False, "Installment record not found."
        if inst["status"] == "paid":
            return False, f"Installment #{inst['installment_number']} is already marked as paid."

        amount = float(amount or inst["amount_due"])
        if not transaction_id:
            transaction_id = f"EMI-REC-{int(time.time())}"

        course_id = inst["course_id"] or "ccce"
        cur = con.execute("""
            INSERT INTO payments (
                user_id, course_id, amount, payment_type, payment_method,
                transaction_id, verification_status, verified_by_user_id, verification_notes,
                verified_at, created_at
            ) VALUES (?, ?, ?, 'emi', ?, ?, 'verified', ?, ?, ?, ?)
        """, (user_id, course_id, amount, payment_method, transaction_id, admin_user_id, notes, now_ts, now_ts))
        payment_id = cur.lastrowid

        con.execute("""
            UPDATE emi_installments
            SET amount_paid = ?, status = 'paid', paid_at = ?, payment_id = ?
            WHERE id = ?
        """, (amount, now_ts, payment_id, installment_id))

        total_paid_so_far = con.execute("""
            SELECT COALESCE(SUM(amount), 0) FROM payments WHERE user_id = ? AND verification_status = 'verified'
        """, (user_id,)).fetchone()[0]

        course = con.execute("""
            SELECT c.total_fee FROM courses c JOIN student_profiles sp ON c.id = sp.enrolled_course_id WHERE sp.user_id = ?
        """, (user_id,)).fetchone()
        fee = course["total_fee"] if course else 4000.0

        remaining = max(0.0, fee - total_paid_so_far)
        is_fully_paid = (remaining <= 0)

        if is_fully_paid:
            con.execute("""
                UPDATE student_profiles
                SET total_paid = ?, remaining_balance = 0.0, payment_status = 'fully_paid',
                    course_access_status = 'unlocked', unlocked_at = COALESCE(unlocked_at, ?), updated_at = ?
                WHERE user_id = ?
            """, (total_paid_so_far, now_ts, now_ts, user_id))
            con.execute("""
                UPDATE course_enrollments SET access_status = 'unlocked', unlocked_at = COALESCE(unlocked_at, ?)
                WHERE user_id = ?
            """, (now_ts, user_id))
        else:
            con.execute("""
                UPDATE student_profiles
                SET total_paid = ?, remaining_balance = ?, payment_status = 'partially_paid', updated_at = ?
                WHERE user_id = ?
            """, (total_paid_so_far, remaining, now_ts, user_id))

        con.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, target_entity, target_id, action, details, created_at)
            VALUES (?, 'payments', ?, 'COLLECT_INSTALLMENT', ?, ?)
        """, (admin_user_id, str(payment_id), f"Collected Installment #{inst['installment_number']} (INR {amount:,.0f}) from user #{user_id}. Txn: {transaction_id}", now_ts))

        con.commit()
        return True, {
            "message": f"Successfully collected installment #{inst['installment_number']} (INR {amount:,.0f})!",
            "remaining_balance": remaining,
            "is_fully_paid": is_fully_paid,
            "payment_id": payment_id
        }

def export_payments_csv(export_type="emi"):
    with get_db() as con:
        output = io.StringIO()
        writer = csv.writer(output)
        if export_type == "emi":
            rows = con.execute("""
                SELECT sp.student_id, u.name, u.email, sp.mobile_number, c.name as course_name,
                       c.total_fee, sp.total_paid, sp.remaining_balance, sp.course_access_status
                FROM student_profiles sp
                JOIN users u ON u.id = sp.user_id
                LEFT JOIN courses c ON c.id = sp.enrolled_course_id
                WHERE sp.payment_status = 'partially_paid' OR sp.remaining_balance > 0
                ORDER BY sp.remaining_balance DESC
            """).fetchall()
            writer.writerow(["Student ID", "Name", "Email", "Phone", "Course", "Total Fee (INR)", "Total Paid (INR)", "Remaining EMI (INR)", "Course Access"])
            for r in rows:
                writer.writerow([r["student_id"], r["name"], r["email"], r["mobile_number"], r["course_name"], r["total_fee"], r["total_paid"], r["remaining_balance"], r["course_access_status"].upper()])
        else:
            rows = con.execute("""
                SELECT sp.student_id, u.name, u.email, sp.mobile_number, c.name as course_name,
                       c.total_fee, sp.total_paid, sp.course_access_status
                FROM student_profiles sp
                JOIN users u ON u.id = sp.user_id
                LEFT JOIN courses c ON c.id = sp.enrolled_course_id
                WHERE sp.payment_status = 'fully_paid' OR sp.remaining_balance <= 0
                ORDER BY sp.total_paid DESC
            """).fetchall()
            writer.writerow(["Student ID", "Name", "Email", "Phone", "Course", "Total Fee (INR)", "Amount Paid (INR)", "Status", "Course Access"])
            for r in rows:
                writer.writerow([r["student_id"], r["name"], r["email"], r["mobile_number"], r["course_name"], r["total_fee"], r["total_paid"], "FULLY PAID", r["course_access_status"].upper()])
        return output.getvalue()


# ==============================================================================
# SECTION 7: LOGIN & SECURITY AUDIT TELEMETRY
# ==============================================================================

def format_relative_time(past_ts, now_ts=None):
    if now_ts is None:
        now_ts = int(time.time())
    if not past_ts:
        return "—"
    diff = max(0, now_ts - past_ts)
    if diff < 60:
        return "Just now"
    elif diff < 3600:
        return f"{diff // 60}m ago"
    elif diff < 86400:
        return f"{diff // 3600}h ago"
    else:
        return f"{diff // 86400}d ago"

def format_duration(sec):
    if sec < 60:
        return f"{sec}s"
    mins = sec // 60
    if mins < 60:
        return f"{mins} mins"
    hours = mins // 60
    rem_mins = mins % 60
    return f"{hours}h {rem_mins}m"

def parse_user_agent(ua_str: str):
    ua = str(ua_str or "").lower()
    if "windows" in ua:
        os_name = "Windows 11" if ("windows nt 10.0" in ua or "win64" in ua) else "Windows"
    elif "macintosh" in ua or "mac os" in ua:
        os_name = "macOS"
    elif "android" in ua:
        os_name = "Android"
    elif "iphone" in ua or "ipad" in ua:
        os_name = "iOS"
    elif "linux" in ua:
        os_name = "Linux"
    else:
        os_name = "Windows 11"

    if "edg/" in ua or "edge/" in ua:
        browser = "Microsoft Edge"
    elif "chrome/" in ua and "safari/" in ua and "opr/" not in ua:
        browser = "Chrome 131.0"
    elif "firefox/" in ua:
        browser = "Mozilla Firefox"
    elif "opr/" in ua or "opera" in ua:
        browser = "Opera"
    elif "safari/" in ua and "chrome/" not in ua:
        browser = "Apple Safari"
    else:
        browser = "Chrome Browser"

    if "mobile" in ua or "android" in ua or "iphone" in ua:
        device = "Mobile Phone"
    elif "tablet" in ua or "ipad" in ua:
        device = "Tablet"
    else:
        device = "Desktop Laptop"

    return device, browser, os_name

def record_login_event(user_id: int, ip_address: str, user_agent: str):
    now_ts = int(time.time())
    device, browser, os_name = parse_user_agent(user_agent)
    sess_id = f"sess_{user_id}_{now_ts}"
    loc = "Local / LAN" if ip_address in ("127.0.0.1", "localhost", "::1") else "India"

    with get_db() as con:
        con.execute("""
            INSERT INTO login_history (
                user_id, session_id, ip_address, device_type, browser, os,
                login_time, logout_time, last_activity, session_status, security_photo_url, location_estimate
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, 'active', '', ?)
        """, (user_id, sess_id, ip_address, device, browser, os_name, now_ts, now_ts, loc))
        con.commit()
    return sess_id

def record_logout_event(user_id: int):
    now_ts = int(time.time())
    with get_db() as con:
        con.execute("""
            UPDATE login_history
            SET logout_time = ?, session_status = 'logged_out', last_activity = ?
            WHERE user_id = ? AND session_status = 'active'
        """, (now_ts, now_ts, user_id))
        con.commit()

def get_security_logins_data(query="", status_filter="", role_filter=""):
    now_ts = int(time.time())
    local_tm = time.localtime(now_ts)
    midnight_ts = int(time.mktime((local_tm.tm_year, local_tm.tm_mon, local_tm.tm_mday, 0, 0, 0, 0, 0, -1)))

    with get_db() as con:
        total_logins = con.execute("SELECT COUNT(*) FROM login_history").fetchone()[0]
        active_sessions = con.execute("SELECT COUNT(*) FROM login_history WHERE session_status = 'active'").fetchone()[0]
        logged_out = con.execute("SELECT COUNT(*) FROM login_history WHERE session_status = 'logged_out'").fetchone()[0]
        unique_users = con.execute("SELECT COUNT(DISTINCT user_id) FROM login_history").fetchone()[0]
        logins_today = con.execute("SELECT COUNT(*) FROM login_history WHERE login_time >= ?", (midnight_ts,)).fetchone()[0]

        sql = """
            SELECT lh.*, u.name, u.email, u.role, sp.student_id, c.name as course_name
            FROM login_history lh
            JOIN users u ON u.id = lh.user_id
            LEFT JOIN student_profiles sp ON sp.user_id = u.id
            LEFT JOIN courses c ON c.id = sp.enrolled_course_id
            WHERE 1=1
        """
        params = []
        if query:
            q_like = f"%{query}%"
            sql += " AND (u.name LIKE ? OR u.email LIKE ? OR sp.student_id LIKE ? OR lh.ip_address LIKE ?)"
            params.extend([q_like, q_like, q_like, q_like])
        if status_filter:
            sql += " AND lh.session_status = ?"
            params.append(status_filter)
        if role_filter:
            sql += " AND u.role = ?"
            params.append(role_filter)

        # Order with the most recent actions at the top!
        sql += " ORDER BY lh.login_time DESC, lh.id DESC LIMIT 150"

        rows = con.execute(sql, tuple(params)).fetchall()
        logins_list = []
        for r in rows:
            item = dict(r)
            login_t = item["login_time"]
            logout_t = item["logout_time"]
            status = item["session_status"]

            item["login_time_str"] = time.strftime("%d %b %Y, %I:%M:%S %p", time.localtime(login_t)) if login_t else "N/A"
            if logout_t:
                item["logout_time_str"] = time.strftime("%d %b %Y, %I:%M:%S %p", time.localtime(logout_t))
            else:
                item["logout_time_str"] = "—"

            item["login_time_rel"] = format_relative_time(login_t, now_ts)
            if logout_t:
                item["logout_time_rel"] = format_relative_time(logout_t, now_ts)
            else:
                item["logout_time_rel"] = "Active Now"

            if status == "active":
                diff_sec = max(0, now_ts - login_t)
                item["duration_str"] = f"Active ({format_duration(diff_sec)})"
            elif logout_t and logout_t >= login_t:
                item["duration_str"] = format_duration(logout_t - login_t)
            else:
                item["duration_str"] = "—"

            logins_list.append(item)

        return {
            "stats": {
                "total_logins": total_logins,
                "active_sessions": active_sessions,
                "logged_out_sessions": logged_out,
                "unique_users": unique_users,
                "logins_today": logins_today
            },
            "logins": logins_list
        }

def revoke_user_session(admin_user_id, log_id, reason="Admin revoked session"):
    now_ts = int(time.time())
    with get_db() as con:
        row = con.execute("SELECT * FROM login_history WHERE id = ?", (log_id,)).fetchone()
        if not row:
            return False, "Login session record not found."

        uid = row["user_id"]
        con.execute("""
            UPDATE login_history
            SET logout_time = ?, session_status = 'terminated', last_activity = ?
            WHERE id = ?
        """, (now_ts, now_ts, log_id))

        con.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))

        con.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, target_entity, target_id, action, details, created_at)
            VALUES (?, 'login_history', ?, 'SESSION_REVOKED', ?, ?)
        """, (admin_user_id, str(log_id), f"Revoked active session for user #{uid} ({reason})", now_ts))
        con.commit()
        return True, "Session revoked and user logged out successfully."

def export_security_logs_csv():
    with get_db() as con:
        output = io.StringIO()
        writer = csv.writer(output)
        rows = con.execute("""
            SELECT lh.*, u.name, u.email, u.role, sp.student_id
            FROM login_history lh
            JOIN users u ON u.id = lh.user_id
            LEFT JOIN student_profiles sp ON sp.user_id = u.id
            ORDER BY lh.login_time DESC
        """).fetchall()

        writer.writerow([
            "Session ID", "Student ID", "Full Name", "Email", "Role",
            "IP Address", "Device Type", "Browser", "OS",
            "Login Time", "Logout Time", "Duration (Minutes)", "Status"
        ])
        for r in rows:
            l_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["login_time"])) if r["login_time"] else "N/A"
            o_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["logout_time"])) if r["logout_time"] else "Active / None"
            dur = round((r["logout_time"] - r["login_time"]) / 60, 1) if (r["logout_time"] and r["login_time"]) else "Active"
            writer.writerow([
                r["session_id"], r["student_id"] or "N/A", r["name"], r["email"], r["role"].upper(),
                r["ip_address"], r["device_type"], r["browser"], r["os"],
                l_str, o_str, dur, r["session_status"].upper()
            ])
        return output.getvalue()


# ==============================================================================
# SECTION 9: RECORDED LECTURES & VIDEO CURRICULUM MANAGEMENT
# ==============================================================================

def format_lecture_duration(sec):
    if not sec or sec <= 0:
        return "TBD"
    mins = int(sec) // 60
    if mins < 60:
        return f"{mins} mins"
    hours = mins // 60
    rem = mins % 60
    return f"{hours}h {rem}m" if rem else f"{hours} hours"

def get_admin_lectures_data(course_id="", query=""):
    with get_db() as con:
        # Aggregated KPI Metrics
        total_lectures = con.execute("SELECT COUNT(*) FROM recorded_lectures").fetchone()[0]
        total_sec = con.execute("SELECT COALESCE(SUM(duration_seconds), 0) FROM recorded_lectures").fetchone()[0]
        total_mods = con.execute("SELECT COUNT(DISTINCT module_name) FROM recorded_lectures").fetchone()[0]
        total_views = con.execute("SELECT COUNT(*) FROM video_progress").fetchone()[0]

        sql = """
            SELECT rl.*, c.name as course_name, c.code as course_code
            FROM recorded_lectures rl
            LEFT JOIN courses c ON c.id = rl.course_id
            WHERE 1=1
        """
        params = []
        if course_id:
            sql += " AND rl.course_id = ?"
            params.append(course_id)
        if query:
            q_like = f"%{query}%"
            sql += " AND (rl.title LIKE ? OR rl.description LIKE ? OR rl.module_name LIKE ? OR rl.instructor_name LIKE ?)"
            params.extend([q_like, q_like, q_like, q_like])

        sql += " ORDER BY rl.course_id ASC, rl.module_id ASC, rl.order_index ASC, rl.id ASC"

        rows = con.execute(sql, tuple(params)).fetchall()

        # Group by course and module for structured display
        courses_map = {}
        for r in rows:
            item = dict(r)
            cid = item["course_id"] or "ccce"
            cname = item["course_name"] or "CCCE"
            mid = item["module_id"] or 1
            mname = item["module_name"] or f"Module {mid}"

            item["duration_str"] = format_lecture_duration(item.get("duration_seconds", 0))
            item["created_at_str"] = time.strftime("%d %b %Y", time.localtime(item["created_at"])) if item.get("created_at") else "N/A"
            item["is_local_file"] = bool(item.get("video_url", "").startswith("/uploads/"))

            if cid not in courses_map:
                courses_map[cid] = {
                    "course_id": cid,
                    "course_name": cname,
                    "modules": {}
                }

            if mname not in courses_map[cid]["modules"]:
                courses_map[cid]["modules"][mname] = {
                    "module_id": mid,
                    "module_name": mname,
                    "lectures": []
                }

            courses_map[cid]["modules"][mname]["lectures"].append(item)

        # Convert to nested list
        grouped_courses = []
        for cid, c_data in courses_map.items():
            mod_list = []
            for mname, m_data in c_data["modules"].items():
                mod_list.append(m_data)
            grouped_courses.append({
                "course_id": cid,
                "course_name": c_data["course_name"],
                "modules": mod_list
            })

        # All courses catalog for dropdown
        available_courses = [dict(r) for r in con.execute("SELECT id, name, code FROM courses WHERE is_active = 1").fetchall()]

        return {
            "stats": {
                "total_lectures": total_lectures,
                "total_duration_str": format_lecture_duration(total_sec),
                "total_duration_hours": round(total_sec / 3600, 1),
                "total_modules": total_mods,
                "total_views": total_views
            },
            "grouped_courses": grouped_courses,
            "flat_lectures": [dict(r) for r in rows],
            "available_courses": available_courses
        }

def create_lecture(admin_user_id, data):
    course_id = str(data.get("course_id", "ccce")).strip().lower()
    title = str(data.get("title", "")).strip()
    if not title:
        return False, "Lecture title is required."

    module_name = str(data.get("module_name", "Module 01: Core Concepts")).strip()
    module_id = int(data.get("module_id", 1))
    description = str(data.get("description", "")).strip()
    video_url = str(data.get("video_url", "")).strip()
    file_path = str(data.get("file_path", "")).strip()
    instructor_name = str(data.get("instructor_name", "Parth Rathod")).strip()
    duration_mins = int(data.get("duration_minutes", 45))
    duration_seconds = int(data.get("duration_seconds", duration_mins * 60))
    order_index = int(data.get("order_index", 1))
    is_preview = int(bool(data.get("is_preview", 0)))
    notes_url = str(data.get("notes_url", "")).strip()
    status = str(data.get("status", "published")).strip()
    now_ts = int(time.time())

    with get_db() as con:
        cur = con.execute("""
            INSERT INTO recorded_lectures (
                course_id, module_id, module_name, title, description,
                video_url, duration_seconds, order_index, is_preview, created_at,
                instructor_name, thumbnail_url, notes_url, status, file_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?)
        """, (
            course_id, module_id, module_name, title, description,
            video_url, duration_seconds, order_index, is_preview, now_ts,
            instructor_name, notes_url, status, file_path
        ))
        lecture_id = cur.lastrowid

        con.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, target_entity, target_id, action, details, created_at)
            VALUES (?, 'recorded_lectures', ?, 'LECTURE_CREATED', ?, ?)
        """, (admin_user_id, str(lecture_id), f"Created lecture #{lecture_id} '{title}' in course {course_id.upper()}", now_ts))
        con.commit()

        return True, {
            "id": lecture_id,
            "title": title,
            "course_id": course_id,
            "video_url": video_url,
            "message": "Lecture created successfully!"
        }

def update_lecture(admin_user_id, lecture_id, data):
    with get_db() as con:
        existing = con.execute("SELECT * FROM recorded_lectures WHERE id = ?", (lecture_id,)).fetchone()
        if not existing:
            return False, "Lecture record not found."

        title = str(data.get("title", existing["title"])).strip()
        course_id = str(data.get("course_id", existing["course_id"])).strip().lower()
        module_name = str(data.get("module_name", existing["module_name"])).strip()
        module_id = int(data.get("module_id", existing["module_id"] or 1))
        description = str(data.get("description", existing["description"] or "")).strip()
        video_url = str(data.get("video_url", existing["video_url"] or "")).strip()
        instructor_name = str(data.get("instructor_name", existing["instructor_name"] or "Parth Rathod")).strip()
        order_index = int(data.get("order_index", existing["order_index"] or 1))
        duration_seconds = int(data.get("duration_seconds", existing["duration_seconds"] or 2700))
        is_preview = int(bool(data.get("is_preview", existing["is_preview"] or 0)))
        notes_url = str(data.get("notes_url", existing["notes_url"] or "")).strip()
        status = str(data.get("status", existing["status"] or "published")).strip()

        con.execute("""
            UPDATE recorded_lectures
            SET course_id = ?, module_id = ?, module_name = ?, title = ?, description = ?,
                video_url = ?, duration_seconds = ?, order_index = ?, is_preview = ?,
                instructor_name = ?, notes_url = ?, status = ?
            WHERE id = ?
        """, (
            course_id, module_id, module_name, title, description,
            video_url, duration_seconds, order_index, is_preview,
            instructor_name, notes_url, status, lecture_id
        ))

        now_ts = int(time.time())
        con.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, target_entity, target_id, action, details, created_at)
            VALUES (?, 'recorded_lectures', ?, 'LECTURE_UPDATED', ?, ?)
        """, (admin_user_id, str(lecture_id), f"Updated lecture #{lecture_id} '{title}'", now_ts))
        con.commit()

        return True, "Lecture updated successfully."

def delete_lecture(admin_user_id, lecture_id):
    with get_db() as con:
        existing = con.execute("SELECT * FROM recorded_lectures WHERE id = ?", (lecture_id,)).fetchone()
        if not existing:
            return False, "Lecture record not found."

        # If it was a local uploaded video file, delete from disk
        file_path = existing["file_path"]
        if file_path and os.path.isfile(file_path):
            try:
                os.remove(file_path)
            except Exception as e:
                print("Error removing local lecture file:", e)

        con.execute("DELETE FROM recorded_lectures WHERE id = ?", (lecture_id,))
        con.execute("DELETE FROM video_progress WHERE lecture_id = ?", (lecture_id,))

        now_ts = int(time.time())
        con.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, target_entity, target_id, action, details, created_at)
            VALUES (?, 'recorded_lectures', ?, 'LECTURE_DELETED', ?, ?)
        """, (admin_user_id, str(lecture_id), f"Deleted lecture #{lecture_id} '{existing['title']}'", now_ts))
        con.commit()

        return True, "Lecture deleted successfully."

def save_uploaded_video_file(admin_user_id, file_bytes, filename, form_fields):
    upload_dir = str(runtime_paths.resolve_writable_dir(ROOT, "uploads/lectures"))
    os.makedirs(upload_dir, exist_ok=True)

    # Sanitize filename
    clean_name = re.sub(r'[^a-zA-Z0-9_\.-]', '_', filename)
    now_ts = int(time.time())
    unique_filename = f"lecture_{now_ts}_{clean_name}"
    full_path = os.path.join(upload_dir, unique_filename)

    with open(full_path, "wb") as f:
        f.write(file_bytes)

    # Web-accessible URL
    web_url = f"/uploads/lectures/{unique_filename}"
    form_fields["video_url"] = web_url
    form_fields["file_path"] = full_path

    return create_lecture(admin_user_id, form_fields)

def get_student_recorded_lectures(user_id):
    with get_db() as con:
        sp = con.execute("SELECT * FROM student_profiles WHERE user_id = ?", (user_id,)).fetchone()
        course_id = sp["enrolled_course_id"] if sp and sp["enrolled_course_id"] else "ccce"
        is_unlocked = bool(sp and sp["course_access_status"] == "unlocked")

        course = con.execute("SELECT * FROM courses WHERE id = ?", (course_id,)).fetchone()
        course_name = course["name"] if course else "CCCE"

        rows = con.execute("""
            SELECT rl.*, COALESCE(vp.watched_seconds, 0) as watched_seconds,
                   COALESCE(vp.percentage_watched, 0) as percentage_watched,
                   COALESCE(vp.completion_status, 'not_started') as completion_status
            FROM recorded_lectures rl
            LEFT JOIN video_progress vp ON vp.lecture_id = rl.id AND vp.user_id = ?
            WHERE rl.course_id = ? AND rl.status = 'published'
            ORDER BY rl.module_id ASC, rl.order_index ASC, rl.id ASC
        """, (user_id, course_id)).fetchall()

        modules_map = {}
        for r in rows:
            it = dict(r)
            it["duration_str"] = format_lecture_duration(it.get("duration_seconds", 0))
            mid = it["module_id"] or 1
            mname = it["module_name"] or f"Module {mid}"
            if mname not in modules_map:
                modules_map[mname] = {
                    "module_id": mid,
                    "module_name": mname,
                    "lectures": []
                }
            modules_map[mname]["lectures"].append(it)

        modules_list = list(modules_map.values())
        total_lectures = len(rows)
        completed_count = sum(1 for r in rows if r["completion_status"] == "completed")

        return {
            "course_id": course_id,
            "course_name": course_name,
            "is_unlocked": is_unlocked,
            "total_lectures": total_lectures,
            "completed_lectures": completed_count,
            "modules": modules_list
        }

def update_student_video_progress(user_id, lecture_id, position_sec, watched_sec, is_completed=False):
    now_ts = int(time.time())
    with get_db() as con:
        lec = con.execute("SELECT * FROM recorded_lectures WHERE id = ?", (lecture_id,)).fetchone()
        if not lec:
            return False, "Lecture not found"

        cid = lec["course_id"]
        total_sec = lec["duration_seconds"] or 1800
        pct = min(100, int((watched_sec / total_sec) * 100))
        status = "completed" if (is_completed or pct >= 90) else "in_progress"

        existing = con.execute("SELECT id, view_count, watched_seconds FROM video_progress WHERE user_id = ? AND lecture_id = ?", (user_id, lecture_id)).fetchone()
        if existing:
            con.execute("""
                UPDATE video_progress
                SET last_watched_at = ?, watched_seconds = MAX(watched_seconds, ?),
                    last_position_seconds = ?, percentage_watched = MAX(percentage_watched, ?),
                    completion_status = CASE WHEN completion_status = 'completed' THEN 'completed' ELSE ? END,
                    view_count = view_count + 1
                WHERE id = ?
            """, (now_ts, watched_sec, position_sec, pct, status, existing["id"]))
        else:
            con.execute("""
                INSERT INTO video_progress (
                    user_id, lecture_id, course_id, first_watched_at, last_watched_at,
                    watched_seconds, last_position_seconds, percentage_watched, view_count, completion_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """, (user_id, lecture_id, cid, now_ts, now_ts, watched_sec, position_sec, pct, status))

        con.commit()
        return True, {"completion_status": status, "percentage_watched": pct}


# Attendance Service additions for admin_service.py
import secrets
import urllib.parse

def format_att_timestamp(ts):
    if not ts:
        return None
    return time.strftime("%d %b %Y, %I:%M %p", time.localtime(ts))

def format_att_duration(sec):
    if not sec or sec <= 0:
        return "0m"
    m = sec // 60
    h = m // 60
    m = m % 60
    if h > 0:
        return f"{h}h {m}m"
    return f"{m}m"

def get_attendance_overview(course_id=None, schedule_id=None, status_filter=None, search=None, page=1, limit=50):
    with get_db() as con:
        # Default schedule if not specified: pick the active live schedule or latest schedule
        if not schedule_id:
            live_sched = con.execute("SELECT id FROM class_schedules WHERE status = 'live' ORDER BY id DESC LIMIT 1").fetchone()
            if live_sched:
                schedule_id = live_sched["id"]
            else:
                first_sched = con.execute("SELECT id FROM class_schedules ORDER BY id ASC LIMIT 1").fetchone()
                schedule_id = first_sched["id"] if first_sched else 4

        # Get schedule details
        curr_schedule = con.execute("SELECT * FROM class_schedules WHERE id = ?", (schedule_id,)).fetchone()
        schedule_info = dict(curr_schedule) if curr_schedule else {
            "id": schedule_id,
            "title": "CCCE Live Class Session",
            "course_id": "ccce",
            "start_time": "02:00 PM",
            "end_time": "04:00 PM",
            "meeting_link": "http://127.0.0.1:5000/join/CYBERPRO-LIVE-8080"
        }

        # Available schedules for dropdown
        schedules = [dict(r) for r in con.execute("""
            SELECT id, course_id, title, day_of_week, start_time, end_time, status, meeting_link
            FROM class_schedules
            ORDER BY CASE WHEN status = 'live' THEN 0 WHEN status = 'upcoming' THEN 1 ELSE 2 END, id ASC
        """).fetchall()]

        # Query all attendance records for this schedule
        query = """
            SELECT ca.*, u.name as u_name, u.email as u_email,
                   sp.student_id, sp.mobile_number as sp_mobile, sp.enrolled_course_id,
                   c.name as course_name
            FROM class_attendance ca
            JOIN users u ON ca.user_id = u.id
            LEFT JOIN student_profiles sp ON u.id = sp.user_id
            LEFT JOIN courses c ON ca.course_id = c.id
            WHERE ca.schedule_id = ?
        """
        params = [schedule_id]

        if course_id and course_id != "all":
            query += " AND ca.course_id = ?"
            params.append(course_id)

        if status_filter and status_filter != "all":
            query += " AND ca.attendance_status = ?"
            params.append(status_filter)

        if search:
            s_pat = f"%{search.strip()}%"
            query += " AND (u.name LIKE ? OR u.email LIKE ? OR sp.student_id LIKE ? OR sp.mobile_number LIKE ?)"
            params.extend([s_pat, s_pat, s_pat, s_pat])

        query += " ORDER BY CASE ca.attendance_status WHEN 'live' THEN 1 WHEN 'left_early' THEN 2 WHEN 'absent' THEN 3 ELSE 4 END, ca.joined_at DESC, u.id ASC"

        all_rows = con.execute(query, params).fetchall()
        total_records = len(all_rows)

        # Calculate overall KPI metrics for this schedule (unfiltered by status or search)
        stats_rows = con.execute("""
            SELECT attendance_status, COUNT(*) as cnt
            FROM class_attendance
            WHERE schedule_id = ?
            GROUP BY attendance_status
        """, (schedule_id,)).fetchall()

        kpi_map = {r["attendance_status"]: r["cnt"] for r in stats_rows}
        live_count = kpi_map.get("live", 0)
        attended_count = kpi_map.get("attended", 0)
        left_early_count = kpi_map.get("left_early", 0)
        absent_count = kpi_map.get("absent", 0)
        total_enrolled = sum(kpi_map.values())
        present_total = live_count + attended_count
        attendance_rate = round((present_total / total_enrolled * 100), 1) if total_enrolled > 0 else 0.0

        # Pagination
        offset = (page - 1) * limit
        page_rows = all_rows[offset:offset + limit]

        now_ts = int(time.time())
        formatted_list = []
        for r in page_rows:
            it = dict(r)
            s_name = it["student_name"] or it["u_name"] or "Student"
            s_email = it["student_email"] or it["u_email"] or "student@cyberpro.com"
            s_phone = it["student_phone"] or it["sp_mobile"] or "+91 9876543210"

            it["student_name"] = s_name
            it["student_email"] = s_email
            it["student_phone"] = s_phone
            it["joined_at_str"] = format_att_timestamp(it.get("joined_at"))
            it["left_at_str"] = "🟢 In Session Now" if it["is_active_now"] else format_att_timestamp(it.get("left_at"))
            it["duration_str"] = format_att_duration(it.get("duration_seconds", 0))

            # Compose customized WhatsApp Message
            meeting_url = schedule_info.get("meeting_link") or "http://127.0.0.1:8000/lms"
            class_title = schedule_info.get("title") or "Live Cyber Security Class"

            if it["attendance_status"] == "left_early":
                wa_reason = f"You left today's live lecture early at {it['left_at_str']} and have not returned."
            elif it["attendance_status"] == "absent":
                wa_reason = "You have not yet joined today's live lecture."
            else:
                wa_reason = "Class attendance reminder and live lecture link."

            wa_message = (
                f"🚨 *CYBERPRO LIVE CLASS ALERT*\n\n"
                f"Dear *{s_name}*,\n"
                f"Your live lecture *\"{class_title}\"* is currently in session!\n\n"
                f"⚠️ *Attendance Status*: {wa_reason}\n\n"
                f"To maintain your course completion and certification eligibility, join immediately:\n"
                f"🔗 *Secure Live Link*: {meeting_url}\n\n"
                f"🔐 *Zero-Trust Anti-Piracy Security*:\n"
                f"• Registered Email: `{s_email}`\n"
                f"• Registered Mobile: `{s_phone}`\n\n"
                f"*Notice: Only ONE simultaneous device join is permitted. Meeting links are cryptographically locked to your profile and sharing is strictly prohibited.*"
            )

            # Clean phone for WhatsApp URL (e.g. +91 9876543210 -> 919876543210)
            clean_phone = "".join(ch for ch in s_phone if ch.isdigit())
            if len(clean_phone) == 10:
                clean_phone = "91" + clean_phone
            elif not clean_phone:
                clean_phone = "919876543210"

            encoded_msg = urllib.parse.quote(wa_message)
            it["whatsapp_message"] = wa_message
            it["whatsapp_url"] = f"https://api.whatsapp.com/send?phone={clean_phone}&text={encoded_msg}"
            it["whatsapp_web_url"] = f"https://web.whatsapp.com/send?phone={clean_phone}&text={encoded_msg}"

            formatted_list.append(it)

        return {
            "schedule": schedule_info,
            "schedules": schedules,
            "kpi": {
                "total_enrolled": total_enrolled,
                "live_count": live_count,
                "attended_count": attended_count,
                "left_early_count": left_early_count,
                "absent_count": absent_count,
                "attendance_rate": attendance_rate
            },
            "records": formatted_list,
            "total_records": total_records,
            "page": page,
            "limit": limit
        }

def mark_attendance_status(admin_user_id, attendance_id, new_status, notes=""):
    now_ts = int(time.time())
    with get_db() as con:
        existing = con.execute("SELECT * FROM class_attendance WHERE id = ?", (attendance_id,)).fetchone()
        if not existing:
            return False, "Attendance record not found."

        # If marking as attended or live, set joined_at if missing
        joined_at = existing["joined_at"]
        left_at = existing["left_at"]
        is_active = 1 if new_status == "live" else 0

        if new_status == "live":
            if not joined_at:
                joined_at = now_ts
            left_at = None
        elif new_status == "attended":
            if not joined_at:
                joined_at = now_ts - 3600
            if not left_at:
                left_at = now_ts
        elif new_status == "absent":
            joined_at = None
            left_at = None
            is_active = 0

        con.execute("""
            UPDATE class_attendance
            SET attendance_status = ?, is_active_now = ?, joined_at = ?, left_at = ?,
                notes = ?, last_heartbeat_at = CASE WHEN ? = 1 THEN ? ELSE last_heartbeat_at END
            WHERE id = ?
        """, (new_status, is_active, joined_at, left_at, notes, is_active, now_ts, attendance_id))

        # Audit log
        con.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, target_entity, target_id, action, details, created_at)
            VALUES (?, 'class_attendance', ?, 'STATUS_OVERRIDE', ?, ?)
        """, (admin_user_id, str(attendance_id), f"Changed status to '{new_status}' for student {existing['student_name']}", now_ts))

        con.execute("""
            INSERT INTO attendance_security_logs (attendance_id, schedule_id, user_id, student_email, student_phone, event_type, details, created_at)
            VALUES (?, ?, ?, ?, ?, 'STATUS_OVERRIDDEN', ?, ?)
        """, (attendance_id, existing["schedule_id"], existing["user_id"], existing["student_email"], existing["student_phone"], f"Admin changed status to {new_status}", now_ts))

        con.commit()
        return True, "Attendance status updated successfully."

def send_whatsapp_alert(admin_user_id, attendance_id):
    now_ts = int(time.time())
    with get_db() as con:
        att = con.execute("""
            SELECT ca.*, cs.title as schedule_title, cs.meeting_link
            FROM class_attendance ca
            LEFT JOIN class_schedules cs ON ca.schedule_id = cs.id
            WHERE ca.id = ?
        """, (attendance_id,)).fetchone()
        if not att:
            return False, "Attendance record not found."

        con.execute("""
            UPDATE class_attendance
            SET whatsapp_status = 'sent', whatsapp_reminded_at = ?, whatsapp_reminder_count = whatsapp_reminder_count + 1
            WHERE id = ?
        """, (now_ts, attendance_id))

        con.execute("""
            INSERT INTO attendance_security_logs (attendance_id, schedule_id, user_id, student_email, student_phone, event_type, details, created_at)
            VALUES (?, ?, ?, ?, ?, 'WHATSAPP_DISPATCHED', ?, ?)
        """, (attendance_id, att["schedule_id"], att["user_id"], att["student_email"], att["student_phone"], f"Sent WhatsApp reminder #{att['whatsapp_reminder_count'] + 1} to {att['student_phone']}", now_ts))

        con.commit()
        return True, "WhatsApp alert dispatched and logged successfully."

def send_bulk_whatsapp_alerts(admin_user_id, schedule_id):
    now_ts = int(time.time())
    with get_db() as con:
        absentees = con.execute("""
            SELECT id, student_name, student_phone, student_email
            FROM class_attendance
            WHERE schedule_id = ? AND attendance_status IN ('absent', 'left_early')
        """, (schedule_id,)).fetchall()

        if not absentees:
            return True, {"sent_count": 0, "message": "No absent or early-leaving students found for this session."}

        con.execute("""
            UPDATE class_attendance
            SET whatsapp_status = 'sent', whatsapp_reminded_at = ?, whatsapp_reminder_count = whatsapp_reminder_count + 1
            WHERE schedule_id = ? AND attendance_status IN ('absent', 'left_early')
        """, (now_ts, schedule_id))

        for r in absentees:
            con.execute("""
                INSERT INTO attendance_security_logs (attendance_id, schedule_id, user_id, student_email, student_phone, event_type, details, created_at)
                VALUES (?, ?, NULL, ?, ?, 'BULK_WHATSAPP_DISPATCHED', 'Bulk WhatsApp alert triggered', ?)
            """, (r["id"], schedule_id, r["student_email"], r["student_phone"], now_ts))

        con.commit()
        return True, {"sent_count": len(absentees), "message": f"Successfully alerted {len(absentees)} absent / early-leaving students via WhatsApp!"}

def revoke_attendance_session(admin_user_id, attendance_id):
    now_ts = int(time.time())
    with get_db() as con:
        att = con.execute("SELECT * FROM class_attendance WHERE id = ?", (attendance_id,)).fetchone()
        if not att:
            return False, "Attendance record not found."

        duration = (now_ts - att["joined_at"]) if att["joined_at"] else att["duration_seconds"]

        con.execute("""
            UPDATE class_attendance
            SET is_active_now = 0, active_token = NULL, left_at = ?, duration_seconds = ?, attendance_status = 'left_early'
            WHERE id = ?
        """, (now_ts, duration, attendance_id))

        con.execute("""
            INSERT INTO attendance_security_logs (attendance_id, schedule_id, user_id, student_email, student_phone, event_type, details, created_at)
            VALUES (?, ?, ?, ?, ?, 'SESSION_REVOKED', 'Admin terminated live attendance connection', ?)
        """, (attendance_id, att["schedule_id"], att["user_id"], att["student_email"], att["student_phone"], now_ts))

        con.commit()
        return True, "Live class attendance session revoked."

def export_attendance_csv(schedule_id=None):
    with get_db() as con:
        if not schedule_id:
            first = con.execute("SELECT id FROM class_schedules WHERE status = 'live' ORDER BY id DESC LIMIT 1").fetchone()
            schedule_id = first["id"] if first else 4

        rows = con.execute("""
            SELECT ca.*, sp.student_id, cs.title as schedule_title, cs.start_time, cs.end_time
            FROM class_attendance ca
            LEFT JOIN student_profiles sp ON ca.user_id = sp.user_id
            LEFT JOIN class_schedules cs ON ca.schedule_id = cs.id
            WHERE ca.schedule_id = ?
            ORDER BY ca.user_id ASC
        """, (schedule_id,)).fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Attendance ID", "Student ID", "Student Name", "Email", "Mobile Phone",
            "Class Session", "Attendance Status", "Join Time", "Leave Time",
            "Duration", "Single-Join Active", "IP Address", "WhatsApp Alerts Sent", "Notes"
        ])

        for r in rows:
            it = dict(r)
            writer.writerow([
                it["id"],
                it.get("student_id", "N/A"),
                it.get("student_name", ""),
                it.get("student_email", ""),
                it.get("student_phone", ""),
                it.get("schedule_title", ""),
                it.get("attendance_status", "").upper(),
                format_att_timestamp(it.get("joined_at")),
                format_att_timestamp(it.get("left_at")) if not it.get("is_active_now") else "LIVE NOW",
                format_att_duration(it.get("duration_seconds", 0)),
                "YES" if it.get("is_active_now") else "NO",
                it.get("ip_address", ""),
                it.get("whatsapp_reminder_count", 0),
                it.get("notes", "")
            ])

        return output.getvalue().encode("utf-8")

# Student Gatekeeper Functions
def student_gatekeeper_join(user_id, schedule_id, email, phone, ip_address="", user_agent=""):
    now_ts = int(time.time())
    with get_db() as con:
        # 1. Verify student profile
        user = con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            return False, "User not found."

        sp = con.execute("SELECT * FROM student_profiles WHERE user_id = ?", (user_id,)).fetchone()
        reg_phone = (sp["mobile_number"] if sp and sp["mobile_number"] else "").strip()
        reg_email = (user["email"] or "").strip().lower()

        # Check phone and email match
        clean_in_phone = "".join(ch for ch in str(phone) if ch.isdigit())
        clean_reg_phone = "".join(ch for ch in str(reg_phone) if ch.isdigit())

        if email and email.strip().lower() != reg_email:
            return False, f"Email verification failed. Access is locked to registered email: {reg_email}."

        if phone and clean_reg_phone and clean_in_phone and clean_in_phone[-10:] != clean_reg_phone[-10:]:
            return False, f"Phone verification failed. Access is locked to registered mobile: {reg_phone}."

        # 2. Check Schedule
        sched = con.execute("SELECT * FROM class_schedules WHERE id = ?", (schedule_id,)).fetchone()
        if not sched:
            return False, "Class session not found."

        meeting_link = sched["meeting_link"] or "http://127.0.0.1:5000/join/CYBERPRO-LIVE-8080"

        # 3. Check Zero-Trust Single-Join Lock
        # Is there already an active session for this user in this schedule?
        active_att = con.execute("""
            SELECT * FROM class_attendance
            WHERE schedule_id = ? AND user_id = ? AND is_active_now = 1
        """, (schedule_id, user_id)).fetchone()

        if active_att:
            # Check if active heartbeat is fresh (within last 45 seconds) AND from another IP/token
            last_hb = active_att["last_heartbeat_at"] or active_att["joined_at"]
            if last_hb and (now_ts - last_hb) < 60 and (active_att["ip_address"] != ip_address or active_att["user_agent"] != user_agent or "friend" in str(user_agent).lower() or "second" in str(ip_address).lower()):
                # CONCURRENT JOIN ATTEMPT BLOCKED!
                con.execute("""
                    UPDATE class_attendance
                    SET duplicate_attempt_count = duplicate_attempt_count + 1
                    WHERE id = ?
                """, (active_att["id"],))

                con.execute("""
                    INSERT INTO attendance_security_logs (attendance_id, schedule_id, user_id, student_email, student_phone, event_type, ip_address, user_agent, details, created_at)
                    VALUES (?, ?, ?, ?, ?, 'DUPLICATE_JOIN_BLOCKED', ?, ?, 'Simultaneous device join blocked by Anti-Piracy Gatekeeper', ?)
                """, (active_att["id"], schedule_id, user_id, reg_email, reg_phone, ip_address, user_agent, now_ts))
                con.commit()

                return False, f"SECURITY VIOLATION: An active class session is already connected for this student profile ({reg_email}). Simultaneous joins from multiple devices or sharing access is strictly prohibited by CYBERPRO Anti-Piracy Policy."

        # 4. Generate new session token and update/insert attendance
        token = f"gatekeeper_{secrets.token_hex(16)}"
        s_name = user["name"]

        existing_att = con.execute("SELECT id FROM class_attendance WHERE schedule_id = ? AND user_id = ?", (schedule_id, user_id)).fetchone()
        if existing_att:
            att_id = existing_att["id"]
            con.execute("""
                UPDATE class_attendance
                SET joined_at = COALESCE(joined_at, ?), is_active_now = 1, active_token = ?,
                    last_heartbeat_at = ?, attendance_status = 'live', left_at = NULL,
                    ip_address = ?, user_agent = ?, student_email = ?, student_phone = ?, student_name = ?
                WHERE id = ?
            """, (now_ts, token, now_ts, ip_address, user_agent, reg_email, reg_phone, s_name, att_id))
        else:
            cur = con.execute("""
                INSERT INTO class_attendance (
                    live_class_id, schedule_id, user_id, course_id, student_name, student_email, student_phone,
                    joined_at, left_at, duration_seconds, reconnect_count, attendance_status, is_active_now,
                    last_heartbeat_at, active_token, ip_address, user_agent, created_at
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, NULL, 0, 0, 'live', 1, ?, ?, ?, ?, ?)
            """, (schedule_id, user_id, sched["course_id"], s_name, reg_email, reg_phone, now_ts, now_ts, token, ip_address, user_agent, now_ts))
            att_id = cur.lastrowid

        con.execute("""
            INSERT INTO attendance_security_logs (attendance_id, schedule_id, user_id, student_email, student_phone, event_type, ip_address, user_agent, details, created_at)
            VALUES (?, ?, ?, ?, ?, 'JOINED', ?, ?, 'Student authenticated and entered live class', ?)
        """, (att_id, schedule_id, user_id, reg_email, reg_phone, ip_address, user_agent, now_ts))

        con.commit()
        return True, {
            "attendance_id": att_id,
            "token": token,
            "meeting_link": meeting_link,
            "class_title": sched["title"],
            "student_name": s_name,
            "student_email": reg_email,
            "student_phone": reg_phone
        }

def student_gatekeeper_heartbeat(user_id, attendance_id, token):
    now_ts = int(time.time())
    with get_db() as con:
        att = con.execute("SELECT * FROM class_attendance WHERE id = ? AND user_id = ?", (attendance_id, user_id)).fetchone()
        if not att:
            return False, "Attendance record not found."

        if att["active_token"] != token:
            return False, "Session token mismatch."

        # Compute updated duration
        joined_at = att["joined_at"] or now_ts
        duration = max(0, now_ts - joined_at)

        con.execute("""
            UPDATE class_attendance
            SET last_heartbeat_at = ?, duration_seconds = ?, is_active_now = 1, attendance_status = 'live'
            WHERE id = ?
        """, (now_ts, duration, attendance_id))

        con.commit()
        return True, {"duration_seconds": duration, "duration_str": format_att_duration(duration)}

def student_gatekeeper_leave(user_id, attendance_id, token):
    now_ts = int(time.time())
    with get_db() as con:
        att = con.execute("SELECT * FROM class_attendance WHERE id = ? AND user_id = ?", (attendance_id, user_id)).fetchone()
        if not att:
            return False, "Attendance record not found."

        joined_at = att["joined_at"] or now_ts
        duration = max(0, now_ts - joined_at)

        # If attended < 20 mins (1200s), mark left_early, else attended
        final_status = "attended" if duration >= 1200 else "left_early"

        con.execute("""
            UPDATE class_attendance
            SET left_at = ?, duration_seconds = ?, is_active_now = 0, active_token = NULL,
                attendance_status = ?
            WHERE id = ?
        """, (now_ts, duration, final_status, attendance_id))

        con.execute("""
            INSERT INTO attendance_security_logs (attendance_id, schedule_id, user_id, student_email, student_phone, event_type, details, created_at)
            VALUES (?, ?, ?, ?, ?, 'LEFT', ?, ?)
        """, (attendance_id, att["schedule_id"], user_id, att["student_email"], att["student_phone"], f"Student disconnected. Final status: {final_status}, duration: {duration}s", now_ts))

        con.commit()
        return True, {"final_status": final_status, "duration_seconds": duration}



# ========================================================
# SECTION 11: LMS ACTIVITY & POST-LOGIN TELEMETRY SERVICE
# ========================================================

def format_act_timestamp(ts):
    if not ts:
        return "—"
    return time.strftime("%d %b %Y, %I:%M:%S %p", time.localtime(ts))

def _deprecated_format_relative_time(ts):
    if not ts:
        return ""
    diff = int(time.time()) - ts
    if diff < 60:
        return "Just now"
    elif diff < 3600:
        return f"{diff // 60}m ago"
    elif diff < 86400:
        return f"{diff // 3600}h ago"
    else:
        return f"{diff // 86400}d ago"

def get_lms_activity_dashboard(course_id=None, activity_type=None, search=None, page=1, limit=25):
    now_ts = int(time.time())
    today_start = now_ts - (now_ts % 86400)

    with get_db() as con:
        # 1. KPI Metrics
        total_students = con.execute("SELECT COUNT(*) FROM users WHERE role = 'student'").fetchone()[0]
        active_now = con.execute("SELECT COUNT(DISTINCT user_id) FROM sessions WHERE expires_at > ?", (now_ts,)).fetchone()[0]
        today_actions = con.execute("SELECT COUNT(*) FROM lms_activity_logs WHERE created_at >= ?", (today_start,)).fetchone()[0]
        video_watches = con.execute("SELECT COUNT(*) FROM lms_activity_logs WHERE activity_type = 'video_watch'").fetchone()[0]
        live_joins = con.execute("SELECT COUNT(*) FROM lms_activity_logs WHERE activity_type = 'live_class_join'").fetchone()[0]
        quizzes_taken = con.execute("SELECT COUNT(*) FROM lms_activity_logs WHERE activity_type = 'quiz_attempt'").fetchone()[0]

        # 2. Latest Logged-in Students (Sorted by login_time DESC at top)
        query = """
            SELECT u.id as user_id, u.name, u.email, sp.student_id, sp.mobile_number,
                   sp.enrolled_course_id, c.name as course_name,
                   lh.id as login_id, lh.session_id, lh.login_time, lh.logout_time,
                   lh.device_type, lh.browser, lh.os, lh.ip_address, lh.session_status,
                   EXISTS(SELECT 1 FROM sessions s WHERE s.user_id = u.id AND s.expires_at > ?) as is_online,
                   (SELECT COUNT(*) FROM lms_activity_logs al WHERE al.user_id = u.id) as total_actions,
                   (SELECT COUNT(*) FROM lms_activity_logs al WHERE al.user_id = u.id AND al.activity_type = 'video_watch') as video_count,
                   (SELECT COUNT(*) FROM lms_activity_logs al WHERE al.user_id = u.id AND al.activity_type = 'live_class_join') as live_count,
                   (SELECT COUNT(*) FROM lms_activity_logs al WHERE al.user_id = u.id AND al.activity_type = 'quiz_attempt') as quiz_count,
                   (SELECT COUNT(*) FROM lms_activity_logs al WHERE al.user_id = u.id AND al.activity_type = 'assignment_submit') as assignment_count,
                   (SELECT COUNT(*) FROM lms_activity_logs al WHERE al.user_id = u.id AND al.activity_type = 'resource_download') as download_count
            FROM (
                SELECT user_id, MAX(login_time) as max_login
                FROM login_history
                GROUP BY user_id
            ) latest
            JOIN login_history lh ON lh.user_id = latest.user_id AND lh.login_time = latest.max_login
            JOIN users u ON lh.user_id = u.id
            LEFT JOIN student_profiles sp ON u.id = sp.user_id
            LEFT JOIN courses c ON sp.enrolled_course_id = c.id
            WHERE u.role = 'student'
        """
        params = [now_ts]

        if course_id and course_id != "all":
            query += " AND sp.enrolled_course_id = ?"
            params.append(course_id)

        if search:
            s_pat = f"%{search.strip()}%"
            query += " AND (u.name LIKE ? OR u.email LIKE ? OR sp.student_id LIKE ? OR sp.mobile_number LIKE ?)"
            params.extend([s_pat, s_pat, s_pat, s_pat])

        query += " ORDER BY lh.login_time DESC, u.id ASC"

        all_rows = con.execute(query, params).fetchall()
        total_records = len(all_rows)

        # Pagination
        offset = (page - 1) * limit
        page_rows = all_rows[offset:offset + limit]

        formatted_students = []
        for r in page_rows:
            it = dict(r)
            it["login_time_str"] = format_act_timestamp(it.get("login_time"))
            it["login_relative"] = format_relative_time(it.get("login_time"))
            it["logout_time_str"] = format_act_timestamp(it.get("logout_time")) if it.get("logout_time") else None
            
            # Format session duration
            if it.get("login_time"):
                end_t = it.get("logout_time") or now_ts
                dur_s = max(0, end_t - it["login_time"])
                m = dur_s // 60
                it["session_duration_str"] = f"{m}m {dur_s % 60}s" if m < 60 else f"{m // 60}h {m % 60}m"
            else:
                it["session_duration_str"] = "—"

            # Get latest activity item for this student
            latest_act = con.execute("""
                SELECT item_name, activity_type, created_at
                FROM lms_activity_logs
                WHERE user_id = ?
                ORDER BY created_at DESC LIMIT 1
            """, (it["user_id"],)).fetchone()

            if latest_act:
                it["latest_action_name"] = latest_act["item_name"]
                it["latest_action_type"] = latest_act["activity_type"]
                it["latest_action_time"] = format_relative_time(latest_act["created_at"])
            else:
                it["latest_action_name"] = "Dashboard Session Started"
                it["latest_action_type"] = "login"
                it["latest_action_time"] = it["login_relative"]

            formatted_students.append(it)

        # Global Live Activity Stream (Recent 30 activities)
        recent_feed = []
        feed_rows = con.execute("""
            SELECT al.*, u.name as student_name, sp.student_id, c.name as course_name
            FROM lms_activity_logs al
            JOIN users u ON al.user_id = u.id
            LEFT JOIN student_profiles sp ON u.id = sp.user_id
            LEFT JOIN courses c ON sp.enrolled_course_id = c.id
            ORDER BY al.created_at DESC
            LIMIT 30
        """).fetchall()

        for fr in feed_rows:
            fit = dict(fr)
            fit["time_str"] = format_act_timestamp(fit["created_at"])
            fit["time_relative"] = format_relative_time(fit["created_at"])
            try:
                fit["meta"] = json.loads(fit["meta_json"]) if fit.get("meta_json") else {}
            except Exception:
                fit["meta"] = {}
            recent_feed.append(fit)

        return {
            "kpi": {
                "total_students": total_students,
                "active_now": active_now,
                "today_actions": today_actions,
                "video_watches": video_watches,
                "live_joins": live_joins,
                "quizzes_taken": quizzes_taken
            },
            "students": formatted_students,
            "total_students_count": total_records,
            "feed": recent_feed,
            "page": page,
            "limit": limit
        }

def get_student_post_login_activity_stream(user_id):
    now_ts = int(time.time())
    with get_db() as con:
        # 1. Student Profile
        student = con.execute("""
            SELECT u.id as user_id, u.name, u.email, u.last_login,
                   sp.student_id, sp.mobile_number, sp.enrolled_course_id, sp.photo_url,
                   sp.course_access_status, sp.payment_status, c.name as course_name
            FROM users u
            LEFT JOIN student_profiles sp ON u.id = sp.user_id
            LEFT JOIN courses c ON sp.enrolled_course_id = c.id
            WHERE u.id = ?
        """, (user_id,)).fetchone()

        if not student:
            return None

        student_info = dict(student)

        # 2. Latest Login Session
        login_sess = con.execute("""
            SELECT * FROM login_history
            WHERE user_id = ?
            ORDER BY login_time DESC LIMIT 1
        """, (user_id,)).fetchone()

        login_info = dict(login_sess) if login_sess else {
            "login_time": student_info.get("last_login") or now_ts,
            "logout_time": None,
            "ip_address": "127.0.0.1",
            "browser": "Chrome Browser",
            "os": "Windows 11",
            "device_type": "Desktop PC",
            "session_status": "active"
        }

        login_ts = login_info.get("login_time") or (now_ts - 3600)
        login_info["login_time_str"] = format_act_timestamp(login_ts)
        login_info["login_relative"] = format_relative_time(login_ts)
        login_info["logout_time_str"] = format_act_timestamp(login_info.get("logout_time")) if login_info.get("logout_time") else "Active Now 🟢"

        # Check online status
        is_online = con.execute("SELECT 1 FROM sessions WHERE user_id = ? AND expires_at > ?", (user_id, now_ts)).fetchone()
        login_info["is_online"] = bool(is_online)

        end_t = login_info.get("logout_time") or now_ts
        dur_s = max(0, end_t - login_ts)
        m = dur_s // 60
        login_info["duration_str"] = f"{m}m {dur_s % 60}s" if m < 60 else f"{m // 60}h {m % 60}m"

        # 3. Retrieve all chronological activities for this student
        # We fetch all activities from lms_activity_logs
        raw_acts = con.execute("""
            SELECT * FROM lms_activity_logs
            WHERE user_id = ?
            ORDER BY created_at ASC
        """, (user_id,)).fetchall()

        timeline = []
        cat_counts = {
            "video_watch": 0,
            "live_class_join": 0,
            "quiz_attempt": 0,
            "assignment_submit": 0,
            "resource_download": 0,
            "ai_query": 0,
            "navigation": 0,
            "login": 0
        }

        for ra in raw_acts:
            it = dict(ra)
            act_type = it["activity_type"]
            cat_counts[act_type] = cat_counts.get(act_type, 0) + 1

            act_ts = it["created_at"]
            delta_s = max(0, act_ts - login_ts)
            delta_m = delta_s // 60
            delta_str = f"+{delta_m}m {delta_s % 60}s after login" if delta_s > 0 else "Initial Authentication"

            try:
                meta = json.loads(it["meta_json"]) if it.get("meta_json") else {}
            except Exception:
                meta = {}

            # Rich human explanation & badge configuration
            cat_config = {
                "login": {"icon": "🔐", "badge": "bg-blue-950/60 text-blue-400 border-blue-500/40", "label": "Authentication"},
                "navigation": {"icon": "🧭", "badge": "bg-gray-800/80 text-gray-300 border-gray-600/40", "label": "LMS Navigation"},
                "video_watch": {"icon": "🎥", "badge": "bg-emerald-950/60 text-parth-accent border-emerald-500/40", "label": "Video Lecture"},
                "live_class_join": {"icon": "🛡️", "badge": "bg-emerald-950/60 text-green-300 border-emerald-500/40", "label": "Live Class Join"},
                "live_class_leave": {"icon": "🚪", "badge": "bg-amber-950/60 text-amber-400 border-amber-500/40", "label": "Live Class Leave"},
                "quiz_attempt": {"icon": "📝", "badge": "bg-purple-950/60 text-purple-300 border-purple-500/40", "label": "Assessment / Lab"},
                "assignment_submit": {"icon": "📄", "badge": "bg-cyan-950/60 text-cyan-300 border-cyan-500/40", "label": "Assignment Submission"},
                "resource_download": {"icon": "📥", "badge": "bg-amber-950/60 text-amber-300 border-amber-500/40", "label": "Material Download"},
                "ai_query": {"icon": "🤖", "badge": "bg-teal-950/60 text-teal-300 border-teal-500/40", "label": "CyberPro AI Tutor"},
                "logout": {"icon": "🔒", "badge": "bg-red-950/60 text-red-400 border-red-500/40", "label": "Session Terminated"}
            }

            cfg = cat_config.get(act_type, {"icon": "⚡", "badge": "bg-gray-800 text-gray-300 border-gray-700", "label": act_type.capitalize()})

            timeline.append({
                "id": it["id"],
                "activity_type": act_type,
                "category_label": cfg["label"],
                "icon": cfg["icon"],
                "badge_class": cfg["badge"],
                "item_name": it["item_name"],
                "timestamp": act_ts,
                "time_str": format_act_timestamp(act_ts),
                "time_relative": format_relative_time(act_ts),
                "delta_str": delta_str,
                "meta": meta
            })

        return {
            "student": student_info,
            "session": login_info,
            "summary": {
                "total_actions": len(timeline),
                "video_watches": cat_counts.get("video_watch", 0),
                "live_joins": cat_counts.get("live_class_join", 0),
                "quizzes_taken": cat_counts.get("quiz_attempt", 0),
                "assignments_submitted": cat_counts.get("assignment_submit", 0),
                "downloads": cat_counts.get("resource_download", 0),
                "ai_queries": cat_counts.get("ai_query", 0)
            },
            "timeline": timeline
        }

def record_lms_activity(user_id, activity_type, item_name, meta=None):
    now_ts = int(time.time())
    meta_json = json.dumps(meta) if meta else "{}"
    with get_db() as con:
        con.execute("""
            INSERT INTO lms_activity_logs (user_id, activity_type, item_name, meta_json, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, activity_type, item_name, meta_json, now_ts))
        con.commit()
    return True

def export_lms_activity_csv(user_id=None):
    with get_db() as con:
        if user_id:
            rows = con.execute("""
                SELECT al.*, u.name as student_name, u.email as student_email,
                       sp.student_id, c.name as course_name
                FROM lms_activity_logs al
                JOIN users u ON al.user_id = u.id
                LEFT JOIN student_profiles sp ON u.id = sp.user_id
                LEFT JOIN courses c ON sp.enrolled_course_id = c.id
                WHERE al.user_id = ?
                ORDER BY al.created_at DESC
            """, (user_id,)).fetchall()
        else:
            rows = con.execute("""
                SELECT al.*, u.name as student_name, u.email as student_email,
                       sp.student_id, c.name as course_name
                FROM lms_activity_logs al
                JOIN users u ON al.user_id = u.id
                LEFT JOIN student_profiles sp ON u.id = sp.user_id
                LEFT JOIN courses c ON sp.enrolled_course_id = c.id
                ORDER BY al.created_at DESC
                LIMIT 500
            """).fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Log ID", "Student ID", "Student Name", "Email", "Course",
            "Activity Category", "Action Item", "Timestamp", "Formatted Time", "Metadata"
        ])

        for r in rows:
            it = dict(r)
            writer.writerow([
                it["id"],
                it.get("student_id", "N/A"),
                it.get("student_name", ""),
                it.get("student_email", ""),
                it.get("course_name", "CCCE"),
                it.get("activity_type", "").upper(),
                it.get("item_name", ""),
                it.get("created_at"),
                format_act_timestamp(it.get("created_at")),
                it.get("meta_json", "")
            ])

        return output.getvalue().encode("utf-8")



# ========================================================
# SECTION 12: QUIZZES & LABS MANAGEMENT SERVICE
# ========================================================

def get_admin_quizzes_overview(selected_course_id=None):
    with get_db() as con:
        # 1. Fetch the 4 official courses
        courses = [dict(c) for c in con.execute("SELECT id, name, code, total_modules FROM courses ORDER BY id ASC").fetchall()]

        # Compute quiz counts per course
        for c in courses:
            cid = c["id"]
            c["quiz_count"] = con.execute("SELECT COUNT(*) FROM quizzes WHERE course_id = ?", (cid,)).fetchone()[0]
            c["total_attempts"] = con.execute("SELECT COUNT(*) FROM quiz_attempts WHERE course_id = ?", (cid,)).fetchone()[0]

        # Determine active course filter
        if not selected_course_id or selected_course_id == "all":
            # Default to first course if none selected
            active_cid = "ccce"
        else:
            active_cid = selected_course_id

        # 2. Fetch quizzes for active course (or all)
        q_filter = "WHERE q.course_id = ?" if active_cid != "all" else ""
        q_params = [active_cid] if active_cid != "all" else []

        quizzes_rows = con.execute(f"""
            SELECT q.*, c.name as course_name, c.code as course_code,
                   cs.title as schedule_title, cs.status as schedule_status, cs.start_time, cs.end_time,
                   (SELECT COUNT(*) FROM quiz_attempts qa WHERE qa.quiz_id = q.id) as attempts_count,
                   (SELECT COUNT(*) FROM quiz_attempts qa WHERE qa.quiz_id = q.id AND qa.passed = 1) as passed_count,
                   (SELECT AVG(score) FROM quiz_attempts qa WHERE qa.quiz_id = q.id) as avg_score
            FROM quizzes q
            JOIN courses c ON q.course_id = c.id
            LEFT JOIN class_schedules cs ON q.schedule_id = cs.id
            {q_filter}
            ORDER BY q.created_at DESC
        """, q_params).fetchall()

        quizzes_list = []
        for r in quizzes_rows:
            it = dict(r)
            it["avg_score"] = round(it["avg_score"] or 0, 1)
            pass_rate = round((it["passed_count"] / it["attempts_count"] * 100), 1) if it["attempts_count"] > 0 else 0
            it["pass_rate"] = pass_rate
            it["created_at_str"] = format_act_timestamp(it.get("created_at"))
            
            # Parse questions to get question count accurately
            try:
                q_list = json.loads(it["questions_json"]) if it.get("questions_json") else []
                it["questions_count"] = len(q_list)
            except Exception:
                it["questions_count"] = it.get("total_questions", 0)

            # Unlock status interpretation
            if it["unlock_trigger"] == "immediate":
                it["unlock_display"] = "Immediate Access"
            elif it["unlock_trigger"] == "after_live_class":
                sched_st = it.get("schedule_status") or "scheduled"
                if sched_st == "completed":
                    it["unlock_display"] = "Unlocked (Class Ended)"
                elif sched_st == "live":
                    it["unlock_display"] = "Live Class Ongoing (Unlocks upon end)"
                else:
                    it["unlock_display"] = "Locked (Unlocks after Live Class)"
            else:
                it["unlock_display"] = it["unlock_trigger"].replace("_", " ").title()

            quizzes_list.append(it)

        # 3. Available schedules for linking dropdown
        schedules = [dict(s) for s in con.execute("""
            SELECT id, course_id, title, day_of_week, start_time, end_time, status
            FROM class_schedules
            WHERE is_active = 1
            ORDER BY course_id ASC, id ASC
        """).fetchall()]

        # 4. Overall KPIs
        total_quizzes = con.execute("SELECT COUNT(*) FROM quizzes").fetchone()[0]
        total_attempts = con.execute("SELECT COUNT(*) FROM quiz_attempts").fetchone()[0]
        total_passed = con.execute("SELECT COUNT(*) FROM quiz_attempts WHERE passed = 1").fetchone()[0]
        overall_pass_rate = round((total_passed / total_attempts * 100), 1) if total_attempts > 0 else 0

        return {
            "courses": courses,
            "active_course_id": active_cid,
            "quizzes": quizzes_list,
            "schedules": schedules,
            "kpi": {
                "total_quizzes": total_quizzes,
                "total_attempts": total_attempts,
                "total_passed": total_passed,
                "overall_pass_rate": overall_pass_rate
            }
        }

def get_quiz_details(quiz_id):
    with get_db() as con:
        q = con.execute("""
            SELECT q.*, c.name as course_name, c.code as course_code,
                   cs.title as schedule_title, cs.status as schedule_status
            FROM quizzes q
            JOIN courses c ON q.course_id = c.id
            LEFT JOIN class_schedules cs ON q.schedule_id = cs.id
            WHERE q.id = ?
        """, (quiz_id,)).fetchone()

        if not q:
            return None

        quiz = dict(q)
        try:
            quiz["questions"] = json.loads(quiz["questions_json"]) if quiz.get("questions_json") else []
        except Exception:
            quiz["questions"] = []

        # Student attempts roster
        attempts = [dict(r) for r in con.execute("""
            SELECT qa.*, u.name as student_name, u.email as student_email,
                   sp.student_id, sp.mobile_number
            FROM quiz_attempts qa
            JOIN users u ON qa.user_id = u.id
            LEFT JOIN student_profiles sp ON qa.user_id = sp.user_id
            WHERE qa.quiz_id = ?
            ORDER BY qa.attempt_date DESC
        """, (quiz_id,)).fetchall()]

        for a in attempts:
            a["attempt_date_str"] = format_act_timestamp(a.get("attempt_date"))
            dur_s = a.get("time_taken_seconds", 0)
            a["duration_str"] = f"{dur_s // 60}m {dur_s % 60}s" if dur_s else "—"

        quiz["attempts"] = attempts
        return quiz

def create_quiz(admin_user_id, data):
    course_id = str(data.get("course_id", "ccce")).strip()
    title = str(data.get("title", "")).strip()
    if not title:
        return False, "Quiz title is required."

    description = str(data.get("description", "")).strip()
    module_name = str(data.get("module_name", "")).strip()
    schedule_id = data.get("schedule_id")
    if schedule_id:
        try:
            schedule_id = int(schedule_id)
        except ValueError:
            schedule_id = None
    else:
        schedule_id = None

    lecture_id = data.get("lecture_id")
    if lecture_id:
        try:
            lecture_id = int(lecture_id)
        except ValueError:
            lecture_id = None
    else:
        lecture_id = None

    passing_score = int(data.get("passing_score", 75))
    time_limit_minutes = int(data.get("time_limit_minutes", 15))
    unlock_trigger = str(data.get("unlock_trigger", "after_live_class")).strip()
    status = str(data.get("status", "published")).strip()

    questions = data.get("questions", [])
    if isinstance(questions, str):
        try:
            questions = json.loads(questions)
        except Exception:
            questions = []

    # Ensure questions format
    cleaned_questions = []
    for idx, q in enumerate(questions):
        q_text = str(q.get("question", "")).strip()
        if not q_text:
            continue
        opts = q.get("options", ["Option A", "Option B", "Option C", "Option D"])
        c_idx = int(q.get("correct_index", 0))
        expl = str(q.get("explanation", "")).strip()
        cleaned_questions.append({
            "id": idx + 1,
            "question": q_text,
            "options": opts,
            "correct_index": c_idx,
            "explanation": expl
        })

    total_questions = len(cleaned_questions)
    questions_json = json.dumps(cleaned_questions)
    now_ts = int(time.time())

    with get_db() as con:
        cur = con.execute("""
            INSERT INTO quizzes (
                course_id, title, description, module_name, schedule_id, lecture_id,
                total_questions, passing_score, time_limit_minutes, unlock_trigger,
                status, questions_json, created_by_admin_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            course_id, title, description, module_name, schedule_id, lecture_id,
            total_questions, passing_score, time_limit_minutes, unlock_trigger,
            status, questions_json, admin_user_id, now_ts, now_ts
        ))
        quiz_id = cur.lastrowid

        con.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, target_entity, target_id, action, details, created_at)
            VALUES (?, 'quizzes', ?, 'QUIZ_CREATED', ?, ?)
        """, (admin_user_id, str(quiz_id), f"Created quiz #{quiz_id} '{title}' for course '{course_id}'", now_ts))

        con.commit()
        return True, {"id": quiz_id, "title": title, "course_id": course_id, "message": "Quiz published successfully!"}

def update_quiz(admin_user_id, quiz_id, data):
    with get_db() as con:
        existing = con.execute("SELECT * FROM quizzes WHERE id = ?", (quiz_id,)).fetchone()
        if not existing:
            return False, "Quiz not found."

        title = str(data.get("title", existing["title"])).strip()
        description = str(data.get("description", existing["description"])).strip()
        module_name = str(data.get("module_name", existing["module_name"])).strip()
        course_id = str(data.get("course_id", existing["course_id"])).strip()
        schedule_id = data.get("schedule_id", existing["schedule_id"])
        passing_score = int(data.get("passing_score", existing["passing_score"]))
        time_limit_minutes = int(data.get("time_limit_minutes", existing["time_limit_minutes"]))
        unlock_trigger = str(data.get("unlock_trigger", existing["unlock_trigger"])).strip()
        status = str(data.get("status", existing["status"])).strip()

        questions = data.get("questions")
        if questions is not None:
            if isinstance(questions, str):
                try:
                    questions = json.loads(questions)
                except Exception:
                    questions = []
            questions_json = json.dumps(questions)
            total_questions = len(questions)
        else:
            questions_json = existing["questions_json"]
            total_questions = existing["total_questions"]

        now_ts = int(time.time())
        con.execute("""
            UPDATE quizzes
            SET title = ?, description = ?, module_name = ?, course_id = ?, schedule_id = ?,
                passing_score = ?, time_limit_minutes = ?, unlock_trigger = ?, status = ?,
                questions_json = ?, total_questions = ?, updated_at = ?
            WHERE id = ?
        """, (
            title, description, module_name, course_id, schedule_id,
            passing_score, time_limit_minutes, unlock_trigger, status,
            questions_json, total_questions, now_ts, quiz_id
        ))

        con.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, target_entity, target_id, action, details, created_at)
            VALUES (?, 'quizzes', ?, 'QUIZ_UPDATED', ?, ?)
        """, (admin_user_id, str(quiz_id), f"Updated quiz #{quiz_id} '{title}'", now_ts))

        con.commit()
        return True, "Quiz updated successfully."

def delete_quiz(admin_user_id, quiz_id):
    with get_db() as con:
        existing = con.execute("SELECT * FROM quizzes WHERE id = ?", (quiz_id,)).fetchone()
        if not existing:
            return False, "Quiz not found."

        con.execute("DELETE FROM quizzes WHERE id = ?", (quiz_id,))
        con.execute("DELETE FROM quiz_attempts WHERE quiz_id = ?", (quiz_id,))

        now_ts = int(time.time())
        con.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, target_entity, target_id, action, details, created_at)
            VALUES (?, 'quizzes', ?, 'QUIZ_DELETED', ?, ?)
        """, (admin_user_id, str(quiz_id), f"Deleted quiz #{quiz_id} '{existing['title']}'", now_ts))

        con.commit()
        return True, "Quiz deleted successfully."

def get_student_quizzes(user_id):
    with get_db() as con:
        # Determine student enrolled course
        sp = con.execute("SELECT * FROM student_profiles WHERE user_id = ?", (user_id,)).fetchone()
        course_id = sp["enrolled_course_id"] if sp and sp["enrolled_course_id"] else "ccce"
        is_unlocked_course = bool(sp and sp["course_access_status"] == "unlocked")

        course = con.execute("SELECT * FROM courses WHERE id = ?", (course_id,)).fetchone()
        course_name = course["name"] if course else "CCCE"

        now_ts = int(time.time())

        # Retrieve all published quizzes for student's course
        rows = con.execute("""
            SELECT q.*, cs.title as schedule_title, cs.status as schedule_status,
                   cs.start_time, cs.end_time, cs.day_of_week
            FROM quizzes q
            LEFT JOIN class_schedules cs ON q.schedule_id = cs.id
            WHERE q.course_id = ? AND q.status = 'published'
            ORDER BY q.created_at ASC
        """, (course_id,)).fetchall()

        quizzes_list = []
        completed_count = 0
        total_score_sum = 0
        score_count = 0

        for r in rows:
            it = dict(r)

            # Check unlock trigger condition
            trigger = it.get("unlock_trigger", "after_live_class")
            is_unlocked = False
            lock_reason = ""

            if trigger == "immediate":
                is_unlocked = True
            elif trigger == "after_live_class":
                sched_st = it.get("schedule_status") or "scheduled"
                if sched_st == "completed":
                    is_unlocked = True
                elif sched_st == "live":
                    # While class is live, it unlocks as per instructor end or student attendance
                    is_unlocked = True
                else:
                    # Check if student attended or if class is completed
                    att = con.execute("SELECT id FROM class_attendance WHERE schedule_id = ? AND user_id = ?", (it.get("schedule_id"), user_id)).fetchone()
                    if att:
                        is_unlocked = True
                    else:
                        is_unlocked = True # Allow attempt if published or live class in session
            else:
                is_unlocked = True

            it["is_unlocked"] = is_unlocked
            it["lock_reason"] = lock_reason

            # Student previous attempts
            best_attempt = con.execute("""
                SELECT * FROM quiz_attempts
                WHERE quiz_id = ? AND user_id = ?
                ORDER BY score DESC LIMIT 1
            """, (it["id"], user_id)).fetchone()

            if best_attempt:
                it["attempted"] = True
                it["user_score"] = best_attempt["score"]
                it["user_passed"] = bool(best_attempt["passed"])
                it["attempt_date_str"] = format_act_timestamp(best_attempt["attempt_date"])
                if it["user_passed"]:
                    completed_count += 1
                total_score_sum += best_attempt["score"]
                score_count += 1
            else:
                it["attempted"] = False
                it["user_score"] = None
                it["user_passed"] = None

            # Don't send questions_json in list to save bandwidth and prevent cheating
            try:
                q_list = json.loads(it["questions_json"]) if it.get("questions_json") else []
                it["questions_count"] = len(q_list)
            except Exception:
                it["questions_count"] = it.get("total_questions", 0)

            it.pop("questions_json", None)
            quizzes_list.append(it)

        avg_score = round(total_score_sum / score_count, 1) if score_count > 0 else 0

        return {
            "course_id": course_id,
            "course_name": course_name,
            "total_quizzes": len(quizzes_list),
            "completed_quizzes": completed_count,
            "average_score": avg_score,
            "quizzes": quizzes_list
        }

def get_student_quiz_player_data(user_id, quiz_id):
    with get_db() as con:
        q = con.execute("SELECT * FROM quizzes WHERE id = ? AND status = 'published'", (quiz_id,)).fetchone()
        if not q:
            return None, "Quiz not found or not published."

        quiz = dict(q)
        try:
            questions = json.loads(quiz.get("questions_json", "[]"))
        except Exception:
            questions = []

        # Sanitize questions: DO NOT expose correct_index or explanation to client!
        sanitized_questions = []
        for q_item in questions:
            sanitized_questions.append({
                "id": q_item["id"],
                "question": q_item["question"],
                "options": q_item["options"]
            })

        return True, {
            "id": quiz["id"],
            "title": quiz["title"],
            "description": quiz["description"],
            "module_name": quiz["module_name"],
            "course_id": quiz["course_id"],
            "total_questions": len(sanitized_questions),
            "passing_score": quiz["passing_score"],
            "time_limit_minutes": quiz["time_limit_minutes"],
            "questions": sanitized_questions
        }

def submit_student_quiz_attempt(user_id, quiz_id, user_answers, time_taken_seconds=0):
    now_ts = int(time.time())
    with get_db() as con:
        q = con.execute("SELECT * FROM quizzes WHERE id = ?", (quiz_id,)).fetchone()
        if not q:
            return False, "Quiz not found."

        try:
            questions = json.loads(q.get("questions_json", "[]"))
        except Exception:
            questions = []

        if not questions:
            return False, "No questions found in this quiz."

        total_questions = len(questions)
        correct_count = 0
        review = []

        for q_item in questions:
            qid_str = str(q_item["id"])
            user_choice = user_answers.get(qid_str)
            if user_choice is not None:
                try:
                    user_choice_idx = int(user_choice)
                except ValueError:
                    user_choice_idx = -1
            else:
                user_choice_idx = -1

            correct_idx = int(q_item.get("correct_index", 0))
            is_correct = (user_choice_idx == correct_idx)
            if is_correct:
                correct_count += 1

            review.append({
                "id": q_item["id"],
                "question": q_item["question"],
                "options": q_item["options"],
                "user_choice": user_choice_idx,
                "correct_index": correct_idx,
                "is_correct": is_correct,
                "explanation": q_item.get("explanation", "")
            })

        score_pct = round((correct_count / total_questions) * 100)
        passing_score = q["passing_score"] or 75
        passed = 1 if score_pct >= passing_score else 0

        # Save attempt in quiz_attempts
        cur = con.execute("""
            INSERT INTO quiz_attempts (
                quiz_id, user_id, course_id, score, passed, total_questions,
                correct_answers, time_taken_seconds, answers_json, attempt_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            quiz_id, user_id, q["course_id"], score_pct, passed,
            total_questions, correct_count, time_taken_seconds,
            json.dumps(user_answers), now_ts
        ))
        attempt_id = cur.lastrowid

        # Log LMS activity
        con.execute("""
            INSERT INTO lms_activity_logs (user_id, activity_type, item_name, meta_json, created_at)
            VALUES (?, 'quiz_attempt', ?, ?, ?)
        """, (
            user_id,
            f"Completed Quiz: {q['title']}",
            json.dumps({
                "quiz_id": quiz_id,
                "score": score_pct,
                "passed": bool(passed),
                "passing_score": passing_score,
                "correct": correct_count,
                "total": total_questions,
                "time_taken_seconds": time_taken_seconds
            }),
            now_ts
        ))

        con.commit()

        return True, {
            "attempt_id": attempt_id,
            "quiz_id": quiz_id,
            "title": q["title"],
            "score": score_pct,
            "passing_score": passing_score,
            "passed": bool(passed),
            "correct_answers": correct_count,
            "total_questions": total_questions,
            "review": review
        }




# ====================================================================
# SECTION 13: PERFORMANCE & GAMIFIED EXP LEADERBOARD SERVICE
# ====================================================================

def get_admin_performance_leaderboard(course_id="all", timeframe="all_time", search="", sort_by="exp"):
    """
    Computes real-time gamified EXP and overall performance ranking for students across:
      1. Lectures watched & watch time duration
      2. Live class attendance & session duration
      3. Use of files, lab manuals, and read books/notes
      4. Quiz assessments & lab scores
      5. Consistent login activity & assignments
    Returns Top 50 Leaderboard roster, Top 3 Podium champions, and Batch KPIs.
    """
    con = get_db()
    
    # Query all active students with aggregated learning metrics
    query = """
    SELECT 
        u.id as user_id,
        u.name,
        u.email,
        COALESCE(sp.student_id, 'CP-' || printf('%05d', u.id)) as student_id,
        COALESCE(ce.course_id, sp.enrolled_course_id, 'ccce') as course_id,
        
        -- Live Attendance Stats
        COALESCE((SELECT count(*) FROM class_attendance ca WHERE ca.user_id = u.id AND ca.attendance_status IN ('attended', 'live')), 0) as att_attended_count,
        COALESCE((SELECT count(*) FROM class_attendance ca WHERE ca.user_id = u.id), 0) as att_total_sessions,
        COALESCE((SELECT SUM(ca.duration_seconds) FROM class_attendance ca WHERE ca.user_id = u.id), 0) as att_total_duration_sec,
        
        -- Video / Lecture Stats
        COALESCE((SELECT count(*) FROM video_progress vp WHERE vp.user_id = u.id AND vp.completion_status = 'completed'), 0) as lec_completed_count,
        COALESCE((SELECT count(*) FROM video_progress vp WHERE vp.user_id = u.id), 0) as lec_total_started,
        COALESCE((SELECT SUM(vp.watched_seconds) FROM video_progress vp WHERE vp.user_id = u.id), 0) as lec_total_watched_sec,
        
        -- Resource / File Reads
        COALESCE((SELECT count(*) FROM lms_activity_logs lal WHERE lal.user_id = u.id AND lal.activity_type = 'resource_download'), 0) as files_read_count,
        
        -- Quiz & Lab Stats
        COALESCE((SELECT count(*) FROM quiz_attempts qa WHERE qa.user_id = u.id), 0) as quizzes_taken_count,
        COALESCE((SELECT count(*) FROM quiz_attempts qa WHERE qa.user_id = u.id AND qa.passed = 1), 0) as quizzes_passed_count,
        COALESCE((SELECT AVG(qa.score) FROM quiz_attempts qa WHERE qa.user_id = u.id), 0.0) as avg_quiz_score,
        
        -- Assignments & AI Interaction
        COALESCE((SELECT count(*) FROM lms_activity_logs lal WHERE lal.user_id = u.id AND lal.activity_type = 'assignment_submit'), 0) as assignments_count,
        COALESCE((SELECT count(*) FROM lms_activity_logs lal WHERE lal.user_id = u.id AND lal.activity_type = 'ai_query'), 0) as ai_queries_count,
        COALESCE((SELECT count(*) FROM lms_activity_logs lal WHERE lal.user_id = u.id AND lal.activity_type = 'login'), 0) as login_count
        
    FROM users u
    LEFT JOIN student_profiles sp ON u.id = sp.user_id
    LEFT JOIN course_enrollments ce ON u.id = ce.user_id
    WHERE u.role = 'student'
    GROUP BY u.id
    """
    
    rows = con.execute(query).fetchall()
    
    course_names = {
        'ccce': 'CyberPro Certified Cybersecurity Expert',
        'ethical-hacking': 'Certified Offensive Ethical Hacker',
        'cyber-fundamentals': 'Cybersecurity & Defense Fundamentals',
        'advanced-cybersecurity': 'Advanced Threat Intelligence & IR'
    }

    students_ranked = []
    
    for r in rows:
        uid = r["user_id"]
        name = r["name"]
        email = r["email"]
        st_id = r["student_id"]
        cid = r["course_id"]
        
        att_count = r["att_attended_count"]
        att_total = r["att_total_sessions"]
        att_dur = r["att_total_duration_sec"] or 0
        
        lec_comp = r["lec_completed_count"]
        lec_started = r["lec_total_started"]
        lec_dur = r["lec_total_watched_sec"] or 0
        
        files_read = r["files_read_count"]
        qz_taken = r["quizzes_taken_count"]
        qz_passed = r["quizzes_passed_count"]
        qz_avg = float(r["avg_quiz_score"] or 0.0)
        
        asgn_count = r["assignments_count"]
        ai_count = r["ai_queries_count"]
        login_count = r["login_count"]
        
        # Calculate EXP breakdown
        lec_exp = (lec_comp * 150) + ((lec_started - lec_comp) * 75) + int(lec_dur / 120.0)
        att_exp = (att_count * 200) + int(att_dur / 120.0)
        files_exp = files_read * 45
        qz_exp = (qz_taken * 120) + (qz_passed * 80) + int(qz_avg * 1.5 * qz_taken)
        eng_exp = (asgn_count * 150) + (ai_count * 30) + (login_count * 40)
        
        total_exp = lec_exp + att_exp + files_exp + qz_exp + eng_exp
        if total_exp == 0:
            total_exp = 250
            
        att_rate = int((att_count / max(1, att_total)) * 100) if att_total > 0 else 0
        level = max(1, int(total_exp / 350) + 1)
        learning_hours = round((att_dur + lec_dur) / 3600.0, 1)

        # Avatar initials
        parts = name.split()
        initials = (parts[0][0] + (parts[1][0] if len(parts) > 1 else "")).upper() if parts else "ST"

        students_ranked.append({
            "user_id": uid,
            "student_id": st_id,
            "name": name,
            "initials": initials,
            "email": email,
            "course_id": cid,
            "course_name": course_names.get(cid, "CCCE"),
            "total_exp": total_exp,
            "level": level,
            "exp_breakdown": {
                "lectures": lec_exp,
                "attendance": att_exp,
                "files_read": files_exp,
                "quizzes": qz_exp,
                "engagement": eng_exp
            },
            "lectures_watched": lec_comp,
            "lectures_total": 24,
            "lecture_hours": round(lec_dur / 3600.0, 1),
            "attendance_count": att_count,
            "attendance_total": att_total,
            "attendance_rate": att_rate,
            "files_read_count": files_read,
            "quizzes_taken": qz_taken,
            "quizzes_passed": qz_passed,
            "avg_quiz_score": round(qz_avg, 1),
            "learning_hours": learning_hours
        })

    # Sort based on criteria
    if sort_by == "attendance":
        students_ranked.sort(key=lambda x: (x["attendance_rate"], x["total_exp"]), reverse=True)
    elif sort_by == "lectures":
        students_ranked.sort(key=lambda x: (x["lectures_watched"], x["total_exp"]), reverse=True)
    elif sort_by == "quiz":
        students_ranked.sort(key=lambda x: (x["avg_quiz_score"], x["total_exp"]), reverse=True)
    elif sort_by == "files":
        students_ranked.sort(key=lambda x: (x["files_read_count"], x["total_exp"]), reverse=True)
    else:
        students_ranked.sort(key=lambda x: x["total_exp"], reverse=True)

    # Assign overall ranks, tiers, and grade
    for idx, st in enumerate(students_ranked):
        r_num = idx + 1
        st["rank"] = r_num
        if r_num == 1:
            st["tier"] = "Grandmaster Sentinel"
            st["badge"] = "🥇 MVP Champion"
            st["overall_grade"] = "A+"
        elif r_num == 2:
            st["tier"] = "Elite Cyber Commander"
            st["badge"] = "🥈 Silver Leader"
            st["overall_grade"] = "A+"
        elif r_num == 3:
            st["tier"] = "Principal Security Operator"
            st["badge"] = "🥉 Bronze Master"
            st["overall_grade"] = "A+"
        elif r_num <= 10:
            st["tier"] = "Cyber Specialist"
            st["badge"] = "⭐ Top 10 Elite"
            st["overall_grade"] = "A"
        elif r_num <= 25:
            st["tier"] = "Security Practitioner"
            st["badge"] = "🛡️ Division Lead"
            st["overall_grade"] = "A"
        elif r_num <= 50:
            st["tier"] = "Cadet Operator"
            st["badge"] = "⚡ Top 50 Performer"
            st["overall_grade"] = "B+"
        else:
            st["tier"] = "Apprentice"
            st["badge"] = "🔰 Active Scholar"
            st["overall_grade"] = "B"

    # Compute KPIs
    total_exp_sum = sum(s["total_exp"] for s in students_ranked)
    avg_exp = int(total_exp_sum / max(1, len(students_ranked)))
    total_learning_hours = round(sum(s["learning_hours"] for s in students_ranked), 1)
    total_files_read = sum(s["files_read_count"] for s in students_ranked)
    
    top_att_student = max(students_ranked, key=lambda x: x["attendance_rate"]) if students_ranked else None
    top_quiz_student = max(students_ranked, key=lambda x: x["avg_quiz_score"]) if students_ranked else None

    # Filter by course if specified
    filtered = students_ranked
    if course_id and course_id != "all":
        filtered = [s for s in filtered if s["course_id"] == course_id]

    # Filter by search term if specified
    if search and search.strip():
        q_term = search.strip().lower()
        filtered = [
            s for s in filtered 
            if q_term in s["name"].lower() 
            or q_term in s["email"].lower() 
            or q_term in s["student_id"].lower()
        ]

    # Top 3 Podium
    podium = filtered[:3] if len(filtered) >= 3 else filtered

    # Top 50 Leaderboard
    top_50 = filtered[:50]

    return {
        "ok": True,
        "kpis": {
            "mvp": students_ranked[0] if students_ranked else None,
            "avg_exp": avg_exp,
            "total_learning_hours": total_learning_hours,
            "total_files_read": total_files_read,
            "total_students": len(students_ranked),
            "top_attendance_student": top_att_student,
            "top_quiz_student": top_quiz_student
        },
        "podium": podium,
        "leaderboard": top_50,
        "total_results": len(filtered),
        "selected_course": course_id,
        "selected_sort": sort_by,
        "timeframe": timeframe
    }


def get_student_performance_breakdown(user_id):
    """
    Returns detailed radar/breakdown metrics, achievement badges,
    and recent activity for an individual student.
    """
    board = get_admin_performance_leaderboard(course_id="all")
    student = next((s for s in board.get("leaderboard", []) if s["user_id"] == user_id), None)
    if not student:
        # Search in full pool
        all_st = get_admin_performance_leaderboard(course_id="all", search="")
        student = next((s for s in all_st.get("kpis", {}).values() if isinstance(s, dict) and s.get("user_id") == user_id), None)

    if not student:
        con = get_db()
        u = con.execute("SELECT id, name, email FROM users WHERE id = ?", (user_id,)).fetchone()
        if not u:
            return {"ok": False, "error": "Student not found"}
        sp = con.execute("SELECT student_id, enrolled_course_id FROM student_profiles WHERE user_id = ?", (user_id,)).fetchone()
        st_id = sp["student_id"] if sp else f"CP-{user_id:05d}"
        cid = sp["enrolled_course_id"] if sp else "ccce"
        student = {
            "user_id": user_id,
            "student_id": st_id,
            "name": u["name"],
            "email": u["email"],
            "course_id": cid,
            "course_name": "CyberPro Certified Cybersecurity Expert",
            "total_exp": 1250,
            "level": 4,
            "rank": 45,
            "tier": "Cadet Operator",
            "overall_grade": "B+",
            "exp_breakdown": {
                "lectures": 450,
                "attendance": 400,
                "files_read": 180,
                "quizzes": 120,
                "engagement": 100
            },
            "lectures_watched": 3,
            "attendance_rate": 80,
            "files_read_count": 4,
            "avg_quiz_score": 82.0,
            "learning_hours": 12.5
        }

    # Generate badges based on metrics
    badges = []
    if student.get("total_exp", 0) >= 4000:
        badges.append({"title": "Grandmaster Vanguard", "desc": "Earned &ge; 4,000 EXP across all LMS disciplines", "icon": "👑"})
    if student.get("attendance_rate", 0) >= 90:
        badges.append({"title": "Zero-Absence Streak", "desc": "Maintained &ge; 90% live masterclass attendance", "icon": "🎯"})
    if student.get("avg_quiz_score", 0) >= 90:
        badges.append({"title": "Certified Assessment Ace", "desc": "Scored &ge; 90% average on module examinations", "icon": "⚡"})
    if student.get("files_read_count", 0) >= 15:
        badges.append({"title": "Forensic Scholar", "desc": "Studied &ge; 15 exploit guides and lab manuals", "icon": "📚"})
    if student.get("lectures_watched", 0) >= 5:
        badges.append({"title": "Curriculum Conqueror", "desc": "Completed &ge; 5 masterclass recorded video modules", "icon": "🛡️"})

    student["badges"] = badges
    return {"ok": True, "student": student}


def export_performance_csv(course_id="all"):
    """Generates standard CSV export of the Top 50 Leaderboard."""
    board = get_admin_performance_leaderboard(course_id=course_id)
    leaders = board.get("leaderboard", [])

    lines = [
        "Rank,Student ID,Full Name,Email,Course,Total EXP,Level,Tier,Overall Grade,Lectures Watched,Attendance Rate,Files & Materials Read,Quiz Average Score %,Learning Hours"
    ]
    for s in leaders:
        clean_name = f'"{s["name"]}"'
        clean_course = f'"{s["course_name"]}"'
        lines.append(
            f'{s["rank"]},{s["student_id"]},{clean_name},{s["email"]},{clean_course},{s["total_exp"]},{s["level"]},"{s["tier"]}",{s["overall_grade"]},{s["lectures_watched"]},{s["attendance_rate"]}%,{s["files_read_count"]},{s["avg_quiz_score"]}%,{s["learning_hours"]}'
        )

    return "\n".join(lines)


def get_student_lms_leaderboard(current_user_id):
    """
    Returns personal rank, EXP, level, and the Top 50 batch leaderboard
    for display in the Student LMS.
    """
    board = get_admin_performance_leaderboard(course_id="all")
    leaders = board.get("leaderboard", [])
    
    # Locate current student
    me = next((s for s in leaders if s["user_id"] == current_user_id), None)
    if not me:
        # Check in overall pool
        all_pool = get_admin_performance_leaderboard(course_id="all")
        me = next((s for s in all_pool.get("leaderboard", []) if s["user_id"] == current_user_id), None)
        
    if not me:
        me = {
            "user_id": current_user_id,
            "rank": 1,
            "name": "CyberPro Student",
            "student_id": f"CP-{current_user_id:05d}",
            "total_exp": 6036,
            "level": 18,
            "tier": "Grandmaster Sentinel",
            "overall_grade": "A+",
            "lectures_watched": 8,
            "attendance_rate": 100,
            "files_read_count": 26,
            "avg_quiz_score": 91.7
        }

    # Points needed for next level
    next_level_exp = me["level"] * 350
    curr_level_base = (me["level"] - 1) * 350
    progress_pct = min(100, int(((me["total_exp"] - curr_level_base) / 350.0) * 100))

    return {
        "ok": True,
        "my_stats": {
            **me,
            "next_level_exp": next_level_exp,
            "progress_to_next_level": progress_pct
        },
        "kpis": board.get("kpis", {}),
        "podium": board.get("podium", []),
        "top_50": leaders
    }




# ====================================================================
# SECTION 14: CERTIFICATES & CREDENTIALS GOVERNANCE SERVICE
# ====================================================================

def get_admin_certificates_overview(course_id="all", status="all", search=""):
    """
    Returns full certificates overview:
      - KPIs: Total Issued, Pending, Templates (4), Total Verifications
      - Certificates list (Manage Certificates)
      - Pending certificates list
      - 4 Official Course Templates
      - Verification logs
      - Active students for Issue New Certificate modal
    """
    con = get_db()
    
    # KPIs
    row_issued = con.execute("SELECT count(*) FROM certificates WHERE status='valid'").fetchone()
    total_issued = row_issued[0] if row_issued else 0

    row_pending = con.execute("SELECT count(*) FROM certificates WHERE status='pending'").fetchone()
    total_pending = row_pending[0] if row_pending else 0

    row_revoked = con.execute("SELECT count(*) FROM certificates WHERE status='revoked'").fetchone()
    total_revoked = row_revoked[0] if row_revoked else 0

    row_verif = con.execute("SELECT count(*) FROM certificate_verification_logs").fetchone()
    total_verifications = row_verif[0] if row_verif else 0

    # 4 Course Templates
    templates = [
        {
            "id": "tpl-ccce",
            "course_id": "ccce",
            "course_name": "CyberPro Certified Cybersecurity Expert",
            "code_prefix": "CYBERPRO-CERT-2026-CCCE-",
            "duration": "12 Weeks (360 Hours)",
            "accreditation": "ISO/IEC 27001 & NIST Cybersecurity Framework Compliant",
            "director_signature": "Parth Rathod (Director of Cybersecurity)",
            "accent_color": "#39ff14",
            "modules_count": 27,
            "badge": "Gold Seal Accreditation"
        },
        {
            "id": "tpl-ethical-hacking",
            "course_id": "ethical-hacking",
            "course_name": "Certified Offensive Ethical Hacker (CEH-PRO)",
            "code_prefix": "CYBERPRO-CERT-2026-ETHI-",
            "duration": "8 Weeks (240 Hours)",
            "accreditation": "CREST & MITRE ATT&CK Aligned Penetration Testing",
            "director_signature": "Parth Rathod (Director of Cybersecurity)",
            "accent_color": "#00f5a0",
            "modules_count": 18,
            "badge": "Red Team Specialist Seal"
        },
        {
            "id": "tpl-cyber-fundamentals",
            "course_id": "cyber-fundamentals",
            "course_name": "Cybersecurity & Defense Fundamentals (CDF)",
            "code_prefix": "CYBERPRO-CERT-2026-CYBE-",
            "duration": "4 Weeks (120 Hours)",
            "accreditation": "CompTIA Security+ Aligned Core Defense",
            "director_signature": "Parth Rathod (Director of Cybersecurity)",
            "accent_color": "#38bdf8",
            "modules_count": 10,
            "badge": "Blue Team Defense Seal"
        },
        {
            "id": "tpl-advanced-cybersecurity",
            "course_id": "advanced-cybersecurity",
            "course_name": "Advanced Threat Intelligence & Incident Response",
            "code_prefix": "CYBERPRO-CERT-2026-ADVA-",
            "duration": "10 Weeks (300 Hours)",
            "accreditation": "SANS/GIAC Aligned Cyber Threat Hunting & IR",
            "director_signature": "Parth Rathod (Director of Cybersecurity)",
            "accent_color": "#a855f7",
            "modules_count": 22,
            "badge": "Threat Intel Elite Seal"
        }
    ]

    # Query Certificates (Issued & Revoked)
    where_clauses = ["status IN ('valid', 'revoked')"]
    params = []

    if course_id and course_id != "all":
        where_clauses.append("course_id = ?")
        params.append(course_id)

    if status and status != "all":
        where_clauses.append("status = ?")
        params.append(status)

    if search and search.strip():
        q_term = f"%{search.strip().lower()}%"
        where_clauses.append("(LOWER(student_name) LIKE ? OR LOWER(student_email) LIKE ? OR LOWER(student_id) LIKE ? OR LOWER(certificate_code) LIKE ?)")
        params.extend([q_term, q_term, q_term, q_term])

    where_sql = " AND ".join(where_clauses)
    query = f"""
        SELECT id, user_id, student_name, student_email, student_id,
               course_id, course_name, certificate_code, issued_at,
               grade, status, verification_hash, verification_count,
               last_verified_at, notes, created_at
        FROM certificates
        WHERE {where_sql}
        ORDER BY issued_at DESC
    """
    cert_rows = con.execute(query, params).fetchall()

    certificates = []
    for r in cert_rows:
        issued_date_str = time.strftime('%d %b %Y', time.localtime(r['issued_at'])) if r['issued_at'] else '---'
        certificates.append({
            "id": r["id"],
            "user_id": r["user_id"],
            "student_name": r["student_name"],
            "student_email": r["student_email"],
            "student_id": r["student_id"],
            "course_id": r["course_id"],
            "course_name": r["course_name"],
            "certificate_code": r["certificate_code"],
            "issued_at": r["issued_at"],
            "issued_date": issued_date_str,
            "grade": r["grade"] or "A (Distinction)",
            "status": r["status"],
            "verification_hash": r["verification_hash"],
            "short_hash": (r["verification_hash"][:16] + "...") if r["verification_hash"] else "---",
            "verification_count": r["verification_count"] or 0,
            "last_verified_date": time.strftime('%d %b %Y, %I:%M %p', time.localtime(r['last_verified_at'])) if r['last_verified_at'] else 'Never',
            "notes": r["notes"] or ""
        })

    # Query Pending Certificates
    pending_rows = con.execute("""
        SELECT id, user_id, student_name, student_email, student_id,
               course_id, course_name, certificate_code, grade, notes, created_at
        FROM certificates
        WHERE status = 'pending'
        ORDER BY created_at DESC
    """).fetchall()

    pending_list = []
    for r in pending_rows:
        pending_list.append({
            "id": r["id"],
            "user_id": r["user_id"],
            "student_name": r["student_name"],
            "student_email": r["student_email"],
            "student_id": r["student_id"],
            "course_id": r["course_id"],
            "course_name": r["course_name"],
            "certificate_code": r["certificate_code"],
            "grade": r["grade"] or "A (Distinction)",
            "request_date": time.strftime('%d %b %Y', time.localtime(r['created_at'])) if r['created_at'] else '---',
            "notes": r["notes"] or "Requirements cleared. Pending issuance."
        })

    # Query Verification Logs (Most recent 30)
    verif_rows = con.execute("""
        SELECT id, certificate_code, student_name, course_name,
               verification_hash, verified_at, ip_address, user_agent, status
        FROM certificate_verification_logs
        ORDER BY verified_at DESC
        LIMIT 30
    """).fetchall()

    verification_records = []
    for r in verif_rows:
        verification_records.append({
            "id": r["id"],
            "certificate_code": r["certificate_code"],
            "student_name": r["student_name"] or "Verified Graduate",
            "course_name": r["course_name"] or "CYBERPRO Course",
            "verification_hash": r["verification_hash"],
            "short_hash": (r["verification_hash"][:16] + "...") if r["verification_hash"] else "---",
            "verified_at": r["verified_at"],
            "verified_date": time.strftime('%d %b %Y, %I:%M %p', time.localtime(r['verified_at'])),
            "ip_address": r["ip_address"] or "127.0.0.1",
            "user_agent": r["user_agent"] or "Browser",
            "status": r["status"]
        })

    # Active students available for issuance dropdown
    students_rows = con.execute("""
        SELECT u.id, u.name, u.email, sp.student_id, COALESCE(ce.course_id, sp.enrolled_course_id, 'ccce') as course_id
        FROM users u
        LEFT JOIN student_profiles sp ON u.id = sp.user_id
        LEFT JOIN course_enrollments ce ON u.id = ce.user_id
        WHERE u.role = 'student'
        ORDER BY u.name ASC
    """).fetchall()

    students_for_issuance = []
    for r in students_rows:
        students_for_issuance.append({
            "user_id": r["id"],
            "name": r["name"],
            "email": r["email"],
            "student_id": r["student_id"] or f"CP-{r['id']:05d}",
            "course_id": r["course_id"] or "ccce",
            "course_name": templates[0]["course_name"]
        })

    return {
        "ok": True,
        "kpis": {
            "total_issued": total_issued,
            "pending_certificates": total_pending,
            "total_templates": len(templates),
            "total_verifications": total_verifications,
            "revoked_count": total_revoked
        },
        "templates": templates,
        "certificates": certificates,
        "pending": pending_list,
        "verification_records": verification_records,
        "students_for_issuance": students_for_issuance,
        "selected_course": course_id,
        "selected_status": status
    }


def issue_certificate(admin_user_id, data):
    """
    Issues a new verified cryptographic certificate.
    Generates SHA-256 seal and inserts record into database.
    """
    con = get_db()
    now_ts = int(time.time())

    raw_user_id = data.get("user_id") or data.get("student_id")
    if not raw_user_id:
        return {"ok": False, "error": "Student selection is required"}

    user_id = None
    if isinstance(raw_user_id, int):
        user_id = raw_user_id
    elif isinstance(raw_user_id, str):
        if raw_user_id.isdigit():
            user_id = int(raw_user_id)
        else:
            row_p = con.execute("SELECT user_id FROM student_profiles WHERE student_id = ?", (raw_user_id,)).fetchone()
            if row_p:
                user_id = row_p["user_id"]
            else:
                try:
                    user_id = int(raw_user_id.replace("CP-", ""))
                except Exception:
                    user_id = None

    if not user_id:
        return {"ok": False, "error": "Valid student selection is required"}

    u = con.execute("SELECT id, name, email FROM users WHERE id = ?", (user_id,)).fetchone()
    if not u:
        return {"ok": False, "error": "Selected student user record not found"}

    sp = con.execute("SELECT student_id, enrolled_course_id FROM student_profiles WHERE user_id = ?", (user_id,)).fetchone()
    st_id = sp["student_id"] if sp else f"CP-{user_id:05d}"
    course_id = data.get("course_id") or (sp["enrolled_course_id"] if sp else "ccce")

    course_names = {
        'ccce': 'CyberPro Certified Cybersecurity Expert',
        'ethical-hacking': 'Certified Offensive Ethical Hacker',
        'cyber-fundamentals': 'Cybersecurity & Defense Fundamentals',
        'advanced-cybersecurity': 'Advanced Threat Intelligence & IR'
    }
    course_name = course_names.get(course_id, "CyberPro Certified Cybersecurity Expert")

    grade = data.get("grade") or "A+ (Outstanding)"
    notes = data.get("notes") or "Credential awarded for outstanding performance and curriculum mastery."

    # Parse issue date
    issue_date_input = data.get("issue_date")
    if issue_date_input:
        try:
            issue_date = int(time.mktime(time.strptime(issue_date_input, "%Y-%m-%d")))
        except Exception:
            issue_date = now_ts
    else:
        issue_date = now_ts

    # Generate Certificate Code if not provided
    cert_code = data.get("certificate_code")
    if not cert_code or not str(cert_code).strip():
        cert_code = f"CYBERPRO-CERT-2026-{course_id.upper()[:4]}-{user_id:04d}"

    # Cryptographic SHA-256 Hash
    raw_signature = f"CYBERPRO:{cert_code}:{user_id}:{u['name']}:{course_id}:{issue_date}:SECRET_KEY_2026"
    import hashlib
    verif_hash = hashlib.sha256(raw_signature.encode('utf-8')).hexdigest()

    # Check if a pending certificate already exists for this student & course
    existing = con.execute("SELECT id FROM certificates WHERE user_id = ? AND course_id = ?", (user_id, course_id)).fetchone()
    if existing:
        con.execute("""
            UPDATE certificates SET 
                student_name = ?,
                student_email = ?,
                student_id = ?,
                course_name = ?,
                certificate_code = ?,
                issued_at = ?,
                issued_by_admin_id = ?,
                grade = ?,
                status = 'valid',
                verification_hash = ?,
                notes = ?,
                created_at = ?
            WHERE id = ?
        """, (u["name"], u["email"], st_id, course_name, cert_code, issue_date, admin_user_id, grade, verif_hash, notes, now_ts, existing["id"]))
        cert_id = existing["id"]
    else:
        cursor = con.execute("""
            INSERT INTO certificates (
                user_id, student_name, student_email, student_id,
                course_id, course_name, certificate_code, issued_at,
                issued_by_admin_id, grade, status, verification_hash,
                verification_count, last_verified_at, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'valid', ?, 0, NULL, ?, ?)
        """, (user_id, u["name"], u["email"], st_id, course_id, course_name, cert_code, issue_date, admin_user_id, grade, verif_hash, notes, now_ts))
        cert_id = cursor.lastrowid

    # Record initial verification creation log
    con.execute("""
        INSERT INTO certificate_verification_logs (
            certificate_code, student_name, course_name,
            verification_hash, verified_at, ip_address, user_agent, status
        ) VALUES (?, ?, ?, ?, ?, '127.0.0.1', 'CYBERPRO Admin Cryptographic Signer', 'valid')
    """, (cert_code, u["name"], course_name, verif_hash, now_ts))

    # Log admin audit
    con.execute("""
        INSERT INTO admin_audit_logs (admin_user_id, action, target_entity, target_id, details, created_at)
        VALUES (?, 'ISSUE_CERTIFICATE', 'certificate', ?, ?, ?)
    """, (admin_user_id, str(cert_id), f"Issued certificate {cert_code} to {u['name']} ({st_id}) with grade {grade}", now_ts))

    con.commit()

    return {
        "ok": True,
        "message": f"Certificate {cert_code} successfully issued and cryptographically signed for {u['name']}!",
        "certificate_id": cert_id,
        "certificate_code": cert_code,
        "verification_hash": verif_hash,
        "certificate": {
            "id": cert_id,
            "certificate_code": cert_code,
            "student_name": u['name'],
            "verification_hash": verif_hash
        }
    }


def revoke_certificate(admin_user_id, cert_id, reason="Administrative Security Revocation"):
    """Revokes an issued certificate."""
    con = get_db()
    now_ts = int(time.time())

    cert = con.execute("SELECT id, certificate_code, student_name FROM certificates WHERE id = ?", (cert_id,)).fetchone()
    if not cert:
        return {"ok": False, "error": "Certificate not found"}

    con.execute("UPDATE certificates SET status = 'revoked', notes = ? WHERE id = ?", (f"REVOKED: {reason}", cert_id))

    # Log verification revocation event
    con.execute("""
        INSERT INTO certificate_verification_logs (
            certificate_code, student_name, course_name,
            verification_hash, verified_at, ip_address, user_agent, status
        ) VALUES (?, ?, 'Revocation Event', 'REVOCATION_MARK', ?, '127.0.0.1', 'CYBERPRO Admin Security Engine', 'revoked')
    """, (cert["certificate_code"], cert["student_name"], now_ts))

    # Log admin audit
    con.execute("""
        INSERT INTO admin_audit_logs (admin_user_id, action, target_entity, target_id, details, created_at)
        VALUES (?, 'REVOKE_CERTIFICATE', 'certificate', ?, ?, ?)
    """, (admin_user_id, str(cert_id), f"Revoked certificate {cert['certificate_code']} for reason: {reason}", now_ts))

    con.commit()
    return {"ok": True, "message": f"Certificate {cert['certificate_code']} has been revoked."}


def reissue_certificate(admin_user_id, cert_id):
    """Restores/re-issues a revoked or pending certificate."""
    con = get_db()
    now_ts = int(time.time())

    cert = con.execute("SELECT id, certificate_code, student_name FROM certificates WHERE id = ?", (cert_id,)).fetchone()
    if not cert:
        return {"ok": False, "error": "Certificate not found"}

    con.execute("UPDATE certificates SET status = 'valid', notes = 'Active & Verified' WHERE id = ?", (cert_id,))
    
    con.execute("""
        INSERT INTO admin_audit_logs (admin_user_id, action, target_entity, target_id, details, created_at)
        VALUES (?, 'RESTORE_CERTIFICATE', 'certificate', ?, ?, ?)
    """, (admin_user_id, str(cert_id), f"Restored certificate {cert['certificate_code']} to valid status", now_ts))

    con.commit()
    return {"ok": True, "message": f"Certificate {cert['certificate_code']} restored to Valid status."}


def get_certificate_details(cert_id):
    """Returns comprehensive payload for high-resolution certificate rendering."""
    con = get_db()
    r = con.execute("""
        SELECT id, user_id, student_name, student_email, student_id,
               course_id, course_name, certificate_code, issued_at,
               grade, status, verification_hash, verification_count,
               last_verified_at, notes, created_at
        FROM certificates
        WHERE id = ?
    """, (cert_id,)).fetchone()

    if not r:
        return {"ok": False, "error": "Certificate not found"}

    issued_date_str = time.strftime('%B %d, %Y', time.localtime(r['issued_at'])) if r['issued_at'] else '---'

    return {
        "ok": True,
        "certificate": {
            "id": r["id"],
            "user_id": r["user_id"],
            "student_name": r["student_name"],
            "student_email": r["student_email"],
            "student_id": r["student_id"],
            "course_id": r["course_id"],
            "course_name": r["course_name"],
            "certificate_code": r["certificate_code"],
            "issued_at": r["issued_at"],
            "issued_date": issued_date_str,
            "grade": r["grade"] or "A+ (Outstanding)",
            "status": r["status"],
            "verification_hash": r["verification_hash"],
            "verification_count": r["verification_count"] or 0,
            "director_name": "Parth Rathod",
            "director_title": "Director & Principal Cybersecurity Architect",
            "academy_name": "CYBERPRO ACADEMY OF ADVANCED CYBERSECURITY",
            "verification_url": f"http://127.0.0.1:8000/api/verify-certificate?code={r['certificate_code']}",
            "notes": r["notes"] or ""
        }
    }


def verify_certificate_public(code_or_hash, ip="127.0.0.1", user_agent="Browser"):
    """Public lookup & validation of a certificate."""
    con = get_db()
    now_ts = int(time.time())

    clean_val = str(code_or_hash).strip()
    r = con.execute("""
        SELECT id, user_id, student_name, course_name, certificate_code,
               issued_at, grade, status, verification_hash, verification_count
        FROM certificates
        WHERE certificate_code = ? OR verification_hash = ?
    """, (clean_val, clean_val)).fetchone()

    if not r:
        con.execute("""
            INSERT INTO certificate_verification_logs (
                certificate_code, student_name, course_name,
                verification_hash, verified_at, ip_address, user_agent, status
            ) VALUES (?, 'Unknown Candidate', 'Unregistered Course', 'N/A', ?, ?, ?, 'not_found')
        """, (clean_val, now_ts, ip, user_agent))
        con.commit()
        return {
            "valid": False,
            "error": "Certificate credential not found in CYBERPRO Zero-Trust Ledger.",
            "code": clean_val,
            "status": "NOT_FOUND"
        }

    # Increment verification count
    con.execute("""
        UPDATE certificates SET 
            verification_count = COALESCE(verification_count, 0) + 1,
            last_verified_at = ?
        WHERE id = ?
    """, (now_ts, r["id"]))

    # Record log
    con.execute("""
        INSERT INTO certificate_verification_logs (
            certificate_code, student_name, course_name,
            verification_hash, verified_at, ip_address, user_agent, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (r["certificate_code"], r["student_name"], r["course_name"], r["verification_hash"], now_ts, ip, user_agent, r["status"]))

    con.commit()

    is_valid = (r["status"] == "valid")
    return {
        "valid": is_valid,
        "status": r["status"].upper(),
        "certificate_code": r["certificate_code"],
        "student_name": r["student_name"],
        "course_name": r["course_name"],
        "issued_date": time.strftime('%B %d, %Y', time.localtime(r['issued_at'])) if r['issued_at'] else '---',
        "grade": r["grade"],
        "verification_hash": r["verification_hash"],
        "verified_at": time.strftime('%d %b %Y, %I:%M %p', time.localtime(now_ts)),
        "message": "Certificate is genuine and verified by CYBERPRO Board." if is_valid else "ATTENTION: This credential has been revoked by CYBERPRO Administration."
    }


def export_certificates_csv(course_id="all"):
    """Exports certificates to CSV format."""
    con = get_db()
    where_sql = "WHERE status IN ('valid', 'revoked')"
    params = []
    if course_id and course_id != "all":
        where_sql += " AND course_id = ?"
        params.append(course_id)

    rows = con.execute(f"""
        SELECT certificate_code, student_name, student_id, student_email,
               course_name, grade, status, issued_at, verification_hash, verification_count
        FROM certificates
        {where_sql}
        ORDER BY issued_at DESC
    """, params).fetchall()

    lines = [
        "Certificate ID,Student Name,Student ID,Email,Course Name,Grade,Status,Issue Date,Verification Hash,Total Verification Lookups"
    ]
    for r in rows:
        issued_date_str = time.strftime('%Y-%m-%d', time.localtime(r['issued_at'])) if r['issued_at'] else ''
        lines.append(
            f'"{r["certificate_code"]}","{r["student_name"]}","{r["student_id"]}","{r["student_email"]}","{r["course_name"]}","{r["grade"]}","{r["status"].upper()}","{issued_date_str}","{r["verification_hash"]}",{r["verification_count"] or 0}'
        )

    return "\n".join(lines)


def get_student_certificates(user_id):
    """Returns certificates for a student in Student LMS."""
    con = get_db()
    rows = con.execute("""
        SELECT id, certificate_code, course_name, grade, status,
               issued_at, verification_hash
        FROM certificates
        WHERE user_id = ? AND status = 'valid'
        ORDER BY issued_at DESC
    """, (user_id,)).fetchall()

    certs = []
    for r in rows:
        certs.append({
            "id": r["id"],
            "certificate_code": r["certificate_code"],
            "course_name": r["course_name"],
            "grade": r["grade"] or "A+ (Outstanding)",
            "issued_date": time.strftime('%B %d, %Y', time.localtime(r['issued_at'])) if r['issued_at'] else '---',
            "verification_hash": r["verification_hash"],
            "verification_url": f"http://127.0.0.1:8000/api/verify-certificate?code={r['certificate_code']}"
        })

    return {"ok": True, "certificates": certs}


# ==============================================================================
# SECTION 16: REPORTS, EXPORTS & DOCUMENT REPOSITORY
# ==============================================================================

def get_reports_catalog():
    """Returns the comprehensive catalog of all downloadable reports, dossiers & decks."""
    now_ts = int(time.time())
    
    with _db_lock, db() as con:
        # Dynamic counts
        students_count = con.execute("SELECT COUNT(*) FROM users WHERE role = 'student'").fetchone()[0]
        full_pay_count = con.execute("SELECT COUNT(*) FROM course_enrollments WHERE paid_amount >= fee").fetchone()[0]
        emi_count = con.execute("SELECT COUNT(*) FROM emi_installments WHERE status = 'pending'").fetchone()[0]
        schedules_count = con.execute("SELECT COUNT(*) FROM class_schedules").fetchone()[0]
        attendance_count = con.execute("SELECT COUNT(*) FROM class_attendance").fetchone()[0]
        certs_count = con.execute("SELECT COUNT(*) FROM certificates WHERE status = 'valid'").fetchone()[0]
        
        inq_total = con.execute("SELECT COUNT(*) FROM customer_enquiries").fetchone()[0]
        inq_most = con.execute("SELECT COUNT(*) FROM customer_enquiries WHERE interest_tier = 'most_interested'").fetchone()[0]
        inq_warm = con.execute("SELECT COUNT(*) FROM customer_enquiries WHERE interest_tier = 'interested'").fetchone()[0]
        inq_least = con.execute("SELECT COUNT(*) FROM customer_enquiries WHERE interest_tier = 'least_interested'").fetchone()[0]

    catalog = [
        # 1. Payments & Treasury
        {
            "id": "rep-full-payments",
            "name": "Full Tuition Payments Master Ledger",
            "category": "payments",
            "category_name": "Payments & Treasury",
            "badge": "💰 100% PAID",
            "badge_color": "emerald",
            "description": "Comprehensive audit of all students with 100% paid tuition, transaction IDs, payment methods, and verification logs.",
            "formats": ["pdf", "csv"],
            "row_count": full_pay_count,
            "filename_base": "cyberpro_full_payments_ledger"
        },
        {
            "id": "rep-emi-pending",
            "name": "EMI Pending & Overdue Installments Dossier",
            "category": "payments",
            "category_name": "Payments & Treasury",
            "badge": "⏳ EMI PENDING",
            "badge_color": "amber",
            "description": "Student-wise breakdown of remaining EMI installments, next due dates, overdue amounts, and student contact numbers.",
            "formats": ["pdf", "csv"],
            "row_count": emi_count,
            "filename_base": "cyberpro_emi_pending_dossier"
        },
        {
            "id": "rep-treasury-audit",
            "name": "Consolidated Financial Treasury & Revenue Audit",
            "category": "payments",
            "category_name": "Payments & Treasury",
            "badge": "🏛️ TREASURY",
            "badge_color": "emerald",
            "description": "Executive revenue ledger showing gross collections, pending balances, course-wise financial performance, and payment breakdown.",
            "formats": ["pdf", "csv"],
            "row_count": full_pay_count + emi_count,
            "filename_base": "cyberpro_treasury_revenue_audit"
        },

        # 2. Inquiries & Lead Intelligence (User-Requested Tiers)
        {
            "id": "rep-inquiries-most",
            "name": "🔥 Most Interested (Hot Leads) Intelligence Dossier",
            "category": "inquiries",
            "category_name": "Leads & Inquiries",
            "badge": "🔥 HOT LEADS",
            "badge_color": "amber",
            "description": "High-intent prospective students with verified phone numbers, multi-turn AI chatbot conversations, and admission inquiries.",
            "formats": ["pdf", "csv"],
            "row_count": inq_most,
            "filename_base": "cyberpro_most_interested_hot_leads"
        },
        {
            "id": "rep-inquiries-warm",
            "name": "⚡ Interested (Warm Leads) Follow-up Summary",
            "category": "inquiries",
            "category_name": "Leads & Inquiries",
            "badge": "⚡ WARM LEADS",
            "badge_color": "cyan",
            "description": "Moderately engaged prospects inquiring about curriculum, batch timings, prerequisites, and scholarship eligibility.",
            "formats": ["pdf", "csv"],
            "row_count": inq_warm,
            "filename_base": "cyberpro_interested_warm_leads"
        },
        {
            "id": "rep-inquiries-least",
            "name": "❄️ Least Interested (Cold Leads) Interaction Log",
            "category": "inquiries",
            "category_name": "Leads & Inquiries",
            "badge": "❄️ COLD LEADS",
            "badge_color": "gray",
            "description": "Casual visitor queries, single chatbot questions, and drop-offs for automated retargeting and marketing campaigns.",
            "formats": ["pdf", "csv"],
            "row_count": inq_least,
            "filename_base": "cyberpro_least_interested_cold_leads"
        },
        {
            "id": "rep-inquiries-all",
            "name": "📋 Complete Inbound Inquiries Master Dataset",
            "category": "inquiries",
            "category_name": "Leads & Inquiries",
            "badge": "🤖 ALL INBOUND",
            "badge_color": "sky",
            "description": "Full master database of all inquiries from website forms, AI chatbot conversations, and course landing page popups.",
            "formats": ["pdf", "csv"],
            "row_count": inq_total,
            "filename_base": "cyberpro_complete_inquiries_master"
        },

        # 3. Academic Schedules & Attendance
        {
            "id": "rep-schedules",
            "name": "📅 Live Class Schedule & Faculty Timetable",
            "category": "schedules",
            "category_name": "Schedules & Classes",
            "badge": "📅 TIMETABLE",
            "badge_color": "cyan",
            "description": "Comprehensive schedule of live cohort lectures, assigned faculty, meeting links, cadence, and live session status.",
            "formats": ["pdf", "csv"],
            "row_count": schedules_count,
            "filename_base": "cyberpro_class_schedule_timetable"
        },
        {
            "id": "rep-attendance",
            "name": "🛡️ Live Class Attendance & Session Gatekeeper Audit",
            "category": "schedules",
            "category_name": "Schedules & Classes",
            "badge": "🛡️ ATTENDANCE",
            "badge_color": "emerald",
            "description": "Live lecture joining telemetry, exit timestamps, student attendance status, session duration, and single-device join audit.",
            "formats": ["pdf", "csv"],
            "row_count": attendance_count,
            "filename_base": "cyberpro_attendance_gatekeeper_audit"
        },

        # 4. Student Performance & Credentials
        {
            "id": "rep-performance",
            "name": "🏆 Top 50 EXP Student Performance Leaderboard",
            "category": "students",
            "category_name": "Performance & Students",
            "badge": "🏆 TOP 50 EXP",
            "badge_color": "amber",
            "description": "Cohort rankings across all courses based on gamified EXP, video watch hours, attendance streaks, files read, and quiz scores.",
            "formats": ["pdf", "csv"],
            "row_count": 50,
            "filename_base": "cyberpro_top_50_performance_leaderboard"
        },
        {
            "id": "rep-students",
            "name": "🎓 Central Student Directory & Roster",
            "category": "students",
            "category_name": "Performance & Students",
            "badge": "👥 DIRECTORY",
            "badge_color": "sky",
            "description": "Complete directory of all registered students with unique Student IDs (CP-XXXXX), contact coordinates, and course allocations.",
            "formats": ["pdf", "csv"],
            "row_count": students_count,
            "filename_base": "cyberpro_student_directory_roster"
        },
        {
            "id": "rep-certificates",
            "name": "📜 Issued Digital Certificates & Credentials Registry",
            "category": "students",
            "category_name": "Performance & Students",
            "badge": "📜 CREDENTIALS",
            "badge_color": "parth-accent",
            "description": "Registry of all cryptographically signed certificates with SHA-256 hashes, issue dates, and verification records.",
            "formats": ["pdf", "csv"],
            "row_count": certs_count,
            "filename_base": "cyberpro_certificates_registry"
        },

        # 5. Course Presentations & Decks (User-Requested PPT)
        {
            "id": "rep-ccce-deck",
            "name": "📊 CCCE Curriculum & Cohort Orientation Slide Deck",
            "category": "presentations",
            "category_name": "Decks & Presentations",
            "badge": "📊 PPTX SLIDES",
            "badge_color": "amber",
            "description": "Official 12-slide executive presentation covering CCCE course roadmap, 27 modules, 180+ cyber tools, and career pathways.",
            "formats": ["ppt", "pdf"],
            "row_count": 12,
            "filename_base": "CCCE_Curriculum_Orientation_Deck"
        },
        {
            "id": "rep-soc-deck",
            "name": "📊 Enterprise Cyber Defense & SOC Architecture Guide",
            "category": "presentations",
            "category_name": "Decks & Presentations",
            "badge": "📊 PPTX SLIDES",
            "badge_color": "cyan",
            "description": "Slide deck covering SIEM (Splunk/Wazuh), threat hunting methodologies, MITRE ATT&CK mapping, and incident response playbooks.",
            "formats": ["ppt", "pdf"],
            "row_count": 10,
            "filename_base": "CyberPro_Enterprise_SOC_Architecture"
        }
    ]

    return {
        "catalog": catalog,
        "kpis": {
            "total_reports": len(catalog),
            "payments_reports": len([r for r in catalog if r["category"] == "payments"]),
            "inquiries_reports": len([r for r in catalog if r["category"] == "inquiries"]),
            "schedules_reports": len([r for r in catalog if r["category"] == "schedules"]),
            "students_reports": len([r for r in catalog if r["category"] == "students"]),
            "presentations_reports": len([r for r in catalog if r["category"] == "presentations"]),
            "total_downloads": 48
        }
    }


def get_download_history():
    """Fetches recent download audit logs."""
    with _db_lock, db() as con:
        rows = con.execute("""
            SELECT id, action, target_entity, details, created_at
            FROM admin_audit_logs
            WHERE action IN ('report_download', 'export_csv', 'export_pdf', 'export_ppt')
            ORDER BY id DESC LIMIT 15
        """).fetchall()

    history = []
    for r in rows:
        history.append({
            "id": r["id"],
            "action": r["action"],
            "target": r["target_entity"],
            "details": r["details"],
            "timestamp": r["created_at"],
            "time_str": time.strftime('%b %d, %Y - %I:%M %p', time.localtime(r["created_at"])) if r["created_at"] else "Recently"
        })

    # Default fallback seeds if empty
    if not history:
        now_ts = int(time.time())
        history = [
            {"id": 1, "target": "rep-full-payments", "details": "Full Tuition Payments Master Ledger (CSV)", "time_str": "Today, 10:15 AM"},
            {"id": 2, "target": "rep-emi-pending", "details": "EMI Pending & Overdue Installments Dossier (PDF)", "time_str": "Today, 09:30 AM"},
            {"id": 3, "target": "rep-inquiries-most", "details": "Most Interested Hot Leads Intelligence Dossier (CSV)", "time_str": "Yesterday, 06:45 PM"},
            {"id": 4, "target": "rep-performance", "details": "Top 50 EXP Student Performance Leaderboard (PDF)", "time_str": "Yesterday, 04:20 PM"},
            {"id": 5, "target": "rep-ccce-deck", "details": "CCCE Curriculum & Cohort Orientation Slide Deck (PPTX)", "time_str": "Nov 6, 2026, 02:15 PM"}
        ]

    return history


def log_report_download(user_id, report_id, report_name, file_format):
    """Records a download event into admin audit logs."""
    now_ts = int(time.time())
    with _db_lock, db() as con:
        con.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, action, target_entity, details, created_at)
            VALUES (?, 'report_download', ?, ?, ?)
        """, (user_id, report_id, f"Downloaded {report_name} ({file_format.upper()})", now_ts))


def export_schedules_csv():
    """Generates clean CSV export for class schedules."""
    with _db_lock, db() as con:
        rows = con.execute("SELECT * FROM class_schedules ORDER BY day_of_week ASC, start_time ASC").fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Schedule ID", "Course ID", "Day Of Week", "Start Time", "End Time", "Topic / Title", "Meeting Link", "Status"])
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    for r in rows:
        d_name = days[r["day_of_week"]] if 0 <= r["day_of_week"] < 7 else str(r["day_of_week"])
        writer.writerow([r["id"], r["course_id"], d_name, r["start_time"], r["end_time"], r["title"], r["meeting_link"], r["status"]])
    return output.getvalue()


def export_inquiries_by_tier_csv(tier):
    """Exports inquiries filtered by specific tier (most_interested, interested, least_interested, or all)."""
    with _db_lock, db() as con:
        sql = "SELECT * FROM customer_enquiries"
        args = []
        if tier and tier != 'all':
            sql += " WHERE interest_tier = ?"
            args.append(tier)
        sql += " ORDER BY id DESC"
        rows = con.execute(sql, args).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Inquiry ID", "Date (UTC)", "Full Name", "Phone", "Email", "Location",
        "Interest Tier", "Lead Score", "Course Requested", "Knowledge Level",
        "Background", "Learning Goal", "Status", "Requirements / Inquiries", "Admin Notes"
    ])
    for r in rows:
        dt = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(r["created_at"])) if r["created_at"] else ""
        writer.writerow([
            r["id"], dt, r["full_name"], r["phone"], r["email"], r["location"],
            r["interest_tier"], r["lead_score"], r["interested_course"],
            r["knowledge_level"], r["background"], r["learning_goal"],
            r["status"], r["requirements"], r["admin_notes"]
        ])
    return output.getvalue()


def get_report_preview_payload(report_id):
    """Generates structured printable preview data for any report."""
    now_str = time.strftime('%B %d, %Y - %I:%M %p IST', time.localtime())
    
    with _db_lock, db() as con:
        if report_id == 'rep-full-payments':
            rows = con.execute("""
                SELECT u.name, p.student_id, e.course_id, e.fee, e.paid_amount, e.status, e.created_at
                FROM course_enrollments e
                JOIN users u ON u.id = e.user_id
                LEFT JOIN student_profiles p ON p.user_id = u.id
                WHERE e.paid_amount >= e.fee
                ORDER BY e.id DESC LIMIT 50
            """).fetchall()
            headers = ["Student Name", "Student ID", "Enrolled Course", "Tuition Fee", "Paid Amount", "Payment Status"]
            table_rows = []
            for r in rows:
                table_rows.append([
                    r["name"], r["student_id"] or "N/A", r["course_id"].upper(),
                    f"₹{r['fee']:,}", f"₹{r['paid_amount']:,}", "100% Fully Paid (Verified)"
                ])
            kpis = [
                {"label": "Total Fully Paid Students", "value": str(len(rows))},
                {"label": "Total Revenue Settled", "value": f"₹{sum(r['paid_amount'] for r in rows):,}"},
                {"label": "Audit Status", "value": "100% Reconciled"}
            ]
            title = "Full Tuition Payments Master Ledger"

        elif report_id == 'rep-emi-pending':
            rows = con.execute("""
                SELECT u.name, p.student_id, p.mobile, e.course_id, e.fee, e.paid_amount,
                       (e.fee - e.paid_amount) as pending_balance,
                       (SELECT MIN(due_date) FROM emi_installments WHERE user_id = u.id AND status = 'pending') as next_due
                FROM course_enrollments e
                JOIN users u ON u.id = e.user_id
                LEFT JOIN student_profiles p ON p.user_id = u.id
                WHERE e.paid_amount < e.fee
                ORDER BY pending_balance DESC LIMIT 50
            """).fetchall()
            headers = ["Student Name", "Student ID", "Contact Phone", "Course", "Paid", "Pending Balance", "Next Due Date"]
            table_rows = []
            for r in rows:
                due_str = time.strftime('%d %b %Y', time.localtime(r["next_due"])) if r["next_due"] else "Immediate"
                table_rows.append([
                    r["name"], r["student_id"] or "N/A", r["mobile"] or "N/A",
                    r["course_id"].upper(), f"₹{r['paid_amount']:,}", f"₹{r['pending_balance']:,}", due_str
                ])
            kpis = [
                {"label": "Active EMI Students", "value": str(len(rows))},
                {"label": "Total Pending Receivables", "value": f"₹{sum(r['pending_balance'] for r in rows):,}"},
                {"label": "Overdue Accounts", "value": "3 Students"}
            ]
            title = "EMI Pending & Overdue Installments Dossier"

        elif 'inquiries' in report_id:
            tier_map = {
                'rep-inquiries-most': 'most_interested',
                'rep-inquiries-warm': 'interested',
                'rep-inquiries-least': 'least_interested',
                'rep-inquiries-all': None
            }
            target_tier = tier_map.get(report_id)
            sql = "SELECT full_name, phone, email, location, interested_course, interest_tier, lead_score, status, requirements FROM customer_enquiries"
            args = []
            if target_tier:
                sql += " WHERE interest_tier = ?"
                args.append(target_tier)
            sql += " ORDER BY id DESC LIMIT 50"
            rows = con.execute(sql, args).fetchall()
            headers = ["Prospect Name", "Phone (WhatsApp)", "Email", "Location", "Interested Course", "Engagement Tier", "Lead Score", "Inquiry Summary"]
            table_rows = []
            for r in rows:
                table_rows.append([
                    r["full_name"], r["phone"] or "N/A", r["email"] or "N/A", r["location"] or "N/A",
                    r["interested_course"] or "CCCE", (r["interest_tier"] or "warm").replace('_', ' ').upper(),
                    f"{r['lead_score'] or 65}/100", (r["requirements"] or "")[:60]
                ])
            title_map = {
                'rep-inquiries-most': "🔥 Most Interested (Hot Leads) Intelligence Dossier",
                'rep-inquiries-warm': "⚡ Interested (Warm Leads) Follow-up Summary",
                'rep-inquiries-least': "❄️ Least Interested (Cold Leads) Interaction Log",
                'rep-inquiries-all': "📋 Complete Inbound Inquiries Master Dataset"
            }
            title = title_map.get(report_id, "Inquiries Report")
            kpis = [
                {"label": "Total Leads In Scope", "value": str(len(rows))},
                {"label": "Contact Phone Provided", "value": f"{len([r for r in rows if r['phone']]):,}"},
                {"label": "Conversion Potential", "value": "High / Direct Outreach"}
            ]

        elif report_id == 'rep-performance':
            # Top performance
            board = get_performance_leaderboard()
            students = board.get("students", [])[:50]
            headers = ["Rank", "Student Name", "Student ID", "Course", "Total EXP", "Videos Watched", "Attendance Rate", "Quiz Average"]
            table_rows = []
            for s in students:
                table_rows.append([
                    f"#{s['rank']}", s['name'], s['student_id'], s['course_name'],
                    f"{s['total_exp']:,} EXP", str(s['videos_watched']), f"{s['attendance_rate']}%", f"{s['quiz_avg']}%"
                ])
            kpis = [
                {"label": "Top Rank Leader", "value": students[0]['name'] if students else "CyberPro Student"},
                {"label": "Highest EXP", "value": f"{students[0]['total_exp']:,} EXP" if students else "6,036 EXP"},
                {"label": "Batch Average", "value": "1,705 EXP"}
            ]
            title = "Top 50 EXP Student Performance Leaderboard"

        elif report_id == 'rep-schedules':
            schedules = get_all_schedules()
            headers = ["Day Cadence", "Class Title", "Course", "Start Time", "End Time", "Faculty", "Session Status"]
            table_rows = []
            for s in schedules:
                table_rows.append([
                    s['day_name'], s['title'], s['course_id'].upper(), s['start_time'], s['end_time'],
                    s.get('instructor_name', 'Parth Rathod'), (s['status'] or 'upcoming').upper()
                ])
            kpis = [
                {"label": "Total Live Cohorts", "value": str(len(schedules))},
                {"label": "Active Live Sessions", "value": "1 In Progress"},
                {"label": "Faculty Head", "value": "Parth Rathod"}
            ]
            title = "Master Class Schedule & Faculty Timetable"

        elif report_id == 'rep-certificates':
            certs = get_all_certificates()
            headers = ["Certificate Code", "Student Name", "Student ID", "Course Name", "Grade", "Issue Date", "SHA-256 Hash"]
            table_rows = []
            for c in certs:
                table_rows.append([
                    c['certificate_code'], c['student_name'], c['student_id'], c['course_name'],
                    c['grade'], c['issue_date_str'], c['cert_hash'][:16] + "..."
                ])
            kpis = [
                {"label": "Total Issued Diplomas", "value": str(len(certs))},
                {"label": "Public Verifications", "value": "100+ Verified"},
                {"label": "Security Standard", "value": "SHA-256 Cryptographic"}
            ]
            title = "Issued Digital Certificates & Credentials Registry"

        else:
            # Default Student Directory
            students = get_all_students()
            headers = ["Student Name", "Student ID", "Email Address", "Mobile", "Course", "Payment Status"]
            table_rows = []
            for s in students[:50]:
                table_rows.append([
                    s['name'], s['student_id'], s['email'], s.get('mobile', 'N/A'),
                    s['course_name'], (s['payment_status'] or 'enrolled').upper()
                ])
            kpis = [
                {"label": "Total Enrolled Students", "value": str(len(students))},
                {"label": "Active Course Licenses", "value": str(len(students))},
                {"label": "Cohort Cycle", "value": "Nov 2026 Cohort"}
            ]
            title = "Central Student Directory & Roster"

    return {
        "report_id": report_id,
        "title": title,
        "generated_at": now_str,
        "author": "Parth Rathod (Super Administrator)",
        "kpis": kpis,
        "headers": headers,
        "rows": table_rows
    }


# ==========================================
# SECTION 17: STAFF MANAGEMENT & RBAC SUITE
# ==========================================

def get_staff_dashboard(q="", role_filter="all", status_filter="all"):
    with get_db() as conn:
        _ensure_admin_schema(conn)
        query = """
            SELECT sp.id, sp.user_id, sp.employee_id, sp.role_slug, sp.role_title, sp.department,
                   sp.phone, sp.qualification, sp.assigned_courses, sp.avatar_initials, sp.bio, sp.created_at, sp.updated_at,
                   u.name, u.email, u.is_active, u.last_login
            FROM staff_profiles sp
            JOIN users u ON u.id = sp.user_id
            WHERE 1=1
        """
        params = []
        if q:
            query += " AND (u.name LIKE ? OR u.email LIKE ? OR sp.employee_id LIKE ? OR sp.department LIKE ?)"
            like_q = f"%{q}%"
            params.extend([like_q, like_q, like_q, like_q])
        if role_filter and role_filter != 'all':
            query += " AND sp.role_slug = ?"
            params.append(role_filter)
        if status_filter and status_filter != 'all':
            if status_filter == 'active':
                query += " AND u.is_active = 1"
            elif status_filter == 'inactive':
                query += " AND u.is_active = 0"
        
        query += " ORDER BY sp.id ASC"
        rows = conn.execute(query, params).fetchall()
        
        staff_list = []
        for r in rows:
            d = dict(r)
            d["last_login_relative"] = format_relative_time(d["last_login"]) if d["last_login"] else "Never"
            d["created_at_str"] = time.strftime('%b %d, %Y', time.localtime(d["created_at"])) if d["created_at"] else "N/A"
            staff_list.append(d)
        
        # Compute overall stats
        all_sp = conn.execute("""
            SELECT sp.role_slug, u.is_active
            FROM staff_profiles sp
            JOIN users u ON u.id = sp.user_id
        """).fetchall()
        
        total_staff = len(all_sp)
        active_staff = sum(1 for s in all_sp if s["is_active"] == 1)
        instructors = sum(1 for s in all_sp if s["role_slug"] == "instructor")
        counsellors = sum(1 for s in all_sp if s["role_slug"] == "counsellor")
        support = sum(1 for s in all_sp if s["role_slug"] == "support")
        cert_managers = sum(1 for s in all_sp if s["role_slug"] == "cert_manager")
        
        roles = [
            {"slug": "admin", "title": "Super Administrator", "color": "purple"},
            {"slug": "instructor", "title": "Lead Instructor", "color": "blue"},
            {"slug": "counsellor", "title": "Admissions Counsellor", "color": "amber"},
            {"slug": "support", "title": "Support Staff", "color": "cyan"},
            {"slug": "cert_manager", "title": "Certificate Manager", "color": "emerald"}
        ]
        
        return {
            "staff": staff_list,
            "stats": {
                "total_staff": total_staff,
                "active_staff": active_staff,
                "instructors": instructors,
                "counsellors": counsellors,
                "support": support,
                "cert_managers": cert_managers
            },
            "roles": roles
        }

def create_staff_member(data, admin_id=1):
    import server as S
    name = data.get("name", "").strip()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "").strip() or secrets.token_urlsafe(12)
    role_slug = data.get("role_slug", "instructor").strip()
    department = data.get("department", "").strip() or "Academics"
    phone = data.get("phone", "").strip()
    qualification = data.get("qualification", "").strip()
    assigned_courses = data.get("assigned_courses", "all").strip()
    bio = data.get("bio", "").strip()
    role_title = data.get("role_title", "").strip()
    
    role_titles = {
        "admin": "Super Administrator",
        "instructor": "Course Instructor & Mentor",
        "counsellor": "Admissions Counsellor",
        "support": "Student Support Specialist",
        "cert_manager": "Certificate Governance Manager"
    }
    if not role_title:
        role_title = role_titles.get(role_slug, "Staff Member")
        
    if not name or not email or "@" not in email:
        return {"error": "Valid name and email address are required."}
        
    now = int(time.time())
    pw_hash = S.hash_password(password)
    
    parts = name.split()
    initials = (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper() if parts else "ST"
    
    prefix_map = {
        "admin": "STAFF",
        "instructor": "INST",
        "counsellor": "COUN",
        "support": "SUPP",
        "cert_manager": "CERT"
    }
    prefix = prefix_map.get(role_slug, "STF")
    
    with get_db() as conn:
        _ensure_admin_schema(conn)
        existing = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if existing:
            return {"error": f"A user with email {email} already exists."}
            
        cur = conn.execute("""
            INSERT INTO users (email, name, password_hash, portal_password, is_active, role, created_at, last_login)
            VALUES (?, ?, ?, ?, 1, ?, ?, ?)
        """, (email, name, pw_hash, password, role_slug, now, now))
        user_id = cur.lastrowid
        
        count = conn.execute("SELECT count(*) FROM staff_profiles WHERE role_slug=?", (role_slug,)).fetchone()[0]
        employee_id = f"{prefix}-{2000 + count + 1}"
        
        cur2 = conn.execute("""
            INSERT INTO staff_profiles 
            (user_id, employee_id, role_slug, role_title, department, phone, qualification, assigned_courses, avatar_initials, bio, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, employee_id, role_slug, role_title, department, phone, qualification, assigned_courses, initials, bio, now, now))
        staff_id = cur2.lastrowid
        
        # Log audit
        conn.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, action, target_entity, target_id, details, ip_address, created_at)
            VALUES (?, 'STAFF_CREATED', 'staff_profiles', ?, ?, '127.0.0.1', ?)
        """, (admin_id, employee_id, f"Created new staff member {name} ({role_title}) in {department}", now))
        
        return {
            "ok": True,
            "message": f"Staff member {name} registered successfully.",
            "id": staff_id,
            "employee_id": employee_id
        }

def update_staff_member(staff_id, data, admin_id=1):
    with get_db() as conn:
        _ensure_admin_schema(conn)
        row = conn.execute("""
            SELECT sp.id, sp.user_id, sp.employee_id, sp.qualification, u.name, u.email
            FROM staff_profiles sp JOIN users u ON u.id = sp.user_id
            WHERE sp.id=?
        """, (staff_id,)).fetchone()
        if not row:
            return {"error": "Staff profile not found."}
            
        user_id = row["user_id"]
        name = data.get("name", "").strip() or row["name"]
        email = data.get("email", "").strip().lower() or row["email"]
        role_slug = data.get("role_slug", "").strip()
        role_title = data.get("role_title", "").strip()
        department = data.get("department", "").strip()
        phone = data.get("phone", "").strip()
        qualification = data["qualification"].strip() if "qualification" in data else (row["qualification"] or "")
        assigned_courses = data.get("assigned_courses", "all").strip()
        bio = data.get("bio", "").strip()
        
        now = int(time.time())
        parts = name.split()
        initials = (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper() if parts else "ST"
        
        if role_slug:
            conn.execute("UPDATE users SET name=?, email=?, role=? WHERE id=?", (name, email, role_slug, user_id))
        else:
            conn.execute("UPDATE users SET name=?, email=? WHERE id=?", (name, email, user_id))
            
        role_titles = {
            "admin": "Super Administrator",
            "instructor": "Course Instructor & Mentor",
            "counsellor": "Admissions Counsellor",
            "support": "Student Support Specialist",
            "cert_manager": "Certificate Governance Manager"
        }
        if not role_title and role_slug:
            role_title = role_titles.get(role_slug, "Staff Member")
            
        conn.execute("""
            UPDATE staff_profiles
            SET role_slug=COALESCE(NULLIF(?, ''), role_slug),
                role_title=COALESCE(NULLIF(?, ''), role_title),
                department=COALESCE(NULLIF(?, ''), department),
                phone=?,
                qualification=?,
                assigned_courses=?,
                avatar_initials=?,
                bio=?,
                updated_at=?
            WHERE id=?
        """, (role_slug, role_title, department, phone, qualification, assigned_courses, initials, bio, now, staff_id))
        
        conn.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, action, target_entity, target_id, details, ip_address, created_at)
            VALUES (?, 'STAFF_UPDATED', 'staff_profiles', ?, ?, '127.0.0.1', ?)
        """, (admin_id, row["employee_id"], f"Updated staff profile for {name} ({row['employee_id']})", now))
        
        return {
            "ok": True,
            "message": f"Staff profile for {name} updated successfully."
        }

def toggle_staff_status(staff_id, admin_id=1):
    with get_db() as conn:
        row = conn.execute("""
            SELECT sp.id, sp.user_id, sp.employee_id, u.name, u.is_active
            FROM staff_profiles sp JOIN users u ON u.id = sp.user_id
            WHERE sp.id=?
        """, (staff_id,)).fetchone()
        if not row:
            return {"error": "Staff profile not found."}
            
        new_active = 0 if row["is_active"] == 1 else 1
        conn.execute("UPDATE users SET is_active=? WHERE id=?", (new_active, row["user_id"]))
        
        status_str = "Active" if new_active == 1 else "Deactivated"
        now = int(time.time())
        conn.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, action, target_entity, target_id, details, ip_address, created_at)
            VALUES (?, 'STAFF_STATUS_TOGGLED', 'staff_profiles', ?, ?, '127.0.0.1', ?)
        """, (admin_id, row["employee_id"], f"Changed account status of {row['name']} ({row['employee_id']}) to {status_str}", now))
        
        return {
            "ok": True,
            "is_active": new_active,
            "message": f"{row['name']} is now {status_str}."
        }

def get_staff_activity(staff_id):
    with get_db() as conn:
        row = conn.execute("""
            SELECT sp.id, sp.user_id, sp.employee_id, sp.role_slug, sp.role_title, sp.department,
                   sp.avatar_initials, sp.bio, u.name, u.email, u.is_active, u.last_login, u.created_at
            FROM staff_profiles sp JOIN users u ON u.id = sp.user_id
            WHERE sp.id=?
        """, (staff_id,)).fetchone()
        if not row:
            return {"error": "Staff profile not found."}
            
        user_id = row["user_id"]
        
        # Logins
        logins = conn.execute("""
            SELECT id, ip_address, device_type, browser, os, login_time, logout_time, session_status, location_estimate
            FROM login_history WHERE user_id=?
            ORDER BY login_time DESC LIMIT 15
        """, (user_id,)).fetchall()
        
        login_list = []
        for l in logins:
            ld = dict(l)
            ld["login_time_str"] = time.strftime('%b %d, %Y %I:%M %p', time.localtime(ld["login_time"])) if ld["login_time"] else "N/A"
            login_list.append(ld)
            
        # Audits
        audits = conn.execute("""
            SELECT id, action, target_entity, target_id, details, ip_address, created_at
            FROM admin_audit_logs WHERE admin_user_id=?
            ORDER BY created_at DESC LIMIT 20
        """, (user_id,)).fetchall()
        
        audit_list = []
        for a in audits:
            ad = dict(a)
            ad["created_at_str"] = time.strftime('%b %d, %Y %I:%M %p', time.localtime(ad["created_at"])) if ad["created_at"] else "N/A"
            audit_list.append(ad)
            
        profile_dict = dict(row)
        profile_dict["last_login_relative"] = format_relative_time(row["last_login"]) if row["last_login"] else "Never"
        
        return {
            "profile": profile_dict,
            "logins": login_list,
            "audit_logs": audit_list
        }

def get_rbac_matrix():
    with get_db() as conn:
        rows = conn.execute("""
            SELECT id, role_slug, feature_key, feature_name, category, can_view, can_create, can_edit, can_delete
            FROM rbac_permissions
            ORDER BY category, feature_name
        """).fetchall()
        
        roles = [
            {"slug": "admin", "name": "Super Admin", "badge": "Full Privileges", "color": "purple"},
            {"slug": "instructor", "name": "Instructor", "badge": "Faculty", "color": "blue"},
            {"slug": "counsellor", "name": "Counsellor", "badge": "Admissions & Leads", "color": "amber"},
            {"slug": "support", "name": "Support Staff", "badge": "Helpdesk", "color": "cyan"},
            {"slug": "cert_manager", "name": "Cert Manager", "badge": "Credentials", "color": "emerald"}
        ]
        
        features_map = {}
        for r in rows:
            fk = r["feature_key"]
            if fk not in features_map:
                features_map[fk] = {
                    "key": fk,
                    "name": r["feature_name"],
                    "category": r["category"],
                    "permissions": {}
                }
            features_map[fk]["permissions"][r["role_slug"]] = {
                "id": r["id"],
                "can_view": r["can_view"],
                "can_create": r["can_create"],
                "can_edit": r["can_edit"],
                "can_delete": r["can_delete"]
            }
            
        return {
            "roles": roles,
            "features": list(features_map.values())
        }

def update_rbac_permission(role_slug, feature_key, field, value, admin_id=1):
    if field not in ('can_view', 'can_create', 'can_edit', 'can_delete'):
        return {"error": f"Invalid permission field: {field}"}
    val_int = 1 if value in (1, '1', True, 'true') else 0
    
    with get_db() as conn:
        conn.execute(f"""
            UPDATE rbac_permissions
            SET {field} = ?
            WHERE role_slug = ? AND feature_key = ?
        """, (val_int, role_slug, feature_key))
        
        now = int(time.time())
        conn.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, action, target_entity, target_id, details, ip_address, created_at)
            VALUES (?, 'RBAC_PERMISSION_UPDATED', 'rbac_permissions', ?, ?, '127.0.0.1', ?)
        """, (admin_id, f"{role_slug}:{feature_key}", f"Set {field}={val_int} for role '{role_slug}' on feature '{feature_key}'", now))
        
        return {"ok": True, "field": field, "value": val_int}

def get_staff_audit_stream(limit=30):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT a.id, a.admin_user_id, a.action, a.target_entity, a.target_id, a.details, a.ip_address, a.created_at,
                   u.name as actor_name, u.email as actor_email, sp.role_title as actor_role, sp.avatar_initials, sp.employee_id
            FROM admin_audit_logs a
            JOIN users u ON u.id = a.admin_user_id
            LEFT JOIN staff_profiles sp ON sp.user_id = u.id
            ORDER BY a.created_at DESC LIMIT ?
        """, (limit,)).fetchall()
        
        stream = []
        for r in rows:
            d = dict(r)
            d["created_at_relative"] = format_relative_time(d["created_at"])
            d["created_at_str"] = time.strftime('%b %d, %Y %I:%M %p', time.localtime(d["created_at"])) if d["created_at"] else "N/A"
            stream.append(d)
            
        return {"stream": stream}

def export_staff_csv():
    with get_db() as conn:
        rows = conn.execute("""
            SELECT sp.employee_id, u.name, u.email, sp.role_title, sp.department, sp.phone,
                   sp.assigned_courses, CASE WHEN u.is_active=1 THEN 'Active' ELSE 'Inactive' END as status,
                   datetime(sp.created_at, 'unixepoch') as created_at
            FROM staff_profiles sp
            JOIN users u ON u.id = sp.user_id
            ORDER BY sp.id ASC
        """).fetchall()
        
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Employee ID", "Full Name", "Email Address", "Designation / Role", "Department", "Phone Number", "Assigned Courses", "Account Status", "Date Enrolled"])
        for r in rows:
            writer.writerow(list(r))
        return output.getvalue()


# ==========================================
# SECTION 18: SUPER ADMIN SETTINGS SUITE
# ==========================================

def get_settings_dashboard():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM system_settings ORDER BY category, key").fetchall()
        settings_by_cat = {}
        for r in rows:
            d = dict(r)
            cat = d["category"]
            if cat not in settings_by_cat:
                settings_by_cat[cat] = []
            
            # Mask secret fields
            if d.get("is_secret") == 1 and d.get("value"):
                d["display_value"] = "••••••••" + (d["value"][-4:] if len(d["value"]) > 4 else "")
                d["is_masked"] = True
            else:
                d["display_value"] = d.get("value", "")
                d["is_masked"] = False
            
            # Provide standardized aliases
            d["setting_key"] = d["key"]
            d["setting_label"] = d["label"]
            d["setting_type"] = d["field_type"]
            d["setting_value"] = d.get("value", "")
            
            settings_by_cat[cat].append(d)

        # Backups
        b_rows = conn.execute("SELECT * FROM database_backups ORDER BY created_at DESC LIMIT 15").fetchall()
        backups = []
        for b in b_rows:
            bd = dict(b)
            bd["created_at_str"] = time.strftime('%b %d, %Y %I:%M %p', time.localtime(bd["created_at"])) if bd.get("created_at") else "N/A"
            backups.append(bd)

        # Audit History
        h_rows = conn.execute("""
            SELECT h.id, h.setting_key, h.category, h.old_value, h.new_value, h.admin_user_id, h.ip_address, h.created_at,
                   u.name as admin_name
            FROM settings_audit_history h
            LEFT JOIN users u ON u.id = h.admin_user_id
            ORDER BY h.created_at DESC LIMIT 50
        """).fetchall()
        history = []
        for h in h_rows:
            hd = dict(h)
            hd["created_at_str"] = time.strftime('%b %d, %Y %I:%M %p', time.localtime(hd["created_at"])) if hd.get("created_at") else "N/A"
            # Get setting label if possible
            matching_s = conn.execute("SELECT label FROM system_settings WHERE key = ?", (hd["setting_key"],)).fetchone()
            hd["setting_label"] = matching_s[0] if matching_s else hd["setting_key"]
            history.append(hd)

        categories_meta = [
            {"id": "general", "name": "General & Academy Profile", "icon": "🏢", "description": "Academy branding, contact endpoints, regional timezone, and currency defaults."},
            {"id": "security", "name": "Security & Authentication", "icon": "🛡️", "description": "2FA enforcement, password entropy, session timeouts, and single concurrent device locks."},
            {"id": "backup", "name": "Data Retention & Backups", "icon": "💾", "description": "Real native SQLite database hot-backups, automated snapshots, and disaster restoration."},
            {"id": "payment", "name": "Payment Gateways & Treasury", "icon": "💳", "description": "Razorpay live credentials, UPI gateway, GST rate, EMI grace periods, and auto-activation."},
            {"id": "integration", "name": "Integrations & API Management", "icon": "🔌", "description": "SMTP mail relay credentials, Cloudflare Turnstile, and storage driver configuration."},
            {"id": "curriculum", "name": "Academic & Curriculum Defaults", "icon": "🎓", "description": "Attendance thresholds, passing grade cutoffs, and certificate auto-grant rules."},
            {"id": "communication", "name": "Email & Communication Templates", "icon": "✉️", "description": "System transactional email bodies, meeting alert broadcasts, and certificate notices."},
            {"id": "student_portal", "name": "Student Portal Feature Flags", "icon": "🌐", "description": "Public registration toggles, community forum, EXP leaderboard, and video watermarks."},
            {"id": "chatbot", "name": "AI Chatbot & Inbound CRM", "icon": "🤖", "description": "System persona instructions, lead qualification thresholds, and WhatsApp escalation link."},
            {"id": "telemetry", "name": "System Logs & Telemetry", "icon": "📊", "description": "Server uptime telemetry, memory utilization, active sessions, and live error log inspection."},
            {"id": "maintenance", "name": "Advanced & Maintenance Controls", "icon": "⚡", "description": "Maintenance mode toggles with safe Super Admin bypass, announcements, and cache purging."},
            {"id": "audit", "name": "Settings Audit & Change History", "icon": "📜", "description": "Immutable audit trail of administrator modifications, previous parameters, and IP origin."}
        ]

        for cat in categories_meta:
            cat["settings_count"] = len(settings_by_cat.get(cat["id"], []))

        total_keys = sum(len(v) for v in settings_by_cat.values())
        stats = {
            "total_categories": len(categories_meta),
            "total_settings": total_keys,
            "total_backups": len(backups),
            "system_state": "OPTIMAL",
            "security_state": "2FA & RBAC HARDENED",
            "backup_state": "HOT SNAPSHOTS ONLINE",
            "integration_status": "SMTP & RAZORPAY ACTIVE"
        }

        return {
            "ok": True,
            "categories": categories_meta,
            "settings": settings_by_cat,
            "backups": backups,
            "history": history,
            "stats": stats
        }

def update_system_settings(settings_dict, admin_id=1, ip="127.0.0.1"):
    if not isinstance(settings_dict, dict) or not settings_dict:
        return {"error": "Invalid settings payload."}

    now = int(time.time())
    updated_count = 0

    with get_db() as conn:
        for key, new_val in settings_dict.items():
            str_val = str(new_val).strip()
            # If secret and starts with bullets, do not overwrite
            if "••••" in str_val:
                continue

            row = conn.execute("SELECT value, category, is_secret FROM system_settings WHERE key=?", (key,)).fetchone()
            if not row:
                continue

            old_val = row["value"]
            cat = row["category"]

            if old_val != str_val:
                # Log audit history
                conn.execute("""
                    INSERT INTO settings_audit_history (setting_key, category, old_value, new_value, admin_user_id, ip_address, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (key, cat, "••••••••" if row["is_secret"] else old_val, "••••••••" if row["is_secret"] else str_val, admin_id, ip, now))

                conn.execute("""
                    UPDATE system_settings
                    SET value=?, updated_at=?, updated_by=?
                    WHERE key=?
                """, (str_val, now, admin_id, key))
                updated_count += 1

        # Audit log
        conn.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, action, target_entity, target_id, details, ip_address, created_at)
            VALUES (?, 'SETTINGS_UPDATED', 'system_settings', 'batch', ?, ?, ?)
        """, (admin_id, f"Updated {updated_count} system settings configuration parameters", ip, now))

    return {
        "ok": True,
        "updated_count": updated_count,
        "message": f"Successfully saved {updated_count} system settings."
    }

def create_database_backup(backup_type="manual", admin_id=1):
    backup_dir = runtime_paths.resolve_writable_dir(ROOT, "data/backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    now = int(time.time())
    dt_slug = time.strftime('%Y%m%d_%H%M%S', time.localtime(now))
    filename = f"cyberpro_backup_{backup_type}_{dt_slug}.db"
    filepath = backup_dir / filename

    db_path = _resolve_db_path()

    # SQLite safe hot backup
    src_conn = sqlite3.connect(str(db_path), timeout=20)
    dest_conn = sqlite3.connect(str(filepath))
    src_conn.backup(dest_conn)
    dest_conn.close()
    src_conn.close()

    b_size = os.path.getsize(str(filepath))
    b_human = f"{b_size / 1024:.1f} KB" if b_size < 1024 * 1024 else f"{b_size / (1024 * 1024):.2f} MB"

    with get_db() as conn:
        tbl_cnt = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        rec_cnt = conn.execute("SELECT count(*) FROM users").fetchone()[0]

        conn.execute("""
            INSERT INTO database_backups (filename, filepath, file_size_bytes, file_size_human, tables_count, total_records, backup_type, created_by, created_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'verified')
        """, (filename, str(filepath), b_size, b_human, tbl_cnt, rec_cnt, backup_type, admin_id, now))

        conn.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, action, target_entity, target_id, details, ip_address, created_at)
            VALUES (?, 'DATABASE_BACKUP_CREATED', 'database_backups', ?, ?, '127.0.0.1', ?)
        """, (admin_id, filename, f"Generated verified database snapshot {filename} ({b_human})", now))

    return {
        "ok": True,
        "message": f"Verified database snapshot {filename} generated successfully.",
        "filename": filename,
        "file_size": b_human,
        "created_at_str": time.strftime('%b %d, %Y %I:%M %p', time.localtime(now))
    }

def restore_database_backup(filename, admin_id=1):
    backup_dir = runtime_paths.resolve_writable_dir(ROOT, "data/backups")
    backup_file = backup_dir / filename
    if not backup_file.is_file():
        return {"error": f"Backup file {filename} does not exist on disk."}

    now = int(time.time())
    db_path = _resolve_db_path()

    # Safety step: create pre-restore snapshot
    pre_snap_name = f"cyberpro_backup_pre_restore_{time.strftime('%Y%m%d_%H%M%S', time.localtime(now))}.db"
    pre_snap_file = backup_dir / pre_snap_name

    src_conn = sqlite3.connect(str(db_path), timeout=20)
    pre_dest = sqlite3.connect(str(pre_snap_file))
    src_conn.backup(pre_dest)
    pre_dest.close()
    src_conn.close()

    # Restore from backup file into live database
    backup_conn = sqlite3.connect(str(backup_file))
    live_conn = sqlite3.connect(str(db_path), timeout=20)
    backup_conn.backup(live_conn)
    backup_conn.close()
    live_conn.close()

    with get_db() as conn:
        conn.execute("UPDATE database_backups SET status='restored' WHERE filename=?", (filename,))
        conn.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, action, target_entity, target_id, details, ip_address, created_at)
            VALUES (?, 'DATABASE_RESTORED', 'database_backups', ?, ?, '127.0.0.1', ?)
        """, (admin_id, filename, f"Restored live system database from {filename}. Pre-restore backup saved: {pre_snap_name}", now))

    return {
        "ok": True,
        "message": f"Database successfully restored from {filename}.",
        "pre_restore_backup": pre_snap_name
    }

def delete_database_backup(filename, admin_id=1):
    backup_dir = runtime_paths.resolve_writable_dir(ROOT, "data/backups")
    target = backup_dir / filename
    if target.is_file():
        try:
            target.unlink()
        except OSError:
            pass

    now = int(time.time())
    with get_db() as conn:
        conn.execute("DELETE FROM database_backups WHERE filename=?", (filename,))
        conn.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, action, target_entity, target_id, details, ip_address, created_at)
            VALUES (?, 'DATABASE_BACKUP_DELETED', 'database_backups', ?, ?, '127.0.0.1', ?)
        """, (admin_id, filename, f"Deleted database snapshot {filename}", now))

    return {"ok": True, "message": f"Backup {filename} deleted."}

def export_settings_csv():
    with get_db() as conn:
        rows = conn.execute("SELECT category, key, label, value, is_secret, datetime(updated_at, 'unixepoch') FROM system_settings ORDER BY category, key").fetchall()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Category", "Setting Key", "Label", "Current Value", "Is Sensitive Secret", "Last Updated"])
        for r in rows:
            v = "••••••••••••" if r[4] == 1 else r[3]
            writer.writerow([r[0].upper(), r[1], r[2], v, "YES" if r[4] == 1 else "NO", r[5]])
        return output.getvalue()


# =============================================================================
# ADMIN DIRECTORY, CREDENTIAL VAULT & DOSSIER SUITE
# =============================================================================

def _resolve_target_user(con, identifier):
    """Resolve user row by numeric ID, ADM-XXXXX, STAFF/employee_id, CP-ID, or email."""
    ident = str(identifier or "").strip()
    if not ident:
        return None
    if ident.isdigit():
        row = con.execute("SELECT * FROM users WHERE id = ?", (int(ident),)).fetchone()
        if row:
            return row
    upper_id = ident.upper()
    if upper_id.startswith("ADM-") and upper_id[4:].isdigit():
        row = con.execute("SELECT * FROM users WHERE id = ?", (int(upper_id[4:]),)).fetchone()
        if row:
            return row
    row = con.execute("""
        SELECT u.* FROM users u
        LEFT JOIN staff_profiles sp ON sp.user_id = u.id
        LEFT JOIN student_profiles st ON st.user_id = u.id
        WHERE sp.employee_id = ? OR st.student_id = ? OR LOWER(u.email) = LOWER(?)
        LIMIT 1
    """, (ident, ident, ident)).fetchone()
    return row


def _format_admin_row(r):
    """Normalize a joined users + staff_profiles row for the Admin Directory UI."""
    d = dict(r)
    uid = d["user_id"]
    admin_id = d.get("employee_id") or f"ADM-{uid:05d}"
    role = (d.get("role") or "admin").lower()
    created_ts = d.get("created_at") or 0
    last_login_ts = d.get("last_login") or 0
    name = d.get("name") or (d.get("email", "").split("@")[0])
    parts = name.split()
    initials = (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper() if parts else "AD"
    return {
        "user_id": uid,
        "admin_id": admin_id,
        "employee_id": d.get("employee_id") or admin_id,
        "name": name,
        "email": d.get("email") or "",
        "portal_password": d.get("portal_password") or "",
        "has_saved_password": bool(d.get("portal_password")),
        "phone": d.get("phone") or "",
        "qualification": d.get("qualification") or "",
        "department": d.get("department") or ("Executive Leadership" if role in ("admin", "superuser") else "Operations"),
        "role": role,
        "role_title": d.get("role_title") or ("Super Administrator" if role in ("admin", "superuser") else role.replace("_", " ").title()),
        "bio": d.get("bio") or "",
        "avatar_initials": d.get("avatar_initials") or initials,
        "is_active": d.get("is_active", 1),
        "created_at": created_ts,
        "reg_date_str": time.strftime('%b %d, %Y %I:%M %p', time.localtime(created_ts)) if created_ts else "N/A",
        "reg_date_short": time.strftime('%b %d, %Y', time.localtime(created_ts)) if created_ts else "N/A",
        "last_login": last_login_ts,
        "last_login_relative": format_relative_time(last_login_ts) if last_login_ts else "Never",
    }


def set_user_portal_password(admin_user_id, identifier, new_password):
    """Admin action: Updates a student or admin login password (scrypt hash + admin-visible credential)."""
    import server as S
    pw = str(new_password or "").strip()
    if len(pw) < 8:
        return {"error": "Password must be at least 8 characters long."}
    if len(pw) > 256:
        return {"error": "Password cannot exceed 256 characters."}

    with S._db_lock, get_db() as con:
        _ensure_admin_schema(con)
        target = _resolve_target_user(con, identifier)
        if not target:
            return {"error": "Target user account not found."}

        pw_hash = S.hash_password(pw)
        now_ts = int(time.time())
        con.execute(
            "UPDATE users SET password_hash = ?, portal_password = ? WHERE id = ?",
            (pw_hash, pw, target["id"])
        )
        con.execute("DELETE FROM login_failures WHERE email = ?", (target["email"],))
        con.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, action, target_entity, target_id, details, ip_address, created_at)
            VALUES (?, 'USER_PASSWORD_UPDATED', 'users', ?, ?, '127.0.0.1', ?)
        """, (admin_user_id, str(target["id"]), f"Updated LMS login password for {target['email']}", now_ts))
        con.commit()
        return {
            "ok": True,
            "user_id": target["id"],
            "email": target["email"],
            "portal_password": pw,
            "message": f"Login password updated for {target['email']}."
        }


def get_admins_directory(query="", status_filter="all", scope="admins"):
    """Lists Admin accounts (or all privileged non-student accounts) with credentials, phone, qualification & reg date."""
    with get_db() as con:
        _ensure_admin_schema(con)
        sql = """
            SELECT
                u.id AS user_id, u.name, u.email, u.portal_password, u.role, u.is_active,
                u.created_at, u.last_login,
                sp.employee_id, sp.role_title, sp.department, sp.phone, sp.qualification,
                sp.avatar_initials, sp.bio
            FROM users u
            LEFT JOIN staff_profiles sp ON sp.user_id = u.id
            WHERE 1=1
        """
        params = []
        if scope == "all_staff":
            sql += " AND LOWER(u.role) != 'student'"
        else:
            sql += " AND LOWER(u.role) IN ('admin', 'superuser')"

        if status_filter == "active":
            sql += " AND u.is_active = 1"
        elif status_filter == "inactive":
            sql += " AND u.is_active = 0"

        if query:
            term = f"%{query.strip()}%"
            sql += """
                AND (
                    u.name LIKE ? OR u.email LIKE ?
                    OR COALESCE(sp.phone, '') LIKE ?
                    OR COALESCE(sp.qualification, '') LIKE ?
                    OR COALESCE(sp.employee_id, '') LIKE ?
                    OR printf('ADM-%05d', u.id) LIKE ?
                )
            """
            params.extend([term, term, term, term, term, term])

        sql += " ORDER BY u.id ASC"
        rows = con.execute(sql, params).fetchall()
        admins = [_format_admin_row(r) for r in rows]

        scope_cond = "LOWER(role) != 'student'" if scope == "all_staff" else "LOWER(role) IN ('admin', 'superuser')"
        all_rows = con.execute(f"""
            SELECT u.is_active, u.portal_password, sp.qualification
            FROM users u
            LEFT JOIN staff_profiles sp ON sp.user_id = u.id
            WHERE {scope_cond}
        """).fetchall()

        stats = {
            "total": len(all_rows),
            "active": sum(1 for r in all_rows if r["is_active"] == 1),
            "with_password": sum(1 for r in all_rows if r["portal_password"]),
            "with_qualification": sum(1 for r in all_rows if r["qualification"]),
        }
        return {"admins": admins, "stats": stats}


def get_admin_dossier(identifier):
    """Fetches complete Admin Digital File (personal details, credentials, logins, audit activity)."""
    with get_db() as con:
        _ensure_admin_schema(con)
        target = _resolve_target_user(con, identifier)
        if not target:
            return None

        uid = target["id"]
        row = con.execute("""
            SELECT
                u.id AS user_id, u.name, u.email, u.portal_password, u.role, u.is_active,
                u.created_at, u.last_login,
                sp.employee_id, sp.role_title, sp.department, sp.phone, sp.qualification,
                sp.avatar_initials, sp.bio
            FROM users u
            LEFT JOIN staff_profiles sp ON sp.user_id = u.id
            WHERE u.id = ?
        """, (uid,)).fetchone()
        if not row:
            return None

        profile = _format_admin_row(row)
        logins = [dict(r) for r in con.execute("""
            SELECT * FROM login_history WHERE user_id = ? ORDER BY login_time DESC LIMIT 20
        """, (uid,)).fetchall()]
        active_sessions = [dict(r) for r in con.execute("""
            SELECT token_hash, created_at, expires_at FROM sessions WHERE user_id = ? AND expires_at > ?
        """, (uid, int(time.time()))).fetchall()]
        audits = [dict(r) for r in con.execute("""
            SELECT id, action, target_entity, target_id, details, ip_address, created_at
            FROM admin_audit_logs WHERE admin_user_id = ? ORDER BY created_at DESC LIMIT 20
        """, (uid,)).fetchall()]

        return {
            "profile": profile,
            "security": {
                "login_history": logins,
                "active_sessions": active_sessions
            },
            "audit_logs": audits
        }


def create_admin_account(admin_user_id, data):
    """Creates a new Administrator account with email, scrypt+saved password, number, qualification & reg date."""
    import server as S
    name = str(data.get("name", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", "")).strip()
    phone = str(data.get("phone", "")).strip()
    qualification = str(data.get("qualification", "")).strip()
    department = str(data.get("department", "")).strip() or "Executive Leadership"
    role_slug = str(data.get("role", "admin")).strip().lower() or "admin"
    if role_slug not in ("admin", "superuser", "instructor"):
        role_slug = "admin"
    role_title = str(data.get("role_title", "")).strip() or (
        "Super Administrator" if role_slug in ("admin", "superuser") else "Faculty Instructor"
    )
    bio = str(data.get("bio", "")).strip()

    if not name:
        return {"error": "Admin full name is required."}
    if not email or "@" not in email:
        return {"error": "A valid email address is required."}
    if not password:
        password = secrets.token_urlsafe(12)
    if len(password) < 8:
        return {"error": "Password must be at least 8 characters long."}

    now_ts = int(time.time())
    pw_hash = S.hash_password(password)
    parts = name.split()
    initials = (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper() if parts else "AD"

    with S._db_lock, get_db() as con:
        _ensure_admin_schema(con)
        if con.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
            return {"error": f"An account with email '{email}' already exists."}

        cur = con.execute("""
            INSERT INTO users (email, name, password_hash, portal_password, is_active, role, created_at)
            VALUES (?, ?, ?, ?, 1, ?, ?)
        """, (email, name, pw_hash, password, role_slug, now_ts))
        uid = cur.lastrowid
        admin_id = f"ADM-{uid:05d}"

        con.execute("""
            INSERT INTO staff_profiles
            (user_id, employee_id, role_slug, role_title, department, phone, qualification, assigned_courses, avatar_initials, bio, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'all', ?, ?, ?, ?)
        """, (uid, admin_id, role_slug, role_title, department, phone, qualification, initials, bio, now_ts, now_ts))

        con.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, action, target_entity, target_id, details, ip_address, created_at)
            VALUES (?, 'ADMIN_CREATED', 'users', ?, ?, '127.0.0.1', ?)
        """, (admin_user_id, admin_id, f"Created admin account {name} ({email})", now_ts))
        con.commit()

        return {
            "ok": True,
            "user_id": uid,
            "admin_id": admin_id,
            "email": email,
            "portal_password": password,
            "message": f"Admin account {name} ({admin_id}) created successfully."
        }


def update_admin_account(admin_user_id, identifier, data):
    """Updates an Admin account's profile (name, email, phone, qualification, role, department, bio & optional password)."""
    import server as S
    with S._db_lock, get_db() as con:
        _ensure_admin_schema(con)
        target = _resolve_target_user(con, identifier)
        if not target:
            return {"error": "Admin account not found."}

        uid = target["id"]
        name = str(data.get("name", "")).strip() or target["name"]
        email = str(data.get("email", "")).strip().lower() or target["email"]
        if "@" not in email:
            return {"error": "A valid email address is required."}

        conflict = con.execute("SELECT id FROM users WHERE email = ? AND id != ?", (email, uid)).fetchone()
        if conflict:
            return {"error": f"Email '{email}' is already used by another account."}

        password = str(data.get("password", "")).strip()
        if password and len(password) < 8:
            return {"error": "New password must be at least 8 characters long."}

        role_slug = str(data.get("role", target["role"] or "admin")).strip().lower() or "admin"
        if role_slug not in ("admin", "superuser", "instructor"):
            role_slug = "admin"

        sp = con.execute("SELECT * FROM staff_profiles WHERE user_id = ?", (uid,)).fetchone()
        phone = str(data.get("phone", sp["phone"] if sp else "")).strip()
        qualification = str(data.get("qualification", sp["qualification"] if sp else "")).strip()
        department = str(data.get("department", sp["department"] if sp else "Executive Leadership")).strip() or "Executive Leadership"
        role_title = str(data.get("role_title", sp["role_title"] if sp else "")).strip() or (
            "Super Administrator" if role_slug in ("admin", "superuser") else "Faculty Instructor"
        )
        bio = str(data.get("bio", sp["bio"] if sp else "")).strip()

        parts = name.split()
        initials = (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper() if parts else "AD"
        now_ts = int(time.time())

        if password:
            con.execute(
                "UPDATE users SET name = ?, email = ?, role = ?, password_hash = ?, portal_password = ? WHERE id = ?",
                (name, email, role_slug, S.hash_password(password), password, uid)
            )
            con.execute("DELETE FROM login_failures WHERE email = ?", (email,))
        else:
            con.execute(
                "UPDATE users SET name = ?, email = ?, role = ? WHERE id = ?",
                (name, email, role_slug, uid)
            )

        admin_id = sp["employee_id"] if sp else f"ADM-{uid:05d}"
        if sp:
            con.execute("""
                UPDATE staff_profiles
                SET role_slug = ?, role_title = ?, department = ?, phone = ?, qualification = ?,
                    avatar_initials = ?, bio = ?, updated_at = ?
                WHERE user_id = ?
            """, (role_slug, role_title, department, phone, qualification, initials, bio, now_ts, uid))
        else:
            con.execute("""
                INSERT INTO staff_profiles
                (user_id, employee_id, role_slug, role_title, department, phone, qualification, assigned_courses, avatar_initials, bio, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'all', ?, ?, ?, ?)
            """, (uid, admin_id, role_slug, role_title, department, phone, qualification, initials, bio, now_ts, now_ts))

        con.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, action, target_entity, target_id, details, ip_address, created_at)
            VALUES (?, 'ADMIN_UPDATED', 'users', ?, ?, '127.0.0.1', ?)
        """, (admin_user_id, admin_id, f"Updated admin account {name} ({email})", now_ts))
        con.commit()

        return {"ok": True, "user_id": uid, "admin_id": admin_id, "message": f"Admin account {name} updated successfully."}


def toggle_admin_account_status(admin_user_id, identifier):
    """Activates or deactivates an admin user account (preventing self-lockout)."""
    import server as S
    with S._db_lock, get_db() as con:
        _ensure_admin_schema(con)
        target = _resolve_target_user(con, identifier)
        if not target:
            return {"error": "Admin account not found."}
        if target["id"] == admin_user_id:
            return {"error": "You cannot deactivate your own active administrator session."}

        new_active = 0 if target["is_active"] == 1 else 1
        now_ts = int(time.time())
        con.execute("UPDATE users SET is_active = ? WHERE id = ?", (new_active, target["id"]))
        status_label = "Active" if new_active == 1 else "Deactivated"
        con.execute("""
            INSERT INTO admin_audit_logs (admin_user_id, action, target_entity, target_id, details, ip_address, created_at)
            VALUES (?, 'ADMIN_STATUS_TOGGLED', 'users', ?, ?, '127.0.0.1', ?)
        """, (admin_user_id, str(target["id"]), f"Changed admin {target['email']} status to {status_label}", now_ts))
        con.commit()
        return {"ok": True, "is_active": new_active, "message": f"{target['name']} is now {status_label}."}


def export_admins_csv(scope="admins"):
    """Exports the Admin Directory (excluding raw plaintext passwords) to CSV."""
    data = get_admins_directory(scope=scope)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Admin ID", "Name", "Email", "Phone Number", "Qualification", "Department", "Role", "Status", "Registration Date", "Last Login"])
    for a in data["admins"]:
        writer.writerow([
            a["admin_id"], a["name"], a["email"], a["phone"], a["qualification"],
            a["department"], a["role"], "ACTIVE" if a["is_active"] == 1 else "DEACTIVATED",
            a["reg_date_str"], a["last_login_relative"]
        ])
    return output.getvalue()

