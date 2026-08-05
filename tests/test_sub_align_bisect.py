"""La bisección tiene que RESCATAR un lote que el modelo devolvió mal, no sólo detectarlo.

Se simula el fallo exacto que se midió en producción: el modelo fusiona dos subtítulos de una
misma frase y sigue numerando, con lo que devuelve N-1 líneas y todo lo posterior queda corrido.
No hace falta GPU: se sustituye la llamada HTTP a Ollama.

Correr:  .venv/bin/python -m pytest tests/test_sub_align_bisect.py -q
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import api.subtitle as SUB

EN = [f'linea inglesa numero {i} con algo de texto detras' for i in range(16)]
ES = [f'linea espanola numero {i} con algo de texto detras' for i in range(16)]


class _Resp:
    def __init__(self, texto):
        self._b = json.dumps({'message': {'content': texto}}).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _falso_ollama(fusiona_si_mayor_que, llamadas):
    """Devuelve un urlopen simulado. Fusiona las líneas 4 y 5 sólo mientras el lote sea grande:
    así se reproduce que el modelo se atraganta con lotes largos y acierta con los cortos, que es
    justo lo que hace que partir por la mitad converja."""
    def urlopen(req, timeout=None):
        cuerpo = json.loads(req.data.decode())
        pedido = cuerpo['messages'][-1]['content']
        lineas = [l for l in pedido.splitlines() if '|' in l]
        n = len(lineas)
        llamadas.append(n)
        # El texto que toca traducir, en el orden en que llegó
        fuentes = [l.split('|', 1)[1] for l in lineas]
        trad = [t.replace('inglesa', 'espanola') for t in fuentes]
        if n > fusiona_si_mayor_que:
            trad = trad[:4] + [trad[4] + ' ' + trad[5]] + trad[6:]   # fusión → renumera y corre
        return _Resp('\n'.join(f'{i+1}|{t}' for i, t in enumerate(trad)))
    return urlopen


def test_sin_verificacion_el_lote_saldria_corrido(monkeypatch):
    """Control: con la fusión, la respuesta cruda YA no cuadra. Si esto no fuera cierto, el resto
    del test no probaría nada."""
    from api.sub_align import indices_completos, parse_numeradas
    llamadas = []
    urlopen = _falso_ollama(0, llamadas)          # fusiona siempre

    class _Req:
        def __init__(self, url, data=None, headers=None, method=None):
            self.data = data
    monkeypatch.setattr(SUB._ur, 'Request', _Req)
    monkeypatch.setattr(SUB._ur, 'urlopen', urlopen)
    numbered = '\n'.join(f'{i+1}|{t}' for i, t in enumerate(EN))
    r = urlopen(_Req('x', json.dumps({'messages': [{'content': numbered}]}).encode()))
    crudo = json.loads(r.read())['message']['content']
    _, vistos = parse_numeradas(crudo, len(EN))
    assert not indices_completos(vistos, len(EN)), 'la simulación no está reproduciendo el fallo'


def test_la_biseccion_rescata_el_lote(monkeypatch):
    llamadas = []

    class _Req:
        def __init__(self, url, data=None, headers=None, method=None):
            self.data = data
    monkeypatch.setattr(SUB._ur, 'Request', _Req)
    monkeypatch.setattr(SUB._ur, 'urlopen', _falso_ollama(4, llamadas))
    monkeypatch.setattr(SUB, '_build_prompt', lambda src='eng': 'PROMPT')

    out = SUB._translate_batch_ollama_raw(list(EN), 'eng')

    assert len(out) == len(EN), 'la bisección no puede cambiar el número de líneas'
    assert out == ES, 'cada línea debe llevar SU traducción, no la del vecino'
    assert len(llamadas) > 1, 'debería haber partido el lote en vez de tragarse el fallo'


def test_un_lote_bueno_no_se_parte(monkeypatch):
    """El coste sólo se paga cuando algo va mal: un lote correcto = una sola llamada."""
    llamadas = []

    class _Req:
        def __init__(self, url, data=None, headers=None, method=None):
            self.data = data
    monkeypatch.setattr(SUB._ur, 'Request', _Req)
    monkeypatch.setattr(SUB._ur, 'urlopen', _falso_ollama(999, llamadas))   # nunca fusiona
    monkeypatch.setattr(SUB, '_build_prompt', lambda src='eng': 'PROMPT')

    out = SUB._translate_batch_ollama_raw(list(EN), 'eng')
    assert out == ES
    assert len(llamadas) == 1
