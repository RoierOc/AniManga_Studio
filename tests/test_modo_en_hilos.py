"""El modo de biblioteca tiene que VIAJAR al hilo de trabajo.

El modo es por hilo, así que un `threading.Thread` nace en la biblioteca normal aunque lo lance una
petición marcada como oculta. Eso hacía que «escalar este capítulo» pedido desde el móvil estando en
la oculta escribiera en la biblioteca de siempre: 200 OK, sin error y sin log. El fallo silencioso
más caro posible, porque el resultado aparece en el sitio equivocado días después.

`thread_guard` es la costura por la que pasan TODOS los hilos de trabajo de la app, y es donde se
arregla una sola vez en vez de en cada endpoint.
"""
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def test_el_hilo_hereda_la_biblioteca_de_quien_lo_lanza():
    from api import runtime
    from api.observability import thread_guard

    visto = {}

    def trabajo():
        visto['dir'] = runtime.manga_dir()

    try:
        runtime.set_request_library_mode('hidden')
        # El envoltorio se construye AQUÍ, en el hilo de la petición: es lo que le deja leer el
        # modo bueno. Envolver dentro del hilo llegaría tarde.
        h = threading.Thread(target=thread_guard('test')(trabajo))
        h.start()
        h.join()
        assert visto['dir'] == runtime.HIDDEN_MANGA_DIR, 'el hilo escribió en la biblioteca normal'
    finally:
        runtime.set_request_library_mode(None)


def test_sin_marca_el_hilo_va_a_la_normal():
    from api import runtime
    from api.observability import thread_guard

    visto = {}
    runtime.set_request_library_mode(None)
    h = threading.Thread(target=thread_guard('test')(lambda: visto.update(dir=runtime.manga_dir())))
    h.start()
    h.join()
    assert visto['dir'] == runtime.MANGA_DIR


def test_el_envoltorio_sigue_tragandose_la_excepcion():
    """Lo que ya hacía no puede haberse roto: un hilo que revienta no debe tumbar nada."""
    from api.observability import thread_guard

    def revienta():
        raise RuntimeError('a propósito')

    h = threading.Thread(target=thread_guard('test')(revienta))
    h.start()
    h.join()   # si el envoltorio dejara escapar la excepción, el hilo moriría igual: se comprueba
    assert not h.is_alive()
