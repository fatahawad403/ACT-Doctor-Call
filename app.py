from flask import (
    Flask, render_template, request, redirect, url_for,
    session, send_from_directory, flash, jsonify, Response, render_template_string
)
import sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from werkzeug.utils import secure_filename
import json
import base64
import os
import secrets

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from pywebpush import webpush, WebPushException


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "doctor_call.db"
UPLOAD_FOLDER = BASE_DIR / "uploads"
STATIC_FOLDER = BASE_DIR / "static"

VAPID_PRIVATE_FILE = BASE_DIR / "vapid_private_key.pem"
VAPID_PUBLIC_FILE = BASE_DIR / "vapid_public_key.txt"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

ALLOWED_EXTENSIONS = {"pdf"}

# Admin sign-in is disabled until both values are configured on Render.
ADMIN_USERNAME = os.environ.get("ACT_ADMIN_USERNAME", "admin").strip().lower()
ADMIN_PASSWORD = os.environ.get("ACT_ADMIN_PASSWORD", "")
ADMIN_READY = bool(os.environ.get("SECRET_KEY") and len(ADMIN_PASSWORD) >= 12
                   and bool(ADMIN_USERNAME) and ADMIN_USERNAME not in ("insurance", "doctor"))


DEMO_USERS = {
    "insurance": {
        "password": "1234",
        "role": "insurance",
        "display_name": "Insurance Desk"
    },
    "doctor": {
        "password": "1234",
        "role": "doctor",
        "display_name": "Demo Doctor"
    },
}


def now_riyadh():
    return datetime.now(ZoneInfo("Asia/Riyadh")).strftime("%Y-%m-%d %H:%M:%S")


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_no TEXT NOT NULL,
                patient_ref TEXT,
                specialty TEXT NOT NULL,
                doctor TEXT NOT NULL,
                pdf_filename TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Sent',
                sent_at TEXT NOT NULL,
                opened_at TEXT,
                decided_at TEXT,
                decision TEXT
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                endpoint TEXT NOT NULL UNIQUE,
                subscription_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        con.execute("CREATE TABLE IF NOT EXISTS application_control (id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL)")
        con.execute("INSERT OR IGNORE INTO application_control(id,enabled,updated_at) VALUES(1,1,?)",(now_riyadh(),))
        con.execute("CREATE TABLE IF NOT EXISTS account_access(username TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1)")
        for name in DEMO_USERS:
            con.execute("INSERT OR IGNORE INTO account_access(username,enabled) VALUES(?,1)",(name,))
        con.execute("CREATE TABLE IF NOT EXISTS usage_totals(metric TEXT PRIMARY KEY, count INTEGER NOT NULL DEFAULT 0)")


def ensure_vapid_keys():
    if VAPID_PRIVATE_FILE.exists() and VAPID_PUBLIC_FILE.exists():
        return

    private_key = ec.generate_private_key(ec.SECP256R1())

    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    VAPID_PRIVATE_FILE.write_bytes(pem)

    public_key = private_key.public_key()
    numbers = public_key.public_numbers()

    raw_public = (
        b"\x04"
        + numbers.x.to_bytes(32, "big")
        + numbers.y.to_bytes(32, "big")
    )
    public_b64 = base64.urlsafe_b64encode(raw_public).rstrip(b"=").decode("ascii")
    VAPID_PUBLIC_FILE.write_text(public_b64, encoding="utf-8")


def vapid_public_key():
    ensure_vapid_keys()
    return VAPID_PUBLIC_FILE.read_text(encoding="utf-8").strip()


def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


def require_role(role):
    return session.get("role") == role


def usage_increment(metric):
    # Aggregate totals only: no IP address or patient information.
    with db() as con:
        con.execute("INSERT INTO usage_totals(metric,count) VALUES(?,1) ON CONFLICT(metric) DO UPDATE SET count=usage_totals.count+1",(metric,))


@app.before_request
def enforce_access():
    if request.endpoint in ("login", "logout", "static", "manifest", "service_worker"):
        return None
    role = session.get("role")
    if not role:
        return None
    if role == "admin":
        if not ADMIN_READY or session.get("username") != ADMIN_USERNAME:
            session.clear()
            return redirect(url_for("login"))
        return None
    if role not in ("doctor", "insurance") or session.get("username") != role:
        session.clear()
        return redirect(url_for("login"))
    with db() as con:
        row = con.execute("SELECT enabled FROM account_access WHERE username=?", (role,)).fetchone()
        state = con.execute("SELECT enabled FROM application_control WHERE id=1").fetchone()
    if not row or not row["enabled"]:
        session.clear()
        if request.path.startswith("/api/"):
            return jsonify({"error":"Account disabled by administrator"}), 403
        return redirect(url_for("login"))
    if not state or not state["enabled"]:
        if request.path.startswith("/api/"):
            return jsonify({"error":"ACT Doctor Call temporarily closed"}), 503
        return render_template_string("""<!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ACT Doctor Call paused</title><body style="font:16px/1.6 system-ui;background:#f5f7fb;color:#172033;text-align:center;padding:12vh 18px"><h1>ACT Doctor Call temporarily closed</h1><p>The administrator has paused access.</p><a href="/logout">Sign out</a></body></html>"""), 503
    return None


