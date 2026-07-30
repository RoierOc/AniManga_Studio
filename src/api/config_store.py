#!/usr/bin/env python3
"""
Config / Secrets store — single source of truth for API keys and connection
settings, editable at runtime from the Settings screen.

Priority when resolving a value:
    config.json (writable overlay) → os.environ → project .env → default

The overlay lives at DATA_ROOT/config.json, is gitignored (data/ is), chmod 600,
and is NEVER included in the sync profile (see sync.py). Secrets stay local.

Why a store instead of just editing .env: several blueprints read their keys at
*import time* (module constants). Routing every consumer through get_secret()
means a key saved from the UI applies immediately, without restarting the server.
"""

import hashlib
import hmac
import json
import os
import threading
import time
from pathlib import Path

from flask import Blueprint, jsonify, request

from api.runtime import DATA_ROOT, PROJECT_ROOT, get_library_mode, set_library_mode

config_bp = Blueprint('config', __name__)

_CONFIG_PATH = Path(DATA_ROOT) / 'config.json'
_ENV_PATH = Path(PROJECT_ROOT) / '.env'
_lock = threading.RLock()
_cache = None  # lazily loaded dict of overlay values


# ── .env fallback (no python-dotenv dependency) ──────────────────────────────

def _read_env_file() -> dict:
    """key=value pairs from the project .env (last-resort fallback)."""
    pairs = {}
    try:
        for line in _ENV_PATH.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, _, v = line.partition('=')
            pairs[k.strip()] = v.strip()
    except Exception:
        pass
    return pairs


_env_file = _read_env_file()


# ── overlay persistence ──────────────────────────────────────────────────────

def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_CONFIG_PATH.read_text(encoding='utf-8')) if _CONFIG_PATH.exists() else {}
        except Exception:
            _cache = {}
    return _cache


def _save(data: dict):
    """Atomic write (tmp + rename) with 0600 perms."""
    global _cache
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _CONFIG_PATH.with_suffix(f'.{os.getpid()}.part')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(_CONFIG_PATH)
    _cache = data


# ── public API ───────────────────────────────────────────────────────────────

def get_secret(key: str, default: str = '') -> str:
    """Resolve a config value: overlay → env → .env → default."""
    with _lock:
        overlay = _load()
    if key in overlay and overlay[key] != '':
        return overlay[key]
    env = os.environ.get(key)
    if env:
        return env
    if _env_file.get(key):
        return _env_file[key]
    return default


def set_secrets(values: dict):
    """Merge non-empty values into the overlay and apply them in-process.

    Empty string clears the override (falls back to env/.env). Also mirrors the
    value into os.environ so the few consumers that still read the environment
    directly (and subprocesses) pick it up without a restart.
    """
    with _lock:
        data = dict(_load())
        for k, v in values.items():
            if v is None:
                continue
            v = str(v)
            if v == '':
                data.pop(k, None)
                os.environ.pop(k, None)
            else:
                data[k] = v
                os.environ[k] = v
        _save(data)


def _mask(value: str) -> str:
    if not value:
        return ''
    if len(value) <= 4:
        return '••••'
    return '••••' + value[-4:]


# ── key registry — the UI is generated from this ─────────────────────────────
# secret=True  → never return the value, only {set, hint}
# secret=False → return the actual value (URLs, model names, usernames)

