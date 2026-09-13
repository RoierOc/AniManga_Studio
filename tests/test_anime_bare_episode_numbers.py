"""Una carpeta local con archivos llamados sólo 1, 2, 3... conserva su numeración real."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from api import anime  # noqa: E402


def test_temporada_local_con_nombres_numericos_no_inventa_faltantes(tmp_path, monkeypatch):
    folder = tmp_path / 'Shingeki No Kyojin Temporada 2'
    folder.mkdir()
    for number in range(1, 13):
        (folder / f'{number}.mkv').write_bytes(b'video')

    monkeypatch.setattr(anime, '_video_duration', lambda _path: 1400.0)
    anime._scan_cache.pop(str(folder), None)

    episodes = anime._scan_local_episodes_sync(str(folder), {})

    assert [ep['num'] for ep in episodes] == list(range(1, 13))
    assert anime._find_video(str(folder), 10).endswith('10.mkv')
