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
                   and ADMIN_USERNAME not in ("insurance", "doctor"))


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
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        user = DEMO_USERS.get(username)

        if user and user["password"] == password:
            session["username"] = username
            session["role"] = user["role"]
            session["display_name"] = user["display_name"]
            return redirect(url_for("home"))

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
