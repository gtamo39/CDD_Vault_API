#!/usr/bin/env python3
"""
notify.py — optional metadata-only email when a CDD import commits.

Two senders with different data scopes:
  * notify_committed        — metadata only (protocol/PID, slurp id, file
                              basename, counts). Never compound ids or values.
  * notify_compound_summary — the per-compound → assays rollup. This one DOES
                              include compound batch ids (SRB-…), by explicit
                              data-owner authorization for internal team
                              distribution (a documented override of the
                              local-only policy; see the wiki). It still stops
                              short of the measured readout VALUES — ids + which
                              assays only, never SMILES/structures/results.

Sending never raises: a broken mailbox or disabled SMTP must not break or roll
back an upload. Every entry point returns True/False and logs a WARN on failure.

Config lives under the top-level `notifications:` block in config/config.yaml;
the SMTP password lives in a separate file (default ~/.cdd_smtp, chmod 600),
never in the config or the repo.
"""

import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path


def _settings(config):
    """The notifications block from a loaded config dict ({} if absent)."""
    return (config or {}).get("notifications") or {}


def _password(password_file):
    """Read the SMTP password from its file, or None if missing/empty."""
    p = Path(password_file or "~/.cdd_smtp").expanduser()
    if not p.exists():
        return None
    return p.read_text().strip() or None


def _secret(path, default=None):
    """Read a one-line secret from a chmod-600 file, or None if missing/empty."""
    p = Path(path or default or "").expanduser()
    if not str(p) or not p.exists():
        return None
    return p.read_text().strip() or None


def _graph_token(n):
    """OAuth2 client-credentials access token for Microsoft Graph, or None.

    App-only flow: the app registration authenticates with its own secret, so no
    user sign-in and no MFA prompt. This is what replaces SMTP basic auth, which
    Microsoft disables by default on M365 tenants.
    """
    import requests

    tenant, client = n.get("tenant_id"), n.get("client_id")
    secret = _secret(n.get("client_secret_file"))
    if not (tenant and client and secret):
        print("WARN: Graph transport needs notifications.tenant_id, .client_id and "
              f".client_secret_file (got tenant={bool(tenant)} client={bool(client)} "
              f"secret={bool(secret)})")
        return None
    r = requests.post(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        data={"grant_type": "client_credentials", "client_id": client,
              "client_secret": secret, "scope": "https://graph.microsoft.com/.default"},
        timeout=30)
    if not r.ok:
        print(f"WARN: Graph token request failed ({r.status_code}): {r.text[:300]}")
        return None
    return r.json().get("access_token")


def _send_graph(n, subject, body, html=None):
    """Send via Microsoft Graph `sendMail` as the configured sender. True on success."""
    import requests

    recipient, sender = n.get("recipient"), n.get("sender")
    if not (recipient and sender):
        print("WARN: notifications enabled but recipient/sender missing")
        return False
    token = _graph_token(n)
    if not token:
        return False
    # Graph carries ONE body part, so prefer the HTML rendering when we have it
    content_type, content = ("HTML", html) if html else ("Text", body)
    payload = {"message": {"subject": subject,
                           "body": {"contentType": content_type, "content": content},
                           "toRecipients": [{"emailAddress": {"address": a.strip()}}
                                            for a in str(recipient).split(",") if a.strip()]},
               "saveToSentItems": True}
    try:
        r = requests.post(f"https://graph.microsoft.com/v1.0/users/{sender}/sendMail",
                          headers={"Authorization": f"Bearer {token}"},
                          json=payload, timeout=30)
        if r.status_code == 202:
            return True
        print(f"WARN: Graph sendMail failed ({r.status_code}): {r.text[:300]}")
        return False
    except Exception as ex:
        print(f"WARN: Graph sendMail error ({type(ex).__name__}: {ex})")
        return False


def _send(n, subject, body, html=None):
    """Low-level send from a notifications block. Returns True on success.

    `transport: graph` uses Microsoft Graph with OAuth2 app-only auth (the
    supported route since M365 disables SMTP basic auth by default); anything
    else uses SMTP. When `html` is given SMTP sends multipart/alternative, while
    Graph sends the HTML part alone.
    """
    if str(n.get("transport", "smtp")).lower() == "graph":
        return _send_graph(n, subject, body, html)
    recipient, sender = n.get("recipient"), n.get("sender")
    host, port = n.get("smtp_host", "smtp.office365.com"), int(n.get("smtp_port", 587))
    pw = _password(n.get("password_file"))
    if not (recipient and sender and pw):
        print("WARN: notifications enabled but recipient/sender/password missing "
              f"(password_file={n.get('password_file') or '~/.cdd_smtp'})")
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, sender, recipient
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    try:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(sender, pw)
            s.send_message(msg)
        return True
    except Exception as ex:  # network / auth / policy — never propagate
        print(f"WARN: notification email failed ({type(ex).__name__}: {ex})")
        return False


