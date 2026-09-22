import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from flask import Flask
from api import anime, anime_batch


class Reply:
    def __init__(self, payload=None, text='Ok.', status=200):
        self._payload = payload
        self.text = text
        self.status_code = status

    def json(self):
        return self._payload


def client():
    app = Flask(__name__)
    app.register_blueprint(anime_batch.anime_batch_bp, url_prefix='/api/anime/batch')
    return app.test_client()


def anime_client():
    app = Flask(__name__)
    app.register_blueprint(anime.anime_bp, url_prefix='/api/anime')
    return app.test_client()


def test_files_metadata_pending_is_not_empty_success(monkeypatch):
    def fake_q(method, path, **kwargs):
        if path == '/torrents/info':
            return Reply([{'hash': 'a' * 40, 'name': 'Batch', 'state': 'metaDL'}])
        if path == '/torrents/files':
            return Reply([])
        raise AssertionError(path)

    monkeypatch.setattr(anime_batch.anime, '_q', fake_q)
    response = client().get('/api/anime/batch/files?hash=' + 'a' * 40)

    assert response.status_code == 202
    assert response.get_json()['metadata_pending'] is True
    assert response.get_json()['files'] == []


def test_files_keep_season_and_index_and_mark_local(monkeypatch, tmp_path):
    content = tmp_path / 'Batch'
    (content / 'Season 01').mkdir(parents=True)
    (content / 'Season 02').mkdir()
    (content / 'Season 01' / 'show - S01E01.mkv').write_bytes(b'local')
    info_hash = 'b' * 40
    raw = [
        {'index': 4, 'name': 'Batch/Season 01/show - S01E01.mkv', 'size': 100, 'progress': 1, 'priority': 1},
        {'index': 8, 'name': 'Batch/Season 02/show - S02E01.mkv', 'size': 200, 'progress': 0, 'priority': 1},
    ]

    def fake_q(method, path, **kwargs):
        if path == '/torrents/info':
            return Reply([{'hash': info_hash, 'name': 'Batch', 'save_path': str(tmp_path),
                           'content_path': str(content)}])
        if path == '/torrents/files':
            return Reply(raw)
        raise AssertionError(path)

    monkeypatch.setattr(anime_batch.anime, '_q', fake_q)
    files = client().get('/api/anime/batch/files?hash=' + info_hash).get_json()['files']

    assert files[0]['id'] == 4
    assert files[0]['season'] == 1 and files[0]['episode'] == 1
    assert files[0]['already_local'] is True
    assert files[0]['selected'] is False
    assert files[1]['season'] == 2 and files[1]['episode'] == 1
    assert files[1]['selected'] is True


def test_apply_validates_ids_sets_priorities_and_persists_only_selected(monkeypatch):
    info_hash = 'c' * 40
    lib = {'42': {'title': 'Show', 'total_episodes': 12, 'episodes': {}}}
    calls = []
    raw = [
        {'index': 7, 'name': 'Season 01/Show - S01E01.mkv', 'size': 10, 'progress': 0, 'priority': 1},
        {'index': 9, 'name': 'Season 02/Show - S02E01.mkv', 'size': 20, 'progress': 0, 'priority': 1},
    ]

    def fake_q(method, path, **kwargs):
        if path == '/torrents/info':
            return Reply([{'hash': info_hash, 'name': 'Batch', 'state': 'paused'}])
        if path == '/torrents/files':
            return Reply(raw)
        if path == '/torrents/filePrio':
            calls.append((path, kwargs['data']))
            return Reply(status=200)
        if path == '/torrents/start':
            calls.append((path, kwargs['data']))
            return Reply(status=200)
        raise AssertionError(path)

    monkeypatch.setattr(anime_batch.anime, '_q', fake_q)
    monkeypatch.setattr(anime_batch.anime, '_lib_read', lambda: lib)
    monkeypatch.setattr(anime_batch.anime, '_lib_write', lambda value: None)

    response = client().post('/api/anime/batch/apply', json={
        'hash': info_hash, 'anime_id': '42', 'selected_file_ids': [9],
    })

    assert response.status_code == 200
    assert calls[0] == ('/torrents/filePrio', {'hash': info_hash, 'id': '9', 'priority': '1'})
    assert calls[1] == ('/torrents/filePrio', {'hash': info_hash, 'id': '7', 'priority': '0'})
    assert calls[2] == ('/torrents/start', {'hashes': info_hash})
    assert 's02e001' in lib['42']['episodes']
    assert 's01e001' not in lib['42']['episodes']



