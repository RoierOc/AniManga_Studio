from api.imgproxy import _ALLOWED_HOSTS


def test_el_proxy_incluye_el_cdn_de_myanimelist_del_selector_de_anime():
    assert 'cdn.myanimelist.net' in _ALLOWED_HOSTS
