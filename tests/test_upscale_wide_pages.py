"""Regression tests for wide/double manga pages.

These tests exercise only the dimension and strip planner; they never load CUDA
or process a real chapter.
"""

import os
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import api.upscale as U  # noqa: E402


def test_wide_scan_is_not_hires_just_because_of_its_width():
    assert not U.is_already_hires(Image.new("RGB", (3333, 2448)), long_side=3000)
    assert U.is_already_hires(Image.new("RGB", (2448, 3333)), long_side=3000)


def test_wide_page_over_reconstruction_cap_is_split_with_overlap(monkeypatch):
    image = Image.new("RGB", (320, 200), "white")
    calls = []
    cap = 1.0
    monkeypatch.setattr(U, "_MAX_RECON_MB", cap)

    def fake_upscale_tiled(img, *args, **kwargs):
        scale = kwargs.get("scale")
        if scale is None:
            # _upscale_horizontal_tiled passes scale positionally.
            scale = args[3] if len(args) > 3 else 4
        calls.append((img.width, img.height))
        return img.resize((img.width * scale, img.height * scale), Image.Resampling.NEAREST)

    monkeypatch.setattr(U, "_upscale_tiled", fake_upscale_tiled)

    result = U._upscale_horizontal_tiled(
        image, in_channels=3, tile_size=64, overlap=8, scale=4
    )

    assert len(calls) > 1
    assert all(U._projected_recon_mb(w, h, 3, 4) <= cap for w, h in calls)
    assert result.size == (1280, 800)


def test_run_upscale_chapter_routes_large_wide_page_to_strips(monkeypatch, tmp_path):
    source = tmp_path / "ch0001_001.png"
    output = tmp_path / "upscaled"
    Image.new("RGB", (40, 20), "white").save(source)
    monkeypatch.setattr(U, "_ensure_gpu_worker", lambda: None)
    monkeypatch.setattr(U, "is_color_page", lambda image: False)
    monkeypatch.setattr(U, "_MAX_RECON_MB", 1.0)
    monkeypatch.setattr(U, "_gpu_in_channels", [3])
    monkeypatch.setattr(U, "_gpu_scale", [2])
    monkeypatch.setattr(U, "_gpu_half", [False])
    monkeypatch.setattr(U, "_projected_recon_mb", lambda w, h, c, scale: w / 10)
    import api.roots as roots
    monkeypatch.setattr(roots, "up_dir_for", lambda path: output)
    monkeypatch.setattr(U, "set_upscale_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(U, "mark_changed", lambda: None)
    monkeypatch.setattr(U, "_mem_snapshot", lambda: (1000, 2000, 100))

    def fake_upscale_tiled(img, *args, **kwargs):
        scale = kwargs.get("scale") or args[3]
        return img.resize((img.width * scale, img.height * scale), Image.Resampling.NEAREST)

    monkeypatch.setattr(U, "_upscale_tiled", fake_upscale_tiled)

    U.run_upscale_chapter(tmp_path, output, [source], "wide-test")

    saved = output / "ch0001_001.jpg"
    assert saved.exists()
    assert Image.open(saved).size == (80, 40)
