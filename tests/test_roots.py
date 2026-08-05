"""Biblioteca de manga repartida en VARIOS DISCOS (`src/api/roots.py`).

Lo que se fija aquí es lo que fallaría EN SILENCIO, que es todo el riesgo de esta feature:
una obra con los caps 1-10 en un disco y los 11-20 en otro NO puede verse como dos obras, ni
exportarse a medias, ni dar "ya escalado" mirando sólo un disco. Ninguna de esas cosas lanza
un error: devuelven menos páginas de las que hay.
"""
import json

import pytest

from api import roots as R


@pytest.fixture
def discos(tmp_path, monkeypatch):
    """Dos 'discos': la raíz base (C:) y una extra (D:), con sus gemelas de escalados."""
    c, cu = tmp_path / 'C', tmp_path / 'C_up'
    d, du = tmp_path / 'D', tmp_path / 'D_up'
    for p in (c, cu, d, du):
        p.mkdir()
    monkeypatch.setattr(R, 'MANGA_DIR', c)
    monkeypatch.setattr(R, 'UPSCALED_DIR', cu)
    monkeypatch.setattr(R, '_ROOTS_FILE', tmp_path / 'manga_roots.json')
    monkeypatch.setattr(R, 'get_library_mode', lambda: 'normal')
    R._write({'roots': [{'id': 'r1', 'label': 'D', 'manga': str(d), 'upscaled': str(du)}]})
    return {'c': c, 'cu': cu, 'd': d, 'du': du}


def pagina(folder, title, name):
    p = folder / title
    p.mkdir(parents=True, exist_ok=True)
    (p / name).write_bytes(b'\xff\xd8fake')
    return p / name


def test_sin_config_solo_esta_la_base(tmp_path, monkeypatch):
    # Quien nunca añadió un disco debe ver exactamente lo de siempre: una raíz, la principal.
    monkeypatch.setattr(R, '_ROOTS_FILE', tmp_path / 'no-existe.json')
    monkeypatch.setattr(R, 'get_library_mode', lambda: 'normal')
    rs = R.roots()
    assert len(rs) == 1 and rs[0]['id'] == R.BASE_ID and rs[0]['removable'] is False


def test_la_misma_obra_en_dos_discos_es_UNA(discos):
    pagina(discos['c'], 'Obra', 'ch0001_001.jpg')
    pagina(discos['d'], 'Obra', 'ch0011_001.jpg')
    titulos = R.series_titles()
    assert list(titulos) == ['Obra']          # UNA obra, no dos
    assert len(titulos['Obra']) == 2          # …viviendo en dos sitios


def test_las_paginas_se_funden_entre_discos(discos):
    pagina(discos['c'], 'Obra', 'ch0001_001.jpg')
    pagina(discos['c'], 'Obra', 'ch0001_002.jpg')
    pagina(discos['d'], 'Obra', 'ch0011_001.jpg')
    # Es LA prueba de la feature: quien lea/exporte/escale ve el capítulo entero.
    assert sorted(R.series_pages('Obra')) == ['ch0001_001.jpg', 'ch0001_002.jpg', 'ch0011_001.jpg']
    assert sorted(R.series_pages('Obra', prefix='ch0011')) == ['ch0011_001.jpg']


def test_escribir_va_al_disco_donde_ya_vive_la_obra(discos):
    pagina(discos['d'], 'Obra', 'ch0001_001.jpg')
    # No estaba en la base: lo nuevo NO puede caer ahí y partir la obra por accidente.
    assert R.series_dir('Obra').parent == discos['d']


def test_gana_el_disco_con_mas_paginas(discos):
    pagina(discos['c'], 'Obra', 'ch0001_001.jpg')
    for i in range(3):
        pagina(discos['d'], 'Obra', f'ch0011_00{i}.jpg')
    assert R.series_dir('Obra').parent == discos['d']


def test_obra_nueva_va_a_la_raiz_activa(discos):
    assert R.series_dir('Nueva').parent == discos['c']       # por defecto, la base
    R._write({**R._read(), 'active': 'r1'})
    assert R.series_dir('Nueva').parent == discos['d']


def test_el_disco_elegido_a_mano_manda(discos):
    pagina(discos['c'], 'Obra', 'ch0001_001.jpg')
    # El selector de carpeta: aunque la obra esté en C:, si pido D: se descarga en D:.
    assert R.series_dir('Obra', 'r1').parent == discos['d']


def test_el_escalado_nunca_cruza_de_disco(discos):
    p = pagina(discos['d'], 'Obra', 'ch0011_001.jpg')
    # Si el 4K cayera en otro disco, quitar el disco dejaría la obra a medias sin avisar.
    assert R.up_dir_for(p) == discos['du'] / 'Obra'


