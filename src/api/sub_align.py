"""El contrato de alineación entre las líneas que se mandan a traducir y las que vuelven.

Por qué existe
──────────────
La traducción manda las líneas numeradas (`N|texto`) y se fía del número que devuelve el modelo
para colocar cada traducción en su sitio. Cuando una frase ocupa DOS subtítulos —«Yeah, this
Detective Shibazaki» / «should've figured out the connection…»— el modelo tiende a fusionarlas en
una sola salida y a seguir numerando: a partir de ahí, todo el lote queda corrido y cada subtítulo
lleva el texto del siguiente. En pantalla se lee la respuesta ANTES que la pregunta, y parece que
el modelo alucinó cuando en realidad tradujo bien y se colocó mal.

MEDIDO el 2026-07-31 sobre los 11 episodios de Zankyou no Terror ya traducidos con `qwen2.5:14b`:
**170 de 3390 líneas corridas (5 %), en 4 episodios** (1, 5, 6 y 8), siempre en tramos seguidos.
Reproducido después en banco con `Tower-Plus-9B`, así que **no es cosa de un modelo**: es del canal.

Y era invisible por construcción: la única guarda era «reintenta si vuelven menos del 85 % de las
líneas», y UNA fusión en un lote de 80 devuelve el 98,75 %. Un lote corrido y uno perfecto daban
exactamente el mismo resultado — la ambigüedad que el repo prohíbe («falló» ≠ «no había»).

Las dos puertas de aquí son complementarias, y hacen falta las dos:
  · `parse_numeradas`  — el modelo fusionó y devolvió N-1 líneas ⇒ faltan índices.
  · `hay_corrimiento`  — el modelo fusionó DOS y partió otra para cuadrar el total ⇒ los índices
                         están completos pero el contenido va desplazado. Sin modelo ni red: sólo
                         longitudes, así que cuesta microsegundos y no puede fallar por su cuenta.
"""
from __future__ import annotations

import re

_NUM = re.compile(r'^\s*(\d+)\s*\|(.*)$')

# Hasta cuántas posiciones se busca el desplazamiento. El de Zankyou era de DOS líneas y el primer
# detector, que sólo miraba una, lo dio por bueno; de ahí que el 5 % medido sea un suelo.
MAX_DESPLAZAMIENTO = 3
# Cuánto mejor tiene que explicar el desplazado para creérselo. Con 0.72 no salta ningún falso
# positivo en los 7 episodios que están limpios, y sí saltan los 4 que están mal.
UMBRAL = 0.72
# Por debajo de esto no hay señal: en un puñado de líneas cortas las longitudes casan por azar.
MIN_LINEAS = 8


def parse_numeradas(raw: str, n: int) -> tuple[list, set]:
    """Devuelve (textos, índices vistos) de una respuesta `N|texto`.

    No decide nada: sólo separa. Quien decide es `indices_completos`, para que la comprobación
    sea explícita en el sitio donde importa y no un efecto colateral del parseo.
    """
    out = [None] * n
    vistos = set()
    for linea in (raw or '').splitlines():
        m = _NUM.match(linea)
        if not m:
            continue
        i = int(m.group(1)) - 1
        if 0 <= i < n:
            out[i] = m.group(2)
            vistos.add(i)
    return out, vistos


def indices_completos(vistos: set, n: int) -> bool:
    """El contrato: TODOS los índices, cada uno una vez. Sin margen.

    El 85 % de antes no era un umbral laxo, era ninguno: el fallo que buscamos cabe entero dentro
    del 15 % de tolerancia.
    """
    return len(vistos) == n and vistos == set(range(n))


def _limpio(t: str) -> str:
    t = re.sub(r'\{[^}]*\}', '', t or '').replace('\\N', ' ').replace('\\n', ' ')
    return re.sub(r'\s+', ' ', t).strip()


def hay_corrimiento(originales: list, traducidas: list) -> int:
    """¿La traducción está pegada unas líneas más arriba o más abajo de su sitio?

    Compara la longitud de cada línea traducida con la de su original y con la de sus vecinas: una
    traducción al español mide parecido a su fuente, así que si las vecinas explican MUCHO mejor el
    conjunto que la propia, el lote está desplazado.

    Devuelve el desplazamiento detectado (…-2, -1, 0, 1, 2…); 0 = alineado.
    No sabe español ni lo necesita: por eso vale igual para cualquier modelo, hoy y dentro de un año.
    """
    n = min(len(originales), len(traducidas))
    if n < MIN_LINEAS:
        return 0
    o = [len(_limpio(x)) for x in originales[:n]]
    t = [len(_limpio(x)) for x in traducidas[:n]]

    def coste(d: int) -> float:
        pares = [(t[i], o[i + d]) for i in range(n) if 0 <= i + d < n]
        return sum(abs(a - b) for a, b in pares) / max(1, len(pares))

    base = coste(0)
    if base == 0:
        return 0
    mejor, mejor_d = base, 0
    for d in range(-MAX_DESPLAZAMIENTO, MAX_DESPLAZAMIENTO + 1):
        if d == 0:
            continue
        c = coste(d)
        if c < mejor:
            mejor, mejor_d = c, d
    return mejor_d if mejor < base * UMBRAL else 0


# Ventana del barrido de fin de episodio. Un tramo corrido puede ser corto (el del ep6 de Zankyou
# eran 24 líneas de 317) y con ventanas grandes se diluye en la media: con 60 se escapaba, con 30
# salen los 4 episodios malos y ninguno de los 7 buenos. MEDIDO sobre la serie entera.
VENTANA = 30


def revisar_episodio(originales: list, traducidas: list, ventana: int = VENTANA) -> list:
    """Barre el episodio ENTERO buscando tramos corridos. Devuelve [(posición, desplazamiento)].

    La puerta por lote ya rechaza lo que puede; esto es el cinturón: comprueba el resultado FINAL,
    que es lo único que el usuario va a ver. Lista vacía = alineado (y aquí «vacío» sí significa
    «no había», porque el barrido no puede fallar por su cuenta: son restas de longitudes).
    """
    n = min(len(originales), len(traducidas))
    if n < MIN_LINEAS:
        return []
    paso = max(1, ventana // 3)     # solapado: un tramo a caballo entre dos ventanas se vería mal
    fuera = []
    for i in range(0, max(1, n - ventana + 1), paso):
        d = hay_corrimiento(originales[i:i + ventana], traducidas[i:i + ventana])
        if d:
            fuera.append((i, d))
    return fuera
