"""Contrato del preset horneado Anime4K: réplica de CTRL+9."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from api.anime_upscale import PRESETS  # noqa: E402


def test_maxima_es_a_a_ul_thin_como_ctrl9():
    assert PRESETS['maxima']['cadena'] == [
        'Anime4K_Clamp_Highlights',
        'Anime4K_Restore_CNN_UL',
        'Anime4K_Upscale_CNN_x2_UL',
        'Anime4K_AutoDownscalePre_x2',
        'Anime4K_AutoDownscalePre_x4',
        'Anime4K_Restore_CNN_M',
        'Anime4K_Upscale_CNN_x2_M',
        'Anime4K_Thin_HQ',
    ]
    assert PRESETS['maxima']['cadena'].count('Anime4K_Restore_CNN_UL') == 1
    assert PRESETS['maxima']['cadena'].count('Anime4K_Restore_CNN_M') == 1
