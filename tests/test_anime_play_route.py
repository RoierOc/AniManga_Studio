from flask import Flask

from api import anime


def test_play_route_points_to_public_handler():
    app = Flask(__name__)
    app.register_blueprint(anime.anime_bp, url_prefix="/api/anime")

    rule = next(rule for rule in app.url_map.iter_rules()
                if rule.rule == "/api/anime/play")

    assert rule.endpoint == "anime.anime_play"
