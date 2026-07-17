"""Configuración compartida de pytest (Fase 0).

Único objetivo por ahora: poner `src/` en el path UNA vez, en lugar de que cada test repita
`sys.path.insert(...)`. Los inserts que ya tienen los tests siguen siendo inocuos (idempotentes),
así que este archivo no rompe nada existente; solo permite que los tests NUEVOS no necesiten el
hack.

El fixture de cliente Flask NO se añade aquí a propósito: importar la app completa arrastra
dependencias pesadas (torch/cv2) y se hará en la Fase 3, cuando testeemos endpoints con contratos.
"""
import os
import sys

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
