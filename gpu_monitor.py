#!/usr/bin/env python3
"""
GPU monitor para inferencia PyTorch — RTX 5070 / MangaUpscaler
Ejecutar en una terminal separada mientras corre el upscaler:

    python gpu_monitor.py

Métricas cada 500 ms:
  SM%     — ocupación real de CUDA cores (0-100%)
  BW%     — uso del controlador de memoria (correlaciona con ancho de banda)
  VRAM    — usado / total en GB
  SM clk  — frecuencia real de los SM (caída = throttling térmico/eléctrico)
  Temp    — temperatura de la GPU en °C
  Watts   — consumo en vatios

Log guardado en gpu_log.jsonl (un JSON por línea) para análisis posterior.
"""

import json
import math
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import pynvml
except ModuleNotFoundError:
    print("Instala nvidia-ml-py:  pip install nvidia-ml-py")
    sys.exit(1)

# ── Configuración ────────────────────────────────────────────────────────────

INTERVAL_S   = 0.5          # frecuencia de muestreo
GPU_INDEX    = 0            # índice de la GPU a monitorear
LOG_FILE     = Path("gpu_log.jsonl")
IDLE_THRESH  = 5            # SM% por debajo de este valor = idle
ACTIVE_THRESH = 30          # SM% por encima de este valor = activo

# ── Colores ANSI ─────────────────────────────────────────────────────────────

_R = "\033[0m"
_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_CYAN   = "\033[96m"
_DIM    = "\033[2m"
_BOLD   = "\033[1m"


def _sm_color(pct: int) -> str:
    if pct >= 70: return _GREEN
    if pct >= 30: return _YELLOW
    return _RED


def _temp_color(t: int) -> str:
    if t >= 85: return _RED
    if t >= 75: return _YELLOW
    return _R


def _fmt_vram(used_b: int, total_b: int) -> str:
    used  = used_b  / 1024**3
    total = total_b / 1024**3
    pct   = used / total * 100
    bar_w = 10
    filled = round(pct / 100 * bar_w)
    bar = "█" * filled + "░" * (bar_w - filled)
    return f"{used:.2f}/{total:.1f}GB [{bar}] {pct:4.1f}%"


# ── Análisis post-sesión ─────────────────────────────────────────────────────