def test_apply_joins_ids_with_pipe_and_preserves_complete_files(monkeypatch):
    info_hash = '1' * 40
    lib = {'42': {'title': 'Show', 'episodes': {}}}
    calls = []
    raw = [
        {'index': 1, 'name': 'Batch/S01E01.mkv', 'size': 10, 'progress': 1, 'priority': 1},
        {'index': 2, 'name': 'Batch/S01E02.mkv', 'size': 10, 'progress': 0, 'priority': 1},
        {'index': 3, 'name': 'Batch/S01E03.mkv', 'size': 10, 'progress': 0, 'priority': 1},
    ]

    def fake_q(method, path, **kwargs):
        if path == '/torrents/info': return Reply([{'hash': info_hash, 'name': 'Batch'}])
        if path == '/torrents/files': return Reply(raw)
        if path == '/torrents/filePrio': calls.append(kwargs['data']); return Reply(status=200)
        if path == '/torrents/start': return Reply(status=200)
        raise AssertionError(path)

    monkeypatch.setattr(anime_batch.anime, '_q', fake_q)
    monkeypatch.setattr(anime_batch.anime, '_lib_read', lambda: lib)
    monkeypatch.setattr(anime_batch.anime, '_lib_write', lambda value: None)
    response = client().post('/api/anime/batch/apply', json={
        'hash': info_hash, 'anime_id': '42', 'selected_file_ids': [2],
    })
    assert response.status_code == 200
    assert calls == [
        {'hash': info_hash, 'id': '2', 'priority': '1'},
        {'hash': info_hash, 'id': '3', 'priority': '0'},
    ]

def test_apply_rejects_file_from_another_torrent(monkeypatch):
    info_hash = 'd' * 40
    lib = {'42': {'title': 'Show', 'episodes': {}}}

    def fake_q(method, path, **kwargs):
        if path == '/torrents/info':
            return Reply([{'hash': info_hash, 'name': 'Batch'}])
        if path == '/torrents/files':
            return Reply([{'index': 1, 'name': 'Show - 01.mkv', 'progress': 0, 'priority': 1}])
        raise AssertionError(path)

    monkeypatch.setattr(anime_batch.anime, '_q', fake_q)
    monkeypatch.setattr(anime_batch.anime, '_lib_read', lambda: lib)
    response = client().post('/api/anime/batch/apply', json={
        'hash': info_hash, 'anime_id': '42', 'selected_file_ids': [99],
    })

    assert response.status_code == 409


def test_library_uses_file_progress_and_does_not_propagate_batch(monkeypatch, tmp_path):
    from api import anime as anime_api

    info_hash = 'e' * 40
    content = tmp_path / 'Batch'
    (content / 'Season 01').mkdir(parents=True)
    (content / 'Season 02').mkdir()
    (content / 'Season 02' / 'show - S02E01.mkv').write_bytes(b'video')
    raw = [
        {'index': 4, 'name': 'Batch/Season 01/show - S01E01.mkv', 'size': 100, 'progress': .25, 'priority': 0},
        {'index': 8, 'name': 'Batch/Season 02/show - S02E01.mkv', 'size': 200, 'progress': 1, 'priority': 1},
    ]

    def fake_q(method, path, **kwargs):
        if path == '/torrents/info':
            return Reply([{'hash': info_hash, 'name': 'Batch', 'progress': 1,
                           'state': 'stalledUP', 'save_path': str(tmp_path),
                           'content_path': str(content)}])
        if path == '/torrents/files':
            return Reply(raw)
        raise AssertionError(path)

    monkeypatch.setattr(anime_api, '_backfill_done', True)
    monkeypatch.setattr(anime_api, '_q', fake_q)
    monkeypatch.setattr(anime_api, '_lib_read', lambda: {
        '42': {'title': 'Show', 'total_episodes': 12, 'episodes': {
            '0': {'info_hash': info_hash, 'from_batch_selection': True},
            's01e001': {'info_hash': info_hash, 'file_index': 4,
                        'relative_path': 'Batch/Season 01/show - S01E01.mkv',
                        'season': 1, 'episode': 1, 'from_batch_selection': True},
            's02e001': {'info_hash': info_hash, 'file_index': 8,
                        'relative_path': 'Batch/Season 02/show - S02E01.mkv',
                        'season': 2, 'episode': 1, 'from_batch_selection': True},
        }, 'watched': {}, 'positions': {}, 'durations': {}},
    })
    monkeypatch.setattr(anime_api, '_lib_write', lambda value: None)
    anime_api._qbt_files_cache.clear()
    anime_api._qbt_file_details_cache.clear()
    anime_api._scan_cache.clear()
    anime_api._folder_checked_at.clear()

    response = anime_client().get('/api/anime/library')
    assert response.status_code == 200
    episodes = response.get_json()[0]['episodes']
    ep1 = next(ep for ep in episodes if ep.get('episode_key') == 's01e001')
    ep2 = next(ep for ep in episodes if ep.get('episode_key') == 's02e001')
    assert ep1['progress'] == 25.0 and ep1['in_local'] is False
    assert ep2['progress'] == 100.0 and ep2['in_local'] is True
    assert ep1['in_qbt'] is True and ep2['in_qbt'] is True


