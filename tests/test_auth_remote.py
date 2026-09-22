"""El guardián de acceso remoto.

Va con test porque es la única costura del proyecto donde equivocarse tiene dos formas de salir
mal y las dos son caras: si deja pasar de más, 272 endpoints quedan abiertos a la red; si corta de
más, deja al usuario fuera de su propia aplicación de escritorio.

Se monta una app Flask mínima en vez de importar `app.py`: lo que se prueba es el guardián, no el
arranque entero del servidor (que levanta Suwayomi, hilos y demás).
"""
import sys
from pathlib import Path

import pytest
from flask import Flask, jsonify

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from api.auth import PUERTO_REMOTO, auth_remote_bp, es_local, guardia, guardia_origen, token_actual


@pytest.fixture()
def cliente():
    app = Flask(__name__)
    app.register_blueprint(auth_remote_bp)
    app.before_request(guardia)
    app.before_request(guardia_origen)

    @app.route('/api/loquesea', methods=['GET', 'POST'])
    def loquesea():
        return jsonify({'ok': True})

    return app.test_client()


def _remoto(**kw):
    """Werkzeug pone 127.0.0.1 por defecto; hay que decirle que venimos de fuera."""
    kw.setdefault('environ_base', {'REMOTE_ADDR': '192.168.0.99'})
    return kw


def test_desde_este_equipo_no_hace_falta_token(cliente):
    # La app de escritorio. Si esto se rompe, el usuario se queda fuera de su propia aplicación.
    assert cliente.get('/api/loquesea').status_code == 200


def test_desde_fuera_sin_token_no_pasa(cliente):
    assert cliente.get('/api/loquesea', **_remoto()).status_code == 401


def test_desde_fuera_con_token_pasa(cliente):
    r = cliente.get('/api/loquesea', **_remoto(
        headers={'Authorization': f'Bearer {token_actual()}'}))
    assert r.status_code == 200


def test_el_token_tambien_vale_por_query(cliente):
    # Imprescindible: un reproductor o un cargador de imágenes recibe la URL y la pide él,
    # sin pasar por nuestro código, así que no siempre puede poner cabeceras.
    r = cliente.get(f'/api/loquesea?token={token_actual()}', **_remoto())
    assert r.status_code == 200


def test_un_token_equivocado_no_pasa(cliente):
    r = cliente.get('/api/loquesea', **_remoto(headers={'Authorization': 'Bearer noesEste'}))
    assert r.status_code == 401


def test_un_prefijo_del_token_no_cuela(cliente):
    # `compare_digest` y no `==`: ni por tiempo ni por prefijo.
    medio = token_actual()[:10]
    r = cliente.get('/api/loquesea', **_remoto(headers={'Authorization': f'Bearer {medio}'}))
    assert r.status_code == 401


def test_post_local_desde_origen_externo_no_pasa(cliente):
    r = cliente.post('/api/loquesea', headers={'Origin': 'https://evil.example'})
    assert r.status_code == 403


def test_post_local_desde_loopback_si_pasa(cliente):
    r = cliente.post('/api/loquesea', headers={'Origin': 'http://127.0.0.1:5101'})
    assert r.status_code == 200


def test_post_local_desde_vite_si_pasa(cliente):
    r = cliente.post('/api/loquesea', headers={'Origin': 'http://localhost:5173'})
    assert r.status_code == 200


def test_post_remoto_con_token_y_origen_externo_si_pasa(cliente):
    r = cliente.post('/api/loquesea', **_remoto(
        headers={
            'Authorization': f'Bearer {token_actual()}',
            'Origin': 'https://evil.example',
        }))
    assert r.status_code == 200


def test_post_local_con_origin_null_no_pasa(cliente):
    r = cliente.post('/api/loquesea', headers={'Origin': 'null'})
    assert r.status_code == 403


def test_get_local_desde_origen_externo_no_se_bloquea(cliente):
    r = cliente.get('/api/loquesea', headers={'Origin': 'https://evil.example'})
    assert r.status_code == 200


def test_post_local_sin_cabeceras_conserva_compatibilidad(cliente):
    assert cliente.post('/api/loquesea').status_code == 200


def test_post_local_con_referer_externo_no_pasa(cliente):
    r = cliente.post('/api/loquesea', headers={'Referer': 'https://evil.example/form'})
    assert r.status_code == 403


def test_post_local_con_referer_loopback_si_pasa(cliente):
    r = cliente.post('/api/loquesea', headers={'Referer': 'http://localhost:5101/app'})
    assert r.status_code == 200


def test_hello_esta_abierto_porque_es_como_se_descubre_el_servidor(cliente):
    r = cliente.get('/api/hello', **_remoto())
    assert r.status_code == 200
    assert r.get_json()['app'] == 'AniManga Studio'
    assert r.get_json()['eres_local'] is False


def test_hello_no_filtra_el_token(cliente):
    cuerpo = cliente.get('/api/hello', **_remoto()).get_data(as_text=True)
    assert token_actual() not in cuerpo


def test_el_token_no_se_puede_pedir_desde_fuera(cliente):
    assert cliente.get('/api/pair', **_remoto()).status_code == 401
    assert cliente.get('/api/pair').status_code == 200


def test_ipv4_mapeada_en_ipv6_cuenta_como_local():
    # Según cómo arranque el socket, la propia máquina llega como ::ffff:127.0.0.1. Olvidarlo
    # dejaría al usuario fuera de su aplicación sin ninguna pista de por qué.
    assert es_local('::ffff:127.0.0.1')
    assert es_local('::1')
    assert not es_local('192.168.0.99')
    assert not es_local(None)


# ── La boca remota ───────────────────────────────────────────────────────────
# El caso que estuvo a punto de colarse: el puente de Windows (`netsh portproxy`) reenvía al bucle
# local, así que TODA petición de la red llega con `remote_addr = 127.0.0.1`. Medido en una tablet
# real antes de arreglarlo: 200 sin token y `/api/pair` devolviendo el token entero a la red.

def _por_el_puente(**kw):
    """Como llega una petición de la red a través del puente: IP local, puerto de la boca remota.

    El puerto va por `base_url` y no por `environ_base`: Werkzeug deriva `SERVER_PORT` del host de
    la URL y pisa lo que le pongas en el entorno.
    """
    kw.setdefault('base_url', f'http://127.0.0.1:{PUERTO_REMOTO}')
    kw.setdefault('environ_base', {'REMOTE_ADDR': '127.0.0.1'})
    return kw


def test_el_puente_no_convierte_la_red_en_local(cliente):
    assert cliente.get('/api/loquesea', **_por_el_puente()).status_code == 401


def test_por_el_puente_con_token_si_pasa(cliente):
    r = cliente.get('/api/loquesea', **_por_el_puente(
        headers={'Authorization': f'Bearer {token_actual()}'}))
    assert r.status_code == 200


def test_el_token_no_se_puede_pedir_a_traves_del_puente(cliente):
    # Lo más caro de todo: `/api/pair` sirviendo el token a cualquiera de la red.
    r = cliente.get('/api/pair', **_por_el_puente())
    assert r.status_code == 401
    assert token_actual() not in r.get_data(as_text=True)


def test_hello_dice_la_verdad_a_traves_del_puente(cliente):
    # Si dijera `eres_local: true` desde la red, sería la pista falsa que nos costó la mañana.
    assert cliente.get('/api/hello', **_por_el_puente()).get_json()['eres_local'] is False
