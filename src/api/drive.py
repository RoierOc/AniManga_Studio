#!/usr/bin/env python3
"""
Google Drive integration — OAuth2 login + CBZ/CBR upload to a MangaJaNai folder.
"""

import os
import json
from pathlib import Path

from flask import Blueprint, jsonify, request, redirect

drive_bp = Blueprint("drive", __name__)

SCOPES = ["https://www.googleapis.com/auth/drive.file",
          "https://www.googleapis.com/auth/userinfo.email",
          "openid"]
TOKEN_PATH = Path.home() / ".mangajanai_gdrive_token.json"
CREDS_FILE = Path(__file__).resolve().parent.parent.parent / "google_credentials.json"
REDIRECT_URI = "http://localhost:5100/api/drive/callback"
DRIVE_FOLDER = "MangaJaNai"

os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

# Store pending Flow instances by state so the callback reuses the same object
# (preserves the PKCE code_verifier generated during authorization_url)
_pending_flows: dict = {}


# ── helpers ──────────────────────────────────────────────────────────────────

def _client_config():
    from api.config_store import get_secret  # runtime-editable keys (Ajustes)
    client_id = get_secret("GOOGLE_CLIENT_ID")
    client_secret = get_secret("GOOGLE_CLIENT_SECRET")
    if CREDS_FILE.exists():
        try:
            raw = json.loads(CREDS_FILE.read_text())
            block = raw.get("web") or raw.get("installed") or {}
            client_id = block.get("client_id", client_id)
            client_secret = block.get("client_secret", client_secret)
        except Exception:
            pass
    return client_id, client_secret


def _flow(state=None):
    from google_auth_oauthlib.flow import Flow
    client_id, client_secret = _client_config()
    cfg = {"web": {
        "client_id": client_id,
        "client_secret": client_secret,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [REDIRECT_URI],
    }}
    kwargs = {"scopes": SCOPES, "redirect_uri": REDIRECT_URI}
    if state:
        kwargs["state"] = state
    return Flow.from_client_config(cfg, **kwargs)


def _load_creds():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    if not TOKEN_PATH.exists():
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN_PATH.write_text(creds.to_json())
        return creds if creds.valid else None
    except Exception:
        return None


def _get_or_create_folder(service):
    q = (f"name='{DRIVE_FOLDER}' and mimeType='application/vnd.google-apps.folder'"
         " and trashed=false")
    res = service.files().list(q=q, fields="files(id)").execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    folder = service.files().create(
        body={"name": DRIVE_FOLDER, "mimeType": "application/vnd.google-apps.folder"},
        fields="id"
    ).execute()
    return folder["id"]


# ── routes ────────────────────────────────────────────────────────────────────

@drive_bp.route("/status")
def status():
    client_id, client_secret = _client_config()
    if not client_id or not client_secret:
        return jsonify({"configured": False, "connected": False})

    creds = _load_creds()
    if not creds:
        return jsonify({"configured": True, "connected": False})

    try:
        from googleapiclient.discovery import build
        svc = build("oauth2", "v2", credentials=creds)
        info = svc.userinfo().get().execute()
        return jsonify({"configured": True, "connected": True,
                        "email": info.get("email", "")})
    except Exception:
        return jsonify({"configured": True, "connected": False})


@drive_bp.route("/auth")
def auth():
    client_id, client_secret = _client_config()
    if not client_id or not client_secret:
        return jsonify({"error": "Credenciales de Google no configuradas"}), 400

    flow = _flow()
    auth_url, state = flow.authorization_url(access_type="offline", prompt="consent")
    _pending_flows[state] = flow   # keep the instance so code_verifier survives
    return jsonify({"auth_url": auth_url})


@drive_bp.route("/callback")
def callback():
    try:
        state = request.args.get("state", "")
        # Reuse the original Flow so the PKCE code_verifier is still attached
        flow = _pending_flows.pop(state, None) or _flow()
        auth_response = request.url.replace("http://", "https://", 1)
        flow.fetch_token(authorization_response=auth_response)
        TOKEN_PATH.write_text(flow.credentials.to_json())
        return redirect("http://localhost:5100/?drive=connected")
    except Exception as e:
        import traceback
        traceback.print_exc()
        return (f"<h2 style='font-family:sans-serif'>Error conectando Drive</h2>"
                f"<pre style='color:red'>{e}</pre>"
                f"<a href='http://localhost:5100'>← Volver a MangaJaNai</a>"), 400


@drive_bp.route("/disconnect", methods=["POST"])
def disconnect():
    if TOKEN_PATH.exists():
        TOKEN_PATH.unlink()
    return jsonify({"ok": True})


@drive_bp.route("/upload_tomo", methods=["POST"])
def upload_tomo():
    """Build CBZ/CBR and upload directly to Google Drive. Returns Drive link."""
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from api.export import build_archive

    creds = _load_creds()
    if not creds:
        return jsonify({"error": "No conectado a Google Drive"}), 401

    data = request.get_json(silent=True) or {}
    try:
        tmp_path, filename = build_archive(data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        service = build("drive", "v3", credentials=creds)
        folder_id = _get_or_create_folder(service)

        fmt = filename.rsplit(".", 1)[-1].lower()
        mime = "application/x-cbz" if fmt == "cbz" else "application/x-cbr"

        media = MediaFileUpload(tmp_path, mimetype=mime, resumable=True)
        uploaded = service.files().create(
            body={"name": filename, "parents": [folder_id]},
            media_body=media,
            fields="id,name,webViewLink"
        ).execute()

        return jsonify({
            "ok": True,
            "name": uploaded["name"],
            "link": uploaded.get("webViewLink", ""),
            "file_id": uploaded["id"],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass
