"""El filtro de tipografiado: no mandar a traducir lo que no es lenguaje.

Caso real que lo motivó (Sonny Boy S01E05): la pista de DIÁLOGO trae 179.782 líneas y 87 MB por
un efecto de «lluvia de código» — miles de líneas de 50 ms con contenido tipo `+$/a*6+$MF&9%$`.
De 4.941 textos únicos sólo 355 eran diálogo. Además de multiplicar el trabajo por 14, ese
galimatías es imposible de devolver en el formato `N|texto`, así que cada lote se bisecaba hasta
lotes de 2 y el ritmo caía de 0,34 a 3,75 s/línea: 41 horas para 8 episodios.

Lo que estos tests protegen NO es el ahorro, es el riesgo: que el filtro no se coma diálogo.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from api.subtitle import _es_tipografiado, _parse_ass, _ratio_letras


def test_basura_del_efecto_se_descarta():
    for basura in ('+$/a*6+$MF&9%$', '?(z=n%W;S[s+y', '{|}~`^*&%$#@!><', '01001010 11%$'):
        assert _es_tipografiado('Sign', basura), basura


def test_dialogo_nunca_se_descarta_aunque_el_estilo_parezca_cartel():
    # La condición de estilo NO basta por sí sola: la letra del ED vive en un estilo de créditos
    # y sí se traduce. Medido sobre el fichero real de Sonny Boy.
    assert not _es_tipografiado('ED - Credits', "From this moment on we're standing eye to eye")
    assert not _es_tipografiado('Sign', 'Nishi High School')
    assert not _es_tipografiado('Signs (mat)', 'Cerrado por vacaciones')
    assert not _es_tipografiado('OP - Song', 'Todo lo que quería decir')


def test_un_estilo_de_dialogo_jamas_se_filtra():
    # Aunque el texto sea raro: si no es un estilo de cartel, se traduce. Fallo SEGURO.
    assert not _es_tipografiado('Default', '?!?!')
    assert not _es_tipografiado('Default', '+$/a*6+$MF&9%$')
    assert not _es_tipografiado('Overlap', '...')
    assert not _es_tipografiado('', '#@$%^&')


def test_ratio_de_letras():
    assert _ratio_letras('Hola mundo') == 1.0
    assert _ratio_letras('') == 0.0
    assert _ratio_letras('   ') == 0.0
    assert _ratio_letras('ab12') == 0.5


def test_los_escapes_ASS_no_cuentan_como_simbolos():
    # `\h` es un espacio duro del formato. Contarlo hundía la proporción de un cartel corto y
    # lo hacía parecer decorado: es el falso positivo real que apareció al validar la biblioteca.
    assert not _es_tipografiado('sign_602_9_Hot__Hot___', r'\h\hHot! Hot!!!\h')
    assert not _es_tipografiado('Signs', '「Life, Death, and...」')


def test_el_parser_guarda_el_estilo():
    ass = (
        '[Events]\n'
        'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
        'Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Hola\n'
        'Dialogue: 0,0:00:03.00,0:00:04.00,Sign,,0,0,0,,{\\pos(1,2)}+$/a*6\n'
    )
    _h, evs = _parse_ass(ass)
    dial = [e for e in evs if not e['passthrough']]
    assert [e['style'] for e in dial] == ['Default', 'Sign']


def test_el_parser_respeta_un_Format_con_otro_orden():
    # Si el orden de columnas cambia, leer Style por índice fijo apuntaría a otra cosa y el
    # filtro decidiría con basura. El Format: manda.
    ass = (
        '[Events]\n'
        'Format: Layer, Style, Start, End, Name, MarginL, MarginR, MarginV, Effect, Text\n'
        'Dialogue: 0,Sign,0:00:01.00,0:00:02.00,,0,0,0,,algo\n'
    )
    _h, evs = _parse_ass(ass)
    dial = [e for e in evs if not e['passthrough']]
    assert dial[0]['style'] == 'Sign'
