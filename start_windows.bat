@echo off
REM ─────────────────────────────────────────────────────────────────────────────
REM  Manga Upscaler — Windows launcher
REM  Starts everything inside WSL: Flask/Waitress (127.0.0.1:5101) + Suwayomi
REM  (127.0.0.1:4567, auto-started by the app) + the WSL2 port proxy.
REM
REM  Run it by double-clicking, or auto-start it at Windows login:
REM    1) Press  Win + R , type  shell:startup , press Enter.
REM    2) Right-click in that folder → New → Shortcut → browse to this .bat.
REM       (Optional: shortcut → Properties → Run: Minimized.)
REM
REM  Then open  http://localhost:5101  in your browser.
REM  Fuentes (Suwayomi) appears ~15-30s after launch while Java boots — it now
REM  self-loads, no need to refresh.
REM ─────────────────────────────────────────────────────────────────────────────

wsl.exe -e bash -lc "cd /Manga_Upscaler_project/workspace/manga-upscaler && exec ./start_server.sh"
