# Android y conexión con el PC

La app Android se mantiene en el proyecto hermano **`animanga-android`**, separado
de este repositorio. No hay un APK ni un módulo Gradle Android dentro de AniManga
Studio PC. Su README y documentación de uso viven en aquel checkout.

## Independencia y límites

| Uso | ¿Necesita el PC encendido? |
|---|---|
| Explorar y obtener manga desde MangaDex o extensiones instaladas | No; la fuente puede requerir Internet |
| Leer manga descargado en el dispositivo | No |
| Leer CBZ/CBR importados en Archivos | No |
| Consultar Mi PC o importar páginas escaladas | Sí, durante el acceso o la transferencia |
| Leer las páginas del PC ya descargadas en el dispositivo | No |
| Reproducir por red un episodio que vive en el PC | Sí |
| Ver un episodio ya descargado para uso sin conexión | No |
| Ejecutar escalado IA o traducción pesada del PC | Sí; esas tareas no se trasladan al móvil |

## Implementación actual

Kotlin y Jetpack Compose; biblioteca y progreso en Room; MangaDex nativo;
cargador de extensiones compatible con Mihon; descargas locales; reproducción
con libmpv; calendario de temporada y biblioteca Archivos diferenciada.

**Este dispositivo** y **Mi PC** son orígenes distintos. Puedes traer capítulos
concretos, incluidas sus páginas escaladas, sin convertir automáticamente toda
la biblioteca de escritorio en biblioteca local. La sincronización de progreso
no significa que la biblioteca entera, sus ficheros o todas las preferencias se
repliquen en ambos sentidos.

## Acceso al backend

Configura el acceso remoto y el emparejamiento desde Ajustes del PC. El token
remoto es un secreto: no debe aparecer en capturas, URLs publicadas o Git.
La implementación distingue tráfico local y una entrada remota autenticada;
en WSL, la dirección accesible puede depender de la configuración de red y del
puente Windows. Usa la dirección que muestre la configuración, no asumas que el
puerto local 5101 sirve directamente para el teléfono.

Para uso fuera de casa, utiliza una red privada como Tailscale o WireGuard.
**No abras el backend directamente a Internet ni el puerto del router.**
WebDAV sigue siendo una integración disponible para lectores externos, no la
descripción de la aplicación Android propia.

## Procedencia y validación

Esta descripción se contrastó el 30-sep-2026 con el checkout Android y sus
documentos, además de los contratos del backend. El historial de aquel proyecto
registra pruebas en Xiaomi Pad 6; no se ejecutó una nueva prueba ni
se instaló un APK durante la renovación del README de PC.

No se incluyen capturas Android nuevas hasta poder obtenerlas y revisarlas en un
dispositivo real. [ANDROID_MIGRATION.md](dev/ANDROID_MIGRATION.md) es el plan
histórico: incluye decisiones y pendientes que no describen por sí solos el
estado implementado. Para construir el APK, consulta el README del proyecto Android.
