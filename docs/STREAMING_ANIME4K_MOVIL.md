# Reproductor del PC en Android con Sunshine y Moonlight

[Volver al recorrido principal](../README.md) · [App Android](ANDROID.md)

Esta alternativa transmite la imagen del reproductor del PC a un dispositivo
Android. El PC ejecuta AniManga Studio y Anime4K; Sunshine captura y codifica su
salida, y Moonlight la muestra en el dispositivo. Es una integración externa,
distinta de descargar archivos y reproducirlos localmente en la app Android.

## Qué necesitas

- Un PC capaz de reproducir el vídeo con el perfil Anime4K seleccionado y codificar
  la transmisión simultáneamente.
- [Sunshine](https://docs.lizardbyte.dev/projects/sunshine/latest/) instalado en
  el sistema que tiene acceso al escritorio: Windows, no dentro de WSL, si la
  ventana nativa se ejecuta en Windows.
- [Moonlight](https://moonlight-stream.org/) en Android y una conexión de red estable.

## Configuración inicial

1. Instala Sunshine siguiendo su documentación para tu sistema operativo.
2. Configura el acceso de administración local y los permisos de captura que
   requiera tu escritorio. No concedas privilegios adicionales sin comprobar
   que el método de captura elegido los necesita.
3. Instala Moonlight en Android y empareja el dispositivo con el PC mediante el PIN.
4. Inicia la transmisión del escritorio y abre AniManga Studio en el PC.
5. Reproduce un episodio, selecciona Anime4K y ajusta resolución, tasa de imágenes
   y bitrate en Moonlight según la red y el dispositivo.

Consulta la [guía oficial de emparejamiento y conexión](https://github.com/moonlight-stream/moonlight-docs/wiki/Setup-Guide)
si el host no aparece. Los requisitos de captura y firewall dependen de la versión
instalada; no abras rangos de puertos a Internet como solución inicial.

## Calidad, recursos y privacidad

La imagen transmitida vuelve a comprimirse, por lo que no es idéntica a la salida
local. Un bitrate mayor necesita más ancho de banda. La codificación por hardware
reduce la carga, pero la captura, los shaders y el escalado simultáneo siguen
compartiendo recursos del PC.

Empieza en la red local. Para acceso remoto, utiliza una conexión privada y revisa
las recomendaciones de seguridad de Sunshine/Moonlight. La transmisión del
escritorio puede mostrar notificaciones y otras ventanas: configura el host con
eso en mente y evita exponer su panel de administración.
