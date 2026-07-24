# qBittorrent atado a Norton VPN (WireGuard)

Objetivo: que el cliente torrent **no pueda comunicarse con Internet si la VPN no está activa**,
aunque Windows cambie solo a Ethernet o Wi-Fi.

## Limitaciones reales de Norton VPN (verificadas antes de configurar nada)

Tres cosas que conviene saber porque **chocan con los objetivos**, y no se arreglan desde
qBittorrent:

1. **P2P solo en una región.** Hay que conectarse explícitamente a la *"P2P-Optimized Region"*
   desde el desplegable de servidores de Norton. En el resto de servidores el torrenting no está
   soportado. En esta cuenta esa región es **Estados Unidos** (la IP sale como Datacamp Ltd, US) —
   no es una fuga, aunque varias guías digan que la región P2P de Norton es Países Bajos.
2. **No hay port forwarding.** Quedas *unconnectable*: solo puedes subir a peers capaces de
   iniciar ellos la conexión. **Sembrarás peor que ahora**, y no tiene arreglo por configuración.
3. **El kill switch de Norton es a nivel de aplicación.** Al matar el proceso de Norton desde el
   Administrador de tareas, la conexión normal se restablece. Por sí solo **no** cumple el
   requisito de "nunca mi IP real".

Por (3), la protección real de este montaje **no depende del kill switch de Norton**.

## Las dos capas

| Capa | Qué hace | Sobrevive a… |
|---|---|---|
| **1. Interface binding** (`vpn-lock.py --bind`) | libtorrent solo emite por el adaptador WireGuard. Si el adaptador desaparece, no hay ruta alternativa. | Que Norton se desconecte |
| **2. Regla de Firewall** (`firewall.ps1`) | ~~Bloquea `qbittorrent.exe` cuando su IP de origen está en la LAN~~ **DESACTIVADA — ver abajo** | Que Norton **muera entero** |

> ### ⚠️ La capa 2 está desactivada a propósito
>
> Al probarla en vivo **ahogaba el tráfico legítimo**: con las reglas activas, DHT se quedaba en
> 0 nodos y los trackers nunca llegaban a contactarse, aun estando el binding bien puesto y el
> adaptador con IP válida. Al desactivarlas con la VPN conectada: DHT 298 nodos y descarga a
> 10,7 MB/s. La culpable es la regla IPv4 `-LocalAddress 192.168.0.0/24`.
>
> **Y resulta que no hacía falta**: con las reglas apagadas y la VPN desconectada, qBittorrent
> queda en `connection_status: disconnected`. **La capa 1 ya falla-cerrado ella sola.**
>
> No reactives `firewall.ps1` sin reescribirlo antes (bloqueando por `-InterfaceAlias`
> Ethernet/Wi-Fi en vez de por IP de origen). Tal como está, rompe las descargas.

La capa 1 es fail-closed por construcción: no hay comprobación que pueda fallar, simplemente no
existe otra salida.

### Por qué la regla de firewall va "al revés"

En el Firewall de Windows un **bloqueo siempre gana a un permitir**, así que la receta intuitiva
("bloquea todo, permite el túnel") no se puede expresar. Lo que sí funciona es bloquear por
**dirección local de origen**:

- VPN conectada → qBittorrent sale con la IP del túnel (10.x/172.x) → no coincide → **pasa**.
- VPN caída → sale por Ethernet/Wi-Fi con `192.168.0.x` → coincide → **bloqueado**.

## Ajustes aplicados (por WebAPI, no editando el .ini)

Se hace por API **a propósito**: qBittorrent reescribe `qBittorrent.ini` al cerrarse, así que
editar el fichero con la app abierta pierde los cambios en silencio. Por API se aplica en
caliente y persiste.

### Anti-fuga

| Ajuste | Valor | Por qué |
|---|---|---|
| **LSD** (Local Peer Discovery) | `OFF` | Manda multicast a la LAN anunciando qué torrents tienes: sale **por fuera** del túnel y delata tu actividad a la red local |
| **UPnP / NAT-PMP** | `OFF` | Le pediría a tu **router real** que abra puertos: inútil detrás de la VPN y abre agujeros en la red que sí quieres proteger |
| **DHT / PeX** | `ON` | Los torrents públicos (Nyaa) **dependen** de ellos. Van por dentro del túnel → anuncian la IP de la VPN, no la tuya |
| **Cifrado** | `Preferido` | "Obligatorio" corta peers que no lo soportan, y con Norton ya vas justo al no haber port forwarding |
| **Puerto** | fijo (33054) | Predecible para diagnosticar. Da igual cuál sea: no se puede abrir en Norton |
| **IPv6** | bloqueado por firewall | El WireGuard de Norton tuneliza IPv4; cualquier IPv6 saldría por fuera con tu IP real |
| **Modo anónimo** | `OFF` | El binding ya protege; esto solo rompe compatibilidad con trackers |

### "Optional IP Address to bind to" — déjalo VACÍO

Es tu pregunta concreta y la respuesta importa: **"All addresses"**, no una IP fija.

