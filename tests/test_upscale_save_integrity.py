import sys
import threading
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

import api.upscale as U


def test_fallo_de_jpeg_no_pisa_ni_borra_la_version_previa(monkeypatch, tmp_path):
    output = tmp_path / 'page.jpg'
    other_format = tmp_path / 'page.png'
    output.write_bytes(b'jpeg anterior')
    other_format.write_bytes(b'png anterior')

    def partial_then_fail(_image, path, **_kwargs):
        Path(path).write_bytes(b'jpeg parcial')
        raise OSError('disco lleno')

    monkeypatch.setattr(Image.Image, 'save', partial_then_fail)
    try:
        U._resize_save(Image.new('RGB', (4, 4)), output, 1, 95)
    except OSError:
        pass
    else:
        raise AssertionError('el error de escritura debe propagarse')

    assert output.read_bytes() == b'jpeg anterior'
    assert other_format.read_bytes() == b'png anterior'


def test_guardado_async_fallido_deja_el_trabajo_en_error_y_conserva_la_pagina(monkeypatch, tmp_path):
    source = tmp_path / 'ch0001_001.png'
    Image.new('RGB', (8, 8), 'white').save(source)
    output = tmp_path / 'upscaled'
    output.mkdir()
    previous = output / source.name
    previous.write_bytes(b'pagina previa')
    statuses = []

    class FailedSavePool:
        def submit(self, *_args, **_kwargs):
            future = Future()
            future.set_exception(OSError('disco lleno'))
            return future

    monkeypatch.setattr(U, '_save_pool', FailedSavePool())
    monkeypatch.setattr(U, '_ensure_gpu_worker', lambda: None)
    monkeypatch.setattr(U, 'is_color_page', lambda _image: False)
    monkeypatch.setattr(U, '_projected_recon_mb', lambda *_args: 1)
    monkeypatch.setattr(U, '_MAX_RECON_MB', 100)
    monkeypatch.setattr(U, '_gpu_in_channels', [3])
    monkeypatch.setattr(U, '_gpu_scale', [2])
    monkeypatch.setattr(U, '_gpu_half', [False])
    monkeypatch.setattr(U, '_upscale_tiled', lambda image, **_kwargs: image.copy())
    monkeypatch.setattr(U, 'set_upscale_status', lambda _id, value: statuses.append(dict(value)))
    monkeypatch.setattr(U, 'mark_changed', lambda: None)
    monkeypatch.setattr(U, '_mem_snapshot', lambda: (1000, 2000, 100))
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(cuda=SimpleNamespace(empty_cache=lambda: None)))
    import api.roots as roots
    monkeypatch.setattr(roots, 'up_dir_for', lambda _path: output)

    U.run_upscale_chapter(tmp_path, output, [source], 'failed-save-test')

    assert statuses[-1]['status'] == 'error'
    assert statuses[-1]['current'] == 0
    assert previous.read_bytes() == b'pagina previa'


def test_cambios_de_modo_no_se_solapan_entre_capitulos(monkeypatch):
    entered_eco = threading.Event()
    release_eco = threading.Event()
    entered_full = threading.Event()
    counts_lock = threading.Lock()
    active = 0
    max_active = 0
    observed_modes = []

    def fake_worker_ready():
        nonlocal active, max_active
        with counts_lock:
            active += 1
            max_active = max(max_active, active)
            observed_modes.append(U._gpu_throttle_ms[0])
            index = len(observed_modes)
        try:
            if index == 1:
                entered_eco.set()
                assert release_eco.wait(2)
            else:
                entered_full.set()
        finally:
            with counts_lock:
                active -= 1

    monkeypatch.setattr(U, '_ensure_gpu_worker', fake_worker_ready)
    monkeypatch.setattr(U, 'set_upscale_status', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(U, '_mem_snapshot', lambda: (1000, 2000, 100))
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(cuda=SimpleNamespace(empty_cache=lambda: None)))
    monkeypatch.setattr(U, '_gpu_throttle_ms', [U._FULL_THROTTLE_MS])
    monkeypatch.setattr(U, '_gpu_tile_throttle_ms', [0])

    eco = threading.Thread(target=U.run_upscale_chapter, args=(Path('.'), Path('.'), [], 'eco-test'),
                           kwargs={'eco': True})
    full = threading.Thread(target=U.run_upscale_chapter, args=(Path('.'), Path('.'), [], 'full-test'))
    try:
        eco.start()
        assert entered_eco.wait(1)
        full.start()
        assert not entered_full.wait(0.1)
        release_eco.set()
        eco.join(2)
        full.join(2)
    finally:
        release_eco.set()
        eco.join(2)
        full.join(2)

    assert not eco.is_alive() and not full.is_alive()
    assert max_active == 1
    assert observed_modes == [U._ECO_THROTTLE_MS, U._FULL_THROTTLE_MS]


def test_no_descarta_worker_gpu_que_sigue_vivo_al_cambiar_modelo(monkeypatch):
    class BusyWorker:
        def is_alive(self):
            return True

        def join(self, timeout=None):
            self.timeout = timeout

    class QueueStub:
        def put(self, _item):
            pass

    worker = BusyWorker()
    monkeypatch.setattr(U, 'MODEL_REGISTRY', {'nuevo': {'models': []}})
    monkeypatch.setattr(U, '_active_model_key', ['anterior'])
    monkeypatch.setattr(U, '_gpu_worker_thread', worker)
    monkeypatch.setattr(U, '_gpu_worker_stopping', False)
    monkeypatch.setattr(U, '_gpu_queue', QueueStub())

    ok, _message = U._switch_model('nuevo')

    assert not ok
    assert U._active_model_key[0] == 'anterior'
    assert U._gpu_worker_thread is worker
    try:
        U._ensure_gpu_worker()
    except RuntimeError as exc:
        assert 'sigue deteniéndose' in str(exc)
    else:
        raise AssertionError('no debe iniciar ni reutilizar otro worker mientras el anterior vive')
