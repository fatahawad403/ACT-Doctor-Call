ACT Doctor Call - Render Ready Demo v2.1
========================================

LOCAL LOGIN
-----------
Insurance: insurance / 1234
Doctor:    doctor / 1234

LOCAL RUN
---------
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
py app.py

RENDER DEPLOYMENT
-----------------
Upload the CONTENTS of this folder to the ROOT of a GitHub repository.

The repository root must contain:
- app.py
- requirements.txt
- render.yaml
- templates/
- static/
- uploads/

Render settings:
Build Command:
pip install -r requirements.txt

Start Command:
gunicorn app:app

After deployment:
1. Open the HTTPS Render URL on the doctor's phone.
2. Login: doctor / 1234
3. Tap "Enable Notifications".
4. Allow browser notifications.
5. Keep the phone/browser notification permission enabled.
6. From another browser/session login as insurance / 1234.
7. Send a NEW dummy PDF case.
8. The subscribed doctor's phone should receive the push notification.
9. Tap the notification to open the case directly.

IMPORTANT DEMO LIMITATIONS
--------------------------
- Use dummy/non-patient PDFs only.
- SQLite, uploaded PDFs, and generated VAPID keys are stored on the service filesystem.
  On a free/ephemeral deployment they can be lost after restart/redeploy.
- If VAPID keys change, the phone must press Enable Notifications again.
- This package is for proof-of-concept testing, not production medical data.

ADMINISTRATION (new)
--------------------
Set Render environment variables before using the administrator controls:
  SECRET_KEY = long randomly generated secret (32+ random bytes)
  ACT_ADMIN_USERNAME = admin (optional)
  ACT_ADMIN_PASSWORD = your own unique strong password, 12+ characters

No admin login is enabled without both SECRET_KEY and ACT_ADMIN_PASSWORD.
Do not use any of the demo credentials for the admin password.

After redeploy, open: https://YOUR-DOCTOR-CALL-SERVICE.onrender.com/admin
Sign in with the configured administrator credentials.
The admin can open/close the application for insurance and doctors, or enable/disable each demo account.
Existing signed-in staff are checked on each request and are blocked when access is revoked.

Usage includes successful sign-ins, dashboard views, and browser-reported PWA installs.
Counters start from feature rollout, not historical downloads. No personal visitor data is tracked.

CRITICAL: The current demo still has weak shared staff credentials (1234).
SQLite database, uploaded PDFs, and keys may be erased after Render restarts/redeploys.
Keep the app limited to dummy data until real staff authentication and persistent storage exist.
