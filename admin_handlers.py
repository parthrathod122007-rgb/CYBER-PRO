import os
import json
import urllib.parse
from http import HTTPStatus
import admin_service as A

def handle_admin_get(handler, path, user):
    """Handles all GET /api/admin/* endpoints."""
    full_path = handler.path if hasattr(handler, "path") else path
    parsed = urllib.parse.urlsplit(full_path)
    clean_path = parsed.path
    query_params = urllib.parse.parse_qs(parsed.query)

    if clean_path == "/api/admin/overview":
        return handler.send_json(HTTPStatus.OK, A.get_overview())

    if clean_path == "/api/admin/courses":
        return handler.send_json(HTTPStatus.OK, A.get_courses_list())

    if clean_path.startswith("/api/admin/courses/") and clean_path.endswith("/students"):
        # /api/admin/courses/<course_id>/students
        parts = clean_path.split("/")
        course_id = parts[4]
        q = query_params.get("q", [""])[0]
        p_status = query_params.get("payment", [""])[0]
        a_status = query_params.get("access", [""])[0]
        students = A.get_course_students(course_id, q, p_status, a_status)
        return handler.send_json(HTTPStatus.OK, {"course_id": course_id, "students": students})

    if clean_path == "/api/admin/students":
        q = query_params.get("q", [""])[0]
        c_filter = query_params.get("course", [""])[0]
        p_filter = query_params.get("payment", [""])[0]
        a_filter = query_params.get("access", [""])[0]
        students = A.get_all_students(q, c_filter, p_filter, a_filter)
        return handler.send_json(HTTPStatus.OK, {"students": students})

    if clean_path.startswith("/api/admin/students/"):
        # /api/admin/students/<identifier>
        parts = clean_path.split("/")
        identifier = urllib.parse.unquote(parts[4]) if len(parts) > 4 else ""
        dossier = A.get_student_digital_file(identifier)
        if not dossier:
            return handler.send_json(HTTPStatus.NOT_FOUND, {"error": "Student not found"})
        return handler.send_json(HTTPStatus.OK, dossier)

    # --- Admin Directory & Detail Suite ---
    if clean_path == "/api/admin/admins":
        q = query_params.get("q", [""])[0]
        status_f = query_params.get("status", ["all"])[0]
        scope_f = query_params.get("scope", ["admins"])[0]
        return handler.send_json(HTTPStatus.OK, A.get_admins_directory(q, status_f, scope_f))

    if clean_path == "/api/admin/admins/export":
        scope_f = query_params.get("scope", ["admins"])[0]
        csv_text = A.export_admins_csv(scope_f)
        data = csv_text.encode('utf-8')
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "text/csv; charset=utf-8")
        handler.send_header("Content-Disposition", 'attachment; filename="cyberpro_admins_directory.csv"')
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
        return

    if clean_path.startswith("/api/admin/admins/"):
        parts = clean_path.split("/")
        identifier = urllib.parse.unquote(parts[4]) if len(parts) > 4 else ""
        dossier = A.get_admin_dossier(identifier)
        if not dossier:
            return handler.send_json(HTTPStatus.NOT_FOUND, {"error": "Admin account not found"})
        return handler.send_json(HTTPStatus.OK, dossier)

        # --- Section 16: Reports, Data Exports & Document Repository ---
    if clean_path == "/api/admin/reports/catalog":
        return handler.send_json(HTTPStatus.OK, A.get_reports_catalog())

    if clean_path == "/api/admin/reports/history":
        return handler.send_json(HTTPStatus.OK, {"history": A.get_download_history()})

    if clean_path.startswith("/api/admin/reports/") and clean_path.endswith("/preview"):
        parts = clean_path.split("/")
        report_id = parts[4]
        preview = A.get_report_preview_payload(report_id)
        return handler.send_json(HTTPStatus.OK, preview)

    if clean_path.startswith("/api/admin/reports/") and clean_path.endswith("/download"):
        parts = clean_path.split("/")
        report_id = parts[4]
        fmt = query_params.get("format", ["csv"])[0].lower()
        
        # Log download
        A.log_report_download(user.get("id", 1), report_id, report_id, fmt)

        if fmt == "ppt":
            import runtime_paths
            pptx_filename = "CCCE_Curriculum_Orientation_Deck.pptx" if "ccce" in report_id else "CyberPro_Enterprise_SOC_Architecture.pptx"
            bundled_path = A.ROOT / "data" / "reports" / pptx_filename
            writable_dir = runtime_paths.resolve_writable_dir(A.ROOT, "data/reports")
            pptx_path = writable_dir / pptx_filename
            if bundled_path.is_file():
                pptx_path = bundled_path
            if not pptx_path.is_file():
                import io
                from pptx import Presentation
                from pptx.util import Inches, Pt
                from pptx.dml.color import RGBColor
                prs = Presentation()
                prs.slide_width = Inches(13.333)
                prs.slide_height = Inches(7.5)
                slide = prs.slides.add_slide(prs.slide_layouts[6])
                bg = slide.shapes.add_shape(1, 0, 0, Inches(13.333), Inches(7.5))
                bg.fill.solid()
                bg.fill.fore_color.rgb = RGBColor(7, 10, 8)
                tx = slide.shapes.add_textbox(Inches(1.5), Inches(2.2), Inches(10.3), Inches(3.0))
                p = tx.text_frame.paragraphs[0]
                p.text = "CYBERPRO DEFENSE ACADEMY"
                p.font.size = Pt(36)
                p.font.bold = True
                p.font.color.rgb = RGBColor(0, 245, 160)
                buf = io.BytesIO()
                prs.save(buf)
                with open(pptx_path, "wb") as f:
                    f.write(buf.getvalue())
            with open(pptx_path, "rb") as f:
                data = f.read()
            handler.send_response(HTTPStatus.OK)
            handler.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.presentationml.presentation")
            handler.send_header("Content-Disposition", f'attachment; filename="{pptx_filename}"')
            handler.send_header("Content-Length", str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)
            return

        # CSV downloads
        filename = f"{report_id}.csv"
        csv_text = ""
        if report_id == 'rep-full-payments':
            csv_text = A.export_payments_csv('full')
            filename = "cyberpro_full_payments_master.csv"
        elif report_id == 'rep-emi-pending':
            csv_text = A.export_payments_csv('emi')
            filename = "cyberpro_emi_pending_dossier.csv"
        elif report_id == 'rep-treasury-audit':
            csv_text = A.export_payments_csv('all')
            filename = "cyberpro_treasury_revenue_audit.csv"
        elif report_id == 'rep-inquiries-most':
            csv_text = A.export_inquiries_by_tier_csv('most_interested')
            filename = "cyberpro_most_interested_hot_leads.csv"
        elif report_id == 'rep-inquiries-warm':
            csv_text = A.export_inquiries_by_tier_csv('interested')
            filename = "cyberpro_interested_warm_leads.csv"
        elif report_id == 'rep-inquiries-least':
            csv_text = A.export_inquiries_by_tier_csv('least_interested')
            filename = "cyberpro_least_interested_cold_leads.csv"
        elif report_id == 'rep-inquiries-all':
            csv_text = A.export_inquiries_by_tier_csv('all')
            filename = "cyberpro_complete_inquiries_master.csv"
        elif report_id == 'rep-schedules':
            csv_text = A.export_schedules_csv()
            filename = "cyberpro_class_schedule_timetable.csv"
        elif report_id == 'rep-attendance':
            csv_text = A.export_attendance_csv()
            filename = "cyberpro_attendance_audit.csv"
        elif report_id == 'rep-performance':
            csv_text = A.export_performance_csv()
            filename = "cyberpro_top_50_performance_leaderboard.csv"
        elif report_id == 'rep-students':
            csv_text = A.export_students_csv()
            filename = "cyberpro_student_directory_roster.csv"
        elif report_id == 'rep-certificates':
            csv_text = A.export_certificates_csv()
            filename = "cyberpro_certificates_registry.csv"
        else:
            csv_text = A.export_students_csv()
            filename = f"{report_id}.csv"

        data = csv_text.encode("utf-8")
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "text/csv; charset=utf-8")
        handler.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
        return

    if clean_path in ("/api/admin/reports/students.csv", "/api/admin/export/students"):
        cid = query_params.get("course_id", [None])[0]
        csv_text = A.export_students_csv(cid)
        data = csv_text.encode("utf-8")
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "text/csv; charset=utf-8")
        handler.send_header("Content-Disposition", 'attachment; filename="cyberpro_students.csv"')
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
        return

    # --- Registrations Management GET ---
    if clean_path in ("/api/admin/registrations/export.csv", "/api/admin/export/registrations"):
        csv_text = A.export_registrations_csv()
        data = csv_text.encode("utf-8")
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "text/csv; charset=utf-8")
        handler.send_header("Content-Disposition", 'attachment; filename="cyberpro_registrations.csv"')
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
        return

    if clean_path == "/api/admin/registrations":
        q = query_params.get("q", [""])[0]
        status = query_params.get("status", [""])[0]
        source = query_params.get("source", [""])[0]
        res = A.get_registrations(q, status, source)
        return handler.send_json(HTTPStatus.OK, res)

    # --- Attendance Management GET ---
    if clean_path in ("/api/admin/attendance", "/api/admin/attendance/overview"):
        course_id = query_params.get("course_id", ["all"])[0]
        schedule_id = query_params.get("schedule_id", [None])[0]
        if schedule_id:
            try:
                schedule_id = int(schedule_id)
            except ValueError:
                schedule_id = None
        status = query_params.get("status", ["all"])[0]
        search = query_params.get("q", [""])[0]
        page = int(query_params.get("page", [1])[0])
        limit = int(query_params.get("limit", [50])[0])
        data = A.get_attendance_overview(course_id, schedule_id, status, search, page, limit)
        return handler.send_json(HTTPStatus.OK, data)

    if clean_path in ("/api/admin/attendance/export.csv", "/api/admin/export/attendance"):
        schedule_id = query_params.get("schedule_id", [None])[0]
        if schedule_id:
            try:
                schedule_id = int(schedule_id)
            except ValueError:
                schedule_id = None
        csv_bytes = A.export_attendance_csv(schedule_id)
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "text/csv; charset=utf-8")
        handler.send_header("Content-Disposition", 'attachment; filename="cyberpro_live_attendance.csv"')
        handler.send_header("Content-Length", str(len(csv_bytes)))
        handler.end_headers()
        handler.wfile.write(csv_bytes)
        return

    # --- Quizzes & Labs GET Endpoints ---
    if clean_path in ("/api/admin/quizzes", "/api/admin/quizzes/overview"):
        cid = query_params.get("course_id", ["ccce"])[0]
        data = A.get_admin_quizzes_overview(cid)
        return handler.send_json(HTTPStatus.OK, data)

    if clean_path.startswith("/api/admin/quizzes/"):
        parts = clean_path.split("/")
        try:
            quiz_id = int(parts[4])
            data = A.get_quiz_details(quiz_id)
            if not data:
                return handler.send_json(HTTPStatus.NOT_FOUND, {"error": "Quiz not found"})
            return handler.send_json(HTTPStatus.OK, data)
        except (ValueError, IndexError):
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid quiz ID"})

    # --- Certificates & Credentials GET Endpoints ---
    if clean_path in ("/api/admin/certificates", "/api/admin/certificates/overview"):
        cid = query_params.get("course_id", ["all"])[0]
        c_status = query_params.get("status", ["all"])[0]
        q_search = query_params.get("search", [""])[0]
        data = A.get_admin_certificates_overview(course_id=cid, status=c_status, search=q_search)
        return handler.send_json(HTTPStatus.OK, data)

    if clean_path in ("/api/admin/certificates/pending", "/api/admin/pending-certificates"):
        cid = query_params.get("course_id", ["all"])[0]
        ov = A.get_admin_certificates_overview(course_id=cid)
        return handler.send_json(HTTPStatus.OK, {"ok": True, "pending": ov.get("pending", []), "pending_students": ov.get("pending", [])})

    if clean_path.startswith("/api/admin/certificates/") and not clean_path.endswith("/export.csv") and not clean_path.endswith("/revoke") and not clean_path.endswith("/restore"):
        cid_str = clean_path.split("/")[-1]
        if cid_str.isdigit():
            try:
                cert_id = int(cid_str)
                data = A.get_certificate_details(cert_id)
                return handler.send_json(HTTPStatus.OK, data)
            except Exception:
                return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid certificate ID"})

    if clean_path in ("/api/admin/certificates/verifications", "/api/admin/verifications"):
        ov = A.get_admin_certificates_overview()
        return handler.send_json(HTTPStatus.OK, {"ok": True, "verifications": ov.get("verification_records", [])})

    if clean_path in ("/api/admin/certificates/export.csv", "/api/admin/export/certificates"):
        cid = query_params.get("course_id", ["all"])[0]
        csv_content = A.export_certificates_csv(course_id=cid)
        b = csv_content.encode("utf-8")
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "text/csv; charset=utf-8")
        handler.send_header("Content-Length", str(len(b)))
        handler.send_header("Content-Disposition", 'attachment; filename="cyberpro_certificates_ledger.csv"')
        handler.end_headers()
        handler.wfile.write(b)
        return

    # --- Performance & EXP Leaderboard GET Endpoints ---
    if clean_path in ("/api/admin/performance", "/api/admin/performance/overview", "/api/admin/leaderboard"):
        cid = query_params.get("course_id", ["all"])[0]
        timeframe = query_params.get("timeframe", ["all_time"])[0]
        q_search = query_params.get("search", [""])[0]
        sort_by = query_params.get("sort_by", ["exp"])[0]
        data = A.get_admin_performance_leaderboard(course_id=cid, timeframe=timeframe, search=q_search, sort_by=sort_by)
        return handler.send_json(HTTPStatus.OK, data)

    if clean_path.startswith("/api/admin/performance/student/"):
        sid_str = clean_path.split("/")[-1]
        try:
            sid = int(sid_str)
            data = A.get_student_performance_breakdown(sid)
            return handler.send_json(HTTPStatus.OK, data)
        except Exception as e:
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid student ID"})

    if clean_path in ("/api/admin/performance/export.csv", "/api/admin/export/performance"):
        cid = query_params.get("course_id", ["all"])[0]
        csv_content = A.export_performance_csv(course_id=cid)
        b = csv_content.encode("utf-8")
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "text/csv; charset=utf-8")
        handler.send_header("Content-Length", str(len(b)))
        handler.send_header("Content-Disposition", 'attachment; filename="cyberpro_performance_leaderboard.csv"')
        handler.end_headers()
        handler.wfile.write(b)
        return

    # --- LMS Activity Telemetry GET ---
    if clean_path in ("/api/admin/activity", "/api/admin/activity/overview"):
        course_id = query_params.get("course_id", ["all"])[0]
        act_type = query_params.get("type", ["all"])[0]
        search = query_params.get("q", [""])[0]
        page = int(query_params.get("page", [1])[0])
        limit = int(query_params.get("limit", [25])[0])
        data = A.get_lms_activity_dashboard(course_id, act_type, search, page, limit)
        return handler.send_json(HTTPStatus.OK, data)

    if clean_path.startswith("/api/admin/activity/student/"):
        parts = clean_path.split("/")
        try:
            target_uid = int(parts[5])
            stream = A.get_student_post_login_activity_stream(target_uid)
            if not stream:
                return handler.send_json(HTTPStatus.NOT_FOUND, {"error": "Student not found"})
            return handler.send_json(HTTPStatus.OK, stream)
        except (ValueError, IndexError):
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid student user ID"})

    if clean_path in ("/api/admin/activity/export.csv", "/api/admin/export/activity"):
        uid = query_params.get("user_id", [None])[0]
        if uid:
            try:
                uid = int(uid)
            except ValueError:
                uid = None
        csv_bytes = A.export_lms_activity_csv(uid)
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "text/csv; charset=utf-8")
        handler.send_header("Content-Disposition", 'attachment; filename="cyberpro_lms_activity.csv"')
        handler.send_header("Content-Length", str(len(csv_bytes)))
        handler.end_headers()
        handler.wfile.write(csv_bytes)
        return

    # --- Recorded Lectures GET ---
    if clean_path in ("/api/admin/lectures", "/api/admin/lectures/catalog"):
        course_id = query_params.get("course_id", [""])[0]
        q = query_params.get("q", [""])[0]
        data = A.get_admin_lectures_data(course_id, q)
        return handler.send_json(HTTPStatus.OK, data)

    # --- Login & Security Telemetry GET ---
    if clean_path in ("/api/admin/security/export.csv", "/api/admin/export/security"):
        csv_text = A.export_security_logs_csv()
        data = csv_text.encode("utf-8")
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "text/csv; charset=utf-8")
        handler.send_header("Content-Disposition", 'attachment; filename="cyberpro_login_security_audit.csv"')
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
        return

    if clean_path in ("/api/admin/security/logins", "/api/admin/security", "/api/admin/security/sessions"):
        q = query_params.get("q", [""])[0]
        status = query_params.get("status", [""])[0]
        role = query_params.get("role", [""])[0]
        data = A.get_security_logins_data(q, status, role)
        return handler.send_json(HTTPStatus.OK, data)

    # --- Payments & EMI Management GET ---
    if clean_path in ("/api/admin/payments/export.csv", "/api/admin/export/payments"):
        p_type = query_params.get("type", ["emi"])[0]
        csv_text = A.export_payments_csv(p_type)
        data = csv_text.encode("utf-8")
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "text/csv; charset=utf-8")
        handler.send_header("Content-Disposition", f'attachment; filename="cyberpro_payments_{p_type}.csv"')
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
        return

    if clean_path in ("/api/admin/payments", "/api/admin/payments/overview"):
        q = query_params.get("q", [""])[0]
        course = query_params.get("course", [""])[0]
        data = A.get_payments_management_data(q, course)
        return handler.send_json(HTTPStatus.OK, data)

    if clean_path.startswith("/api/admin/payments/student/") and clean_path.endswith("/emi"):
        parts = clean_path.split("/")
        uid = parts[5]
        drill = A.get_student_emi_drilldown(uid)
        if not drill:
            return handler.send_json(HTTPStatus.NOT_FOUND, {"error": "Student not found"})
        return handler.send_json(HTTPStatus.OK, drill)

    # --- Schedules Management GET ---
    if clean_path == "/api/admin/schedules":
        course_id = query_params.get("course_id", [""])[0]
        day = query_params.get("day", [""])[0]
        status = query_params.get("status", [""])[0]
        schedules = A.get_schedules(course_id or None, day if day != "" else None, status or None)
        return handler.send_json(HTTPStatus.OK, {"schedules": schedules})

    if clean_path.startswith("/api/admin/registrations/"):
        parts = clean_path.split("/")
        try:
            reg_id = int(parts[4])
            ok, msg = A.delete_registration(user["id"], reg_id)
            if not ok:
                return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": msg})
            return handler.send_json(HTTPStatus.OK, {"ok": True, "message": msg})
        except (ValueError, IndexError):
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid registration ID"})

    if clean_path.startswith("/api/admin/lectures/"):
        parts = clean_path.split("/")
        try:
            lec_id = int(parts[4])
            ok, msg = A.update_lecture(user["id"], lec_id, body)
            if not ok:
                return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": msg})
            return handler.send_json(HTTPStatus.OK, {"ok": True, "message": msg})
        except (ValueError, IndexError):
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid lecture ID"})

    if clean_path.startswith("/api/admin/quizzes/"):
        parts = clean_path.split("/")
        try:
            quiz_id = int(parts[4])
            ok, msg = A.update_quiz(user["id"], quiz_id, body)
            if not ok:
                return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": msg})
            return handler.send_json(HTTPStatus.OK, {"ok": True, "message": msg})
        except (ValueError, IndexError):
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid quiz ID"})

    if clean_path.startswith("/api/admin/quizzes/"):
        parts = clean_path.split("/")
        try:
            quiz_id = int(parts[4])
            ok, msg = A.delete_quiz(user["id"], quiz_id)
            if not ok:
                return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": msg})
            return handler.send_json(HTTPStatus.OK, {"ok": True, "message": msg})
        except (ValueError, IndexError):
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid quiz ID"})

    if clean_path.startswith("/api/admin/quizzes/"):
        parts = clean_path.split("/")
        try:
            quiz_id = int(parts[4])
            ok, msg = A.update_quiz(user["id"], quiz_id, body)
            if not ok:
                return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": msg})
            return handler.send_json(HTTPStatus.OK, {"ok": True, "message": msg})
        except (ValueError, IndexError):
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid quiz ID"})

    if clean_path.startswith("/api/admin/quizzes/"):
        parts = clean_path.split("/")
        try:
            quiz_id = int(parts[4])
            ok, msg = A.delete_quiz(user["id"], quiz_id)
            if not ok:
                return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": msg})
            return handler.send_json(HTTPStatus.OK, {"ok": True, "message": msg})
        except (ValueError, IndexError):
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid quiz ID"})

    if clean_path.startswith("/api/admin/schedules/"):
        parts = clean_path.split("/")
        try:
            sched_id = int(parts[4])
            s = A.get_schedule_by_id(sched_id)
            if not s:
                return handler.send_json(HTTPStatus.NOT_FOUND, {"error": "Schedule not found"})
            return handler.send_json(HTTPStatus.OK, {"schedule": s})
        except (ValueError, IndexError):
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid schedule ID"})

    # --- Section 17: Staff Management & RBAC Suite ---
    if clean_path == "/api/admin/staff":
        q = query_params.get("q", [""])[0]
        role_f = query_params.get("role", ["all"])[0]
        status_f = query_params.get("status", ["all"])[0]
        return handler.send_json(HTTPStatus.OK, A.get_staff_dashboard(q, role_f, status_f))

    if clean_path == "/api/admin/staff/export":
        csv_text = A.export_staff_csv()
        data = csv_text.encode('utf-8')
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "text/csv; charset=utf-8")
        handler.send_header("Content-Disposition", 'attachment; filename="cyberpro_staff_team.csv"')
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
        return

    if clean_path == "/api/admin/rbac/matrix":
        return handler.send_json(HTTPStatus.OK, A.get_rbac_matrix())

    if clean_path == "/api/admin/staff/audit-stream":
        return handler.send_json(HTTPStatus.OK, A.get_staff_audit_stream())

    if clean_path.startswith("/api/admin/staff/") and clean_path.endswith("/activity"):
        parts = clean_path.split("/")
        try:
            s_id = int(parts[4])
            return handler.send_json(HTTPStatus.OK, A.get_staff_activity(s_id))
        except (ValueError, IndexError):
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid staff ID"})


    # Section 18: System Settings & Backup Vault
    if clean_path == "/api/admin/settings":
        return handler.send_json(HTTPStatus.OK, A.get_settings_dashboard())

    if clean_path.startswith("/api/admin/settings/backup/download/"):
        import runtime_paths
        filename = clean_path.split("/")[-1]
        filename = os.path.basename(filename)
        backup_path = str(runtime_paths.resolve_writable_dir(A.ROOT, "data/backups") / filename)
        if not os.path.exists(backup_path):
            return handler.send_json(HTTPStatus.NOT_FOUND, {"error": "Backup file not found on disk"})
        with open(backup_path, "rb") as f:
            data = f.read()
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "application/x-sqlite3")
        handler.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
        return

    if clean_path == "/api/admin/settings/export":
        csv_text = A.export_settings_csv()
        data = csv_text.encode("utf-8")
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "text/csv; charset=utf-8")
        handler.send_header("Content-Disposition", 'attachment; filename="cyberpro_settings_export.csv"')
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
        return

    # --- Section 20: REFER & EARN MANAGEMENT (GET) ---
    if clean_path == "/api/admin/referrals/overview":
        import referral_service as rs
        return handler.send_json(HTTPStatus.OK, rs.get_admin_referral_overview())

    if clean_path == "/api/admin/referrals/records":
        import referral_service as rs
        filters = {
            "search": query_params.get("search", [""])[0] or query_params.get("q", [""])[0],
            "referral_status": query_params.get("referral_status", [""])[0] or query_params.get("status", [""])[0],
            "reward_status": query_params.get("reward_status", [""])[0],
            "code_type": query_params.get("code_type", [""])[0],
            "course_id": query_params.get("course_id", [""])[0]
        }
        return handler.send_json(HTTPStatus.OK, rs.get_admin_referral_records(filters))

    if clean_path == "/api/admin/referrals/codes":
        import referral_service as rs
        filters = {
            "search": query_params.get("search", [""])[0] or query_params.get("q", [""])[0],
            "code_type": query_params.get("code_type", [""])[0],
            "is_active": query_params.get("is_active", [""])[0]
        }
        return handler.send_json(HTTPStatus.OK, rs.get_admin_referral_codes(filters))

    if clean_path == "/api/admin/referrals/leaderboard":
        import referral_service as rs
        limit = int(query_params.get("limit", [50])[0])
        return handler.send_json(HTTPStatus.OK, rs.get_referral_leaderboard(limit=limit))

    if clean_path in ("/api/admin/referrals/export", "/api/admin/referrals/export.csv"):
        import referral_service as rs
        csv_text = rs.generate_referrals_csv()
        data = csv_text.encode("utf-8")
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "text/csv; charset=utf-8")
        handler.send_header("Content-Disposition", 'attachment; filename="cyberpro_referrals_export.csv"')
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
        return

    if clean_path == "/api/admin/referrals/settings":
        import referral_service as rs
        return handler.send_json(HTTPStatus.OK, {
            "success": True,
            "settings": {
                "default_reward_amount": rs.get_setting("default_reward_amount", "500"),
                "default_reward_type": rs.get_setting("default_reward_type", "cash"),
                "min_code_length": rs.get_setting("min_code_length", "4"),
                "max_code_length": rs.get_setting("max_code_length", "20"),
                "allow_custom_changes": rs.get_setting("allow_custom_changes", "1"),
                "auto_approve_on_payment": rs.get_setting("auto_approve_on_payment", "1")
            }
        })

    # --- ADVANCED CYBERSECURITY ECOSYSTEM (ADMIN GET) ---
    if clean_path == "/api/admin/cyber-range/labs":
        import ecosystem_service as eco
        labs = eco.get_cyber_range_labs()
        # Add telemetry counts
        with eco.get_db() as conn:
            for lab in labs:
                c_total = conn.execute("SELECT COUNT(*) FROM cyber_range_sessions WHERE lab_id=?", (lab['id'],)).fetchone()[0]
                c_done = conn.execute("SELECT COUNT(*) FROM cyber_range_sessions WHERE lab_id=? AND status='completed'", (lab['id'],)).fetchone()[0]
                lab['total_attempts'] = c_total
                lab['completions'] = c_done
        return handler.send_json(HTTPStatus.OK, {"success": True, "labs": labs})

    if clean_path == "/api/admin/bug-bounty/reports":
        import ecosystem_service as eco
        with eco.get_db() as conn:
            rows = conn.execute("""
            SELECT r.*, u.name as student_name, u.email as student_email, c.title as challenge_title
            FROM bug_bounty_reports r
            JOIN users u ON r.student_id = u.id
            JOIN bug_bounty_challenges c ON r.challenge_id = c.id
            ORDER BY r.id DESC
            """).fetchall()
            return handler.send_json(HTTPStatus.OK, {"success": True, "reports": [dict(r) for r in rows]})

    if clean_path == "/api/admin/soc/investigations":
        import ecosystem_service as eco
        with eco.get_db() as conn:
            rows = conn.execute("""
            SELECT i.*, u.name as student_name, u.email as student_email, s.title as scenario_title, s.attack_type, s.severity as scenario_severity
            FROM soc_investigations i
            JOIN users u ON i.student_id = u.id
            JOIN soc_scenarios s ON i.scenario_id = s.id
            ORDER BY i.id DESC
            """).fetchall()
            return handler.send_json(HTTPStatus.OK, {"success": True, "investigations": [dict(r) for r in rows]})

    if clean_path == "/api/admin/projects":
        import ecosystem_service as eco
        projects = eco.get_student_projects()
        return handler.send_json(HTTPStatus.OK, {"success": True, "projects": projects})

    if clean_path == "/api/admin/employer/candidates":
        import ecosystem_service as eco
        parsed_q = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
        q = parsed_q.get("q", [""])[0]
        skill = parsed_q.get("skill", [""])[0]
        candidates = eco.get_employer_candidate_pool(search_query=q, skill_filter=skill)
        return handler.send_json(HTTPStatus.OK, {"success": True, "candidates": candidates})

    if clean_path == "/api/admin/employer/jobs":
        import ecosystem_service as eco
        with eco.get_db() as conn:
            jobs = conn.execute("""
            SELECT j.*, e.company_name, 
                   (SELECT COUNT(*) FROM job_applications ja WHERE ja.job_id = j.id) as applicant_count
            FROM employer_jobs j
            JOIN employer_profiles e ON j.employer_id = e.id
            ORDER BY j.id DESC
            """).fetchall()
            apps = conn.execute("""
            SELECT ja.*, u.name as student_name, u.email as student_email, j.title as job_title, e.company_name
            FROM job_applications ja
            JOIN users u ON ja.student_id = u.id
            JOIN employer_jobs j ON ja.job_id = j.id
            JOIN employer_profiles e ON j.employer_id = e.id
            ORDER BY ja.id DESC
            """).fetchall()
            return handler.send_json(HTTPStatus.OK, {"success": True, "jobs": [dict(j) for j in jobs], "applications": [dict(a) for a in apps]})

    return handler.send_json(HTTPStatus.NOT_FOUND, {"error": "Endpoint not found"})