def test_una_pagina_de_la_base_va_a_la_escalada_de_la_base(discos):
    p = pagina(discos['c'], 'Obra', 'ch0001_001.jpg')
    assert R.up_dir_for(p) == discos['cu'] / 'Obra'


def test_find_file_no_necesita_saber_el_disco(discos):
    pagina(discos['d'], 'Obra', 'ch0011_001.jpg')
    # La URL de una página es `<obra>/<archivo>`: si llevara el disco, mover la obra
    # invalidaría todo enlace guardado.
    assert R.find_file('Obra/ch0011_001.jpg') is not None
    assert R.find_file('Obra/no-existe.jpg') is None


def test_prefiere_la_escalada_a_la_original(discos):
    pagina(discos['c'], 'Obra', 'ch0001_001.jpg')
    pagina(discos['cu'], 'Obra', 'ch0001_001.jpg')
    assert R.find_file('Obra/ch0001_001.jpg').parent.parent == discos['cu']
    assert R.find_file('Obra/ch0001_001.jpg', prefer_upscaled=False).parent.parent == discos['c']


def test_glob_series_no_cuenta_dos_veces_una_obra_repartida(discos):
    for disco in (discos['c'], discos['d']):
        f = disco / 'Obra'
        f.mkdir(parents=True, exist_ok=True)
        (f / '.identity.json').write_text('{}')
    # Un barrido de identidad que viera las dos copias reportaría el doble de problemas.
    assert len(R.glob_series('*/.identity.json')) == 1


def test_un_disco_desenchufado_no_rompe_nada(discos, tmp_path):
    pagina(discos['c'], 'Obra', 'ch0001_001.jpg')
    R._write({'roots': [{'id': 'r9', 'label': 'USB', 'manga': str(tmp_path / 'nope'),
                         'upscaled': str(tmp_path / 'nope_up')}]})
    # "El disco no está" no puede parecerse a "la biblioteca está vacía".
    assert list(R.series_titles()) == ['Obra']
    assert sorted(R.series_pages('Obra')) == ['ch0001_001.jpg']


def test_el_modo_oculto_no_ve_los_discos_extra(discos, monkeypatch):
    pagina(discos['d'], 'Obra', 'ch0011_001.jpg')
    monkeypatch.setattr(R, 'get_library_mode', lambda: 'hidden')
    monkeypatch.setattr(R, 'manga_dir', lambda: discos['c'] / 'oculta')
    monkeypatch.setattr(R, 'upscaled_dir', lambda: discos['cu'] / 'oculta')
    # Mezclar raíces delataría la biblioteca oculta al listar/medir.
    rs = R.roots()
    assert len(rs) == 1 and rs[0]['id'] == 'hidden'
    assert R.series_titles() == {}


# ── API ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def cliente(discos):
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(R.roots_bp, url_prefix='/api/roots')
    return app.test_client()


def test_listar_da_espacio_libre(cliente):
    d = cliente.get('/api/roots').get_json()
    assert len(d['roots']) == 2
    # El espacio libre es EL motivo por el que alguien añade un disco.
    assert all('free' in r and 'total' in r for r in d['roots'])
    assert d['active'] == R.BASE_ID


def test_anadir_crea_las_dos_carpetas(cliente, tmp_path):
    nueva = tmp_path / 'E'
    r = cliente.post('/api/roots/add', json={'manga': str(nueva)})
    assert r.status_code == 200
    assert nueva.is_dir() and (tmp_path / 'E_Upscaled').is_dir()   # la gemela, al lado


def test_no_se_puede_anadir_dos_veces_la_misma(cliente, discos):
    assert cliente.post('/api/roots/add', json={'manga': str(discos['d'])}).status_code == 400


def test_la_raiz_principal_no_se_puede_quitar(cliente):
    assert cliente.post('/api/roots/remove', json={'id': R.BASE_ID}).status_code == 400


def test_quitar_no_borra_nada_del_disco(cliente, discos):
    pagina(discos['d'], 'Obra', 'ch0011_001.jpg')
    assert cliente.post('/api/roots/remove', json={'id': 'r1'}).status_code == 200
    # Dejar de mirar un disco y borrarlo no son la misma acción.
    assert (discos['d'] / 'Obra' / 'ch0011_001.jpg').exists()


def test_quitar_la_activa_devuelve_la_activa_a_la_base(cliente):
    cliente.post('/api/roots/active', json={'id': 'r1'})
    cliente.post('/api/roots/remove', json={'id': 'r1'})
    assert R.active_id() == R.BASE_ID


