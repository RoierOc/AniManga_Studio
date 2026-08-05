"""El contrato de alineación de subtítulos.

Es la costura con el historial de fallo silencioso más caro de la traducción: un lote CORRIDO y un
lote perfecto devolvían exactamente lo mismo, así que el episodio salía con la respuesta antes que
la pregunta y nadie se enteraba hasta verlo. Los casos de aquí salen de datos REALES: el tramo de
Zankyou no Terror ep5 (09:36-10:09) que se midió desplazado dos líneas.

Correr:  .venv/bin/python -m pytest tests/test_sub_align.py -q
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from api.sub_align import hay_corrimiento, indices_completos, parse_numeradas, revisar_episodio

# El tramo real. `EN` son los subtítulos ingleses; `ES_OK` la traducción bien colocada.
EN = [
    'an angel who planted a grapevine in the Garden of Eden',
    'and incurred the wrath of God.',
    'His name was Sammael, an angel also known as the Red Serpent.',
    'A red angel...',
    'We can assume that the punishment corresponding with the file is forced running.',
    "A red serpent that's forced to run.",
    'What does that remind you of?',
    'A red serpent... running...',
    'What is stretched out like a snake and runs?',
    'A train?',
    "That's right. And a red train in the middle of the city would be...?",
    'The Shuto Shinjuku Line!',
]
ES_OK = [
    'a un ángel que plantó una vid en el Jardín del Edén',
    'y se ganó la ira de Dios.',
    'Su nombre era Sammael, un ángel también conocido como la Serpiente Roja.',
    'Un ángel rojo...',
    'Podemos asumir que el castigo correspondiente al archivo es correr forzado.',
    'Una serpiente roja que está obligada a correr.',
    '¿Qué te recuerda eso?',
    'Una serpiente roja... corriendo...',
    '¿Qué se extiende como una serpiente y corre?',
    '¿Un tren?',
    'Eso es. Y un tren rojo en medio de la ciudad sería...?',
    '¡La Línea Shuto Shinjuku!',
]


def _corrido(n):
    """La traducción pegada n líneas más arriba: el fallo tal cual salió en producción."""
    return ES_OK[n:] + ES_OK[:n]


# ── Contrato de índices ───────────────────────────────────────────────────────

def test_respuesta_perfecta_pasa():
    raw = '\n'.join(f'{i+1}|{t}' for i, t in enumerate(ES_OK))
    textos, vistos = parse_numeradas(raw, len(ES_OK))
    assert indices_completos(vistos, len(ES_OK))
    assert textos == ES_OK


def test_una_fusion_deja_un_hueco():
    """El caso REAL: el modelo junta dos subtítulos de una misma frase y renumera. Devuelve el
    98,75 % de las líneas, que es justo lo que la guarda vieja del 85 % dejaba pasar."""
    salida = ES_OK[:2] + [ES_OK[2] + ' ' + ES_OK[3]] + ES_OK[4:]
    raw = '\n'.join(f'{i+1}|{t}' for i, t in enumerate(salida))
    _, vistos = parse_numeradas(raw, len(ES_OK))
    assert len(vistos) == len(ES_OK) - 1
    assert not indices_completos(vistos, len(ES_OK))


def test_un_indice_repetido_no_cuenta_dos_veces():
    raw = '1|uno\n2|dos\n2|dos otra vez'
    _, vistos = parse_numeradas(raw, 3)
    assert not indices_completos(vistos, 3)


def test_indices_fuera_de_rango_se_ignoran():
    """Un `99|` no puede escribir fuera del lote ni contar como línea entregada."""
    raw = '1|uno\n2|dos\n99|inventada'
    textos, vistos = parse_numeradas(raw, 2)
    assert textos == ['uno', 'dos'] and indices_completos(vistos, 2)


def test_lineas_sin_numerar_se_ignoran():
    """Preámbulos del tipo «Aquí tienes la traducción:» no pueden colarse como línea."""
    raw = 'Claro, aquí tienes:\n1|uno\n2|dos'
    textos, vistos = parse_numeradas(raw, 2)
    assert textos == ['uno', 'dos'] and indices_completos(vistos, 2)


def test_lo_no_devuelto_queda_en_None():
    """Para que quien llama pueda dejar el ORIGINAL: una línea sin traducir se ve y se arregla;
    una línea con el texto de otra pasa por buena."""
    textos, _ = parse_numeradas('1|uno', 3)
    assert textos == ['uno', None, None]


# ── Detector de corrimiento ───────────────────────────────────────────────────

def test_traduccion_alineada_no_dispara():
    assert hay_corrimiento(EN, ES_OK) == 0


def test_detecta_el_desfase_de_una_linea():
    assert hay_corrimiento(EN, _corrido(1)) != 0


def test_detecta_el_desfase_de_DOS_lineas():
    """El de Zankyou era de dos, y el primer detector —que sólo miraba una— lo dio por bueno.
    Por eso el 5 % que se midió es un suelo y no un total."""
    assert hay_corrimiento(EN, _corrido(2)) != 0


def test_pocas_lineas_no_inventan_corrimiento():
    """Con un puñado de líneas cortas las longitudes casan por azar: mejor callar que dar un
    falso positivo que mandaría a rehacer un lote bueno."""
    assert hay_corrimiento(EN[:4], ES_OK[1:5]) == 0


def test_no_dispara_con_traduccion_libre():
    """Traducir no conserva la longitud exacta. Si el detector saltara con esto, cada lote se
    rehría en bucle y la traducción no terminaría nunca."""
    libre = [t.upper()[:max(3, len(t) - 6)] for t in ES_OK]
    assert hay_corrimiento(EN, libre) == 0


def test_no_revienta_con_listas_vacias_o_desiguales():
    assert hay_corrimiento([], []) == 0
    assert hay_corrimiento(EN, ES_OK[:3]) == 0


# ── Barrido de fin de episodio ────────────────────────────────────────────────

def test_barrido_encuentra_un_tramo_corrido_en_medio():
    """El caso del ep6: un tramo corto corrido dentro de un episodio por lo demás correcto. Con
    ventanas grandes se diluía en la media y pasaba por bueno."""
    en = EN * 12                       # 144 líneas
    es = ES_OK * 12
    es[60:84] = (ES_OK * 2)[2:26]      # 24 líneas corridas en medio, como el ep6
    assert revisar_episodio(en, es), 'un tramo corrido en medio no puede pasar desapercibido'


def test_barrido_no_marca_un_episodio_bueno():
    assert revisar_episodio(EN * 12, ES_OK * 12) == []