def handle_admin_post(handler, path, user):
    """Handles all POST /api/admin/* endpoints."""
    clean_path = urllib.parse.urlsplit(path).path
    body = handler.read_json() or {}

    if clean_path == "/api/admin/students":
        ok, res = A.add_student(user["id"], body)
        if not ok:
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": res})
        return handler.send_json(HTTPStatus.CREATED, {"ok": True, "student": res})

    if clean_path.startswith("/api/admin/students/") and clean_path.endswith("/override-access"):
        # /api/admin/students/<id>/override-access
        student_id = clean_path.split("/")[4]
        new_status = str(body.get("access_status", "unlocked")).strip().lower()
        reason = str(body.get("reason", "")).strip()
        course_id = body.get("course_id")
        ok, msg = A.override_course_access(user["id"], student_id, new_status, reason, course_id=course_id)
        if not ok:
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": msg})
        return handler.send_json(HTTPStatus.OK, {"ok": True, "message": msg, "access_status": new_status})

    if clean_path == "/api/admin/users/set-password":
        target_id = body.get("identifier") or body.get("user_id") or body.get("email") or ""
        new_pw = body.get("password", "")
        res = A.set_user_portal_password(user.get("id", 1), target_id, new_pw)
        return handler.send_json(HTTPStatus.OK if res.get("ok") else HTTPStatus.BAD_REQUEST, res)

    if (clean_path.startswith("/api/admin/students/") or clean_path.startswith("/api/admin/admins/")) and clean_path.endswith("/password"):
        target_id = urllib.parse.unquote(clean_path.split("/")[4])
        res = A.set_user_portal_password(user.get("id", 1), target_id, body.get("password", ""))
        return handler.send_json(HTTPStatus.OK if res.get("ok") else HTTPStatus.BAD_REQUEST, res)

    if clean_path == "/api/admin/admins":
        res = A.create_admin_account(user.get("id", 1), body)
        return handler.send_json(HTTPStatus.CREATED if res.get("ok") else HTTPStatus.BAD_REQUEST, res)

    if clean_path.startswith("/api/admin/admins/") and clean_path.endswith("/update"):
        target_id = urllib.parse.unquote(clean_path.split("/")[4])
        res = A.update_admin_account(user.get("id", 1), target_id, body)
        return handler.send_json(HTTPStatus.OK if res.get("ok") else HTTPStatus.BAD_REQUEST, res)

    if clean_path.startswith("/api/admin/admins/") and clean_path.endswith("/toggle"):
        target_id = urllib.parse.unquote(clean_path.split("/")[4])
        res = A.toggle_admin_account_status(user.get("id", 1), target_id)
        return handler.send_json(HTTPStatus.OK if res.get("ok") else HTTPStatus.BAD_REQUEST, res)

    if clean_path in ("/api/admin/security/revoke-session", "/api/admin/security/revoke"):
        log_id = int(body.get("log_id", 0))
        reason = str(body.get("reason", "Admin revoked session")).strip()
        ok, msg = A.revoke_user_session(user["id"], log_id, reason)
        if not ok:
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": msg})
        return handler.send_json(HTTPStatus.OK, {"ok": True, "message": msg})

    if clean_path in ("/api/admin/payments/collect", "/api/admin/payments/collect-installment"):
        user_id = int(body.get("user_id", 0))
        installment_id = int(body.get("installment_id", 0))
        amount = body.get("amount")
        method = str(body.get("payment_method", "upi")).strip()
        txn_id = str(body.get("transaction_id", "")).strip()
        notes = str(body.get("notes", "")).strip()
        ok, res = A.collect_student_installment(user["id"], user_id, installment_id, amount, method, txn_id, notes)
        if not ok:
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": res})
        return handler.send_json(HTTPStatus.OK, {"ok": True, "result": res})

    if clean_path.startswith("/api/admin/payments/") and clean_path.endswith("/verify"):
        # /api/admin/payments/<id>/verify
        payment_id = int(clean_path.split("/")[4])
        v_status = str(body.get("status", "verified")).strip()
        notes = str(body.get("notes", "")).strip()
        ok, msg = A.verify_payment(user["id"], payment_id, v_status, notes)
        if not ok:
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": msg})
        return handler.send_json(HTTPStatus.OK, {"ok": True, "message": msg})

    # --- Recorded Lectures POST Endpoints ---
    if clean_path == "/api/admin/lectures/upload":
        content_type = handler.headers.get("Content-Type", "")
        content_length = int(handler.headers.get("Content-Length", 0))
        if content_length <= 0:
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "No file uploaded."})
        raw_body = handler.rfile.read(content_length)

        import email.parser
        msg_bytes = f"Content-Type: {content_type}\r\n\r\n".encode() + raw_body
        msg = email.parser.BytesParser().parsebytes(msg_bytes)
        fields = {}
        files = {}
        for part in msg.get_payload():
            cd = part.get("Content-Disposition", "")
            name = None
            filename = None
            for item in cd.split(";"):
                item = item.strip()
                if item.startswith("name="):
                    name = item[5:].strip('"')
                elif item.startswith("filename="):
                    filename = item[9:].strip('"')
            if filename:
                files[name] = {
                    "filename": filename,
                    "content_type": part.get_content_type(),
                    "data": part.get_payload(decode=True)
                }
            elif name:
                fields[name] = part.get_payload(decode=True).decode("utf-8", errors="ignore")

        v_file = files.get("video_file")
        if not v_file or not v_file["data"]:
            v_url = fields.get("video_url", "").strip()
            if not v_url:
                return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Please provide a video file to upload or enter a video streaming URL."})
            ok, res = A.create_lecture(user["id"], fields)
            if not ok:
                return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": res})
            return handler.send_json(HTTPStatus.CREATED, {"ok": True, "lecture": res})

        ok, res = A.save_uploaded_video_file(user["id"], v_file["data"], v_file["filename"], fields)
        if not ok:
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": res})
        return handler.send_json(HTTPStatus.CREATED, {"ok": True, "lecture": res})

    if clean_path == "/api/admin/lectures":
        ok, res = A.create_lecture(user["id"], body)
        if not ok:
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": res})
        return handler.send_json(HTTPStatus.CREATED, {"ok": True, "lecture": res})

    if clean_path.startswith("/api/admin/lectures/") and clean_path.endswith("/delete"):
        parts = clean_path.split("/")
        try:
            lec_id = int(parts[4])
            ok, msg = A.delete_lecture(user["id"], lec_id)
            if not ok:
                return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": msg})
            return handler.send_json(HTTPStatus.OK, {"ok": True, "message": msg})
        except (ValueError, IndexError):
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid lecture ID"})

    # --- Registrations Management POST Endpoints ---
    if clean_path.startswith("/api/admin/registrations/"):
        parts = clean_path.split("/")
        try:
            reg_id = int(parts[4])
        except (ValueError, IndexError):
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid registration ID"})

        if clean_path.endswith("/status"):
            new_status = str(body.get("status", "")).strip()
            notes = str(body.get("notes", "")).strip()
            ok, msg = A.update_registration_status(user["id"], reg_id, new_status, notes)
            if not ok:
                return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": msg})
            return handler.send_json(HTTPStatus.OK, {"ok": True, "message": msg})

        if clean_path.endswith("/convert-student"):
            course_id = str(body.get("course_id", "ccce")).strip()
            fee = float(body.get("fee", 4000))
            paid = float(body.get("paid_amount", 4000))
            ok, res = A.convert_registration_to_student(user["id"], reg_id, course_id, fee, paid)
            if not ok:
                return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": res})
            return handler.send_json(HTTPStatus.OK, {"ok": True, "result": res})

        if clean_path.endswith("/delete"):
            ok, msg = A.delete_registration(user["id"], reg_id)
            if not ok:
                return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": msg})
            return handler.send_json(HTTPStatus.OK, {"ok": True, "message": msg})

    # --- Quizzes & Labs POST Endpoints ---
    if clean_path == "/api/admin/quizzes":
        ok, res = A.create_quiz(user["id"], body)
        if not ok:
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": res})
        return handler.send_json(HTTPStatus.CREATED, {"ok": True, "quiz": res})

    if clean_path.startswith("/api/admin/quizzes/"):
        parts = clean_path.split("/")
        try:
            quiz_id = int(parts[4])
        except (ValueError, IndexError):
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid quiz ID"})

        if clean_path.endswith("/update"):
            ok, msg = A.update_quiz(user["id"], quiz_id, body)
            if not ok:
                return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": msg})
            return handler.send_json(HTTPStatus.OK, {"ok": True, "message": msg})

        if clean_path.endswith("/delete"):
            ok, msg = A.delete_quiz(user["id"], quiz_id)
            if not ok:
                return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": msg})
            return handler.send_json(HTTPStatus.OK, {"ok": True, "message": msg})

    # --- Attendance Management POST Endpoints ---
    if clean_path == "/api/admin/attendance/mark":
        att_id = body.get("attendance_id")
        new_status = body.get("status", "attended")
        notes = body.get("notes", "")
        if not att_id:
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Missing attendance_id"})
        ok, msg = A.mark_attendance_status(user["id"], int(att_id), new_status, notes)
        if not ok:
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": msg})
        return handler.send_json(HTTPStatus.OK, {"ok": True, "message": msg})

    if clean_path == "/api/admin/attendance/whatsapp/send":
        att_id = body.get("attendance_id")
        if not att_id:
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Missing attendance_id"})
        ok, msg = A.send_whatsapp_alert(user["id"], int(att_id))
        if not ok:
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": msg})
        return handler.send_json(HTTPStatus.OK, {"ok": True, "message": msg})

    if clean_path == "/api/admin/attendance/whatsapp/bulk":
        sched_id = body.get("schedule_id")
        if not sched_id:
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Missing schedule_id"})
        ok, res = A.send_bulk_whatsapp_alerts(user["id"], int(sched_id))
        if not ok:
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": res})
        return handler.send_json(HTTPStatus.OK, {"ok": True, "result": res})

    if clean_path == "/api/admin/attendance/revoke-session":
        att_id = body.get("attendance_id")
        if not att_id:
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Missing attendance_id"})
        ok, msg = A.revoke_attendance_session(user["id"], int(att_id))
        if not ok:
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": msg})
        return handler.send_json(HTTPStatus.OK, {"ok": True, "message": msg})

    # --- Schedule Management POST Endpoints ---
    if clean_path == "/api/admin/schedules":
        ok, res = A.create_schedule(user["id"], body)
        if not ok:
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": res})
        return handler.send_json(HTTPStatus.CREATED, {"ok": True, "schedule": res})

    if clean_path.startswith("/api/admin/schedules/"):
        parts = clean_path.split("/")
        try:
            sched_id = int(parts[4])
        except (ValueError, IndexError):
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid schedule ID"})

        if clean_path.endswith("/update"):
            ok, res = A.update_schedule(user["id"], sched_id, body)
            if not ok:
                return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": res})
            return handler.send_json(HTTPStatus.OK, {"ok": True, "schedule": res})

        if clean_path.endswith("/reschedule"):
            new_date = str(body.get("class_date", "")).strip()
            new_start = str(body.get("start_time", "")).strip()
            new_end = str(body.get("end_time", "")).strip()
            reason = str(body.get("reason", "")).strip()
            ok, msg = A.reschedule_schedule(user["id"], sched_id, new_date, new_start, new_end, reason)
            if not ok:
                return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": msg})
            return handler.send_json(HTTPStatus.OK, {"ok": True, "message": msg})

        if clean_path.endswith("/status"):
            new_status = str(body.get("status", "")).strip()
            notes = str(body.get("notes", "")).strip()
            ok, msg = A.update_schedule_status(user["id"], sched_id, new_status, notes)
            if not ok:
                return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": msg})
            return handler.send_json(HTTPStatus.OK, {"ok": True, "message": msg})

        if clean_path.endswith("/delete"):
            ok, msg = A.delete_schedule(user["id"], sched_id)
            if not ok:
                return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": msg})
            return handler.send_json(HTTPStatus.OK, {"ok": True, "message": msg})

    # --- Section 14: Certificate Actions POST ---
    if clean_path == "/api/admin/certificates/issue":
        res = A.issue_certificate(user["id"], body)
        return handler.send_json(HTTPStatus.OK if res.get("ok") else HTTPStatus.BAD_REQUEST, res)

    if clean_path.startswith("/api/admin/certificates/") and clean_path.endswith("/revoke"):
        parts = clean_path.split("/")
        try:
            c_id = int(parts[4])
            reason = body.get("reason", "Revocation by CyberPro Administration")
            res = A.revoke_certificate(user["id"], c_id, reason)
            return handler.send_json(HTTPStatus.OK if res.get("ok") else HTTPStatus.BAD_REQUEST, res)
        except Exception as e:
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": str(e)})

    if clean_path.startswith("/api/admin/certificates/") and clean_path.endswith("/restore"):
        parts = clean_path.split("/")
        try:
            c_id = int(parts[4])
            res = A.reissue_certificate(user["id"], c_id)
            return handler.send_json(HTTPStatus.OK if res.get("ok") else HTTPStatus.BAD_REQUEST, res)
        except Exception as e:
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": str(e)})

    # --- Section 17: Staff Management & RBAC Suite ---
    if clean_path == "/api/admin/staff":
        res = A.create_staff_member(body, user.get("id", 1))
        return handler.send_json(HTTPStatus.CREATED if res.get("ok") else HTTPStatus.BAD_REQUEST, res)

    if clean_path.startswith("/api/admin/staff/") and clean_path.endswith("/toggle"):
        parts = clean_path.split("/")
        try:
            s_id = int(parts[4])
            res = A.toggle_staff_status(s_id, user.get("id", 1))
            return handler.send_json(HTTPStatus.OK if res.get("ok") else HTTPStatus.BAD_REQUEST, res)
        except (ValueError, IndexError):
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid staff ID"})

    if clean_path.startswith("/api/admin/staff/") and clean_path.endswith("/update"):
        parts = clean_path.split("/")
        try:
            s_id = int(parts[4])
            res = A.update_staff_member(s_id, body, user.get("id", 1))
            return handler.send_json(HTTPStatus.OK if res.get("ok") else HTTPStatus.BAD_REQUEST, res)
        except (ValueError, IndexError):
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid staff ID"})

    if clean_path == "/api/admin/rbac/update":
        res = A.update_rbac_permission(body.get("role_slug", ""), body.get("feature_key", ""), body.get("field", ""), body.get("value", 0), user.get("id", 1))
        return handler.send_json(HTTPStatus.OK if res.get("ok") else HTTPStatus.BAD_REQUEST, res)

    # Section 18: System Settings & Backup Vault
    if clean_path == "/api/admin/settings":
        settings_dict = body.get("settings", {})
        if not settings_dict and isinstance(body, dict):
            settings_dict = {k: v for k, v in body.items() if k != "settings"}
        ip = handler.client_address[0] if hasattr(handler, "client_address") else "127.0.0.1"
        res = A.update_system_settings(settings_dict, user.get("id", 1), ip)
        return handler.send_json(HTTPStatus.OK if res.get("ok") else HTTPStatus.BAD_REQUEST, res)

    if clean_path == "/api/admin/settings/backup/create":
        backup_type = body.get("type", "manual")
        res = A.create_database_backup(backup_type, user.get("id", 1))
        return handler.send_json(HTTPStatus.OK if res.get("ok") else HTTPStatus.INTERNAL_SERVER_ERROR, res)

    if clean_path == "/api/admin/settings/backup/restore":
        filename = body.get("filename")
        if not filename:
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Backup filename required"})
        res = A.restore_database_backup(filename, user.get("id", 1))
        return handler.send_json(HTTPStatus.OK if res.get("ok") else HTTPStatus.INTERNAL_SERVER_ERROR, res)

    if clean_path == "/api/admin/settings/backup/delete":
        filename = body.get("filename")
        if not filename:
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Backup filename required"})
        res = A.delete_database_backup(filename, user.get("id", 1))
        return handler.send_json(HTTPStatus.OK if res.get("ok") else HTTPStatus.INTERNAL_SERVER_ERROR, res)

    # --- Section 20: REFER & EARN MANAGEMENT (POST) ---
    if clean_path == "/api/admin/referrals/codes/toggle":
        import referral_service as rs
        code_id = body.get("code_id")
        if not code_id:
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Missing code_id"})
        res = rs.toggle_code_active_status(code_id, admin_id=user.get("id", 1))
        return handler.send_json(HTTPStatus.OK if res.get("success") else HTTPStatus.BAD_REQUEST, res)

    if clean_path == "/api/admin/referrals/status/update":
        import referral_service as rs
        rec_id = body.get("record_id")
        status = body.get("status")
        reason = body.get("reason", "")
        if not rec_id or not status:
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Missing record_id or status"})
        res = rs.update_referral_record_status(rec_id, status, reason=reason, admin_id=user.get("id", 1))
        return handler.send_json(HTTPStatus.OK if res.get("success") else HTTPStatus.BAD_REQUEST, res)

    if clean_path == "/api/admin/referrals/reward/update":
        import referral_service as rs
        rec_id = body.get("record_id")
        reward_status = body.get("reward_status")
        payout_ref = body.get("payout_reference")
        notes = body.get("notes")
        if not rec_id or not reward_status:
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Missing record_id or reward_status"})
        res = rs.update_reward_payout_status(rec_id, reward_status, payout_reference=payout_ref, notes=notes, admin_id=user.get("id", 1))
        return handler.send_json(HTTPStatus.OK if res.get("success") else HTTPStatus.BAD_REQUEST, res)

    if clean_path == "/api/admin/referrals/settings":
        import referral_service as rs
        for k in ("default_reward_amount", "default_reward_type", "min_code_length", "max_code_length", "allow_custom_changes", "auto_approve_on_payment"):
            if k in body:
                rs.set_setting(k, body[k])
        return handler.send_json(HTTPStatus.OK, {"success": True, "message": "Referral settings updated successfully."})

    # --- ADVANCED CYBERSECURITY ECOSYSTEM (ADMIN POST) ---
    if clean_path == "/api/admin/bug-bounty/reports/review":
        import ecosystem_service as eco
        report_id = int(body.get("report_id") or 0)
        status = str(body.get("status", "approved"))
        points = int(body.get("points_awarded", 0))
        feedback = str(body.get("feedback", ""))
        res = eco.review_bug_bounty_report(report_id, user.get("id", 1), status, points, feedback)
        return handler.send_json(HTTPStatus.OK if res.get("success") else HTTPStatus.BAD_REQUEST, res)

    if clean_path == "/api/admin/projects/review":
        import ecosystem_service as eco
        proj_id = int(body.get("project_id") or 0)
        rubric = body.get("rubric", {})
        feedback = str(body.get("feedback", ""))
        decision = str(body.get("decision", "approved"))
        res = eco.review_student_project(proj_id, user.get("id", 1), rubric, feedback, decision)
        return handler.send_json(HTTPStatus.OK if res.get("success") else HTTPStatus.BAD_REQUEST, res)

    if clean_path == "/api/admin/employer/applications/update":
        import ecosystem_service as eco
        app_id = int(body.get("application_id", 0))
        status = str(body.get("status", "shortlisted"))
        feedback = body.get("feedback")
        interview = body.get("interview_details")
        res = eco.update_job_application_status(app_id, status, feedback, interview)
        return handler.send_json(HTTPStatus.OK if res.get("success") else HTTPStatus.BAD_REQUEST, res)

    if clean_path == "/api/admin/skill-passport/endorse":
        import ecosystem_service as eco
        student_id = int(body.get("student_id", 0))
        skill_name = str(body.get("skill_name", ""))
        res = eco.verify_student_skill_by_instructor(student_id, skill_name, user.get("id", 1))
        return handler.send_json(HTTPStatus.OK if res.get("success") else HTTPStatus.BAD_REQUEST, res)

    if clean_path == "/api/admin/employer/jobs":
        import ecosystem_service as eco
        title = body.get("title")
        job_type = body.get("job_type", "Full-time")
        location = body.get("location", "Remote")
        salary = body.get("stipend_or_salary", "Competitive")
        skills = json.dumps(body.get("required_skills", ["Cybersecurity"]))
        desc = body.get("description", "")
        with eco.get_db() as conn:
            conn.execute("""
            INSERT INTO employer_jobs (employer_id, title, job_type, location, stipend_or_salary, required_skills_json, description)
            VALUES (1, ?, ?, ?, ?, ?, ?)
            """, (title, job_type, location, salary, skills, desc))
            conn.commit()
        return handler.send_json(HTTPStatus.OK, {"success": True, "message": "New opportunity published successfully."})

    return handler.send_json(HTTPStatus.NOT_FOUND, {"error": "Admin endpoint not found"})