def test_where_dice_en_que_discos_esta(cliente, discos):
    pagina(discos['c'], 'Obra', 'ch0001_001.jpg')
    pagina(discos['d'], 'Obra', 'ch0011_001.jpg')
    d = cliente.get('/api/roots/where/Obra').get_json()
    assert d['split'] is True
    assert {r['id']: r['pages'] for r in d['roots']} == {R.BASE_ID: 1, 'r1': 1}


def test_config_corrupta_no_tumba_la_biblioteca(discos):
    R._ROOTS_FILE.write_text('{esto no es json')
    # Un JSON ilegible deja la biblioteca en su raíz base, no sin biblioteca.
    assert [r['id'] for r in R.roots()] == [R.BASE_ID]


# ── Traducción: el caso que casi se me escapa ────────────────────────────────
# La traducción REESCRIBE las páginas en sitio. Si escribiera en el disco «principal» de la obra
# cuando el capítulo vive en el otro, no lo traduciría: lo DUPLICARÍA — el mismo capítulo dos
# veces, en dos idiomas y en dos discos, sin un solo error.

def test_chapter_dir_apunta_al_disco_del_capitulo(discos):
    pagina(discos['c'], 'Obra', 'ch0001_001.jpg')
    pagina(discos['d'], 'Obra', 'ch0011_001.jpg')
    assert R.chapter_dir('Obra', 'ch0001') == discos['c'] / 'Obra'
    assert R.chapter_dir('Obra', 'ch0011') == discos['d'] / 'Obra'


def test_chapter_dir_es_None_si_el_capitulo_no_esta(discos):
    pagina(discos['c'], 'Obra', 'ch0001_001.jpg')
    # None ≠ "el disco principal": quien escribe decide qué hacer, no recibe un destino inventado.
    assert R.chapter_dir('Obra', 'ch0099') is None


def test_liberar_espacio_olvida_la_medicion_cacheada(discos, monkeypatch, tmp_path):
    """El panel tiene que reflejar el borrado YA.

    La carpeta borrada no va a cambiar de mtime nunca más (no existe), así que un caché por
    mtime se queda contando bytes que ya no están. Esto vivía escrito como `prune()` SIN
    argumentos: un TypeError que el `except` de al lado se tragaba entero, así que liberar
    espacio no invalidaba nada y el panel seguía enseñando el tamaño viejo.
    """
    import api.storage as S
    from api import index_db

    pagina(discos['d'], 'Obra', 'ch0011_001.jpg')
    index_db.cached_measure('manga', 'Obra@r1', str(discos['d'] / 'Obra'),
                            lambda p: (999, 1, {}))
    assert index_db.cached_measure('manga', 'Obra@r1', str(discos['d'] / 'Obra'),
                                   lambda p: (0, 0, {}))[0] == 999   # cacheado

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(S.storage_bp, url_prefix='/api/storage')
    r = app.test_client().post('/api/storage/series/delete',
                               json={'title': 'Obra', 'scope': 'all'})
    assert r.status_code == 200

    (discos['d'] / 'Obra').mkdir(parents=True, exist_ok=True)   # re-medir daría 0, no 999
    assert index_db.cached_measure('manga', 'Obra@r1', str(discos['d'] / 'Obra'),
                                   lambda p: (0, 0, {}))[0] == 0


def test_escalar_NO_falla_por_elegir_otro_disco(discos):
    """El fallo que se llevó el usuario: «No se pudo iniciar el escalado».

    Lo introduje yo al mandar la carpeta elegida (`root`) al escalado. Si el disco elegido no
    tenía esa obra, la guarda `input_folder.exists()` devolvía 404 «Folder not found» — con las
    páginas perfectamente localizables en el disco de al lado.

    El selector dice dónde caen las DESCARGAS. El escalado no descarga: lee páginas que ya
    existen y escribe el 4K al lado de cada original, porque nunca cruza de disco. Así que aquí
    lo único que es un 404 es «no está en NINGÚN disco».
    """
    import api.upscale as U
    pagina(discos['c'], 'Obra', 'ch0001_001.jpg')      # la obra vive en C…
    assert not (discos['d'] / 'Obra').exists()          # …y NO en D

    inp, out = U._folders_for('Obra')
    assert inp.parent == discos['c'] and out.parent == discos['cu']
    # La firma ya no acepta un disco a mano: mandarlo era el bug.
    with pytest.raises(TypeError):
        U._folders_for('Obra', 'r1')
    assert R.series_dirs('Obra')                        # la guarda correcta: ¿está en algún disco?
    assert R.series_dirs('NoExiste') == []
