# Manga: de la biblioteca a tu edición

[Volver al recorrido principal](../README.md) · [Instalación](INSTALL_DESKTOP.md)

Descubre obras, continúa leyendo y prepara tomos en un mismo espacio.
La biblioteca distingue los capítulos descargados de tus archivos locales:
importar un CBZ/CBR no obliga a asociarlo con una serie del catálogo.

![Biblioteca de manga](img/current/manga-library.webp)

## Empieza a leer

1. Busca una obra en MangaDex o en las fuentes configuradas con Suwayomi.
2. Descarga los capítulos que quieras; aparecen en tu biblioteca local.
3. Abre el lector y elige lectura paginada o continua. El progreso permite
   volver a la página donde te quedaste.

Para contenido que ya tienes, utiliza la biblioteca de archivos CBZ/CBR.
Los archivos RAR/CBR necesitan `bsdtar`. Las fuentes externas requieren
Internet al buscar o descargar; leer capítulos ya descargados es local.

## Mejora la imagen con IA

El escalado genera páginas de mayor resolución usando un modelo que registras
por separado. Requiere GPU NVIDIA, CUDA y pesos compatibles con spandrel.
No necesitas esos componentes para limitarte a leer u organizar.

![Comparación animada del original y el escalado 4×](img/upscale_compare.gif)

Registra el modelo según la [guía de modelos](MODELS.md), selecciona un capítulo
desde la ficha y sigue el trabajo en Actividad. Revisa el resultado frente al
original antes de preparar una edición: más resolución no garantiza recuperar
detalles ausentes ni corregir todos los defectos de un scan.

## Construye un tomo

En la ficha, selecciona los capítulos y abre Tomo Builder. Elige portada,
originales o escalados, formato de imagen y calidad. La salida CBZ incorpora
metadatos ComicInfo.xml para lectores compatibles.

![Selección y exportación de tomos](img/current/tomo-builder.webp)

Puedes configurar otro tomo mientras el anterior se procesa. Consulta el progreso
en la ficha o en Actividad y guarda el archivo desde la app. La exportación no
requiere Google Drive ni WebDAV; esas integraciones son alternativas opcionales.

## Lleva la lectura al móvil

La app Android es un proyecto independiente: puede leer manga localmente y usar
el PC como origen adicional para importar capítulos, incluidos los escalados.
Consulta la [guía de Android](ANDROID.md) para entender esa separación.