def handle_admin_put(handler, path, user):
    """Handles all PUT /api/admin/* endpoints."""
    clean_path = urllib.parse.urlsplit(path).path
    body = handler.read_json() or {}

    if clean_path.startswith("/api/admin/schedules/"):
        parts = clean_path.split("/")
        try:
            sched_id = int(parts[4])
            ok, res = A.update_schedule(user["id"], sched_id, body)
            if not ok:
                return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": res})
            return handler.send_json(HTTPStatus.OK, {"ok": True, "schedule": res})
        except (ValueError, IndexError):
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid schedule ID"})

    if clean_path.startswith("/api/admin/staff/"):
        parts = clean_path.split("/")
        try:
            s_id = int(parts[4])
            res = A.update_staff_member(s_id, body, user.get("id", 1))
            return handler.send_json(HTTPStatus.OK if res.get("ok") else HTTPStatus.BAD_REQUEST, res)
        except (ValueError, IndexError):
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid staff ID"})





    return handler.send_json(HTTPStatus.NOT_FOUND, {"error": "Endpoint not found"})

def handle_admin_delete(handler, path, user):
    """Handles all DELETE /api/admin/* endpoints."""
    clean_path = urllib.parse.urlsplit(path).path
    if clean_path.startswith("/api/admin/schedules/"):
        parts = clean_path.split("/")
        try:
            sched_id = int(parts[4])
            ok, msg = A.delete_schedule(user["id"], sched_id)
            if not ok:
                return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": msg})
            return handler.send_json(HTTPStatus.OK, {"ok": True, "message": msg})
        except (ValueError, IndexError):
            return handler.send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid schedule ID"})

    return handler.send_json(HTTPStatus.NOT_FOUND, {"error": "Endpoint not found"})