def send_case_push(case_id, case_no, specialty):
    ensure_vapid_keys()

    with db() as con:
        subscriptions = con.execute(
            "SELECT * FROM push_subscriptions WHERE username='doctor'"
        ).fetchall()

    payload = json.dumps({
        "title": "🔔 New Case - ACT Doctor Call",
        "body": f"{specialty} case {case_no} is waiting for review.",
        "url": f"/case/{case_id}/open",
        "case_id": case_id
    })

    dead_endpoints = []

    for row in subscriptions:
        try:
            subscription = json.loads(row["subscription_json"])

            webpush(
                subscription_info=subscription,
                data=payload,
                vapid_private_key=str(VAPID_PRIVATE_FILE),
                vapid_claims={
                    "sub": "mailto:act-doctor-call@example.com"
                },
                timeout=10,
            )
        except WebPushException as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in (404, 410):
                dead_endpoints.append(row["endpoint"])
        except Exception as exc:
            print("Push error:", exc)

    if dead_endpoints:
        with db() as con:
            con.executemany(
                "DELETE FROM push_subscriptions WHERE endpoint=?",
                [(endpoint,) for endpoint in dead_endpoints]
            )


@app.route("/")
def home():
    if session.get("role") == "insurance":
        return redirect(url_for("insurance_dashboard"))
    if session.get("role") == "doctor":
        return redirect(url_for("doctor_dashboard"))
    if session.get("role") == "admin":
        return redirect(url_for("admin_dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        user = DEMO_USERS.get(username)

        if ADMIN_READY and username == ADMIN_USERNAME and secrets.compare_digest(password, ADMIN_PASSWORD):
            session.clear()
            session["username"] = ADMIN_USERNAME
            session["role"] = "admin"
            session["display_name"] = "ACT Administrator"
            usage_increment("logins")
            return redirect(url_for("admin_dashboard"))

        if user and secrets.compare_digest(user["password"], password):
            with db() as con:
                account = con.execute("SELECT enabled FROM account_access WHERE username=?", (username,)).fetchone()
                state = con.execute("SELECT enabled FROM application_control WHERE id=1").fetchone()
            if not account or not account["enabled"] or not state or not state["enabled"]:
                flash("Your access is currently paused. Contact the administrator.")
            else:
                session.clear()
                session["username"] = username
                session["role"] = user["role"]
                session["display_name"] = user["display_name"]
                usage_increment("logins")
                return redirect(url_for("home"))
        else:
            flash("Invalid username or password.")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/insurance")
def insurance_dashboard():
    if not require_role("insurance"):
        return redirect(url_for("login"))

    with db() as con:
        cases = con.execute(
            "SELECT * FROM cases ORDER BY id DESC"
        ).fetchall()

    usage_increment("dashboard_views")
    return render_template("insurance.html", cases=cases)


@app.route("/insurance/new", methods=["POST"])
def new_case():
    if not require_role("insurance"):
        return redirect(url_for("login"))

    case_no = request.form.get("case_no", "").strip()
    patient_ref = request.form.get("patient_ref", "").strip()
    specialty = request.form.get("specialty", "").strip()
    doctor = request.form.get("doctor", "").strip()
    pdf = request.files.get("pdf")

    if not case_no or not specialty or not doctor or not pdf:
        flash("Please complete all required fields.")
        return redirect(url_for("insurance_dashboard"))

    if not allowed_file(pdf.filename):
        flash("Only PDF files are allowed.")
        return redirect(url_for("insurance_dashboard"))

    safe_name = secure_filename(pdf.filename)
    stamped_name = f"{int(datetime.now().timestamp())}_{safe_name}"
    save_path = UPLOAD_FOLDER / stamped_name
    pdf.save(save_path)

    with db() as con:
        cur = con.execute("""
            INSERT INTO cases
            (case_no, patient_ref, specialty, doctor, pdf_filename, status, sent_at)
            VALUES (?, ?, ?, ?, ?, 'Sent', ?)
        """, (
            case_no, patient_ref, specialty, doctor,
            stamped_name, now_riyadh()
        ))
        case_id = cur.lastrowid

    # Send push after the case is safely stored.
    send_case_push(case_id, case_no, specialty)

    flash("Case sent to doctor successfully.")
    return redirect(url_for("insurance_dashboard"))


@app.route("/doctor")
def doctor_dashboard():
    if not require_role("doctor"):
        return redirect(url_for("login"))

    with db() as con:
        cases = con.execute("""
            SELECT * FROM cases
            ORDER BY
                CASE
                    WHEN status='Sent' THEN 0
                    WHEN status='Opened' THEN 1
                    ELSE 2
                END,
                id DESC
        """).fetchall()

    usage_increment("dashboard_views")
    return render_template(
        "doctor.html",
        cases=cases,
        vapid_public_key=vapid_public_key()
    )


@app.route("/case/<int:case_id>/open")
def open_case(case_id):
    if session.get("role") not in {"doctor", "insurance"}:
        return redirect(url_for("login"))

    with db() as con:
        case = con.execute(
            "SELECT * FROM cases WHERE id=?",
            (case_id,)
        ).fetchone()

        if not case:
            return "Case not found", 404

        if session.get("role") == "doctor" and not case["opened_at"]:
            con.execute("""
                UPDATE cases
                SET status='Opened', opened_at=?
                WHERE id=?
            """, (now_riyadh(), case_id))

            case = con.execute(
                "SELECT * FROM cases WHERE id=?",
                (case_id,)
            ).fetchone()

    return render_template("case_view.html", case=case)


@app.route("/case/<int:case_id>/decision/<decision>", methods=["POST"])
def case_decision(case_id, decision):
    if not require_role("doctor"):
        return redirect(url_for("login"))

    if decision not in {"Accepted", "Rejected"}:
        return "Invalid decision", 400

    with db() as con:
        con.execute("""
            UPDATE cases
            SET status=?, decision=?, decided_at=?
            WHERE id=?
        """, (decision, decision, now_riyadh(), case_id))

    flash(f"Case {decision.lower()} successfully.")
    return redirect(url_for("doctor_dashboard"))


@app.route("/api/push/subscribe", methods=["POST"])
def push_subscribe():
    if not require_role("doctor"):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    subscription = request.get_json(silent=True)
    if not subscription or "endpoint" not in subscription:
        return jsonify({"ok": False, "error": "Invalid subscription"}), 400

    endpoint = subscription["endpoint"]

    with db() as con:
        con.execute("""
            INSERT INTO push_subscriptions
            (username, endpoint, subscription_json, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET
                username=excluded.username,
                subscription_json=excluded.subscription_json,
                created_at=excluded.created_at
        """, (
            session.get("username", "doctor"),
            endpoint,
            json.dumps(subscription),
            now_riyadh()
        ))

    return jsonify({"ok": True})


@app.route("/api/push/status")
def push_status():
    if not require_role("doctor"):
        return jsonify({"enabled": False}), 401

    with db() as con:
        count = con.execute(
            "SELECT COUNT(*) AS c FROM push_subscriptions WHERE username='doctor'"
        ).fetchone()["c"]

    return jsonify({"enabled": count > 0})


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    if not session.get("role"):
        return redirect(url_for("login"))

    return send_from_directory(
        app.config["UPLOAD_FOLDER"],
        filename
    )



ADMIN_HTML = """
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ACT Doctor Call · Admin Control</title><style>
*{box-sizing:border-box}body{margin:0;background:#f3f6fb;color:#18334f;font:15px/1.5 system-ui,Arial}
main{max-width:900px;margin:20px auto;padding:0 14px}header{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
section{background:white;border:1px solid #dfe6f0;border-radius:16px;padding:18px;margin:15px 0;box-shadow:0 3px 15px #182c4d10}
h1{font-size:25px;margin:0}h2{font-size:18px;margin-top:0}a{color:#1764ad;text-decoration:none}
form{display:inline-block}button{padding:11px 17px;border:0;border-radius:9px;background:#155ca5;color:white;cursor:pointer;font-weight:750}
button.danger{background:#b12e38}small{color:#667c91}.metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.metrics div{background:#eef4fc;border-radius:12px;padding:14px}.metrics strong{font-size:26px}
table{width:100%;border-collapse:collapse}td,th{padding:12px 8px;border-bottom:1px solid #e5edf3;text-align:left}
.alert{background:#fff3dd;color:#794d14;padding:12px;border-radius:10px}
@media(max-width:550px){.metrics{grid-template-columns:1fr}main{margin:8px auto}section{padding:14px}}
</style></head><body><main>
<section><header><h1>Doctor Call · Admin Control</h1><a href="/logout">Sign out</a></header>
<p><small>Control staff access from phone or desktop. Administrators retain access.</small></p>
{% if notice %}<p role="status" class="alert">{{ notice }}</p>{% endif %}
</section>
<section><h2>Application</h2><p>Status: <strong>{{ 'OPEN' if app_enabled else 'CLOSED' }}</strong></p>
<form method="post" action="/admin/access"><input type="hidden" name="csrf_token" value="{{ csrf_token }}">
<input type="hidden" name="enabled" value="{{ '0' if app_enabled else '1' }}">
<button class="{{ 'danger' if app_enabled else '' }}" type="submit">{{ 'Close for all staff' if app_enabled else 'Reopen for staff' }}</button></form>
</section>
<section><h2>User access</h2><div style="overflow-x:auto"><table><thead><tr><th>Account</th><th>Role</th><th>Status</th><th>Action</th></tr></thead>
<tbody>{% for u in accounts %}<tr><td>{{ u['username'] }}</td><td>{{ u['username']|title }}</td>
<td>{{ 'Enabled' if u['enabled'] else 'Disabled' }}</td><td>
<form method="post" action="/admin/user/{{ u['username'] }}/toggle">
<input type="hidden" name="csrf_token" value="{{ csrf_token }}">
<button type="submit">{{ 'Disable' if u['enabled'] else 'Enable' }}</button></form>
</td></tr>{% endfor %}</tbody></table></div></section>
<section><h2>Usage (from activation)</h2><div class="metrics">
<div>Sign-ins<br><strong>{{ usage.get('logins',0) }}</strong></div>
<div>Dashboard views<br><strong>{{ usage.get('dashboard_views',0) }}</strong></div>
<div>Reported installs<br><strong>{{ usage.get('installs',0) }}</strong></div></div>
<p><small>Install events are reported only on supported browsers, not app-store downloads.</small></p>
</section>
<section><p class="alert">Demo storage: SQLite settings and counters may reset after Render restarts or redeploys. Do not use real patient PDFs without persistent storage and secure staff authentication.</p></section>
</main></body></html>
"""


def admin_token():
    token = session.get("doctorcall_admin_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["doctorcall_admin_csrf"] = token
    return token


def admin_form_authorized():
    token = session.get("doctorcall_admin_csrf")
    return (require_role("admin") and ADMIN_READY and bool(token)
            and secrets.compare_digest(token, request.form.get("csrf_token", "")))


@app.route("/admin")
def admin_dashboard():
    if not require_role("admin") or not ADMIN_READY:
        return redirect(url_for("login"))
    with db() as con:
        accounts = con.execute("SELECT username,enabled FROM account_access ORDER BY username").fetchall()
        state = con.execute("SELECT enabled FROM application_control WHERE id=1").fetchone()
        rows = con.execute("SELECT metric,count FROM usage_totals").fetchall()
    notices = {"app_changed":"Application access updated.","user_changed":"Account status updated.","invalid":"Request rejected."}
    return render_template_string(ADMIN_HTML, accounts=accounts,
                                  app_enabled=bool(state and state["enabled"]),
                                  usage={r["metric"]:r["count"] for r in rows},
                                  csrf_token=admin_token(),
                                  notice=notices.get(request.args.get("notice"),""))


@app.route("/admin/access", methods=["POST"])
def admin_access():
    if not admin_form_authorized():
        return "Forbidden", 403
    enabled = request.form.get("enabled")
    if enabled not in ("0","1"):
        return redirect(url_for("admin_dashboard",notice="invalid"))
    with db() as con:
        con.execute("UPDATE application_control SET enabled=?,updated_at=? WHERE id=1",
                    (int(enabled),now_riyadh()))
    return redirect(url_for("admin_dashboard",notice="app_changed"))


@app.route("/admin/user/<username>/toggle", methods=["POST"])
def admin_toggle_user(username):
    if not admin_form_authorized():
        return "Forbidden", 403
    if username not in DEMO_USERS:
        return redirect(url_for("admin_dashboard",notice="invalid"))
    with db() as con:
        con.execute("UPDATE account_access SET enabled=CASE WHEN enabled=1 THEN 0 ELSE 1 END WHERE username=?", (username,))
    return redirect(url_for("admin_dashboard",notice="user_changed"))


@app.route("/api/usage/install", methods=["POST"])
def app_installed():
    if session.get("role") not in ("doctor","insurance"):
        return jsonify({"error":"Unauthorized"}), 401
    usage_increment("installs")
    return Response(status=204)


@app.route("/sw.js")
def service_worker():
    response = send_from_directory(
        STATIC_FOLDER,
        "sw.js",
        mimetype="application/javascript"
    )
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/manifest.json")
def manifest():
    return send_from_directory(
        STATIC_FOLDER,
        "manifest.json",
        mimetype="application/manifest+json"
    )


# Initialize folders, database, and VAPID keys for both local Flask
# and production servers such as Gunicorn/Render.
UPLOAD_FOLDER.mkdir(exist_ok=True)
STATIC_FOLDER.mkdir(exist_ok=True)
init_db()
ensure_vapid_keys()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