def notify_committed(config, *, protocol_name, pid, slurp_id, filename,
                     records_committed=None, total_records=None):
    """Email a 'committed' summary if notifications + on_commit are enabled.

    Body is metadata only. Returns True if an email was sent, else False
    (disabled, missing creds, or send error). Never raises.
    """
    n = _settings(config)
    if not (n.get("enabled") and n.get("on_commit")):
        return False
    lines = [
        "A CDD Vault import committed successfully.",
        "",
        f"protocol: {protocol_name} (PID {pid})",
        f"slurp id: {slurp_id}",
        f"file:     {Path(filename).name}",
    ]
    if records_committed is not None:
        lines.append(f"records committed: {records_committed}")
    if total_records is not None:
        lines.append(f"total records:     {total_records}")
    lines += ["", "(metadata only — no compound data)"]
    return _send(n, f"[CDD] committed: {protocol_name} (PID {pid})", "\n".join(lines))


def _esc(s):
    """Minimal HTML escape for cell text."""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def render_summary_text(rows):
    """Plain-text fallback: '<batch id>\\t<run date>\\t<assay, assay, ...>' per line."""
    return "\n".join(f"{r['batch_id']}\t{r.get('run_date', '')}\t{', '.join(r['assays'])}"
                     for r in rows)


def render_summary_html(rows):
    """An inline-styled HTML table (compound | run date | assays) for email clients."""
    th = "text-align:left;padding:6px 16px 6px 0;border-bottom:2px solid #b4471f;font-family:sans-serif;font-size:13px"
    tdk = "padding:5px 16px 5px 0;border-bottom:1px solid #e3ddd0;font-family:monospace;font-size:13px;white-space:nowrap"
    tdd = "padding:5px 16px 5px 0;border-bottom:1px solid #e3ddd0;font-family:sans-serif;font-size:13px;white-space:nowrap"
    tdv = "padding:5px 0;border-bottom:1px solid #e3ddd0;font-family:sans-serif;font-size:13px"
    body = "".join(
        f"<tr><td style='{tdk}'>{_esc(r['batch_id'])}</td>"
        f"<td style='{tdd}'>{_esc(r.get('run_date', ''))}</td>"
        f"<td style='{tdv}'>{_esc(', '.join(r['assays']))}</td></tr>"
        for r in rows)
    return (f"<table style='border-collapse:collapse'>"
            f"<thead><tr><th style='{th}'>Compound</th><th style='{th}'>Run date</th>"
            f"<th style='{th}'>Assays</th></tr></thead>"
            f"<tbody>{body}</tbody></table>")


def notify_compound_summary(config, rows, subject="[CDD] upload summary"):
    """Email the per-compound → assays rollup as a formatted table.

    Requires `enabled` (ignores `on_commit` — this is the batch summary, not the
    per-file commit notice). Body includes compound batch ids (authorized). No-op
    returning False if disabled or there are no rows. Never raises.
    """
    n = _settings(config)
    if not n.get("enabled"):
        print("WARN: notifications.enabled is false — summary not sent")
        return False
    if not rows:
        print("WARN: no compounds to summarize — nothing sent")
        return False
    intro = f"Per-compound assay summary for the latest CDD upload ({len(rows)} compounds)."
    text = intro + "\n\n" + render_summary_text(rows) + "\n"
    html = f"<p style='font-family:sans-serif;font-size:14px'>{intro}</p>" + render_summary_html(rows)
    return _send(n, subject, text, html=html)


def send_test(config):
    """Send one test email to verify SMTP works (no upload). Ignores on_commit,
    still requires `enabled`. Returns True on success."""
    n = _settings(config)
    if not n.get("enabled"):
        print("WARN: notifications.enabled is false — nothing sent")
        return False
    return _send(n, "[CDD] test notification",
                 "This is a test from import_to_protocol.py --test-notification.\n"
                 "SMTP is configured correctly. (metadata only — no compound data)")