Fijar la IP del túnel parece más seguro pero es **contraproducente**: Norton asigna una IP nueva
en cada reconexión, y en cuanto cambie, qBittorrent se queda mudo sin motivo aparente y tú
persiguiendo un fantasma. El **adaptador** es el ancla estable; la IP no lo es. Y no pierdes nada:
atado al adaptador, no puede salir por otro sitio aunque la IP baile.

### Volumen (80+ torrents sembrando)

| Ajuste | Antes | Ahora | Por qué |
|---|---|---|---|
| Conexiones totales | 500 | **1000** | Sin entrantes, necesitas más salientes |
| Conexiones por torrent | 100 | **50** | Reparte entre los 80 en vez de que 3 se coman el cupo |
| Slots de subida | 20 | **40** | Más torrents sembrando a la vez |
| Cola de torrents | 5 activos | **sin cola** | Quieres los 80 sembrando, no 5 |
| Hilos de E/S | 10 | **16** | Sesiones largas con muchos ficheros |
| Ficheros abiertos | 100 | **200** | 80+ torrents abren muchos a la vez |
| Límite de memoria | 512 MB | **1024 MB** | Cabecera para sesiones de días |
| Velocidad de conexión | 30 | **20** | WireGuard sufre con ráfagas de conexiones nuevas |

## El vigilante (`vpn-watch.py`) — automático

Servicio de systemd que sondea cada 20 s y hace **dos** cosas:

1. **Reata tras cada reconexión.** Norton asigna una IP de túnel nueva al reconectar y qBittorrent
   se queda escuchando en la vieja: trackers anunciando 26-37 seeds y **0 conexiones**, con el
   adaptador presente y el binding correcto — todo "bien" y nada funcionando. Reaplicar el binding
   lo desbloquea al instante (medido: 0 → 852 KB/s). Esto es **disponibilidad**, no seguridad.
2. **Restaura el binding si desaparece.** Una actualización de qBittorrent, un reset de
   preferencias o un `--unbind` olvidado te dejarían saliendo por la conexión real *en silencio*.
   Aquí sí es seguridad. Probado a mano: `--unbind` → detectado y restaurado en <20 s.

```bash
sudo cp servarr/vpn-watch.service /etc/systemd/system/
sudo systemctl enable --now vpn-watch
systemctl status vpn-watch          # ¿vivo?
journalctl -u vpn-watch -f          # qué ha ido haciendo
```

**Su límite:** vive en WSL, así que solo corre mientras WSL esté levantado. Si arrancas el PC y
abres únicamente qBittorrent, no habrá vigilante. **Eso no te expone** — el binding está guardado
en qBittorrent y sigue en pie; simplemente nadie reparará una IP de túnel obsoleta hasta que
arranques la app. El síntoma sería "no descarga", nunca una fuga.

## Puesta en marcha

```bash
python3 servarr/vpn-lock.py --status   # ver estado
python3 servarr/vpn-lock.py --tune     # ajustes (ya aplicado)
python3 servarr/vpn-lock.py --bind     # atar a Norton — REQUIERE la VPN conectada
python3 servarr/vpn-lock.py --bind mullvad   # atar a OTRA VPN (busca por trozo del nombre)
python3 servarr/vpn-lock.py --unbind   # deshacer (emergencias)
```

En PowerShell **como administrador**:

```powershell
powershell -ExecutionPolicy Bypass -File servarr\firewall.ps1
powershell -ExecutionPolicy Bypass -File servarr\firewall.ps1 -Remove   # deshacer
```

Orden correcto: **conectar Norton a la región P2P → `--bind` → `firewall.ps1` → probar**.

## Checklist de pruebas

Con los 80 torrents **parados** (como están ahora), y reanudándolos solo en el paso 3.

### A. Con la VPN conectada

- [ ] **A1 — La IP pública es la de la VPN.**
      En Windows: `curl.exe https://api.ipify.org` → debe ser una IP holandesa, **no** la tuya.
      Compárala con la que ves con la VPN apagada.
- [ ] **A2 — El binding está puesto.**
      `python3 servarr/vpn-lock.py --status` → "Interfaz: Norton VPN WireGuard Adapter"
      y "IP fijada: (todas las del adaptador)".
- [ ] **A3 — Fuga de DNS.** Entra en `https://www.dnsleaktest.com` (prueba extendida): ningún
      servidor debe pertenecer a tu ISP.
- [ ] **A4 — Los torrents descargan.** Añade un torrent público sano (una ISO de Linux sirve y es
      legal e ideal para probar) → debe empezar a bajar.
- [ ] **A5 — Los torrents siembran.** Reanuda unos pocos → en *Peers* debe haber conexiones y
      subida > 0. **Espera lo peor aquí**: sin port forwarding, tardará más en arrancar y verás
      menos peers. Es la limitación conocida de Norton, no un fallo de la configuración.
