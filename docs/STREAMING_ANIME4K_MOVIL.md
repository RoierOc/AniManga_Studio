# Ver Anime4K (MPV) en el celular por streaming (Sunshine + Moonlight)

> Guía de referencia, no implementada aún. Externa a esta app — no requiere tocar código,
> solo configurar software en las máquinas host y el celular. Pensada para dos hosts:
> esta máquina Arch Linux (ahora) y la PC principal Windows+WSL2 (más adelante).

## Idea general

Anime4K **no se puede correr directamente en el celular** (los shaders GLSL rompen en
`mpv-android` por incompatibilidades de compilación — issue abierto en `bloc97/Anime4K#99`,
sin arreglar a marzo 2026). La alternativa real: el PC sigue renderizando MPV+Anime4K con su
GPU como ya hace, y **Sunshine** captura esa salida, la codifica por hardware (NVENC) y la
transmite por WiFi/LAN a **Moonlight** en el celular, que solo decodifica y muestra. El
celular ve el video ya procesado, con latencia baja (misma tecnología que streaming de juegos).

Sunshine = servidor (se instala en el PC). Moonlight = cliente (app en el celular). Cada host
se empareja por separado con el celular.

---

## Parte A — Arch Linux + Hyprland + RTX 3050 (esta máquina)

### 1. Instalar Sunshine
LizardByte (autores) **no dan soporte a los paquetes de AUR** — piden usar su repo oficial de
pacman (evita compilar a mano y ya trae dependencias de NVENC):

```bash
# Seguir "pacman-repo" en la doc oficial de LizardByte para añadir el repo, luego:
sudo pacman -S sunshine
```

### 2. Habilitar captura de pantalla (Wayland/wlroots)
Sunshine tiene 3 rutas de captura en Linux: **KMS** (menor latencia, lee del kernel
directamente), **wlr-screencopy/dmabuf** (nativo de wlroots — Hyprland/Sway lo soportan) y
portal genérico (más lento, no aplica en wlroots). Para habilitar KMS:

```bash
sudo setcap cap_sys_admin+p $(readlink -f $(which sunshine))
```

Si KMS no funciona en tu sesión, Sunshine cae automático a `wlr-screencopy`, que también
funciona bien en Hyprland.

### 3. Arrancar como servicio de usuario
Tiene que ser servicio de **usuario** (no de sistema) porque necesita acceso a la sesión
Wayland activa:

```bash
systemctl --user enable --now sunshine
```

### 4. Configuración inicial (Web UI)
Abrir `https://localhost:47990` desde esta misma máquina → crear usuario/contraseña admin.

Desde ahí, crear una entrada de **Aplicación** (no solo "Desktop") que lance MPV con el
perfil Anime4K vía `cmd`, para que el celular abra directo al reproductor en vez de ver todo
el escritorio. En Linux no existe pantalla virtual nativa (a diferencia de Windows), así que
esa app simplemente lanza MPV fullscreen sobre el monitor real.

### 5. Firewall
Abrir los puertos de Sunshine: `47984/tcp`, `47989/tcp`, `47990/tcp`, `48010/tcp`,
`47998-48000/udp`, `48002/udp`. Comando exacto depende de si usas `ufw`, `firewalld` o
`nftables` directo — revisar cuál está activo (`systemctl status firewalld` / `ufw status`)
antes de escribir las reglas.

### 6. Cliente (celular)
Instalar **Moonlight** (Play Store o F-Droid) en el mismo WiFi → debería auto-descubrir el PC
por mDNS, o añadir la IP manualmente → emparejar con el PIN que muestra el Web UI de Sunshine.

### Nota — GPU compartida
La RTX 3050 ya se usa para el worker de upscaling de esta app (VRAM 50%, throttle 80ms para
que MPV corra en paralelo — ver memoria `development`). NVENC es un bloque de hardware
**separado** del compute CUDA que usa el upscaler, así que no debería competir por VRAM/cómputo,
pero vale la pena vigilar temperatura si algún día corren upscale + Anime4K + Sunshine a la vez.

---

## Parte B — PC principal (Windows + WSL2, RTX 5070) — para más adelante

Más simple que Linux: la captura de pantalla en Windows es nativa y madura en Sunshine.

### 1. Instalar
Instalador `.exe` oficial de Sunshine — **en el lado Windows**, no dentro de WSL2 (WSL no
tiene acceso directo a la sesión de escritorio para capturar).

### 2. NVENC
Funciona out-of-the-box con el driver NVIDIA de Windows ya instalado — sin pasos extra en
GeForce (a diferencia de la restricción histórica de NVFBC en captura X11, que aquí ni aplica).

### 3. Pantalla virtual (opcional)
Sunshine en Windows puede traer soporte de **display virtual** integrado — útil si quieres
streamear sin depender de que el monitor físico esté encendido/en la resolución correcta.
Revisar si tu versión instalada lo trae antes de configurarlo.

### 4. App dedicada a MPV
Igual que en Linux: crear una entrada de Aplicación en el Web UI que lance `mpv.exe`
directamente (esta app ya lo hace vía PowerShell para Anime Studio — mismo binario, mismo
patrón), para que el celular abra directo al reproductor.

### 5. Firewall
Windows Defender Firewall — el instalador de Sunshine suele añadir las reglas de los mismos
puertos automáticamente; verificar en "Reglas de entrada" si el celular no logra conectar.

### 6. Cliente
Mismo Moonlight del celular — se empareja por separado con este host (cada PC = un
emparejamiento independiente, no comparten configuración).

---

## Puertos de referencia (ambos hosts)

| Puerto | Protocolo | Uso |
|--------|-----------|-----|
| 47984 | TCP | HTTPS (legado) |
| 47989 | TCP | Control/handshake |
| 47990 | TCP | Web UI de configuración |
| 48010 | TCP | RTSP |
| 47998-48000 | UDP | Video/audio/control stream |
| 48002 | UDP | Control |

(Confirmar contra la doc oficial de Sunshine antes de abrir reglas — los puertos pueden variar
entre versiones.)

## Notas generales

- Diseñado para uso en **LAN local** (misma WiFi). Para acceso fuera de la red local haría
  falta una VPN tipo Tailscale — no cubierto aquí, evaluar solo si hace falta.
- Cada host (Arch / Windows) se configura y empareja de forma independiente; no hay estado
  compartido entre ambos.
- Esta guía es externa al código de `manga-upscaler` — no depende de ningún cambio en el repo.
