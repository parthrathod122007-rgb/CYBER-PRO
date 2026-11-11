#!/usr/bin/env python3
"""Manage LMS accounts. There are NO default accounts: create one before anyone can sign in.

  python3 manage_users.py add student@example.com --name "Student Name"   (prompts for password)
  python3 manage_users.py list
  python3 manage_users.py passwd student@example.com
  python3 manage_users.py disable|enable|delete student@example.com
"""
import argparse, getpass, sys, time
import server as S

MIN_LEN = 8


def prompt_password():
    pw = getpass.getpass("Password: ")
    if len(pw) < MIN_LEN:
        sys.exit(f"Password must be at least {MIN_LEN} characters.")
    if pw != getpass.getpass("Repeat password: "):
        sys.exit("Passwords do not match.")
    return pw


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add"); a.add_argument("email"); a.add_argument("--name", default="")
    a.add_argument("--role", default="student", choices=["student", "admin", "instructor", "superuser"])
    a.add_argument("--password-stdin", action="store_true", help="read password from stdin (for scripting)")
    sub.add_parser("list")
    for c in ("passwd", "disable", "enable", "delete"):
        sub.add_parser(c).add_argument("email")
    args = ap.parse_args()
    S.init_db()
    email = getattr(args, "email", "").strip().lower()
    with S._db_lock, S.db() as con:
        if args.cmd == "add":
            if "@" not in email:
                sys.exit("Enter a valid email address.")
            pw = sys.stdin.readline().rstrip("\n") if args.password_stdin else prompt_password()
            if len(pw) < MIN_LEN:
                sys.exit(f"Password must be at least {MIN_LEN} characters.")
            try:
                con.execute("INSERT INTO users(email,name,password_hash,role,created_at) VALUES (?,?,?,?,?)",
                            (email, args.name.strip() or email.split("@")[0], S.hash_password(pw), args.role, int(time.time())))
            except Exception as e:
                sys.exit(f"Could not add user: {e}")
            print(f"Created {email} ({args.role})")
        elif args.cmd == "list":
            for r in con.execute("SELECT email,name,is_active,last_login FROM users ORDER BY id"):
                ll = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["last_login"])) if r["last_login"] else "never"
                print(f"{r['email']:40} {r['name']:20} {'active' if r['is_active'] else 'DISABLED':9} last login: {ll}")
        else:
            row = con.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
            if not row:
                sys.exit("No such user.")
            if args.cmd == "passwd":
                con.execute("UPDATE users SET password_hash=? WHERE id=?", (S.hash_password(prompt_password()), row["id"]))
                con.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))   # force re-login everywhere
            elif args.cmd in ("disable", "enable"):
                con.execute("UPDATE users SET is_active=? WHERE id=?", (1 if args.cmd == "enable" else 0, row["id"]))
                if args.cmd == "disable":
                    con.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
            elif args.cmd == "delete":
                con.execute("DELETE FROM users WHERE id=?", (row["id"],))
            print("Done.")


if __name__ == "__main__":
    main()
