"""El caché de imágenes se resolvía con `glob('<key>.*')` sobre el directorio ENTERO — 7796
ficheros, 3,4 ms de Python con el GIL cogido, en CADA petición. Una rejilla pide 18 portadas a la
vez y el colapso era superlineal: 8 hilos tardaban 1881 ms (un CSS estático de 199 KB por el mismo
servidor, 17 ms). Ahora se prueban las extensiones con `stat()` y se memoriza el acierto.

Lo que se fija aquí es la costura peligrosa: que el índice no MIENTA. Un índice que sigue
afirmando que un fichero existe después de borrarlo serviría un error en vez de re-descargar.
"""
import importlib


def _mod(tmp_path, monkeypatch):
    from api import imgproxy
    importlib.reload(imgproxy)
    monkeypatch.setattr(imgproxy, '_CACHE_DIR', tmp_path)
    imgproxy._INDEX.clear()
    return imgproxy


def test_encuentra_el_fichero_sea_cual_sea_su_extension(tmp_path, monkeypatch):
    m = _mod(tmp_path, monkeypatch)
    for ext in ('.jpg', '.png', '.webp'):
        key = 'k' + ext.strip('.')
        (tmp_path / f'{key}{ext}').write_bytes(b'xx')
        assert m._lookup(key).name == f'{key}{ext}'


def test_un_fichero_vacio_NO_cuenta_como_acierto(tmp_path, monkeypatch):
    # Un .part a medias o un disco lleno dejan un fichero de 0 bytes: servirlo sería peor que
    # volver a bajarlo. El `glob` original ya lo comprobaba; no se pierde al cambiar de mecanismo.
    m = _mod(tmp_path, monkeypatch)
    (tmp_path / 'vacio.jpg').write_bytes(b'')
    assert m._lookup('vacio') is None


def test_si_lo_borran_por_debajo_el_indice_no_miente(tmp_path, monkeypatch):
    m = _mod(tmp_path, monkeypatch)
    p = tmp_path / 'kk.jpg'
    p.write_bytes(b'xx')
    assert m._lookup('kk') == p          # entra en el índice
    p.unlink()
    assert m._lookup('kk') is None       # y sale de él al desaparecer


def test_un_fallo_NO_se_cachea(tmp_path, monkeypatch):
    # Sólo se memorizan aciertos: `warm()` escribe desde otro hilo, y cachear el "no está"
    # dejaría esa portada invisible hasta reiniciar.
    m = _mod(tmp_path, monkeypatch)
    assert m._lookup('tarde') is None
    (tmp_path / 'tarde.jpg').write_bytes(b'xx')
    assert m._lookup('tarde') is not None


def test_la_extension_escrita_es_siempre_una_que_se_sabe_buscar(tmp_path, monkeypatch):
    m = _mod(tmp_path, monkeypatch)
    assert m._ext_for('image/jpeg', 'http://x/a') in m._EXTS
    assert m._ext_for('image/bmp', 'http://x/a.bmp') == '.jpg'   # desconocida → .jpg, no un huérfano
