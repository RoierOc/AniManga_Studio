#!/usr/bin/env python3
"""Limpia los episodios que quedaron «vistos» Y «a medias» a la vez.

El fallo está arreglado en `anime.py::_guardar_posicion` (retomar un episodio lo desmarca a partir
de dos minutos), pero lo que ya estaba escrito en el disco sigue mintiendo: 21 episodios de 14
series tienen posición guardada y `watched: true` al mismo tiempo, y en la app salen como vistos.

Aplica la MISMA regla a lo ya guardado: posición por encima de dos minutos → deja de estar visto.
Se puede deshacer marcándolos a mano, y el fichero se escribe con copia (`.bak`).

    .venv/bin/python scripts/arreglar_vistos_a_medias.py          # sólo enseña qué haría
    .venv/bin/python scripts/arreglar_vistos_a_medias.py --aplica
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Sin esto, `MANGA_DIR` cae al valor por defecto (`<repo>/data`) y el script mira una biblioteca
# vacía: dice «0 episodios» y parece que no hay nada que arreglar. Es la misma trampa de siempre —
# medir en el proceso equivocado da un cero que se lee como un «no pasa nada».
from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from api.anime import _lib_path, _lib_read, _WATCHED_TAIL_SECS  # noqa: E402
from api.runtime import write_json_atomic  # noqa: E402


def main() -> int:
    aplica = '--aplica' in sys.argv
    lib = _lib_read()
    tocados = []

    for entrada in lib.values():
        vistos = entrada.get('watched') or {}
        for ep, seg in (entrada.get('positions') or {}).items():
            if vistos.get(ep) and seg > _WATCHED_TAIL_SECS:
                tocados.append((entrada.get('title', '?'), ep, seg,
                                (entrada.get('durations') or {}).get(ep)))
                if aplica:
                    vistos.pop(ep, None)

    for titulo, ep, seg, dur in sorted(tocados):
        print(f'{titulo} · ep {ep}: {seg} s de {dur} s')
    print(f'\n{len(tocados)} episodios en {len({t[0] for t in tocados})} series')

    if not tocados:
        return 0
    if aplica:
        write_json_atomic(_lib_path(), lib, indent=2, keep_backup=True)
        print('Aplicado. La copia anterior queda en el .bak de al lado.')
    else:
        print('Nada tocado. Vuelve a lanzarlo con --aplica si te parece bien.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
