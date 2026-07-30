#!/usr/bin/env python3
"""
Sync API — "Guardar / Recuperar biblioteca".

Mirrors the portable *profile* (manga/anime progress, favorites, reading history,
portable UI prefs) to a private Git repo so it survives a machine change, reinstall
or local DB corruption, and is ready for future multi-device sync.

Design:
- SyncBackend is the abstraction; GitBackend is today's implementation. A realtime
  backend (CouchDB/Supabase) can be dropped in later without touching the UI.
- One JSON file per domain under DATA_ROOT/profile/ so unrelated edits never conflict.
- Each save() is a commit → immutable, restorable point-in-time history.
- SECRETS ARE NEVER SYNCED: config.json (API keys, the sync PAT) lives outside the
  profile dir and is not tracked. The commit identity is pinned to the PERSONAL
  GitHub account — never a corporate one.
- Merge on restore reuses backup.apply_payload (additive, never destroys).
"""

import json
import os
import stat
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request

from api.runtime import DATA_ROOT, write_json_atomic
from api.config_store import get_secret, set_secrets, get_prefs, set_prefs
from api.observability import record_error

sync_bp = Blueprint('sync', __name__)

_PROFILE_DIR = Path(DATA_ROOT) / 'profile'

# Commit identity — PERSONAL account only. Overridable via env, but never defaults
# to anything else. See project constraints: corporate account must never be used.
_COMMIT_NAME = os.environ.get('SYNC_GIT_NAME', 'RoierOc')
_COMMIT_EMAIL = os.environ.get('SYNC_GIT_EMAIL', 'rogerbonilla9@gmail.com')


# ── git plumbing ─────────────────────────────────────────────────────────────

def _askpass_script(pat: str) -> str:
    """Write a temp GIT_ASKPASS script. The PAT is passed via env (SYNC_PAT), never
    on the command line or in the remote URL (which would leak in `ps`/reflog)."""
    fd, path = tempfile.mkstemp(prefix='askpass_', suffix='.sh')
    with os.fdopen(fd, 'w') as f:
        f.write('#!/bin/sh\ncase "$1" in\n'
                '  *[Uu]sername*) printf "x-access-token" ;;\n'
                '  *) printf "%s" "$SYNC_PAT" ;;\n'
                'esac\n')
    os.chmod(path, stat.S_IRWXU)
    return path


def _run_git(args, check=True, pat=None):
    env = dict(os.environ)
    env['GIT_TERMINAL_PROMPT'] = '0'
    askpass = None
    if pat:
        askpass = _askpass_script(pat)
        env['GIT_ASKPASS'] = askpass
        env['SYNC_PAT'] = pat
    try:
        r = subprocess.run(
            ['git', '-C', str(_PROFILE_DIR), *args],
            capture_output=True, text=True, env=env, timeout=120,
        )
    finally:
        if askpass:
            try:
                os.unlink(askpass)
            except OSError:
                pass
    if check and r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or 'git error').strip())
    return r


def _remote_url():
    return get_secret('SYNC_REMOTE_URL')


def _pat():
    return get_secret('SYNC_GIT_PAT')


def _ensure_repo():
    """Idempotently init the profile repo, pin identity, wire the remote."""
    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    if not (_PROFILE_DIR / '.git').exists():
        _run_git(['init', '-q'])
        _run_git(['checkout', '-q', '-B', 'main'])
    # Identity is set at the REPO level so it can never inherit a global corporate one.
    _run_git(['config', 'user.name', _COMMIT_NAME])
    _run_git(['config', 'user.email', _COMMIT_EMAIL])
    _run_git(['config', 'commit.gpgsign', 'false'], check=False)
    url = _remote_url()
    if url:
        existing = _run_git(['remote'], check=False).stdout.split()
        if 'origin' in existing:
            _run_git(['remote', 'set-url', 'origin', url])
        else:
            _run_git(['remote', 'add', 'origin', url])


# ── profile assembly (what gets synced) ──────────────────────────────────────

def _write_json(name, obj):
    write_json_atomic(_PROFILE_DIR / name, obj, indent=2, durable=False)


def _read_json(name, default):
    p = _PROFILE_DIR / name
    try:
        return json.loads(p.read_text(encoding='utf-8')) if p.exists() else default
    except Exception:
        return default


