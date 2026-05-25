#!/usr/bin/env python3
"""
Status API - Get download/upscale status + SSE stream.
"""

import json
import time

from flask import Blueprint, Response, jsonify, stream_with_context

status_bp = Blueprint('status', __name__)


def _all_status():
    from api.download import get_download_status as get_dl_status
    from api.upscale import get_upscale_status
    from api.export import get_export_tasks

    dl_status = get_dl_status()
    up_status = get_upscale_status()
    export_tasks = {
        k: {x: v[x] for x in v if x != 'tmp_path'}
        for k, v in get_export_tasks().items()
        if v.get('status') not in ('downloaded',)
    }
    return {'downloads': dl_status, 'upscale': up_status, 'exports': export_tasks}


@status_bp.route('/stream')
def stream_status():
    """Server-Sent Events stream — pushes aggregated status + event bus every 500 ms."""
    from api.runtime import get_sse_events_since

    def generate():
        last_seq = 0
        while True:
            try:
                payload = _all_status()
                new_events = get_sse_events_since(last_seq)
                if new_events:
                    last_seq = new_events[-1]['seq']
                    payload['events'] = new_events
                yield f"data: {json.dumps(payload)}\n\n"
            except Exception:
                yield "data: {}\n\n"
            time.sleep(0.5)

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
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