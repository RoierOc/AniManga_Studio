"""El modo de biblioteca POR PETICIÓN: que no se filtre a la petición de al lado.

El móvil pide la biblioteca oculta del PC mandando su código en una cabecera, y el servidor le
sirve la otra raíz **sólo para esa petición** — encender el modo global (`set_library_mode`) le
cambiaría la biblioteca al navegador del propio PC, que es de otra persona sentada delante.

Lo que se prueba aquí es lo único que puede salir caro: **que el marcado no sobreviva**. El
servidor atiende con un grupo de hilos que reutiliza, así que un hilo que quede marcado serviría
la biblioteca oculta a la siguiente petición normal que le toque. No hay error, no hay log, no hay
excepción: sale contenido de la carpeta oculta en la pantalla de alguien que no lo pidió.
"""
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def test_el_modo_por_peticion_manda_sobre_el_del_proceso():
    from api import runtime

    try:
        assert runtime.manga_dir() == runtime.MANGA_DIR

        runtime.set_request_library_mode('hidden')
        assert runtime.manga_dir() == runtime.HIDDEN_MANGA_DIR
        assert runtime.upscaled_dir() == runtime.HIDDEN_UPSCALED_DIR

        # `None` limpia. Es lo que hace `modo_por_peticion` al EMPEZAR cada petición, y por eso
        # limpia siempre y no sólo cuando acierta el código.
        runtime.set_request_library_mode(None)
        assert runtime.manga_dir() == runtime.MANGA_DIR
    finally:
        runtime.set_request_library_mode(None)


def test_un_hilo_marcado_no_contamina_a_los_demas():
    """El caso que importa: dos peticiones a la vez, una oculta y otra normal."""
    from api import runtime

    visto = {}
    listo = threading.Event()

    def oculto():
        runtime.set_request_library_mode('hidden')
        visto['oculto'] = runtime.manga_dir()
        listo.set()

    def normal():
        listo.wait(timeout=5)
        # Sin tocar nada: este hilo nunca pidió la oculta.
        visto['normal'] = runtime.manga_dir()

    a, b = threading.Thread(target=oculto), threading.Thread(target=normal)
    a.start(); b.start(); a.join(); b.join()

    assert visto['oculto'] == runtime.HIDDEN_MANGA_DIR
    assert visto['normal'] == runtime.MANGA_DIR, 'el modo se filtró de un hilo a otro'


def test_un_codigo_que_no_casa_deja_la_biblioteca_normal():
    """`modo_por_peticion` no es la puerta —eso es `guardia`, que ya pidió el token— pero un
    código que no casa no puede abrir nada. Y sin cabecera, tampoco."""
    from flask import Flask

    from api import runtime
    from api.config_store import modo_por_peticion

    app = Flask(__name__)
    app.before_request(modo_por_peticion)

    @app.route('/donde')
    def donde():
        return str(runtime.manga_dir())

    with app.test_client() as c:
        assert c.get('/donde').data.decode() == str(runtime.MANGA_DIR)
        # Un código inventado. Aunque el PC no tenga ninguno configurado, esto no puede abrir.
        r = c.get('/donde', headers={'X-Hidden-Code': 'esto-no-es-el-codigo'})
        assert r.data.decode() == str(runtime.MANGA_DIR)
