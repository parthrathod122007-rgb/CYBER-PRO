"""
UNIVERSAL AI KNOWLEDGE & REASONING ENGINE FOR CYBERPRO AI
Provides ChatGPT / Claude / Gemini-style multi-domain intelligence:
  - Safe AST mathematics, percentages & unit conversions
  - Live current date / time / calendar queries
  - Worldwide factual & encyclopedia Q&A via Wikimedia Search & REST Extracts
  - Topic comparisons (X vs Y)
  - Coding & engineering mentor (Python, JavaScript, SQL, HTML/CSS, Bash)
  - Writing, drafting & study planning helper
  - Worldwide motivational & cybersecurity quotes
"""

import ast
import datetime
import json
import math
import operator
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

_WIKI_CACHE: Dict[str, Dict[str, str]] = {}
_MAX_CACHE = 200
_HEADERS = {"User-Agent": "CyberPro-AI-Assistant/2.0 (https://cyberpro.local; support@cyberpro.com)"}

# ---------------------------------------------------------------------------
# 1. SAFE AST MATH & UNIT CONVERTER
# ---------------------------------------------------------------------------
_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_MATH_FUNCS = {
    "sqrt": math.sqrt,
    "abs": abs,
    "round": round,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "ceil": math.ceil,
    "floor": math.floor,
    "pow": pow,
}
_MATH_CONSTS = {"pi": math.pi, "e": math.e}