def _collect_profile():
    """Snapshot the local state into the profile dir (one file per domain)."""
    from api.backup import build_payload
    from api.reader import _history_read, _progress_read

    payload = build_payload()
    _write_json('manga.json', payload.get('manga', []))
    _write_json('anime.json', payload.get('anime', []))
    _write_json('reading.json', _history_read())
    # Qué capítulos has leído y por dónde vas en cada obra. NO lo cubre reading.json (que son
    # los últimos 500 capítulos terminados): sin esto, restaurar en otra máquina traía la
    # biblioteca pero la dejaba entera "sin leer".
    _write_json('manga_progress.json', _progress_read())
    _write_json('settings.json', get_prefs())
    # Collections are a planned concept; keep the file present but empty for forward-compat.
    if not (_PROFILE_DIR / 'collections.json').exists():
        _write_json('collections.json', {})


def _apply_profile():
    """Merge the profile files back into local state (additive, never destroys)."""
    from api.backup import apply_payload
    from api.reader import (_history_read, _history_write, _progress_path,
                            _progress_read, merge_progress)

    counts = apply_payload({
        'manga': _read_json('manga.json', None),
        'anime': _read_json('anime.json', None),
    })

    # Reading history: union by (title, chapter), keep the newest read_at.
    incoming = _read_json('reading.json', [])
    if isinstance(incoming, list) and incoming:
        merged = {}
        for h in _history_read() + incoming:
            k = (h.get('title'), str(h.get('chapter')))
            if k not in merged or h.get('read_at', 0) > merged[k].get('read_at', 0):
                merged[k] = h
        ordered = sorted(merged.values(), key=lambda h: h.get('read_at', 0), reverse=True)
        _history_write(ordered)

    # Progreso de manga: se FUNDE con lo local (unión de leídos, la posición más reciente
    # gana), nunca se sustituye — restaurar no puede borrar lo que has leído en esta máquina.
    prog_in = _read_json('manga_progress.json', {})
    if isinstance(prog_in, dict) and prog_in:
        write_json_atomic(_progress_path(), merge_progress(_progress_read(), prog_in),
                          indent=2, keep_backup=True)

    prefs = _read_json('settings.json', {})
    if isinstance(prefs, dict) and prefs:
        set_prefs(prefs)

    return counts


# ── SyncBackend / GitBackend ─────────────────────────────────────────────────

class SyncBackend:
    def configure(self, remote_url, pat): ...
    def status(self): ...
    def save(self): ...
    def restore(self): ...


class GitBackend(SyncBackend):
    def configure(self, remote_url, pat):
        vals = {}
        if remote_url is not None:
            vals['SYNC_REMOTE_URL'] = remote_url.strip()
        if pat:  # only overwrite when a new token is provided
            vals['SYNC_GIT_PAT'] = pat.strip()
        set_secrets(vals)
        _ensure_repo()
        return self.status()

    def status(self):
        url = _remote_url()
        st = {'remote_set': bool(url), 'pat_set': bool(_pat()), 'last_saved_at': None,
              'auto': auto_enabled(), 'auto_every_days': _AUTO_EVERY // 86400,
              'auto_last_error': _auto_state.get('error'),
              'identity': f'{_COMMIT_NAME} <{_COMMIT_EMAIL}>'}
        if (_PROFILE_DIR / '.git').exists():
            r = _run_git(['log', '-1', '--format=%ct'], check=False)
            if r.returncode == 0 and r.stdout.strip():
                st['last_saved_at'] = int(r.stdout.strip())
        return st

    def _align_to_remote(self):
        """Reposiciona la rama local sobre `origin/main` ANTES de commitear, para que
        un guardado desde otra máquina/sesión (o el commit inicial del repo, p.ej. un
        README) no rechace nuestro push por 'fetch first' / histórico divergente.

        El perfil se regenera entero desde el estado local justo después, así que solo
        necesitamos que HEAD quede sobre el remoto: `reset --mixed` mueve el puntero de
        la rama a `origin/main` SIN tocar el árbol de trabajo, y el snapshot fresco se
        commitea encima → el push avanza rápido. Tolera el primer push (aún sin `main`
        en el remoto): si no existe la ref, no hace nada y el push la crea."""
        if not _remote_url():
            return
        fetch = _run_git(['fetch', 'origin', 'main'], check=False, pat=_pat())
        if fetch.returncode != 0:
            return  # remoto vacío/inalcanzable → deja que el push cree main o falle claro
        ref = _run_git(['rev-parse', '--verify', '-q', 'origin/main'], check=False)
        if ref.returncode != 0 or not ref.stdout.strip():
            return
        _run_git(['reset', '--mixed', 'origin/main'], check=False)

    def save(self):
        if not _remote_url() or not _pat():
            raise RuntimeError('Configura el repo remoto y el token antes de guardar.')
        _ensure_repo()
        self._align_to_remote()
        _collect_profile()
        _run_git(['add', '-A'])
        # Nothing changed? still a success (idempotent save).
        if _run_git(['diff', '--cached', '--quiet'], check=False).returncode == 0:
            pushed = self._push()
            return {'ok': True, 'changed': False, 'pushed': pushed, **self.status()}
        stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        _run_git(['commit', '-q', '-m', f'profile: {stamp}'])
        pushed = self._push()
        return {'ok': True, 'changed': True, 'pushed': pushed, **self.status()}

    def _push(self):
        r = _run_git(['push', '-u', 'origin', 'HEAD:main'], check=False, pat=_pat())
        if r.returncode != 0:
            raise RuntimeError(f'Push falló: {(r.stderr or r.stdout).strip()}')
        return True

    def restore(self):
        if not _remote_url() or not _pat():
            raise RuntimeError('Configura el repo remoto y el token antes de recuperar.')
        _ensure_repo()
        # Fetch and hard-align the working tree to the remote, then merge into local state.
        fetch = _run_git(['fetch', 'origin', 'main'], check=False, pat=_pat())
        if fetch.returncode != 0:
            raise RuntimeError(f'No se pudo leer el remoto: {(fetch.stderr or fetch.stdout).strip()}')
        _run_git(['reset', '--hard', 'origin/main'])
        counts = _apply_profile()
        return {'ok': True, **counts, **self.status()}


