# Anime: encuentra, organiza y reproduce

[Volver al recorrido principal](../README.md) · [Instalación](INSTALL_DESKTOP.md)

Anime Studio reúne búsqueda, biblioteca, episodios y estrenos. Puedes empezar
con archivos que ya tienes o conectar qBittorrent para descargar releases.

![Biblioteca de anime](img/current/anime-library.webp)

## Añade una serie

Busca el título en AniList y consulta su ficha. Añádelo a la biblioteca para
administrar sus episodios. Si ya tienes una carpeta local, vincúlala desde la
app en lugar de descargar los mismos archivos otra vez.

Para torrents, configura la WebUI de qBittorrent y su conexión con AniManga.
La búsqueda de releases usa Nyaa; disponer de un título en el catálogo no
significa que sus episodios estén descargados.

![Ficha y episodios de Attack on Titan](img/current/anime-detail.webp)

## Reproduce en la app nativa

Abre un episodio disponible para usar el reproductor libmpv integrado en Windows.
Sus controles permiten seleccionar audio y subtítulos, ajustar la reproducción,
saltar la introducción y acceder a los episodios.

![Reproductor nativo con subtítulos y controles](img/current/native-player.webp)

Anime4K procesa la imagen en tiempo real: no crea un vídeo nuevo ni sustituye
el archivo original. El escalado que guarda una nueva salida es otra operación.
La capacidad de la GPU y el perfil elegido condicionan el rendimiento.

## Sigue los estrenos

El calendario muestra episodios por día en vista semanal o agenda. Puedes
consultar todo el catálogo disponible o limitarlo a tu biblioteca. Los horarios
proceden de los metadatos externos y pueden cambiar.

![Calendario con estrenos disponibles](img/current/anime-schedule.webp)

Las fichas también reúnen relaciones y orden de estreno de la franquicia.
Que una obra esté relacionada no equivale necesariamente a que sea una secuela.

## Prepara los subtítulos

Según las fuentes configuradas, puedes buscar pistas, sincronizarlas con
ffsubsync y traducirlas con las herramientas disponibles. Estas operaciones
requieren dependencias y, en algunos casos, credenciales propias; no vienen
habilitadas por disponer únicamente del reproductor.

Consulta [instalación y configuración](INSTALL.md) para preparar cada integración.
