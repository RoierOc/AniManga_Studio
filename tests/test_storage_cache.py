from api import storage_cache as cache


def test_summary_separa_temporales_protegidos_de_huerfanos(monkeypatch, tmp_path):
    image = tmp_path / "images"
    export = tmp_path / "exports"
    image.mkdir()
    export.mkdir()
    (image / "cover.jpg").write_bytes(b"1234")
    keep = export / "active.cbz"
    keep.write_bytes(b"12")
    (export / "orphan.cbz").write_bytes(b"123456")
    monkeypatch.setattr(cache, "_category_roots", lambda: {"image_cache": image, "export_temp": export})
    monkeypatch.setattr(cache, "_protected_export_paths", lambda root: {keep.resolve()})

    assert cache.summary() == {
        "image_cache": 4, "export_temp": 8, "export_orphan": 6, "total": 12,
    }


def test_purge_export_no_toca_un_temporal_de_una_tarea(monkeypatch, tmp_path):
    root = tmp_path / "exports"
    root.mkdir()
    keep = root / "active.cbz"
    orphan = root / "orphan.cbz"
    keep.write_bytes(b"keep")
    orphan.write_bytes(b"drop")
    monkeypatch.setattr(cache, "_category_roots", lambda: {"image_cache": tmp_path / "images", "export_temp": root})
    monkeypatch.setattr(cache, "_protected_export_paths", lambda _: {keep.resolve()})

    result = cache.purge("export_temp")

    assert result == {"ok": True, "target": "export_temp", "freed": 4}
    assert keep.exists()
    assert not orphan.exists()


def test_purge_rechaza_categorias_de_contenido(monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "_category_roots", lambda: {})
    try:
        cache.purge("original")
    except ValueError as exc:
        assert str(exc) == "target inválido"
    else:
        raise AssertionError("la limpieza de originales no debe estar disponible")
