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

## Instalación Android

Las instrucciones para compilar e instalar la app se mantienen junto al código
Android, en el README de su repositorio independiente.
