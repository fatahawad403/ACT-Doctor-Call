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