KEY_SPECS = [
    # group, key, label, secret, placeholder
    ('MangaDex', 'MANGADEX_CLIENT_ID',     'Client ID',     True,  'personal-client-…'),
    ('MangaDex', 'MANGADEX_CLIENT_SECRET', 'Client Secret', True,  ''),
    ('MangaDex', 'MANGADEX_USERNAME',      'Usuario',       False, 'tu usuario'),
    ('MangaDex', 'MANGADEX_PASSWORD',      'Contraseña',    True,  ''),

    ('TMDB', 'TMDB_API_KEY', 'API Key', True, 'para portadas HD de anime'),

    ('Traducción', 'OLLAMA_URL',          'Ollama URL',      False, 'http://localhost:11434'),
    ('Traducción', 'OLLAMA_MODEL',        'Modelo Ollama',   False, 'qwen2.5:14b'),

    ('Subtítulos', 'JIMAKU_API_KEY',          'Jimaku API Key',       True,  ''),
    ('Subtítulos', 'OPENSUBTITLES_API_KEY',   'OpenSubtitles API Key',True,  ''),
    ('Subtítulos', 'OPENSUBTITLES_USERNAME',  'OpenSubtitles Usuario',False, ''),
    ('Subtítulos', 'OPENSUBTITLES_PASSWORD',  'OpenSubtitles Contraseña', True, ''),
    ('Subtítulos', 'SUBDL_API_KEY',           'SubDL API Key',        True,  ''),

    ('qBittorrent', 'QBT_URL',      'WebUI URL',   False, 'http://localhost:8080'),
    ('qBittorrent', 'QBT_USERNAME', 'Usuario',     False, 'admin'),
    ('qBittorrent', 'QBT_PASSWORD', 'Contraseña',  True,  ''),

    ('Google Drive', 'GOOGLE_CLIENT_ID',     'Client ID',     True, ''),
    ('Google Drive', 'GOOGLE_CLIENT_SECRET', 'Client Secret', True, ''),

    # SYNC_REMOTE_URL / SYNC_GIT_PAT are also stored via the store, but are edited
    # from the dedicated "Copia y sincronización" section (sync.configure), not here,
    # to avoid two edit points for the same secret.
]


def describe_all() -> list:
    """Grouped view for the UI. Secrets never expose their value."""
    groups = {}
    for group, key, label, secret, placeholder in KEY_SPECS:
        raw = get_secret(key)
        field = {
            'key': key,
            'label': label,
            'secret': secret,
            'placeholder': placeholder,
            'set': bool(raw),
        }
        if secret:
            field['hint'] = _mask(raw) if raw else ''
        else:
            field['value'] = raw
        groups.setdefault(group, []).append(field)
    return [{'group': g, 'fields': f} for g, f in groups.items()]


def import_env_text(text: str) -> list:
    """Parse a .env blob and store any keys we recognise. Returns keys detected."""
    known = {k for _, k, *_ in KEY_SPECS}
    found = {}
    for line in (text or '').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, _, v = line.partition('=')
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k in known and v:
            found[k] = v
    if found:
        set_secrets(found)
    return sorted(found.keys())


# ── routes ───────────────────────────────────────────────────────────────────

@config_bp.route('/keys')
def get_keys():
    return jsonify({'groups': describe_all()})


@config_bp.route('/keys', methods=['POST'])
def post_keys():
    data = request.get_json(silent=True) or {}
    values = data.get('values') if isinstance(data.get('values'), dict) else data
    if not isinstance(values, dict):
        return jsonify({'error': 'expected {key: value}'}), 400
    known = {k for _, k, *_ in KEY_SPECS}
    filtered = {k: v for k, v in values.items() if k in known}
    set_secrets(filtered)
    return jsonify({'ok': True, 'saved': sorted(filtered.keys()), 'groups': describe_all()})


# ── portable UI prefs (NON-secret — these ARE included in the sync profile) ──
# Kept separate from config.json (secrets). Holds settings that were previously
# only in the browser's localStorage (lib-sort, reader prefs, …) so a restore
# brings them back. Values are opaque to the backend.

_PREFS_PATH = Path(DATA_ROOT) / 'prefs.json'


def get_prefs() -> dict:
    try:
        return json.loads(_PREFS_PATH.read_text(encoding='utf-8')) if _PREFS_PATH.exists() else {}
    except Exception:
        return {}


def set_prefs(values: dict) -> dict:
    with _lock:
        data = get_prefs()
        data.update(values or {})
        _PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PREFS_PATH.with_suffix(f'.{os.getpid()}.part')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(_PREFS_PATH)
    return data


@config_bp.route('/prefs')
def get_prefs_route():
    return jsonify(get_prefs())


@config_bp.route('/prefs', methods=['POST'])
def post_prefs_route():
    data = request.get_json(silent=True) or {}
    return jsonify(set_prefs(data))


