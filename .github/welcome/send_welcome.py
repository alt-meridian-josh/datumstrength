"""Send the Datum Strength welcome email to new waitlist signups.

Runs from .github/workflows/welcome.yml. Standard library only.
Logs row ids only, never email addresses: this repo's Actions logs are public.

Env:
  SUPABASE_URL                 project URL (not secret)
  SUPABASE_SERVICE_ROLE_KEY    repo secret (service_role or sb_secret key)
  GMAIL_ADDRESS                repo secret, the sending Gmail address
  GMAIL_APP_PASSWORD           repo secret, a Gmail app password
  SEND_ENABLED                 "true" lets scheduled runs email the list
  MODE                         "run" (default) or "test" (one email to GMAIL_ADDRESS, no table changes)
"""
import json, os, smtplib, ssl, sys, urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

HERE = os.path.dirname(os.path.abspath(__file__))
SUBJECT = "You're on the Datum Strength waitlist"
FROM_NAME = "Datum Strength"
BATCH = 20

url = os.environ.get("SUPABASE_URL", "").rstrip("/")
key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
gmail = os.environ.get("GMAIL_ADDRESS", "")
app_pw = os.environ.get("GMAIL_APP_PASSWORD", "")
mode = os.environ.get("MODE", "run") or "run"
enabled = os.environ.get("SEND_ENABLED", "false").lower() == "true"


def api(method, path, body=None, prefer=None):
    headers = {"apikey": key, "Content-Type": "application/json"}
    if key.startswith("eyJ"):  # legacy service_role JWT
        headers["Authorization"] = "Bearer " + key
    if prefer:
        headers["Prefer"] = prefer
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url + "/rest/v1/" + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def build(to_addr):
    msg = EmailMessage()
    msg["Subject"] = SUBJECT
    msg["From"] = formataddr((FROM_NAME, gmail))
    msg["To"] = to_addr
    msg["Reply-To"] = gmail
    msg["Message-ID"] = make_msgid(domain="datumstrength.com")
    with open(os.path.join(HERE, "welcome.txt"), encoding="utf-8") as f:
        msg.set_content(f.read())
    with open(os.path.join(HERE, "welcome.html"), encoding="utf-8") as f:
        msg.add_alternative(f.read(), subtype="html")
    return msg


def main():
    if mode not in ("run", "test"):
        sys.exit(f"Unknown MODE {mode!r}")
    if not (gmail and app_pw):
        print("::notice::GMAIL_ADDRESS or GMAIL_APP_PASSWORD secret not set; nothing sent.")
        return
    if mode == "test":
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
            s.login(gmail, app_pw)
            s.send_message(build(gmail))
        print("Test email sent to the sending Gmail address. Table not touched.")
        return
    if not (url and key):
        print("::notice::SUPABASE_SERVICE_ROLE_KEY secret not set; nothing sent.")
        return

    pending = api("GET", f"waitlist?select=id,email&welcomed_at=is.null&order=id.asc&limit={BATCH}")
    print(f"Pending signups: {len(pending)}")
    if not pending:
        return
    if not enabled:
        print("SEND_ENABLED is not true; checked only, nothing sent.")
        return

    sent = failed = 0
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(gmail, app_pw)
        for row in pending:
            rid = row["id"]
            now = datetime.now(timezone.utc).isoformat()
            # Claim the row first so an overlapping run can't send it twice.
            claimed = api("PATCH", f"waitlist?id=eq.{rid}&welcomed_at=is.null",
                          {"welcomed_at": now}, prefer="return=representation")
            if not claimed:
                continue
            try:
                s.send_message(build(row["email"]))
                sent += 1
                print(f"Sent: row {rid}")
            except Exception as e:  # unclaim so the next run retries
                failed += 1
                api("PATCH", f"waitlist?id=eq.{rid}", {"welcomed_at": None})
                print(f"::warning::Row {rid} not sent ({type(e).__name__}); will retry next run.")
    print(f"Done. Sent {sent}, failed {failed}.")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
