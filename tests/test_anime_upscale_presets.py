"""Contrato de la receta Anime4K máxima: detalle fuerte sin shimmer temporal."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from api.anime_upscale import PRESETS  # noqa: E402


def test_maxima_conserva_las_dos_etapas_y_suaviza_solo_la_segunda_restauracion():
    assert PRESETS['maxima']['cadena'] == [
        'Anime4K_Clamp_Highlights',
        'Anime4K_Restore_CNN_UL',
        'Anime4K_Upscale_CNN_x2_UL',
        'Anime4K_AutoDownscalePre_x2',
        'Anime4K_AutoDownscalePre_x4',
        'Anime4K_Restore_CNN_Soft_M',
        'Anime4K_Upscale_CNN_x2_M',
        'Anime4K_Thin_HQ',
    ]
    assert 'Anime4K_Restore_CNN_M' not in PRESETS['maxima']['cadena']