def _eval_ast(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_ast(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.Name) and node.id in _MATH_CONSTS:
        return float(_MATH_CONSTS[node.id])
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return float(_UNARY_OPS[type(node.op)](_eval_ast(node.operand)))
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        right = _eval_ast(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("Exponent too large")
        return float(_BIN_OPS[type(node.op)](_eval_ast(node.left), right))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _MATH_FUNCS:
        args = [_eval_ast(a) for a in node.args]
        return float(_MATH_FUNCS[node.func.id](*args))
    raise ValueError("Unsupported math expression")


def try_math_or_units(user_msg: str) -> Optional[str]:
    low = user_msg.strip().lower()

    # Percentage query: "what is 18% of 4500"
    m_pct = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent)\s+of\s+(\d+(?:\.\d+)?)", low)
    if m_pct:
        pct = float(m_pct.group(1))
        base = float(m_pct.group(2))
        val = (pct / 100.0) * base
        nice = int(val) if val.is_integer() else round(val, 6)
        return f"**Calculation Result:**\n\n`{pct:g}%` of `{base:g}` = **{nice:,}**" if isinstance(nice, int) else f"**Calculation Result:**\n\n`{pct:g}%` of `{base:g}` = **{nice}**"

    # Common unit conversions
    m_conv = re.search(
        r"(-?\d+(?:\.\d+)?)\s*(km|kilometers?|mi|miles?|m|meters?|cm|inch|inches|kg|kilograms?|lb|lbs|pounds?|c|celsius|f|fahrenheit|gb|mb|tb|kb)\s+(?:to|in|into)\s+(km|kilometers?|mi|miles?|m|meters?|cm|inch|inches|kg|kilograms?|lb|lbs|pounds?|c|celsius|f|fahrenheit|gb|mb|tb|kb)\b",
        low,
    )
    if m_conv:
        val = float(m_conv.group(1))
        u1 = m_conv.group(2).rstrip("s")
        u2 = m_conv.group(3).rstrip("s")
        alias = {"kilometer": "km", "mile": "mi", "meter": "m", "inche": "inch", "kilogram": "kg", "pound": "lb", "celsiu": "c", "fahrenheit": "f"}
        u1 = alias.get(u1, u1)
        u2 = alias.get(u2, u2)
        out = None
        if {u1, u2} == {"km", "mi"}:
            out = val * 0.621371 if u1 == "km" else val / 0.621371
        elif {u1, u2} == {"cm", "inch"}:
            out = val / 2.54 if u1 == "cm" else val * 2.54
        elif {u1, u2} == {"m", "cm"}:
            out = val * 100.0 if u1 == "m" else val / 100.0
        elif {u1, u2} == {"km", "m"}:
            out = val * 1000.0 if u1 == "km" else val / 1000.0
        elif {u1, u2} == {"kg", "lb"}:
            out = val * 2.20462 if u1 == "kg" else val / 2.20462
        elif {u1, u2} == {"c", "f"}:
            out = (val * 9.0 / 5.0 + 32.0) if u1 == "c" else ((val - 32.0) * 5.0 / 9.0)
        elif u1 in ("kb", "mb", "gb", "tb") and u2 in ("kb", "mb", "gb", "tb"):
            scale = {"kb": 1, "mb": 1024, "gb": 1024 ** 2, "tb": 1024 ** 3}
            out = val * scale[u1] / scale[u2]
        if out is not None:
            return f"**Unit Conversion:**\n\n`{val:g} {u1.upper()}` = **{round(out, 4):g} {u2.upper()}**"

    # Arithmetic expression
    cleaned = re.sub(
        r"^(?:what\s+is|whats|what's|calculate|compute|solve|evaluate|find|=)\s+",
        "",
        low,
    ).rstrip("?=. ")
    cleaned = cleaned.replace("^", "**").replace("×", "*").replace("÷", "/").replace(",", "")
    if re.search(r"[\d)]\s*[+\-*/%]\s*[\d(]|(?:sqrt|sin|cos|tan|log|pow|abs|round)\(", cleaned):
        if re.fullmatch(r"[0-9a-zA-Z_+\-*/%().,\s]+", cleaned):
            try:
                tree = ast.parse(cleaned, mode="eval")
                ans = _eval_ast(tree)
                if math.isfinite(ans):
                    fmt = f"{int(ans):,}" if ans.is_integer() and abs(ans) < 1e15 else f"{round(ans, 8):g}"
                    return f"**Calculation Result:**\n\n`{cleaned}` = **{fmt}**"
            except Exception:
                pass
    return None


# ---------------------------------------------------------------------------
# 2. DATE, TIME & CALENDAR HELPER
# ---------------------------------------------------------------------------
def try_datetime_query(user_msg: str) -> Optional[str]:
    low = user_msg.lower().strip()
    is_date_q = bool(re.search(r"\b(?:what(?:'s| is)?\s+(?:the\s+)?date|today'?s?\s+date|date\s+(?:is\s+)?today|current\s+date|what\s+day\s+(?:is\s+(?:it|today))|what\s+year\s+is\s+it)\b", low))
    is_time_q = bool(re.search(r"\b(?:what(?:'s| is)?\s+(?:the\s+)?time(?:\s+now|\s+is\s+it)?|current\s+time)\b", low))
    if is_date_q or is_time_q or re.fullmatch(r"(?:what\s+is\s+the\s+)?(?:date|time|day)(?:\s+today)?\??", low):
        now = datetime.datetime.now()
        return (
            "**Current Date & Time:**\n\n"
            f"• **Date:** {now.strftime('%A, %B %d, %Y')}\n"
            f"• **Local Server Time:** {now.strftime('%I:%M %p')}"
        )
    return None


# ---------------------------------------------------------------------------
# 3. WORLDWIDE & CYBERSECURITY QUOTES
# ---------------------------------------------------------------------------
_QUOTES = [
    ("Knowledge", "An investment in knowledge pays the best interest.", "Benjamin Franklin"),
    ("Innovation", "The best way to predict the future is to invent it.", "Alan Kay"),
    ("Science", "Imagination is more important than knowledge. Knowledge is limited; imagination encircles the world.", "Albert Einstein"),
    ("Cybersecurity", "Security is always excessive until it's not enough.", "Robbie Sinclair"),
    ("Persistence", "It always seems impossible until it's done.", "Nelson Mandela"),
    ("Engineering", "Talk is cheap. Show me the code.", "Linus Torvalds"),
    ("Defense", "Amateurs hack systems, professionals safeguard architectures through continuous learning.", "CYBERPRO Defense Principle"),
    ("Computing", "Sometimes it is the people no one can imagine anything of who do the things no one can imagine.", "Alan Turing"),
]


def try_quotes_query(user_msg: str) -> Optional[str]:
    low = user_msg.lower()
    if any(k in low for k in ("quote", "quotes", "quotation", "inspirational", "motivational", "thought of the day")):
        idx = abs(hash(user_msg + datetime.datetime.now().strftime("%H%M"))) % len(_QUOTES)
        picks = [_QUOTES[(idx + i) % len(_QUOTES)] for i in range(3)]
        lines = "\n".join(f"> *\"{q}\"* — **{author}** ({tag})" for tag, q, author in picks)
        return (
            "**Selected Quotes:**\n\n"
            f"{lines}\n\n"
            "Want quotes on a specific topic like **cybersecurity**, **programming**, **leadership**, or **study motivation**?"
        )
    return None


# ---------------------------------------------------------------------------
# 4. CODING & WRITING MENTOR
# ---------------------------------------------------------------------------
def try_coding_assistant(user_msg: str, active_mode: str) -> Optional[str]:
    if active_mode == "cyberpro":
        return None
    low = user_msg.lower()
    is_code_ask = any(
        w in low for w in (
            "write a ", "write script", "write python", "write javascript", "write code",
            "code for", "program for", "function to", "example of", "how to code",
            "snippet", "regex", "sql query", "debug", "fix code", "algorithm",
            "in python", "in javascript", "in bash", "in sql", "in html", "in css", "in c++", "in java"
        )
    )
    code_topics = (
        "fibonacci", "palindrome", "binary search", "bubble sort", "quicksort", "prime number",
        "hello world", "factorial", "reverse string", "sorting", "rest api", "flask", "fastapi",
        "git ", "docker", "regex", "scrypt", "bcrypt", "xss", "sql injection", "sqli", "parameterized",
        "recursion", "recursive", "oop", "object oriented", "list vs tuple", "tuple", "sql join", "inner join",
        "try except", "exception", "error handling", "decorator", "loop", "json", "promise"
    )
    if not is_code_ask and active_mode != "code" and not any(k in low for k in code_topics):
        return None

    # In ethical_hacking mode, only serve security-relevant code patterns
    if active_mode == "ethical_hacking":
        sec_code_keys = ("sql", "database", "select", "bash", "linux", "port", "hash", "scrypt", "password", "auth", "sanitize", "csp", "xss", "validate", "regex", "socket", "nmap")
        if not any(k in low for k in sec_code_keys):
            return None

    sec_note = (
        "\n\n**Security Best Practice:** Validate and bound untrusted inputs, avoid raw string concatenation in queries or shell commands, and sanitize outputs before rendering."
        if active_mode in ("code", "ethical_hacking")
        else ""
    )

    if "recursion" in low or "recursive" in low:
        return (
            "**Recursion in Python (with Base Case Guard):**\n\n"
            "```python\ndef countdown(n: int) -> list[int]:\n    if n <= 0:  # Base case prevents infinite recursion\n        return []\n    return [n] + countdown(n - 1)\n\nprint(countdown(5))  # [5, 4, 3, 2, 1]\n```"
            + sec_note
        )
    if "oop" in low or "object oriented" in low or "class " in low:
        return (
            "**Object-Oriented Programming Pattern (Python Class):**\n\n"
            "```python\nclass SecurityAlert:\n    def __init__(self, source_ip: str, severity: str) -> None:\n        self.source_ip = source_ip\n        self.severity = severity\n\n    def summary(self) -> str:\n        return f\"[{self.severity.upper()}] Alert from {self.source_ip}\"\n```"
        )
    if "tuple" in low and "list" in low:
        return (
            "**Python `list` vs `tuple`:**\n\n"
            "• **`list` (`[1, 2, 3]`):** Mutable (items can be appended, removed, modified); slightly more memory overhead.\n"
            "• **`tuple` (`(1, 2, 3)`):** Immutable fixed sequence; faster, hashable (usable as `dict` keys), safer for constants."
        )
    if "join" in low and "sql" in low:
        return (
            "**SQL `INNER JOIN` vs `LEFT JOIN`:**\n\n"
            "```sql\n-- INNER JOIN returns only matching rows in both tables\n"
            "SELECT u.name, e.course FROM users u INNER JOIN enrollments e ON u.id = e.user_id;\n\n"
            "-- LEFT JOIN keeps all left rows even without a match on the right\n"
            "SELECT u.name, e.course FROM users u LEFT JOIN enrollments e ON u.id = e.user_id;\n```"
        )
    if "exception" in low or "try except" in low or "error handling" in low:
        return (
            "**Clean Error Handling Pattern (Python):**\n\n"
            "```python\nimport json\n\ndef parse_config(raw: str) -> dict:\n    try:\n        return json.loads(raw)\n    except json.JSONDecodeError as exc:\n        raise ValueError(f'Invalid JSON config: {exc.msg}') from exc\n```"
        )
    if "decorator" in low:
        return (
            "**Python Decorator Pattern (Timing / Logging Wrapper):**\n\n"
            "```python\nimport functools, time\n\ndef timed(fn):\n    @functools.wraps(fn)\n    def wrapper(*args, **kwargs):\n        t0 = time.perf_counter()\n        try:\n            return fn(*args, **kwargs)\n        finally:\n            print(f'{fn.__name__} took {(time.perf_counter() - t0)*1000:.1f}ms')\n    return wrapper\n```"
        )
    if "fibonacci" in low:
        return (
            "**Fibonacci Sequence (Python — Iterative O(n)):**\n\n"
            "```python\ndef fibonacci(n: int) -> list[int]:\n    seq = []\n    a, b = 0, 1\n    for _ in range(n):\n        seq.append(a)\n        a, b = b, a + b\n    return seq\n\nprint(fibonacci(10))\n```"
            + sec_note
        )
    if "factorial" in low:
        return (
            "**Factorial Function (Python — Iterative with Validation):**\n\n"
            "```python\ndef factorial(n: int) -> int:\n    if n < 0:\n        raise ValueError('Negative numbers do not have a factorial')\n    result = 1\n    for i in range(2, n + 1):\n        result *= i\n    return result\n\nprint(factorial(5))  # 120\n```"
            + sec_note
        )
    if "reverse" in low and "string" in low:
        return (
            "**Reverse a String (Python & JavaScript):**\n\n"
            "```python\ntext = 'cyberpro'\nprint(text[::-1])  # orprebyc\n```\n\n"
            "```javascript\nconst reverseStr = (s) => [...s].reverse().join('');\nconsole.log(reverseStr('cyberpro'));\n```"
        )
    if "palindrome" in low:
        return (
            "**Palindrome Check (Python):**\n\n"
            "```python\ndef is_palindrome(text: str) -> bool:\n    clean = ''.join(ch.lower() for ch in text if ch.isalnum())\n    return clean == clean[::-1]\n\nprint(is_palindrome('A man, a plan, a canal: Panama'))\n```"
            + sec_note
        )
    if "prime" in low:
        return (
            "**Prime Number Check (Python — Optimized √n):**\n\n"
            "```python\ndef is_prime(n: int) -> bool:\n    if n < 2:\n        return False\n    for d in range(2, int(n ** 0.5) + 1):\n        if n % d == 0:\n            return False\n    return True\n```"
            + sec_note
        )
    if "binary search" in low:
        return (
            "**Binary Search (Python — O(log n)):**\n\n"
            "```python\ndef binary_search(arr: list[int], target: int) -> int:\n    lo, hi = 0, len(arr) - 1\n    while lo <= hi:\n        mid = (lo + hi) // 2\n        if arr[mid] == target:\n            return mid\n        if arr[mid] < target:\n            lo = mid + 1\n        else:\n            hi = mid - 1\n    return -1\n```"
            + sec_note
        )
    if "sort" in low or "bubble" in low or "quick" in low:
        return (
            "**Sorting in Python (Built-in Timsort & Custom Algorithm):**\n\n"
            "```python\nnums = [42, 7, 19, 3, 25]\n# Production O(n log n) Timsort:\nsorted_nums = sorted(nums)\n\n# Bubble sort example (educational):\ndef bubble_sort(arr: list[int]) -> list[int]:\n    a = arr[:]\n    for i in range(len(a)):\n        for j in range(0, len(a) - i - 1):\n            if a[j] > a[j + 1]:\n                a[j], a[j + 1] = a[j + 1], a[j]\n    return a\n```"
        )
    if "hash" in low or "scrypt" in low or "password" in low:
        return (
            "**Secure Password Hashing (Python Built-in `hashlib.scrypt`):**\n\n"
            "```python\nimport hashlib, hmac, os\n\ndef hash_password(password: str) -> str:\n    salt = os.urandom(16)\n    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)\n    return f\"{salt.hex()}:{digest.hex()}\"\n\ndef verify_password(password: str, stored: str) -> bool:\n    salt_hex, digest_hex = stored.split(':')\n    check = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=16384, r=8, p=1)\n    return hmac.compare_digest(check.hex(), digest_hex)\n```"
        )
    if "regex" in low or "email validation" in low:
        return (
            "**Safe Input Validation Regex (Python):**\n\n"
            "```python\nimport re\n\nEMAIL_RE = re.compile(r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}$')\ndef is_valid_email(value: str) -> bool:\n    return bool(EMAIL_RE.fullmatch(value.strip()))\n```"
            + sec_note
        )
    if "sql" in low or "database" in low or "select" in low:
        return (
            "**Safe Parameterized SQL Query (Prevents SQL Injection):**\n\n"
            "```python\nimport sqlite3\n\ndef find_user_by_email(con: sqlite3.Connection, email: str):\n    # Never concatenate user strings into SQL — always bind parameters:\n    cur = con.execute('SELECT id, full_name, email FROM users WHERE email = ?', (email,))\n    return cur.fetchone()\n```"
        )
    if "api" in low or "fastapi" in low or "flask" in low or "server" in low:
        return (
            "**Lightweight JSON API Endpoint Pattern (Python FastAPI / Standard HTTP):**\n\n"
            "```python\nfrom fastapi import FastAPI, HTTPException\nfrom pydantic import BaseModel\n\napp = FastAPI()\n\nclass Item(BaseModel):\n    name: str\n\n@app.post('/items')\ndef create_item(item: Item):\n    if not item.name.strip():\n        raise HTTPException(status_code=400, detail='Name is required')\n    return {'ok': True, 'name': item.name.strip()}\n```"
            + sec_note
        )
    if "javascript" in low or "fetch" in low or " js " in low or "async" in low:
        return (
            "**Modern JavaScript Async API Fetch (with Error Handling):**\n\n"
            "```javascript\nasync function fetchJson(url) {\n  const res = await fetch(url, { headers: { 'Accept': 'application/json' } });\n  if (!res.ok) throw new Error(`HTTP ${res.status}`);\n  return res.json();\n}\n```"
            + sec_note
        )
    if "html" in low or "form" in low or "login" in low:
        return (
            "**Clean Accessible HTML Form Pattern:**\n\n"
            "```html\n<form method=\"POST\" action=\"/submit\">\n  <label for=\"email\">Email</label>\n  <input id=\"email\" name=\"email\" type=\"email\" required autocomplete=\"email\">\n  <button type=\"submit\">Submit</button>\n</form>\n```"
            + sec_note
        )
    if "css" in low or "glass" in low or "flex" in low or "grid" in low:
        return (
            "**Responsive Glassmorphism Card & Grid Pattern (CSS):**\n\n"
            "```css\n.glass-card {\n  background: rgba(14, 28, 20, 0.72);\n  backdrop-filter: blur(16px) saturate(160%);\n  border: 1px solid rgba(255, 255, 255, 0.14);\n  border-radius: 18px;\n  padding: 20px;\n}\n```"
        )
    if "git " in low or "git" == low.strip():
        return (
            "**Essential Git Workflow Commands:**\n\n"
            "```bash\ngit status\ngit checkout -b feature/my-change\ngit add -p\ngit commit -m \"Implement feature concisely\"\ngit diff --stat\n```"
        )
    if "docker" in low:
        return (
            "**Clean Multi-Stage Python Dockerfile:**\n\n"
            "```dockerfile\nFROM python:3.12-slim\nWORKDIR /app\nCOPY requirements.txt .\nRUN pip install --no-cache-dir -r requirements.txt\nCOPY . .\nUSER nobody\nCMD [\"python\", \"server.py\"]\n```"
        )
    if "bash" in low or "linux" in low or "port" in low:
        return (
            "**Useful Linux / Bash Defensive Checks:**\n\n"
            "```bash\n# Inspect listening TCP/UDP sockets safely\nss -tulnp\n\n# Check recent failed auth logs (Debian/Ubuntu)\ngrep \"Failed password\" /var/log/auth.log | tail -n 20\n```"
            + sec_note
        )
    if "hello world" in low or "python" in low:
        return (
            "**Clean Python Starter & CLI Pattern:**\n\n"
            "```python\ndef main() -> None:\n    print('Hello from clean Python 3!')\n\nif __name__ == '__main__':\n    main()\n```"
        )
    return None


def try_writing_assistant(user_msg: str) -> Optional[str]:
    low = user_msg.lower()
    if any(k in low for k in ("write an email", "write email", "draft email", "leave application", "cover letter", "resume summary")):
        return (
            "**Draft Template:**\n\n"
            "```\nSubject: Follow-Up / Inquiry Regarding [Topic / Opportunity]\n\n"
            "Dear [Name / Hiring Manager],\n\n"
            "I hope you are doing well. I am writing to share a concise update regarding [insert specific goal or role]. "
            "With practical experience in [key skill #1] and [key skill #2], I am confident I can contribute immediately.\n\n"
            "Please let me know a convenient time to connect this week.\n\n"
            "Best regards,\n[Your Name]\n[Contact Info]\n```\n\n"
            "Share the specific recipient, tone (formal/friendly), or details you'd like included and I will customize this draft for you."
        )
    return None


# ---------------------------------------------------------------------------
# 5. LIVE WORLDWIDE ENCYCLOPEDIA ENGINE (WIKIMEDIA SEARCH + SUMMARY API)
# ---------------------------------------------------------------------------
_NOISE_PREFIXES = (
    r"can you (?:please )?(?:tell me|explain|define|describe|summarize|give me(?: info(?:rmation)?(?: about| on)?)?)\s+",
    r"(?:could|would) you (?:please )?(?:tell me|explain|define|describe|summarize)\s+",
    r"(?:please )?(?:tell me|explain|define|describe|summarize)\s+(?:all\s+)?(?:about|regarding|how|what|why|who)\s+",
    r"(?:what|who|where|when|why|how)\s+(?:is|are|was|were|did|does|do|can|means?)\s+(?:a|an|the)?\s*",
    r"(?:who|what)\s+(?:invented|discovered|created|founded|built|wrote|won)\s+(?:the\s+)?",
    r"(?:founder|owner|ceo|capital|population|currency|president|prime minister|history|meaning|definition|summary|overview|facts)\s+(?:of|for|about|on)\s+(?:the\s+)?",
)

_TECH_ALIASES = {
    "python": "Python (programming language)",
    "java": "Java (programming language)",
    "c": "C (programming language)",
    "ruby": "Ruby (programming language)",
    "rust": "Rust (programming language)",
    "go": "Go (programming language)",
    "golang": "Go (programming language)",
    "react": "React (software)",
}


def _extract_search_query(user_msg: str) -> str:
    q = user_msg.strip().rstrip("?!. ")
    for _ in range(2):
        for pat in _NOISE_PREFIXES:
            q = re.sub("^" + pat, "", q, flags=re.IGNORECASE).strip()
    q = re.sub(r"^(?:the|a|an)\s+", "", q, flags=re.IGNORECASE).strip()
    return _TECH_ALIASES.get(q.lower(), q) or user_msg.strip()


def _http_get_json(url: str, timeout: float = 3.5) -> Optional[dict]:
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def lookup_wikipedia(raw_query: str) -> Optional[Dict[str, str]]:
    clean = _extract_search_query(raw_query)
    cache_key = clean.lower()
    if cache_key in _WIKI_CACHE:
        return _WIKI_CACHE[cache_key]

    candidates: List[str] = []
    # Exact canonical title first if concise query (e.g. Microsoft, Tokyo, Quantum computing)
    if len(clean.split()) <= 4:
        candidates.append(clean)

    # Also search full original question phrasing for entity lookup ("founder of Microsoft", "capital of Japan")
    search_q = clean if len(raw_query.split()) <= 2 else raw_query.strip().rstrip("?!. ")
    search_url = (
        "https://en.wikipedia.org/w/api.php?action=query&list=search&utf8=1&format=json&srlimit=4&srsearch="
        + urllib.parse.quote(search_q)
    )
    sdata = _http_get_json(search_url)
    if sdata:
        for item in sdata.get("query", {}).get("search", []):
            title = item.get("title", "")
            if title and "(disambiguation)" not in title.lower() and title not in candidates:
                candidates.append(title)

    # Pull rich lead extract via MediaWiki Extracts API (captures founders, dates, capitals & core details)
    for title in candidates[:4]:
        extract_url = (
            "https://en.wikipedia.org/w/api.php?action=query&prop=extracts|description|info"
            "&inprop=url&exintro=1&explaintext=1&exchars=1500&redirects=1&format=json&titles="
            + urllib.parse.quote(title)
        )
        edata = _http_get_json(extract_url)
        pages = edata.get("query", {}).get("pages", {}) if edata else {}
        page = next(iter(pages.values()), {}) if pages else {}
        extract = re.sub(r"\s*\n+\s*", "\n\n", str(page.get("extract", "")).strip())
        if len(extract) < 40 or "may refer to:" in extract[:120].lower():
            continue
        desc = str(page.get("description", "")).strip()
        page_url = str(page.get("fullurl", ""))
        result = {
            "title": str(page.get("title", title)),
            "description": desc,
            "extract": extract,
            "url": page_url,
        }
        if len(_WIKI_CACHE) >= _MAX_CACHE:
            _WIKI_CACHE.pop(next(iter(_WIKI_CACHE)))
        _WIKI_CACHE[cache_key] = result
        return result

    return None


def try_comparison_query(user_msg: str) -> Optional[str]:
    m = re.search(
        r"(?:difference\s+between|compare)\s+(.+?)\s+(?:and|vs\.?|versus)\s+(.+?)[?.!]*$",
        user_msg.strip(),
        flags=re.IGNORECASE,
    )
    if not m:
        m = re.search(r"^(.+?)\s+(?:vs\.?|versus)\s+(.+?)[?.!]*$", user_msg.strip(), flags=re.IGNORECASE)
    if not m:
        return None

    left = m.group(1).strip(" ?.,")
    right = m.group(2).strip(" ?.,")
    if len(left) < 2 or len(right) < 2 or len(left) > 60 or len(right) > 60:
        return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(lookup_wikipedia, left)
        f2 = pool.submit(lookup_wikipedia, right)
        r1 = f1.result()
        r2 = f2.result()

    if not r1 and not r2:
        return None

    parts = [f"**Comparison: {r1['title'] if r1 else left} vs. {r2['title'] if r2 else right}**\n"]
    if r1:
        parts.append(f"• **{r1['title']}** ({r1.get('description') or 'Overview'}): {r1['extract'][:420]}")
    if r2:
        parts.append(f"• **{r2['title']}** ({r2.get('description') or 'Overview'}): {r2['extract'][:420]}")
    parts.append("\nAsk a follow-up if you want examples, practical tradeoffs, or a deeper breakdown!")
    return "\n\n".join(parts)


def _is_computing_or_security_topic(text: str) -> bool:
    low = text.lower()
    return any(
        k in low for k in (
            "computer", "software", "network", "security", "protocol", "internet",
            "algorithm", "programming", "encryption", "web", "linux", "server",
            "database", "cyber", "hack", "exploit", "vulnerability", "firewall",
            "penetration test", "malware", "phishing", "owasp", "cryptography"
        )
    )


def _is_ethical_hacking_topic(text: str) -> bool:
    low = text.lower()
    return any(
        k in low for k in (
            "cyber", "security", "hack", "exploit", "vulnerability", "pentest", "penetration",
            "malware", "ransomware", "phishing", "firewall", "owasp", "encryption", "cryptography",
            "network", "protocol", "tcp", "dns", "http", "ssl", "tls", "vpn", "siem", "splunk",
            "wireshark", "nmap", "burp", "metasploit", "kali", "linux", "active directory",
            "forensic", "zero trust", "authentication", "authorization", "buffer overflow", "xss",
            "sql injection", "kerberos", "osint", "social engineering", "reverse shell", "honeypot",
            "botnet", "trojan", "keylogger", "cidr", "subnet", "vlan", "ipsec", "ssh", "smb", "ldap",
            "cve", "zero-day", "zero day", "hydra", "aircrack", "snort", "suricata", "wazuh", "mitre",
            "privilege escalation", "idor", "ssrf", "csrf", "xxe", "jwt", "arp", "ddos", "dos attack"
        )
    )


def _is_coding_topic(text: str) -> bool:
    low = text.lower()
    return any(
        k in low for k in (
            "programming", "software", "code", "algorithm", "data structure", "compiler",
            "python", "javascript", "typescript", "java", "c++", "rust", "golang", "html",
            "css", "sql", "nosql", "database", "api", "rest", "graphql", "git", "docker",
            "kubernetes", "framework", "library", "function", "object-oriented", "linux",
            "bash", "regex", "recursion", "polymorphism", "inheritance", "django", "fastapi",
            "react", "vue", "node.js", "json", "yaml", "http", "async", "concurrency", "oop"
        )
    )


def try_world_knowledge(user_msg: str, active_mode: str, mode_enrichment: str = "") -> Optional[str]:
    if active_mode == "cyberpro":
        return None
    if active_mode == "ethical_hacking" and not _is_ethical_hacking_topic(user_msg):
        return None
    if active_mode == "code" and not _is_coding_topic(user_msg):
        return None

    clean_words = re.findall(r"[a-zA-Z0-9]+", user_msg)
    if not clean_words:
        return None

    # First check direct X vs Y comparison
    comp = try_comparison_query(user_msg)
    if comp:
        return comp + (mode_enrichment if active_mode != "worldwide" else "")

    hit = lookup_wikipedia(user_msg)
    if not hit:
        return None

    hit_text = f"{hit['title']} {hit.get('description', '')} {hit['extract']}"
    if active_mode == "ethical_hacking" and not _is_ethical_hacking_topic(hit_text):
        return None
    if active_mode == "code" and not _is_coding_topic(hit_text):
        return None

    desc_line = f"*{hit['description']}*\n\n" if hit.get("description") else ""
    body = hit["extract"]
    if len(body) > 1350:
        cut = body[:1350].rsplit(". ", 1)[0]
        body = (cut + ".") if len(cut) > 250 else body[:1350] + "..."

    ref_line = f"\n\nReference: [{hit['title']} — Wikipedia]({hit['url']})" if hit.get("url") else ""
    return (
        f"**{hit['title']}**\n"
        f"{desc_line}"
        f"{body}"
        f"{ref_line}"
        f"{mode_enrichment if _is_computing_or_security_topic(hit_text) else ''}"
    )


def answer_universal_query(user_msg: str, history: List[Dict[str, str]], active_mode: str, mode_enrichment: str = "") -> Optional[str]:
    """Tries deterministic math/date/quotes/coding/writing, then worldwide encyclopedia lookup."""
    for fn in (try_datetime_query, try_math_or_units, try_quotes_query, try_writing_assistant):
        res = fn(user_msg)
        if res:
            return res

    code_res = try_coding_assistant(user_msg, active_mode)
    if code_res:
        return code_res

    world_res = try_world_knowledge(user_msg, active_mode, mode_enrichment=mode_enrichment)
    if world_res:
        return world_res

    return None
