#!/usr/bin/env python3
"""
Status API - Get download/upscale status + SSE stream.
"""

import json
import time

from flask import Blueprint, Response, jsonify, request, stream_with_context

status_bp = Blueprint('status', __name__)

# Tareas terminadas hace más de esto no viajan en el snapshot (medido: 388 tareas muertas =
# 92 KB serializados 2×/s = 681 MB/hora de SSE con la app en reposo). Se PODAN del payload,
# no de los dicts de origen — el historial por tarea sigue consultable por su endpoint.
_FINISHED = ('complete', 'cancelled', 'error')
_KEEP_FINISHED_SECS = 600


def _prune_finished(tasks: dict) -> dict:
    now = time.time()
    out = {}
    for k, v in tasks.items():
        if isinstance(v, dict) and v.get('status') in _FINISHED:
            ended = v.get('ended_at') or 0
            # Sin ended_at no sabemos cuándo terminó (tareas de sesiones viejas): fuera.
            if not ended or now - ended > _KEEP_FINISHED_SECS:
                continue
        out[k] = v
    return out


def _all_status():
    from api.download import get_download_status as get_dl_status
    from api.upscale import get_upscale_status
    from api.export import get_export_tasks
    from api.transplant import get_transplant_tasks
    from api.subtitle import get_subtitle_tasks
    from api.subtitle_batch import get_batch_tasks

    dl_status = _prune_finished(get_dl_status())
    up_status = _prune_finished(get_upscale_status())
    export_tasks = {
        k: {x: v[x] for x in v if x != 'tmp_path'}
        for k, v in get_export_tasks().items()
        if v.get('status') not in ('downloaded',)
    }
    # Translation/version-download tasks ride the same aggregated snapshot as everything
    # else so the unified Activity center + the in-modal progress read ONE source of truth.
    transplant_tasks = get_transplant_tasks()
    # Traducción de subtítulos de anime (modelo Gemini/Qwen) — misma vía unificada, para
    # que Actividad sea el registro global. Las descargas de anime NO se incluyen (vista propia).
    subtitle_tasks = get_subtitle_tasks()
    # Lote de subtítulos: UNA entrada agregada por lote (5/12). Las traducciones IA hijas van en
    # `subtitles` con su `batch_id`; el front las suprime para no contarlas dos veces.
    subtitle_batches = get_batch_tasks()
    return {'downloads': dl_status, 'upscale': up_status, 'exports': export_tasks,
            'transplant': transplant_tasks, 'subtitles': subtitle_tasks,
            'subtitle_batches': subtitle_batches}


@status_bp.route('/stream')
def stream_status():
    """Server-Sent Events stream — pushes aggregated status + event bus every 500 ms.

    `?since=<seq>` lets a reconnecting client resume exactly where it left off
    instead of replaying the whole 500-event ring buffer. A *fresh* connection
    (no `since`) starts from the current seq — it must NOT replay old events
    like a stale 'watched' from a previous episode, which would re-open the
    autoplay countdown after the user already dismissed it."""
    from api.runtime import get_sse_events_since, get_current_seq

    since = request.args.get('since', type=int)

    def generate():
        last_seq = since if since is not None else get_current_seq()
        # Emitir SOLO cuando el snapshot cambie (antes: 92 KB fijos 2×/s = 681 MB/hora en
        # reposo, parseados en el hilo principal del WebView). Latido cada 15 s para que la
        # conexión no parezca muerta; un comentario SSE (línea ": …") no llega a onmessage.
        last_json = None
        idle = 0.0
        while True:
            try:
                payload = _all_status()
                new_events = get_sse_events_since(last_seq)
                if new_events:
                    last_seq = new_events[-1]['seq']
                    payload['events'] = new_events
                body = json.dumps(payload)
                if new_events or body != last_json:
                    last_json = body if not new_events else json.dumps({k: v for k, v in payload.items() if k != 'events'})
                    idle = 0.0
                    yield f"data: {body}\n\n"
                elif idle >= 15:
                    idle = 0.0
                    # Latido como mensaje REAL, no comentario SSE: `onmessage` no se dispara con
                    # los comentarios, y el indicador de conexión del sidebar se apoya en él para
                    # distinguir "silencio porque no pasa nada" de "silencio porque me caí".
                    # `lib/sse.js` lo ignora (no trae downloads/upscale/events).
                    yield 'data: {"hb":1}\n\n'
            except Exception:
                yield "data: {}\n\n"
            time.sleep(0.5)
            idle += 0.5

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            # NOTE: do NOT set 'Connection' — it's a hop-by-hop header forbidden by
            # PEP 3333; Waitress raises AssertionError and the whole stream 500s,
            # which made the frontend reconnect every 3s and flooded the logs.
        },
    )


@status_bp.route('/download/<download_id>')
def get_download_status(download_id):
    from api.download import get_download_status as get_dl_status
    return jsonify(get_dl_status(download_id))

@status_bp.route('/upscale/<upscale_id>')
def get_upscale_status_route(upscale_id):
    from api.upscale import get_upscale_status
    return jsonify(get_upscale_status(upscale_id))

@status_bp.route('')
def get_all_status_route():
    return jsonify(_all_status())


@status_bp.route('/errors')
def get_error_counts_route():
    """Diagnóstico: cuántos errores ha registrado cada costura desde el arranque
    (ver api.observability). Hace consultable el "fallo silencioso" que antes no dejaba
    rastro. Los eventos SSE de tipo 'error' llevan el detalle en vivo."""
    from api.observability import error_counts
    return jsonify(error_counts())