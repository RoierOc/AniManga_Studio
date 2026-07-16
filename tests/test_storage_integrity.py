"""El chequeo de integridad: ¿lo que hay en disco sigue siendo lo que el torrent dice?

Existe porque los vídeos SON el contenido que qBittorrent siembra, y escribir encima rompe el hash
en silencio (qBittorrent sigue diciendo `stalledUP`). Encontró 2 archivos rotos de 137 que llevaban
semanas así.

El test que más importa es `test_our_sidecar_is_not_corruption`: al traducir dejamos
`<vídeo>.spa.ass` JUNTO al vídeo. Si el chequeo midiera carpetas en vez de ficheros declarados,
cada episodio traducido saldría como corrupto — un detector que grita en los casos buenos se
ignora, y entonces no detecta nada.

Correr:  python -m pytest tests/test_storage_integrity.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class FakeResp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


@pytest.fixture()
def qbt(monkeypatch):
    """Sustituye el cliente de qBittorrent. Nada de red ni de biblioteca real."""
    state = {'info': [], 'files': {}}

    def _q(method, path, **kw):
        if path == '/torrents/info':
            return FakeResp(state['info'])
        if path == '/torrents/files':
            return FakeResp(state['files'].get(kw.get('params', {}).get('hash'), []))
        raise AssertionError(f'ruta inesperada: {path}')

    import api.anime
    monkeypatch.setattr(api.anime, '_q', _q)
    monkeypatch.setattr('api.storage._qbt_wsl_path', lambda p: p)
    return state


def _video(tmp_path, name, size):
    f = tmp_path / name
    f.write_bytes(b'\0' * size)
    return f


def test_matching_file_is_ok(qbt, tmp_path):
    from api.storage import torrent_integrity
    f = _video(tmp_path, 'Ep01.mkv', 1000)
    qbt['info'] = [{'name': 'Ep01', 'content_path': str(f), 'total_size': 1000, 'state': 'stalledUP'}]
    r = torrent_integrity()
    assert r['available'] and r['checked'] == 1 and r['ok'] == 1
    assert r['mismatched'] == []


def test_rewritten_file_is_reported(qbt, tmp_path):
    """El caso real: el episodio creció al incrustarle una pista y el torrent quedó roto."""
    from api.storage import torrent_integrity
    f = _video(tmp_path, 'Ep10.mkv', 1000 + 35677)
    qbt['info'] = [{'name': 'Ep10', 'content_path': str(f), 'total_size': 1000, 'state': 'stalledUP'}]
    r = torrent_integrity()
    assert r['ok'] == 0 and len(r['mismatched']) == 1
    m = r['mismatched'][0]
    assert m['diff'] == 35677 and m['declared'] == 1000 and m['state'] == 'stalledUP'


def test_our_sidecar_is_not_corruption(qbt, tmp_path):
    """EL TEST QUE IMPORTA. El sidecar `.spa.ass` vive junto al vídeo pero NO lo declara el
    torrent: no puede contar como bytes de más. Si esto falla, cada episodio traducido se
    reporta como roto y el chequeo se vuelve ruido."""
    from api.storage import torrent_integrity
    f = _video(tmp_path, 'Ep02.mkv', 1000)
    (tmp_path / 'Ep02.spa.ass').write_bytes(b'x' * 32045)      # nuestra traducción
    (tmp_path / 'Ep02.es_injected').write_bytes(b'1')
    qbt['info'] = [{'name': 'Ep02', 'content_path': str(f), 'total_size': 1000, 'state': 'stalledUP'}]
    r = torrent_integrity()
    assert r['ok'] == 1 and r['mismatched'] == []


def test_multifile_torrent_checks_each_declared_file(qbt, tmp_path):
    from api.storage import torrent_integrity
    d = tmp_path / 'Season'
    d.mkdir()
    (d / 'a.mkv').write_bytes(b'\0' * 500)
    (d / 'b.mkv').write_bytes(b'\0' * 700)          # el torrent dice 600 -> roto
    (d / 'a.spa.ass').write_bytes(b'x' * 999)       # nuestro: no declarado, se ignora
    qbt['info'] = [{'name': 'Season', 'hash': 'H', 'content_path': str(d),
                    'save_path': str(tmp_path), 'total_size': 1100, 'state': 'stalledUP'}]
    qbt['files']['H'] = [{'name': 'Season/a.mkv', 'size': 500}, {'name': 'Season/b.mkv', 'size': 600}]
    r = torrent_integrity()
    assert r['checked'] == 2 and r['ok'] == 1
    assert [m['file'] for m in r['mismatched']] == ['b.mkv']


def test_missing_file_is_skipped_not_reported_as_broken(qbt, tmp_path):
    """Un archivo movido o borrado no es corrupción. Alarmar por eso entrena a ignorar el aviso."""
    from api.storage import torrent_integrity
    qbt['info'] = [{'name': 'Ido', 'hash': 'H', 'content_path': str(tmp_path / 'no_existe.mkv'),
                    'save_path': str(tmp_path), 'total_size': 10, 'state': 'pausedUP'}]
    qbt['files']['H'] = [{'name': 'no_existe.mkv', 'size': 10}]
    r = torrent_integrity()
    assert r['mismatched'] == [] and r['skipped'] == 1 and r['checked'] == 0


def test_qbt_offline_says_so_instead_of_reporting_everything_clean(qbt, monkeypatch):
    """Si qBittorrent no responde NO puede salir '0 problemas': eso es exactamente el fallo
    silencioso que este panel viene a cazar (ver E-12)."""
    import api.anime
    def _boom(*a, **k):
        raise RuntimeError('connection refused')
    monkeypatch.setattr(api.anime, '_q', _boom)
    from api.storage import torrent_integrity
    r = torrent_integrity()
    assert r['available'] is False
    assert 'reason' in r and r.get('mismatched') is None
