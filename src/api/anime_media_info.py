"""Ficha técnica bajo demanda para un episodio ya resuelto en la biblioteca."""
import json
import re
import stat
import subprocess
from functools import lru_cache
from pathlib import Path

from flask import Blueprint, jsonify

from api.anime import video_de_biblioteca
from api.observability import record_error

anime_media_info_bp = Blueprint('anime_media_info', __name__)
_EPISODE_KEY = re.compile(r'(?i)(?:(?P<number>\d{1,4})|s(?P<season>\d{1,3})e(?P<episode>\d{1,4}))')


def _number(value):
    try:
        number = float(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def _track(stream):
    tags = stream.get('tags') or {}
    disposition = stream.get('disposition') or {}
    return {
        'codec': stream.get('codec_name') or 'Desconocido',
        'language': str(tags.get('language') or '').strip(),
        'title': str(tags.get('title') or '').strip(),
        'channels': stream.get('channels'),
        'default': bool(disposition.get('default')),
    }


@lru_cache(maxsize=96)
def _probe_cached(path: str, size: int, modified_ns: int) -> dict:
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-print_format', 'json', '-show_streams', '-show_format', path],
        capture_output=True, text=True, timeout=15, check=True,
    )
    probe = json.loads(result.stdout or '{}')
    streams = probe.get('streams') or []
    video_stream = next((stream for stream in streams
                         if stream.get('codec_type') == 'video'
                         and not (stream.get('disposition') or {}).get('attached_pic')), None)
    if not video_stream:
        raise ValueError('El archivo no contiene una pista de vídeo.')
    container = (probe.get('format') or {}).get('format_name') or ''
    duration = _number((probe.get('format') or {}).get('duration')) or _number(video_stream.get('duration'))
    return {
        'container': container.split(',', 1)[0],
        'duration_seconds': duration,
        'video': {
            'codec': video_stream.get('codec_name') or 'Desconocido',
            'width': video_stream.get('width'),
            'height': video_stream.get('height'),
        },
        'audio_tracks': [_track(stream) for stream in streams if stream.get('codec_type') == 'audio'],
        'subtitle_tracks': [_track(stream) for stream in streams if stream.get('codec_type') == 'subtitle'],
    }


@anime_media_info_bp.get('/<anime_id>/<episode_key>')
def episode_media_info(anime_id, episode_key):
    match = _EPISODE_KEY.fullmatch(episode_key)
    if not match:
        return jsonify(error='Identidad de episodio no válida.'), 400
    episode = int(match.group('number') or match.group('episode'))

    try:
        video, error = video_de_biblioteca(anime_id, episode, episode_key)
    except Exception as exc:
        record_error('anime', exc, op='media_info_resolve', anime=anime_id, episode=episode_key)
        return jsonify(error='No se pudo resolver el archivo del episodio.'), 500
    if error:
        status = error[1] if len(error) > 1 and error[1] in (403, 404, 502) else 500
        messages = {
            403: 'No se pudo validar el archivo de este episodio.',
            404: 'No hay un archivo disponible para este episodio.',
            502: 'No se pudo consultar qBittorrent para resolver el archivo.',
        }
        return jsonify(error=messages.get(status, 'No se pudo resolver el archivo del episodio.')), status

    try:
        path = Path(video)
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            return jsonify(error='El archivo del episodio ya no está disponible.'), 404
        info = _probe_cached(str(path), metadata.st_size, metadata.st_mtime_ns)
    except FileNotFoundError:
        return jsonify(error='El archivo del episodio ya no está disponible.'), 404
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        record_error('anime', exc, op='media_info_probe', anime=anime_id, episode=episode_key)
        return jsonify(error='No se pudo leer la ficha técnica del episodio.'), 503

    return jsonify({**info, 'size_bytes': metadata.st_size})
