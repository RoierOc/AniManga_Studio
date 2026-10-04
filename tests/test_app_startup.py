"""El precalentamiento no debe servir peticiones durante el registro de rutas."""
import os
from pathlib import Path
import subprocess
import sys


def test_warmup_runs_after_routes_are_registered(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'src'
    script = '''
import threading
import sys
import traceback
sys.excepthook = lambda *args: traceback.print_exception(*args, file=sys.__stderr__)

def start_immediately(self):
    if self._target.__name__ == '_warm_anime_library':
        self.run()

threading.Thread.start = start_immediately
from api import anime
anime._load_library = lambda: {}
import app
assert '/uploads/<path:filename>' in {str(r) for r in app.app.url_map.iter_rules()}
'''
    result = subprocess.run(
        [sys.executable, '-c', script], cwd=tmp_path,
        env={**os.environ, 'PYTHONPATH': str(source), 'SUWAYOMI_EAGER': '0'},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
