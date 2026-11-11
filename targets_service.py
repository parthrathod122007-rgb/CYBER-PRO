"""
CYBERPRO TRAINING TARGETS SERVICE
Provides safe, isolated, deliberately vulnerable testbed endpoints
for Bug Bounty Arena practice challenges.
"""

import json
import re

def handle_vulnerable_orders(query_params):
    order_id = query_params.get('order_id', ['1'])[0]
    # Check for SQL injection patterns
    is_sqli = any(p in order_id.upper() for p in ["'", "UNION", "OR 1=1", "--", "/*", "SLEEP"])
    if is_sqli:
        return {
            'status': 'success',
            'query_executed': f"SELECT * FROM orders WHERE id = {order_id}",
            'results': [
                {'order_id': 101, 'product': 'CCCE Full Track', 'amount': 4000.0, 'status': 'Paid'},
                {'order_id': 999, 'product': 'SYSTEM_LEAK_VAULT', 'secret_token': 'CYBERPRO{bounty_union_sqli_orders_dump_839}', 'status': 'CRITICAL_EXPOSURE'}
            ],
            'vulnerability_note': 'SQL Injection Verified: Unparameterized query executed.'
        }
    return {
        'status': 'success',
        'query_executed': f"SELECT * FROM orders WHERE id = {order_id}",
        'results': [{'order_id': int(order_id) if order_id.isdigit() else 1, 'product': 'CyberPro Certified Cybersecurity Expert', 'amount': 4000.0, 'status': 'Paid'}]
    }

def handle_vulnerable_search(query_params):
    q = query_params.get('q', [''])[0]
    is_xss = any(p in q.lower() for p in ['<script', 'onerror', 'onload', 'javascript:', 'alert('])
    if is_xss:
        return (
            f"<html><body><h2>Search Results for: {q}</h2>"
            f"<div class='alert alert-warning'>[VULNERABILITY DETECTED: Reflected XSS] "
            f"Flag: CYBERPRO{{bounty_reflected_xss_sanitization_missing_119}}</div></body></html>"
        )
    return f"<html><body><h2>Search Results for: {q}</h2><p>No results found for query.</p></body></html>"

def handle_vulnerable_auth(auth_header):
    if not auth_header:
        return {'error': 'Missing Authorization header', 'code': 401}
    # Check for unsigned or none algorithm token
    token = auth_header.replace('Bearer ', '').strip()
    if 'none' in token.lower() and ('admin' in token.lower() or 'superadmin' in token.lower()):
        return {
            'authenticated': True,
            'role': 'superadmin',
            'message': 'Authentication bypassed via alg: none!',
            'flag': 'CYBERPRO{bounty_jwt_alg_none_auth_bypass_662}'
        }
    return {'authenticated': False, 'error': 'Invalid cryptographic signature', 'code': 403}

def handle_vulnerable_profile(query_params, requesting_user_id):
    target_id = query_params.get('student_id', ['1'])[0]
    # Check IDOR: if target_id is 1 or 2 (superadmin / root)
    if str(target_id) in ['1', '2', '0']:
        return {
            'user_id': target_id,
            'name': 'Parth Rathod',
            'email': 'parth@cyberpro.in',
            'role': 'superadmin',
            'internal_clearance': 'LEVEL-5-TOP-SECRET',
            'flag': 'CYBERPRO{bounty_idor_broken_object_level_auth_441}',
            'note': 'IDOR Vulnerability Confirmed: Accessed superadmin profile without authorization check.'
        }
    return {
        'user_id': target_id,
        'name': f'Student #{target_id}',
        'email': f'student{target_id}@cyberpro.in',
        'role': 'student',
        'enrolled_track': 'CCCE Flagship'
    }

def handle_vulnerable_misconfig():
    return (
        "[core]\n"
        "\trepositoryformatversion = 0\n"
        "\tfilemode = false\n"
        "\tbare = false\n"
        "\tlogallrefupdates = true\n"
        "[remote \"origin\"]\n"
        "\turl = https://github.com/cyberpro-internal/core-lms-enterprise.git\n"
        "\tfetch = +refs/heads/*:refs/remotes/origin/*\n"
        "# LEAKED CREDENTIAL IN COMMIT HISTORY:\n"
        "# SECRET_FLAG=CYBERPRO{bounty_git_leak_exposed_env_secrets_903}\n"
    )

def handle_vulnerable_upload(filename, content_bytes):
    ext = filename.split('.')[-1].lower() if '.' in filename else ''
    if ext in ['php', 'phtml', 'py', 'sh', 'pl', 'jsp']:
        return {
            'status': 'uploaded',
            'stored_path': f"/uploads/shells/{filename}",
            'execution_url': f"/uploads/shells/{filename}",
            'flag': 'CYBERPRO{bounty_unrestricted_upload_rce_shell_299}',
            'message': 'File uploaded without server-side MIME type or extension restriction!'
        }
    return {
        'status': 'uploaded',
        'stored_path': f"/uploads/{filename}",
        'message': 'File uploaded safely.'
    }

def handle_vulnerable_register(data):
    if data.get('is_admin') is True or str(data.get('is_admin')).lower() == 'true':
        return {
            'success': True,
            'role': 'admin',
            'message': 'Privilege escalation via Mass Assignment confirmed!',
            'flag': 'CYBERPRO{bounty_mass_assignment_privilege_escalation_712}'
        }
    return {'success': True, 'role': 'student', 'message': 'Registered with standard student privileges.'}

def handle_vulnerable_debug(query_params):
    err = query_params.get('err', [''])[0]
    if err == 'trigger':
        return (
            "Traceback (most recent call last):\n"
            "  File \"/cyberpro/core/dispatcher.py\", line 44, in dispatch_request\n"
            "  File \"/cyberpro/core/db.py\", line 102, in connect_vault\n"
            "Exception: Simulated unhandled database exception.\n\n"
            "ENVIRONMENT DUMP (DEBUG=True):\n"
            "DB_HOST=10.0.0.4\n"
            "VAULT_MASTER_KEY=CYBERPRO{bounty_info_disclosure_debug_stacktrace_885}\n"
            "SESSION_SECRET=a8f93e183bc7492c"
        )
    return "Application running in DEBUG mode. Pass ?err=trigger to simulate unhandled exception."