def _print_summary(samples: list[dict], duration_s: float, log_path: Path) -> None:
    if not samples:
        return

    sm_vals   = [s["sm_pct"]   for s in samples]
    bw_vals   = [s["membw_pct"] for s in samples]
    w_vals    = [s["watts"]    for s in samples]
    t_vals    = [s["temp_c"]   for s in samples]
    vram_vals = [s["vram_used_gb"] for s in samples]
    clk_vals  = [s["sm_clk_mhz"] for s in samples]

    n = len(samples)
    idle_n = sum(1 for v in sm_vals if v < IDLE_THRESH)
    idle_s = idle_n * INTERVAL_S

    # Contar transiciones idle → activo (gaps reales)
    gaps = 0
    was_idle = True
    for v in sm_vals:
        is_idle = v < IDLE_THRESH
        if was_idle and not is_idle:
            gaps += 1
        was_idle = is_idle

    def avg(lst): return sum(lst) / len(lst) if lst else 0

    print(f"\n{_BOLD}{'═'*62}{_R}")
    print(f"{_BOLD}Resumen — {duration_s:.1f}s monitoreados  |  {log_path} ({n} muestras){_R}")
    print(f"  {'SM%':<8}  prom {avg(sm_vals):>3.0f}%   mín {min(sm_vals):>3}%   máx {max(sm_vals):>3}%")
    print(f"  {'BW%':<8}  prom {avg(bw_vals):>3.0f}%")
    print(f"  {'SM clk':<8}  prom {avg(clk_vals):>4.0f} MHz   mín {min(clk_vals):>4} MHz  (caída = throttling)")
    print(f"  {'VRAM':<8}  máx {max(vram_vals):.2f} GB")
    print(f"  {'Temp':<8}  prom {avg(t_vals):>3.0f}°C   máx {max(t_vals):>3}°C")
    print(f"  {'Watts':<8}  prom {avg(w_vals):>5.1f}W   máx {max(w_vals):>5.1f}W")
    print(f"  {'Idle':<8}  {idle_s:.1f}s ({idle_n/n*100:.0f}%)  — muestras con SM% < {IDLE_THRESH}%")
    print(f"  {'Gaps':<8}  {gaps} transiciones idle→activo detectadas")
    print(f"{_BOLD}{'═'*62}{_R}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    pynvml.nvmlInit()
    handle = pynvml.nvmlDeviceGetHandleByIndex(GPU_INDEX)

    raw_name = pynvml.nvmlDeviceGetName(handle)
    gpu_name = raw_name if isinstance(raw_name, str) else raw_name.decode()

    mem_total = pynvml.nvmlDeviceGetMemoryInfo(handle).total
    total_gb  = mem_total / 1024**3

    # Max SM clock para detectar throttling
    try:
        max_sm_clk = pynvml.nvmlDeviceGetMaxClockInfo(handle, pynvml.NVML_CLOCK_SM)
    except Exception:
        max_sm_clk = 0

    print(f"\n{_BOLD}GPU:{_R} {gpu_name}  |  VRAM: {total_gb:.1f} GB")
    if max_sm_clk:
        print(f"{_BOLD}SM clk máx:{_R} {max_sm_clk} MHz  (throttling si cae)")
    print(f"{_BOLD}Log:{_R} {LOG_FILE.absolute()}")
    print(f"{_DIM}Intervalo: {INTERVAL_S*1000:.0f}ms  |  Ctrl+C para detener{_R}\n")

    header = (
        f"{'HORA':8}  "
        f"{'SM%':>5}  "
        f"{'BW%':>4}  "
        f"{'VRAM':^35}  "
        f"{'CLK':>7}  "
        f"{'TEMP':>5}  "
        f"{'WATTS':>7}"
    )
    print(f"{_DIM}{header}{_R}")
    print(f"{_DIM}{'-'*80}{_R}")

    samples:  list[dict] = []
    log_f     = LOG_FILE.open("a", buffering=1)  # line-buffered
    t_start   = time.monotonic()
    running   = True

    def _stop(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT,  _stop)
    signal.signal(signal.SIGTERM, _stop)

    while running:
        loop_start = time.monotonic()
        ts = time.time()

        try:
            util    = pynvml.nvmlDeviceGetUtilizationRates(handle)
            mem     = pynvml.nvmlDeviceGetMemoryInfo(handle)
            temp    = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
            pwr_mw  = pynvml.nvmlDeviceGetPowerUsage(handle)
            sm_clk  = pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_SM)
        except pynvml.NVMLError as e:
            print(f"  nvml error: {e}", flush=True)
            time.sleep(INTERVAL_S)
            continue

        sm_pct    = util.gpu
        bw_pct    = util.memory
        watts     = pwr_mw / 1000.0
        vram_used = mem.used

        # Alerta de throttling: SM clk cayó más del 10% del máx
        clk_warn = ""
        if max_sm_clk and sm_clk < max_sm_clk * 0.90 and sm_pct > ACTIVE_THRESH:
            clk_warn = f" {_RED}⚠ THROTTLE{_R}"

        sc = _sm_color(sm_pct)
        tc = _temp_color(temp)

        line = (
            f"{datetime.fromtimestamp(ts).strftime('%H:%M:%S'):8}  "
            f"{sc}{sm_pct:>4}%{_R}  "
            f"{bw_pct:>3}%  "
            f"{_CYAN}{_fmt_vram(vram_used, mem.total):<35}{_R}  "
            f"{sm_clk:>4}MHz  "
            f"{tc}{temp:>3}°C{_R}  "
            f"{watts:>6.1f}W"
            f"{clk_warn}"
        )
        print(line, flush=True)

        entry = {
            "ts":           round(ts, 3),
            "time":         datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3],
            "sm_pct":       sm_pct,
            "membw_pct":    bw_pct,
            "vram_used_gb": round(vram_used / 1024**3, 3),
            "vram_free_gb": round(mem.free  / 1024**3, 3),
            "vram_total_gb":round(mem.total / 1024**3, 3),
            "sm_clk_mhz":  sm_clk,
            "temp_c":       temp,
            "watts":        round(watts, 1),
        }
        log_f.write(json.dumps(entry) + "\n")
        samples.append(entry)

        # Dormir el tiempo restante del intervalo
        elapsed = time.monotonic() - loop_start
        sleep_t = max(0.0, INTERVAL_S - elapsed)
        time.sleep(sleep_t)

    log_f.close()
    pynvml.nvmlShutdown()

    duration = time.monotonic() - t_start
    _print_summary(samples, duration, LOG_FILE)


if __name__ == "__main__":
    main()
