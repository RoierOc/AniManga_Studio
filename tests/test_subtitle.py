"""Costuras del módulo de subtítulos que YA se han roto una vez.

No busca cobertura: cubre sólo los sitios con historial de bugs silenciosos, los que fallan
"plausible" y no se notan hasta que ves un episodio raro semanas después.

  - mask/unmask de tags ASS: el LLM se comía las cursivas y los `\\N`.
  - dibujos vectoriales (`\\p1`): se traducían y se pintaban como texto sobre el vídeo.
  - restyle: pisaba la tipografía de los CARTELES, descuadrándolos del arte (ver docstring
    de `restyle_ass_header`).
  - prioridad de idiomas: `slang` es una LISTA DE PRIORIDAD y mpv coge la PRIMERA que case;
    tener `spa` delante hacía que `es-419` no se mirara nunca. Debe ir en lockstep con
    `desktop/native/src/player.rs`.

Correr:  python -m pytest tests/test_subtitle.py -q
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from api.subtitle import (  # noqa: E402
    _is_sign_style,
    ass_style_overrides,
    is_ass_drawing,
    mask_ass_tags,
    restyle_ass_header,
    unmask_ass_tags,
)

REPO = os.path.join(os.path.dirname(__file__), '..')


# --------------------------------------------------------------------------- dibujos
@pytest.mark.parametrize('text, drawing', [
    (r'{\p1}m 0 0 l 230 0 l 230 44{\p0}', True),
    (r'{\p4}m 0 0 s 1 1', True),
    (r'{\pos(190,270)}Hola', False),       # \pos NO es \p
    (r'{\i1}Cursiva{\i0}', False),
    ('Texto plano', False),
])
def test_is_ass_drawing(text, drawing):
    assert is_ass_drawing(text) is drawing


# --------------------------------------------------------------------------- mask/unmask
@pytest.mark.parametrize('src', [
    r'{\i1}Vaya{\i0}, qué frío',
    r'{\pos(1,2)}Uno{\r}\NDos',
    r'{\an8}Solo al inicio',
    r'{\fad(231,1)\3c&H4C4745&}Con varios tags',
    'Sin tags ninguno',
    '',
])
def test_mask_unmask_roundtrip(src):
    masked, tags = mask_ass_tags(src)
    assert unmask_ass_tags(masked, tags, src) == src


def test_masked_text_hides_tags_from_llm():
    masked, _ = mask_ass_tags(r'{\i1}Hello{\i0} world')
    assert '{' not in masked and '}' not in masked
    assert 'Hello' in masked and 'world' in masked


def test_lost_marker_loses_the_tag_never_the_text():
    """Si el modelo se come un marcador se pierde el TAG. El texto es sagrado."""
    src = r'{\i1}Hello{\i0} world'
    _, tags = mask_ass_tags(src)
    out = unmask_ass_tags('Hola mundo', tags, src)          # sin ningún marcador
    assert 'Hola mundo' in out


# --------------------------------------------------------------------------- carteles
@pytest.mark.parametrize('name', [
    'sign_2503_32_Ryugu_s_First_Sp', 'Sign_Arial', 'Cart_A_Tre', 'Cart_C_Tim',
    'song', 'Opening Song', 'Créditos', 'Ending Credits', 'Karaoke', 'OP', 'ED', 'Signs',
])
def test_sign_styles_detected(name):
    assert _is_sign_style(name)


@pytest.mark.parametrize('name', [
    'Default', 'Default - Top', 'Italics', 'Italics Top', 'Flashback',
    'Flashback Italics', 'Overlap', 'Gen_Main', 'Gen_Italics_top', 'Editor',
])
def test_dialogue_styles_not_mistaken_for_signs(name):
    # 'Italics Top' es la trampa: contiene "op". Si casa, el diálogo se queda sin re-estilar.
    assert not _is_sign_style(name)


HEADER = [
    '[V4+ Styles]\n',
    'Format: Name, Fontname, Fontsize, PrimaryColour, Bold, Outline, Shadow\n',
    'Style: Default,Trebuchet MS,24,&H00FFFFFF,0,2,2\n',
    'Style: Italics,Trebuchet MS,24,&H00FFFFFF,0,2,2\n',
    'Style: sign_7436_74_childlike,Times New Roman,24,&H001F1E1E,0,3,1\n',
]


def _styles(header):
    return {l.split(':', 1)[1].split(',')[0].strip(): [v.strip() for v in l.split(',')]
            for l in header if l.startswith('Style:')}


@pytest.fixture()
def baked(monkeypatch):
    """El horneado está APAGADO por defecto (el estilo lo pone el player en vivo). Los tests que
    comprueban CÓMO hornea tienen que encenderlo a mano."""
    monkeypatch.setattr('api.subtitle._STYLE_FONT', 'Adobe Arabic')


def test_baked_restyle_is_off_by_default():
    """Si esto falla, hay DOS dueños del estilo (horneado + player) pisándose."""
    import api.subtitle as S
    assert S._STYLE_FONT == ''
    assert restyle_ass_header(HEADER) == HEADER


def test_restyle_touches_dialogue_only(baked):
    st = _styles(restyle_ass_header(HEADER))
    assert st['Default'][1] == 'Adobe Arabic' and st['Default'][2] == '26'
    assert st['Italics'][1] == 'Adobe Arabic'
    # el cartel conserva SU fuente y SU tamaño: el grupo los casó con el arte del vídeo
    assert st['sign_7436_74_childlike'][1] == 'Times New Roman'
    assert st['sign_7436_74_childlike'][2] == '24'


def test_restyle_keeps_each_styles_own_colour(baked):
    st = _styles(restyle_ass_header(HEADER))
    assert st['Default'][3] == '&H00FFFFFF'


def test_restyle_reads_columns_by_name_not_position(baked):
    """Un Format: en otro orden no debe hacer que escribamos el tamaño encima del color."""
    header = [
        '[V4+ Styles]\n',
        'Format: Name, Fontsize, Fontname, PrimaryColour, Bold, Outline, Shadow\n',
        'Style: Default,24,Trebuchet MS,&H00FFFFFF,0,2,2\n',
    ]
    st = _styles(restyle_ass_header(header))
    assert st['Default'][1] == '26'                 # Fontsize va 2º en ESTE Format
    assert st['Default'][2] == 'Adobe Arabic'
    assert st['Default'][3] == '&H00FFFFFF'


def test_restyle_leaves_malformed_rows_alone(baked):
    header = ['[V4+ Styles]\n',
              'Format: Name, Fontname, Fontsize\n',
              'Style: Roto,Arial\n']                 # menos campos que columnas
    assert 'Style: Roto,Arial\n' in restyle_ass_header(header)


def test_restyle_disabled_by_empty_font(monkeypatch, baked):
    monkeypatch.setattr('api.subtitle._STYLE_FONT', '')
    assert restyle_ass_header(HEADER) == HEADER


# --------------------------------------------------------------------------- estilo en vivo
def test_overrides_are_emitted_per_style_never_global():
    """LA razón de ser de esto. MEDIDO con mpv sobre un fotograma con SÓLO un cartel en pantalla:
    `Default.Fontname=X` lo deja byte a byte idéntico; `Fontname=X` (sin prefijo) lo destroza.
    Si algún día sale una entrada sin prefijo, los carteles se descuadran del arte del vídeo."""
    ov = ass_style_overrides(['Default', 'Italics'], font='Adobe Arabic', size='26')
    assert ov == ('Default.Fontname=Adobe Arabic,Default.Fontsize=26,'
                  'Italics.Fontname=Adobe Arabic,Italics.Fontsize=26')
    for entry in ov.split(','):
        assert '.' in entry.split('=')[0], f'entrada sin prefijo de estilo: {entry}'


def test_empty_fields_are_left_alone_not_blanked():
    """'' = 'no toques este campo'. Mandar `Fontsize=` pondría basura en el estilo."""
    ov = ass_style_overrides(['Default'], font='Arial', size='', bold='', outline='2', shadow='')
    assert ov == 'Default.Fontname=Arial,Default.Outline=2'


def test_no_fields_means_no_override():
    assert ass_style_overrides(['Default']) == ''


def test_no_styles_means_no_override():
    assert ass_style_overrides([], font='Arial') == ''


@pytest.mark.parametrize('bad', ['Weird=Name', 'With,Comma'])
def test_styles_that_would_break_the_option_syntax_are_skipped(bad):
    """mpv separa por ',' y libass parte por el ÚLTIMO '=': un nombre así corrompería el resto
    de entradas. Se descarta ese estilo en vez de emitir algo que rompa a los demás."""
    ov = ass_style_overrides(['Default', bad], font='Arial')
    assert ov == 'Default.Fontname=Arial'


def test_zero_is_a_real_value_not_absence():
    """Shadow=0 ('sin sombra') es justo lo que el usuario quiere; no puede tratarse como vacío."""
    assert ass_style_overrides(['Default'], shadow='0') == 'Default.Shadow=0'


# --------------------------------------------------------------------------- ya está en español
@pytest.mark.parametrize('lang, es', [
    ('spa', True), ('es', True),
    ('eng', False), ('por', False), ('ara', False), ('und', False), ('', False), (None, False),
])
def test_is_es_track(lang, es):
    from api.subtitle import is_es_track
    assert is_es_track({'language': lang}) is es


def test_es_criterion_is_shared_by_every_caller():
    """El criterio de "ya está en español" decide TRES cosas: qué se ofrece traducir, qué se
    bloquea (409) y cuál es la pista fuente. Si vuelve a copiarse a mano, se desincronizan y
    acabas ofreciendo algo que el backend rechaza (o peor, traduciendo español a español)."""
    src = open(os.path.join(REPO, 'src/api/subtitle.py'), encoding='utf-8').read()
    assert "('spa', 'es')" not in src.replace("_ES_LANGS = ('spa', 'es')", ''), \
        'hay una comprobación de español a mano: usa is_es_track()/_ES_LANGS'


# --------------------------------------------------------------------------- no tocar el vídeo
def _fake(tmp_path):
    mkv = tmp_path / 'Ep10.mkv'
    mkv.write_bytes(b'x' * 4096)          # no hace falta un MKV real: el camino sidecar sólo copia
    sub = tmp_path / 'translated.ass'
    sub.write_text('[Script Info]\n', encoding='utf-8')
    return mkv, sub


def test_translating_never_rewrites_the_video(tmp_path):
    """EL PUNTO. Los .mkv son el contenido que qBittorrent siembra: reescribirlos cambia el hash
    y rompe el torrent en silencio (medido: ep10 quedó +35.677 B sobre lo que declara el suyo).
    Traducir NO puede tocar el archivo del usuario."""
    import api.subtitle as S
    mkv, sub = _fake(tmp_path)
    before = (mkv.read_bytes(), mkv.stat().st_mtime_ns)

    out = S._inject_sub(str(mkv), str(sub), 0)

    assert (mkv.read_bytes(), mkv.stat().st_mtime_ns) == before, 'el vídeo fue modificado'
    assert out == str(tmp_path / 'Ep10.spa.ass')
    assert os.path.exists(out)


def test_sidecar_is_named_so_the_player_finds_it(tmp_path):
    """El nombre no es cosmético: `_sidecar_subs` (anime.py) exige que empiece por el stem del
    vídeo y saca el idioma del sufijo. Si cambia, el sidecar deja de ser una pista."""
    import api.subtitle as S
    mkv, sub = _fake(tmp_path)
    out = os.path.basename(S._inject_sub(str(mkv), str(sub), 0))
    assert out.startswith('Ep10')
    assert '.spa.' in out
    assert out.endswith('.ass')          # conserva ASS: si cayera a .srt se perderían los estilos


def test_marker_is_written(tmp_path):
    import api.subtitle as S
    mkv, sub = _fake(tmp_path)
    S._inject_sub(str(mkv), str(sub), 0)
    assert (tmp_path / 'Ep10.es_injected').exists()


def test_retranslating_replaces_the_sidecar_not_accumulates(tmp_path):
    import api.subtitle as S
    mkv, sub = _fake(tmp_path)
    S._inject_sub(str(mkv), str(sub), 0)
    sub.write_text('[Script Info]\n; v2\n', encoding='utf-8')
    S._inject_sub(str(mkv), str(sub), 0)
    assert len(list(tmp_path.glob('Ep10*.ass'))) == 1
    assert '; v2' in (tmp_path / 'Ep10.spa.ass').read_text()


def test_embedding_is_opt_in(monkeypatch, tmp_path):
    """Por defecto NO se incrusta. Si esto falla, hemos vuelto a romper torrents."""
    import api.subtitle as S
    assert S._EMBED_IN_MKV is False


# --------------------------------------------------------------------------- prioridad
def test_sub_lang_priority_matches_player_rs():
    """anime.py y player.rs DEBEN listar los idiomas igual: si divergen, la pista que elige
    el reproductor deja de ser la que preparó el backend."""
    import api.anime as A
    langs = A._SUB_LANGS if isinstance(A._SUB_LANGS, str) else ','.join(A._SUB_LANGS)

    rs = open(os.path.join(REPO, 'desktop/native/src/player.rs'), encoding='utf-8').read()
    block = re.search(r'set_property\("slang",\s*concat!\((.*?)\)\)', rs, re.S)
    assert block, 'no encuentro el slang en player.rs'
    from_rs = ''.join(re.findall(r'"([^"]*)"', block.group(1)))

    assert [x for x in langs.split(',') if x] == [x for x in from_rs.split(',') if x]


def test_latino_beats_spain_and_english():
    import api.anime as A
    langs = A._SUB_LANGS if isinstance(A._SUB_LANGS, str) else ','.join(A._SUB_LANGS)
    order = [x for x in langs.split(',') if x]
    assert order.index('es-419') < order.index('es-es')
    assert order.index('es-es') < order.index('eng')
