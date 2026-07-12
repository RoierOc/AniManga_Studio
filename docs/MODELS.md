# Modelos de escalado — "pon tu modelo"

El escalador no tiene ningún modelo cableado en el código: lee `models/registry.json`
al arrancar y carga lo que encuentre ahí. Para usar un modelo nuevo, basta con
**descargar el archivo de pesos, colocarlo en `models/`, y agregar una entrada al JSON**
— no hace falta tocar `src/api/upscale.py`.

Cualquier arquitectura soportada por [spandrel](https://github.com/chaiNNer-org/spandrel)
funciona (ESRGAN, RRDBNet, SwinIR, HAT, DAT, Real-ESRGAN, etc.), en cualquier escala
(2x, 4x, ...) — la escala y los canales de entrada (B&N vs color) se leen del propio
archivo de pesos en tiempo de carga, no se asumen.

## Formato de `models/registry.json`

```json
{
  "mi_modelo": {
    "label": "Nombre que ve el usuario en el selector",
    "models": [
      {"sub_key": "mi_modelo", "file": "nombre-del-archivo.pth"}
    ]
  }
}
```

- La clave de primer nivel (`"mi_modelo"`) es el identificador interno — es lo que
  manda el frontend a `/api/upscale/set_model`.
- `label` es el texto mostrado en el selector de modelo de la UI.
- `file` se resuelve relativo a `MODELS_DIR` (por defecto `models/` en la raíz del
  repo; configurable con la variable de entorno `MODELS_DIR`) si no es una ruta
  absoluta. Puedes usar una ruta absoluta si prefieres guardar los pesos en otro
  lugar (otro disco, una carpeta compartida, etc.).
- `sub_key` identifica la sub-entrada dentro del modelo — para un modelo simple
  puede ser igual a la clave de primer nivel.

### Modelos adaptativos (varios sub-modelos según la altura de la página)

Algunos modelos (p. ej. MangaJaNai) entrenan variantes distintas para páginas de
distinta altura. Para registrar uno así:

```json
{
  "mi_modelo_adaptativo": {
    "label": "Mi modelo adaptativo",
    "adaptive": true,
    "models": [
      {"sub_key": "variante_baja", "file": "modelo-1200p.pth", "height_max": 1290},
      {"sub_key": "variante_alta", "file": "modelo-1400p.pth"}
    ]
  }
}
```

- `height_max` es la altura máxima (en px) de página para la que esa variante
  aplica. Las entradas se evalúan en orden ascendente de `height_max`.
- La entrada **sin** `height_max` es el catch-all: se usa para cualquier página
  más alta que todos los `height_max` declarados. Debe haber exactamente una.
- Si solo hay una entrada en `models`, `adaptive` no hace nada (se usa siempre esa).

## Dónde colocar los pesos

Por defecto, todos los archivos referenciados en `registry.json` deben vivir en
`models/` en la raíz del repo (creala si no existe). Podés cambiar esa carpeta
con la variable de entorno `MODELS_DIR` en tu `.env`:

```
MODELS_DIR=/ruta/a/tu/carpeta/de/modelos
```

Los pesos (`.pth`, `.safetensors`, `.onnx*`) están en `.gitignore` — nunca se
suben al repo, solo `registry.json` (que es texto, no binario).

## Modelos incluidos por defecto (enlaces de descarga)

> El repo no trae los pesos por su tamaño. Hay dos formas de colocarlos:
>
> **Automática** — `bash scripts/fetch-models.sh` lee `registry.json`, ve qué
> archivos faltan y descarga los que conozca su URL. Como las URLs oficiales no
> son estables/directas para todos los modelos, el script las toma (en orden) de:
> 1. una variable de entorno por archivo —
>    `MODEL_URL__<archivo con no-alfanum → _>` (ej.
>    `MODEL_URL__4x_MangaJaNai_1200p_V1_ESRGAN_70k_pth=…`), o
> 2. un archivo `scripts/model-urls.env` (líneas `CLAVE=URL`, gitignorado).
>
> Rellená ahí las URLs una vez y el fetcher (y todas las instalaciones futuras)
> las reusa. Sin URL, el script no rompe: avisa qué falta.
>
> **Manual** — descargalos de las fuentes oficiales y colocalos en `models/` con
> estos nombres exactos para que el `registry.json` por defecto los encuentre:

| Archivo | Modelo | Descarga |
|---|---|---|
| `4x-eula-digimanga-bw-v2-nc1.pth` | eula-digimanga (B&N) | _(completar enlace)_ |
| `4x_MangaJaNai_1200p_V1_ESRGAN_70k.pth` | MangaJaNai V1 — variante 1200p | _(completar enlace)_ |
| `4x_MangaJaNai_1400p_V1_ESRGAN_105k.pth` | MangaJaNai V1 — variante 1400p | _(completar enlace)_ |

Otros modelos compatibles que podés agregar tú mismo siguiendo el formato de
arriba: IllustrationJaNai, AnimeSharp, DWTP, o cualquier modelo spandrel-compatible
de [OpenModelDB](https://openmodeldb.info/).

## Cambiar el modelo activo

Desde la UI: selector de modelo en la pantalla de escalado (lee `GET
/api/upscale/models`, que devuelve lo que haya en `registry.json`).

Por API: `POST /api/upscale/set_model` con `{"model": "mi_modelo"}` — reinicia
el worker de GPU para cargar el nuevo modelo en la próxima página a escalar.

Si un archivo referenciado en `registry.json` no existe, `set_model` devuelve
404 con la lista de rutas faltantes en vez de fallar a medias.