_backend = GitBackend()


# ── copia automática ─────────────────────────────────────────────────────────
# Un respaldo que hay que acordarse de pulsar no es un respaldo. Este hilo hace el mismo
# `save()` que el botón cuando ha pasado una semana desde el ÚLTIMO COMMIT del perfil.
#
# La fecha del último guardado NO se guarda aparte: se lee del propio historial de git
# (`status()['last_saved_at']`). Así no hay un segundo estado que pueda desincronizarse —
# si guardas a mano, la semana se cuenta desde ahí, y si el repo se restaura en otra
# máquina, el reloj viene con él.
#
# Fallar es normal (sin red, VPN caída, token caducado): se registra y se reintenta en el
# siguiente latido, nunca tumba el proceso ni bloquea nada del camino del usuario.

_AUTO_EVERY = int(os.environ.get('SYNC_AUTO_EVERY', 7 * 86400))   # cada cuánto toca copia
_AUTO_CHECK = 6 * 3600                                            # cada cuánto se comprueba
_AUTO_FIRST = 120                                                 # margen tras arrancar
_auto_state = {'error': None, 'thread': None}


def auto_enabled() -> bool:
    """Encendida salvo que el usuario la apague (get_prefs es el sitio portable)."""
    return bool(get_prefs().get('sync_auto', True))


def _auto_tick():
    """Una comprobación. Devuelve el motivo de no hacer nada, o 'saved'."""
    if not auto_enabled():
        return 'off'
    if not _remote_url() or not _pat():
        return 'unconfigured'          # sin repo/token no hay nada que hacer: no es un fallo
    last = _backend.status().get('last_saved_at') or 0
    if time.time() - last < _AUTO_EVERY:
        return 'fresh'
    _backend.save()
    return 'saved'


def _auto_loop():
    time.sleep(_AUTO_FIRST)
    while True:
        try:
            if _auto_tick() == 'saved':
                _auto_state['error'] = None
                print('[sync] copia semanal automática subida', flush=True)
        except Exception as e:
            # Que falle es esperable (sin red, token caducado). Lo que NO puede ser es
            # que falle en silencio y el usuario crea que tiene copias al día.
            _auto_state['error'] = str(e)
            record_error('sync', e, op='auto_save')
        time.sleep(_AUTO_CHECK)


def start_auto_sync():
    """Arranca el hilo (idempotente). Lo llama app.py al registrar los blueprints."""
    if _auto_state['thread'] and _auto_state['thread'].is_alive():
        return
    t = threading.Thread(target=_auto_loop, name='sync-auto', daemon=True)
    _auto_state['thread'] = t
    t.start()


# ── routes ───────────────────────────────────────────────────────────────────

@sync_bp.route('/status')
def status():
    try:
        return jsonify(_backend.status())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@sync_bp.route('/configure', methods=['POST'])
def configure():
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(_backend.configure(data.get('remote_url'), data.get('pat')))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@sync_bp.route('/save', methods=['POST'])
def save():
    try:
        return jsonify(_backend.save())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@sync_bp.route('/restore', methods=['POST'])
def restore():
    try:
        return jsonify(_backend.restore())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@sync_bp.route('/auto', methods=['POST'])
def set_auto():
    """Enciende/apaga la copia semanal automática."""
    body = request.get_json(silent=True) or {}
    set_prefs({'sync_auto': bool(body.get('enabled', True))})
    return jsonify(_backend.status())