def test_resolve_selected_slot_uses_relative_path(monkeypatch, tmp_path):
    from api import anime as anime_api

    info_hash = 'f' * 40
    content = tmp_path / 'Batch'
    (content / 'Season 02').mkdir(parents=True)
    selected = content / 'Season 02' / 'show - S02E01.mkv'
    selected.write_bytes(b'video')
    monkeypatch.setattr(anime_api, '_q', lambda method, path, **kwargs: Reply([
        {'hash': info_hash, 'save_path': str(tmp_path), 'content_path': str(content)}
    ]))
    video, error = anime_api.resolve_episode_video({
        'anime_id': '42', 'episode': 1, 'info_hash': info_hash,
        'relative_path': 'Batch/Season 02/show - S02E01.mkv',
    })
    assert error is None
    assert video == str(selected)


def test_existing_partial_file_is_not_treated_as_local(monkeypatch, tmp_path):
    info_hash = '2' * 40
    root = tmp_path / 'Batch'
    root.mkdir()
    (root / 'Show - S01E01.mkv').write_bytes(b'short')
    raw = [{'index': 5, 'name': 'Batch/Show - S01E01.mkv', 'size': 100, 'progress': 0, 'priority': 1}]

    def fake_q(method, path, **kwargs):
        if path == '/torrents/info':
            return Reply([{'hash': info_hash, 'name': 'Batch', 'save_path': str(tmp_path),
                           'content_path': str(root)}])
        if path == '/torrents/files':
            return Reply(raw)
        raise AssertionError(path)

    monkeypatch.setattr(anime_batch.anime, '_q', fake_q)
    files = client().get('/api/anime/batch/files?hash=' + info_hash).get_json()['files']
    assert files[0]['already_local'] is False
    assert files[0]['selected'] is True


def test_delete_granular_episode_keeps_shared_torrent(monkeypatch, tmp_path):
    from api import anime as anime_api
    info_hash = '3' * 40
    root = tmp_path / 'Batch'
    root.mkdir()
    victim = root / 'Show - S01E01.mkv'
    victim.write_bytes(b'video')
    lib = {'42': {'episodes': {
        '0': {'info_hash': info_hash, 'from_batch_selection': True},
        's01e001': {'info_hash': info_hash, 'file_index': 4,
                    'relative_path': 'Batch/Show - S01E01.mkv',
                    'from_batch_selection': True},
        's01e002': {'info_hash': info_hash, 'file_index': 5,
                    'relative_path': 'Batch/Show - S01E02.mkv',
                    'from_batch_selection': True},
    }, 'batch_files': {info_hash: {'files': {'4': {}, '5': {}}}},
    'local_path': str(tmp_path)}}
    calls = []

    def fake_q(method, path, **kwargs):
        calls.append((method, path, kwargs.get('data')))
        if path == '/torrents/info':
            return Reply([{'hash': info_hash, 'save_path': str(tmp_path)}])
        return Reply(status=200)

    monkeypatch.setattr(anime_api, '_lib_read', lambda: lib)
    monkeypatch.setattr(anime_api, '_lib_write', lambda value: None)
    monkeypatch.setattr(anime_api, '_q', fake_q)
    response = anime_client().delete('/api/anime/library/42/episode/s01e001',
                                      json={'delete_files': True})
    assert response.status_code == 200
    assert not victim.exists()
    assert ('post', '/torrents/delete', {'hashes': info_hash, 'deleteFiles': 'true'}) not in calls
    assert ('post', '/torrents/filePrio', {'hash': info_hash, 'id': '4', 'priority': '0'}) in calls
    assert 's01e002' in lib['42']['episodes']