- [ ] **A6 — El tráfico va por WireGuard, no por Ethernet.** En PowerShell:
      `Get-NetTCPConnection -OwningProcess (Get-Process qbittorrent).Id | Select LocalAddress -Unique`
      → **todas** las direcciones locales deben ser la del túnel. Si aparece un `192.168.0.x`,
      hay fuga: para todo y revisa el binding.

### B. Kill switch (lo importante)

- [ ] **B1 — Desconecta la VPN desde Norton.** qBittorrent debe quedarse sin conectividad:
      trackers en error, peers a 0, velocidad 0. No debe seguir sembrando "un poquito".
- [ ] **B2 — Verifica que está mudo de verdad.** Repite el comando de A6: no debe haber ninguna
      conexión establecida a IPs externas.
- [ ] **B3 — La prueba dura: mata Norton.** Administrador de tareas → termina el proceso de
      Norton VPN. La conexión normal volverá (kill switch de Norton es de aplicación), **pero
      qBittorrent debe seguir bloqueado** por la regla de firewall. Este paso es el que valida
      la capa 2; si aquí hay tráfico, la regla no está bien puesta.

### C. Reconexión

- [ ] **C1 — Reconecta la VPN** a la región P2P. Los torrents deben reanudar solos en 1-2 min
      (los trackers se reanuncian). Si no, `--status` para ver si el adaptador cambió de nombre.
- [ ] **C2 — Sesión larga.** Déjalo sembrando unas horas y vuelve a mirar: subida > 0 y sin
      errores de tracker. Es la prueba de que aguanta periodos largos.

### D. Comprobación de fugas dedicada

- [ ] **D1 — Test de fuga por torrent.** `https://ipleak.net` tiene un apartado *"Torrent Address
      detection"* que te da un magnet de prueba: añádelo a qBittorrent y la web te dirá **con qué
      IP te ve el tracker**. Es la prueba definitiva y la única que mide la IP que ven los peers,
      no la del navegador. Debe ser la de la VPN.

## Cuando caduque la suscripción o cambies de VPN

Lo primero, para que no cunda el pánico: **esto falla hacia el lado seguro**. qBittorrent está
atado a un adaptador concreto; si ese adaptador desaparece, no tiene por dónde salir y se queda
mudo. **No se pone a torrentear por tu IP real en silencio.** Es un fallo ruidoso y visible,
que es exactamente lo que quieres que pase.

### Síntoma que verás

qBittorrent parece vivo pero no hace nada: **0 nodos DHT**, trackers que no responden, 0 peers,
velocidad 0. En la API, `connection_status: disconnected`. Es idéntico a un fallo de red, así que
es fácil despistarse — si esto aparece de la nada, **lo primero que hay que mirar es la VPN**.

Diagnóstico en una orden (dice si el adaptador sigue ahí y a qué está atado):

```bash
python3 servarr/vpn-lock.py --status
```

### Caso A — Cambias a otra VPN

1. Instala la VPN nueva y **conéctala** (a un servidor que permita P2P).
2. Átale qBittorrent pasando un trozo del nombre de su adaptador:

   ```bash
   python3 servarr/vpn-lock.py --bind mullvad     # o proton, windscribe, wireguard…
   ```

   El nombre no tiene que ser exacto: busca por subcadena, sin distinguir mayúsculas. Si no
   acierta, **te lista los adaptadores que hay** y eliges de ahí. `--bind` a secas sigue
   asumiendo Norton.
3. Comprueba `--status`: debe salir el adaptador nuevo e "IP fijada: (todas las del adaptador)".

**No hay que volver a tocar `--tune`.** LSD/UPnP apagados, DHT/PeX encendidos y los límites de
conexiones no dependen de qué VPN uses; ya están guardados en qBittorrent.

### Caso B — Te quedas sin VPN y quieres torrentear igualmente

```bash
python3 servarr/vpn-lock.py --unbind
```

Esto quita la atadura y qBittorrent vuelve a poder usar cualquier interfaz, **incluida tu conexión
real**. Es la única orden de este documento que te deja expuesto: úsala sabiendo lo que haces, y
vuelve a `--bind` en cuanto tengas VPN otra vez.

### Lo que NO se rompe

El resto de la app es ajeno a todo esto. La biblioteca, el escalado, las traducciones, Suwayomi y
el stack Servarr no pasan por la VPN. Lo único que deja de funcionar es **descargar por torrent**:
lo que ya está en disco se sigue viendo y leyendo con normalidad.

## Si algo falla

| Síntoma | Causa probable |
|---|---|
| **0 nodos DHT y trackers sin contactar** | Casi seguro la VPN: caducada, desconectada o adaptador renombrado → `--status`. Si las reglas de `firewall.ps1` están activas, son ellas: desactívalas |
| qBittorrent mudo con la VPN conectada | El adaptador cambió de nombre al recrearse → `--status` y `--bind` otra vez |
| Sembrando muy poco | Normal: sin port forwarding. No es arreglable por configuración |
| Trackers en error tras reconectar | Espera 2 min al reanuncio; si sigue, para y reanuda los torrents |
| `ipleak` muestra tu IP real | Para todo. El binding no está puesto o la regla de firewall falta |
