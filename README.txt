ACT Doctor Call - Demo v2 (Push Notification)
=============================================

WHAT CHANGED
------------
- Added Web Push Notifications.
- Doctor can register the phone/browser by pressing:
  "Enable Notifications".
- When Insurance sends a new case, the subscribed doctor's device receives a push.
- Tapping the notification opens the case directly.
- Notification text includes only Case No. + Specialty (no patient name).

LOCAL TEST
----------
1) Open Terminal inside this folder.
2) Create/activate venv if needed:
   py -m venv .venv
   .venv\Scripts\activate

3) Install:
   pip install -r requirements.txt

4) Run:
   py app.py

5) Open:
   http://127.0.0.1:5000

Demo accounts:
Insurance: insurance / 1234
Doctor:    doctor / 1234

IMPORTANT FOR PHONE TEST
------------------------
127.0.0.1 belongs only to the laptop.

To receive the notification on a phone, deploy this app to an HTTPS URL
(for example Render). Then:

A) Open the HTTPS URL on the doctor's phone.
B) Login as doctor / 1234.
C) Press "Enable Notifications".
D) Allow notifications when the browser asks.
E) From Insurance, send a NEW demo case.
F) The phone should receive a Web Push notification.
G) Tap it to open the case.

ANDROID
-------
Chrome/Chromium browsers can receive Web Push over HTTPS when notification
permission is granted. Notification sound/vibration follows the phone and
browser notification settings.

DEMO SAFETY
-----------
Use dummy/demo PDFs only.
Do not upload real patient information until production security,
authentication, storage, auditing, retention, and access controls are designed.

FILES CREATED AUTOMATICALLY
---------------------------
doctor_call.db
vapid_private_key.pem
vapid_public_key.txt

For a deployed demo, keep the same VAPID key pair. If the private key changes,
existing browser subscriptions must be registered again.
