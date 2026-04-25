#!/usr/bin/env python3
"""
Status API - Get download/upscale status
"""

from flask import Blueprint, jsonify

status_bp = Blueprint('status', __name__)

@status_bp.route('/download/<download_id>')
def get_download_status(download_id):
    from api.download import get_download_status as get_dl_status
    return jsonify(get_dl_status(download_id))

@status_bp.route('/upscale/<upscale_id>')
def get_upscale_status_route(upscale_id):
    from api.upscale import get_upscale_status
    return jsonify(get_upscale_status(upscale_id))

@status_bp.route('')
def get_all_status():
    from api.download import get_download_status as get_dl_status
    from api.upscale import get_upscale_status

    dl_status = get_dl_status()
    up_status = get_upscale_status()

    return jsonify({
        'downloads': dl_status,
        'upscale': up_status
    })