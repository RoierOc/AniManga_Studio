# qBittorrent y una VPN

[Volver al recorrido principal](../README.md) · [Instalación](INSTALL.md)

La VPN es opcional y se configura fuera de AniManga Studio. Para limitar las
descargas a su conexión, qBittorrent permite seleccionar una interfaz de red.
Esa configuración debe comprobarse en tu equipo: ningún script sustituye una
prueba de desconexión y reconexión.

## Vincular la interfaz

1. Pausa las descargas y el sembrado antes de cambiar la configuración.
2. Conecta la VPN a un servidor que permita el tráfico que necesitas.
3. En qBittorrent, abre **Opciones → Avanzadas → Interfaz de red** y selecciona
   el adaptador de la VPN, no «Cualquier interfaz».
4. Si la IP del túnel cambia al reconectar, evita fijar una dirección obsoleta.
5. Comprueba con una descarga de prueba autorizada que el tráfico se detiene al
   desconectar la VPN y se recupera después de reconectarla.

## Herramientas incluidas

Los auxiliares de `servarr/` utilizan la Web API de qBittorrent:

```bash
python3 servarr/vpn-lock.py --status
python3 servarr/vpn-lock.py --bind nombre-del-adaptador
```

`--bind` busca por una parte del nombre del adaptador. Confirma en `--status` que
ha elegido el correcto. `--tune` cambia preferencias de conexión; revisa su código
antes de ejecutarlo, porque sus valores no son adecuados para todas las redes.

`--unbind` elimina la vinculación y permite utilizar otras interfaces, incluida
tu conexión habitual. No lo uses para recuperar descargas si necesitas mantener
el tráfico limitado a la VPN.

El vigilante `vpn-watch.py` puede restaurar la vinculación. Antes de instalar
`vpn-watch.service`, adapta sus rutas absolutas y revisa el adaptador que vigila.
Si se ejecuta dentro de WSL, solo estará activo mientras ese entorno esté en marcha.

## Comprobaciones importantes

- Revisa la IP que comunica el propio cliente torrent; la IP del navegador no
  demuestra por dónde circula qBittorrent.
- Comprueba desconexión, reconexión, reinicio del cliente y cambio de adaptador.
- Revisa por separado IPv6, DNS y opciones de descubrimiento local.
- Consulta las restricciones de P2P y port forwarding de tu proveedor: pueden
  afectar a la disponibilidad y al sembrado.
- No publiques preferencias exportadas, credenciales de Web UI ni datos de la VPN.

**No ejecutes `servarr/firewall.ps1` como una receta universal.** Sus reglas
dependen de rangos y adaptadores concretos y pueden bloquear tráfico válido o no
cubrir tu instalación. Cualquier regla debe adaptarse y verificarse por separado.

La biblioteca local y el lector no dependen de que los torrents estén activos.
Si el cliente pierde conectividad, revisa primero la VPN y la interfaz seleccionada
antes de retirar esa protección.
