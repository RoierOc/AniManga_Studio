"""El vigilante que mantiene a qBittorrent atado al adaptador de la VPN.

Costura con historial de fallo SILENCIOSO, dos veces: el ancla del adaptador caducó (primero la
marca "norton", luego el protocolo "wireguard") y las dos veces el vigilante leyó «no lo encuentro»
como «VPN caída» — el estado en que su trabajo es NO hacer nada. qBittorrent se quedaba
`disconnected` con DHT 0 sin una sola queja.

Va con API simulada a propósito: qBittorrent DERIVA `current_interface_name` del valor y rechaza
uno que no exista, así que el estado que rompió en producción (nombre viejo + valor bueno) no se
puede sembrar contra el servicio real.
"""
import importlib.util
import json
from pathlib import Path

import pytest

_RUTA = Path(__file__).resolve().parents[1] / 'servarr' / 'vpn-watch.py'


def _cargar():
    spec = importlib.util.spec_from_file_location('vpn_watch', _RUTA)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def vw(monkeypatch):
    """El módulo con la API de qBittorrent simulada. `vw.prefs` es lo que 'tiene guardado'."""
    mod = _cargar()
    mod.prefs = {'current_network_interface': 'iftype53_32769',
                 'current_interface_name': 'AVG Secure VPN Wintun',
                 'listen_port': 10467}
    mod.adaptadores = [{'name': 'Tailscale', 'value': 'iftype53_32768'},
                       {'name': 'AVG Secure VPN Wintun', 'value': 'iftype53_32769'},
                       {'name': 'Ethernet', 'value': 'ethernet_32769'}]

    def api(path, data=None, timeout=15):
        if path == 'app/networkInterfaceList':
            return mod.adaptadores
        if path == 'app/preferences':
            return dict(mod.prefs)
        if path.startswith('app/networkInterfaceAddressList'):
            return ['fe80::1%51', '172.16.16.2']
        if path == 'app/setPreferences':
            mod.prefs.update(json.loads(data['json']))
            return ''
        raise AssertionError(f'ruta inesperada: {path}')

    monkeypatch.setattr(mod, 'api', api)
    monkeypatch.setattr(mod.time, 'sleep', lambda _s: None)   # el re-listen no espera en el test
    return mod


def test_encuentra_la_vpn_aunque_cambie_el_protocolo(vw):
    """Mimic levanta «…Wintun», WireGuard levanta «…WireGuard»: las dos son la VPN."""
    assert vw.vpn_adapter() == ('AVG Secure VPN Wintun', 'iftype53_32769')
    vw.adaptadores[1]['name'] = 'AVG Secure VPN WireGuard'
    assert vw.vpn_adapter()[0] == 'AVG Secure VPN WireGuard'
    vw.adaptadores[1]['name'] = 'Norton VPN WireGuard'
    assert vw.vpn_adapter()[0] == 'Norton VPN WireGuard'


def test_tailscale_no_es_la_vpn_de_salida(vw):
    """Es una red privada, no una ruta a Internet: atar los torrents ahí es tan mudo como no atar."""
    vw.adaptadores = [{'name': 'Tailscale', 'value': 'iftype53_32768'},
                      {'name': 'Ethernet', 'value': 'ethernet_32769'}]
    assert vw.vpn_adapter() is None


def test_nombre_obsoleto_con_el_valor_BUENO_se_detecta(vw):
    """El caso real: las ranuras `iftype53_NNNNN` se RECICLAN.

    Al pasar a Mimic, el valor de WireGuard pasó a ser el del Wintun y casaba solo; el nombre
    seguía diciendo «…WireGuard», que es por donde ata libtorrent → DHT 0. Mirar sólo el valor
    habría dado el binding por bueno.
    """
    vw.prefs['current_interface_name'] = 'AVG Secure VPN WireGuard'   # nombre viejo, valor intacto
    vw.tick({'qbt_up': True, 'ip': '172.16.16.2'})                    # sin cambio de IP que lo tape
    assert vw.prefs['current_interface_name'] == 'AVG Secure VPN Wintun'


def test_reatar_fuerza_el_re_listen(vw):
    """Guardar el binding no basta: AVG reusa el GUID entre protocolos, qBittorrent no ve cambio
    y se queda escuchando en la IP del túnel anterior. El puerto tiene que volver a su sitio."""
    puertos = []
    api_real = vw.api

    def espia(path, data=None, timeout=15):
        if path == 'app/setPreferences' and 'listen_port' in json.loads(data['json']):
            puertos.append(json.loads(data['json'])['listen_port'])
        return api_real(path, data, timeout)

    vw.api = espia
    vw.rebind('AVG Secure VPN Wintun', 'iftype53_32769')
    assert puertos == [10468, 10467], f'no forzó el re-listen: {puertos}'
    assert vw.prefs['listen_port'] == 10467, 'dejó el puerto cambiado'


def test_sin_vpn_no_reata_nada(vw):
    """«VPN caída» es el estado en que NO hay que tocar el binding: qBittorrent debe quedarse mudo."""
    vw.adaptadores = [{'name': 'Ethernet', 'value': 'ethernet_32769'}]
    vw.prefs['current_interface_name'] = 'AVG Secure VPN Wintun'
    vw.tick({'qbt_up': True, 'ip': '172.16.16.2'})
    assert vw.prefs['current_network_interface'] == 'iftype53_32769'   # intacto, no "cualquiera"