@config_bp.route('/import_env', methods=['POST'])
def post_import_env():
    text = ''
    if request.files.get('file'):
        text = request.files['file'].read().decode('utf-8', 'replace')
    else:
        data = request.get_json(silent=True) or {}
        text = data.get('text', '')
    detected = import_env_text(text)
    return jsonify({'ok': True, 'detected': detected, 'groups': describe_all()})


# ── Biblioteca oculta: código secreto ─────────────────────────────────────────
# El código se guarda hasheado (nunca en claro) en el mismo overlay que las
# demás claves — se beneficia del mismo chmod 600 y de que nunca se sincroniza.
# El intento fallido añade un backoff progresivo en memoria (no persistido) para
# frenar el fuerza-bruta sobre un código corto sin tocar el overlay en disco.

_HIDDEN_CODE_KEY = 'HIDDEN_LIBRARY_CODE_HASH'
_HIDDEN_SALT = b'animanga-studio-hidden-library-v1'

_hidden_fail_lock = threading.Lock()
_hidden_fail_count = 0
_hidden_fail_until = 0.0


def _hash_code(code: str) -> str:
    return hmac.new(_HIDDEN_SALT, str(code).strip().encode('utf-8'), hashlib.sha256).hexdigest()


def _hidden_configured() -> bool:
    return bool(get_secret(_HIDDEN_CODE_KEY))


def _hidden_backoff_seconds() -> float:
    with _hidden_fail_lock:
        return max(0.0, _hidden_fail_until - time.monotonic())


def _hidden_register_fail():
    global _hidden_fail_count, _hidden_fail_until
    with _hidden_fail_lock:
        _hidden_fail_count += 1
        # El primer fallo no bloquea (un error de tecleo humano no debe asustar);
        # a partir del 2º, backoff exponencial 0.5s, 1s, 2s ... tope 30s — disuade
        # el fuerza-bruta casi de inmediato sin bloquear la app entera.
        if _hidden_fail_count < 2:
            _hidden_fail_until = 0.0
        else:
            _hidden_fail_until = time.monotonic() + min(30.0, 0.5 * (2 ** (_hidden_fail_count - 2)))


def _hidden_register_success():
    global _hidden_fail_count, _hidden_fail_until
    with _hidden_fail_lock:
        _hidden_fail_count = 0
        _hidden_fail_until = 0.0


@config_bp.route('/hidden/status')
def get_hidden_status():
    return jsonify({
        'configured': _hidden_configured(),
        'active': get_library_mode() == 'hidden',
    })


@config_bp.route('/hidden/set-code', methods=['POST'])
def post_hidden_set_code():
    data = request.get_json(silent=True) or {}
    new_code = str(data.get('code') or '').strip()
    current_code = str(data.get('current_code') or '').strip()
    if not new_code:
        return jsonify({'error': 'code requerido'}), 400

    if _hidden_configured():
        wait = _hidden_backoff_seconds()
        if wait > 0:
            return jsonify({'error': 'demasiados intentos, espera', 'retry_after': wait}), 429
        if not hmac.compare_digest(_hash_code(current_code), get_secret(_HIDDEN_CODE_KEY)):
            _hidden_register_fail()
            return jsonify({'error': 'código actual incorrecto'}), 403

    set_secrets({_HIDDEN_CODE_KEY: _hash_code(new_code)})
    _hidden_register_success()
    return jsonify({'ok': True, 'configured': True})


@config_bp.route('/hidden/toggle', methods=['POST'])
def post_hidden_toggle():
    if not _hidden_configured():
        return jsonify({'error': 'no hay código configurado'}), 400

    wait = _hidden_backoff_seconds()
    if wait > 0:
        return jsonify({'error': 'demasiados intentos, espera', 'retry_after': wait}), 429

    data = request.get_json(silent=True) or {}
    code = str(data.get('code') or '').strip()
    if not hmac.compare_digest(_hash_code(code), get_secret(_HIDDEN_CODE_KEY)):
        _hidden_register_fail()
        return jsonify({'error': 'código incorrecto'}), 403

    _hidden_register_success()
    next_mode = 'normal' if get_library_mode() == 'hidden' else 'hidden'
    set_library_mode(next_mode)
    return jsonify({'ok': True, 'active': next_mode == 'hidden'})
