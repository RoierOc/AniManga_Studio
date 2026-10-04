# Series y películas con Servarr

[Volver al recorrido principal](../README.md) · [Cine](CINE.md)

AniManga Studio integra Sonarr, Radarr y Prowlarr en el área de Cine.
Son servicios externos: organizan el catálogo y las búsquedas; qBittorrent
gestiona las descargas. Sus interfaces permiten ajustar perfiles e indexadores.

## Servicios

| Servicio | Dirección local predeterminada | Función |
|---|---|---|
| Prowlarr | http://localhost:9696 | Indexadores compartidos |
| Sonarr | http://localhost:8989 | Series y episodios |
| Radarr | http://localhost:7878 | Películas |
| qBittorrent | http://localhost:8080 | Cliente de descargas |

Los scripts del proyecto están preparados para Linux/WSL, con qBittorrent en
Windows. Si tu instalación utiliza otras rutas o clientes, revisa la configuración
antes de ejecutar `configure.py`: contiene destinos predeterminados en `D:\Media`.

## Preparación

1. Activa la Web UI de qBittorrent y configura su acceso desde la app.
2. Descarga y arranca los servicios:

   ```bash
   bash servarr/fetch.sh
   bash servarr/start.sh
   ```

3. Revisa las rutas en `servarr/configure.py` y configura la integración:

   ```bash
   python3 servarr/configure.py
   python3 servarr/verify.py
   ```

4. Añade tus indexadores en Prowlarr y comprueba su sincronización con Sonarr y Radarr.
5. Elige los perfiles de calidad, carpetas raíz y reglas de renombrado de cada servicio.
6. Utiliza Cine para buscar títulos, incorporarlos y seguir su disponibilidad.

El arranque del motor intenta iniciar Servarr cuando su script está disponible.
`SERVARR_SKIP=1` permite omitirlo. Para detener únicamente estos servicios:

```bash
bash servarr/stop.sh
```

## Carpetas e importación

Si qBittorrent informa una ruta de Windows, Sonarr/Radarr en WSL necesitan su
equivalente Linux. Por ejemplo, `D:\Media\downloads` corresponde a
`/mnt/d/Media/downloads`. Ajusta el mapeo remoto al disco y directorio reales.
La dirección del cliente también debe ser accesible desde donde corre Servarr;
`localhost` no equivale al host Windows en todas las configuraciones de WSL.

Mantener descargas y biblioteca en el mismo sistema de archivos permite utilizar
hardlinks cuando el servicio y el volumen lo admiten. Entre discos habrá copia:
ten en cuenta el espacio de descarga, importación y siembra.

## Configuración y mantenimiento

- Define perfiles de calidad y límites de tamaño acordes con tu almacenamiento.
- Mantén separadas las categorías de series y películas en qBittorrent.
- Revisa las reglas de eliminación antes de activar la limpieza automática.
- Los servicios guardan configuración y claves en `servarr/config/`, excluido de Git.
  Respalda esa carpeta de forma privada; no publiques sus bases de datos ni `config.xml`.
- Ejecuta `python3 servarr/verify.py` para diagnosticar conexiones entre servicios.

La disponibilidad de un título depende de los indexadores configurados. Utiliza
contenido que tengas derecho a descargar y respeta las condiciones de cada fuente.
