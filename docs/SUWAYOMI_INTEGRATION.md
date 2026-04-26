# Suwayomi Integration Plan
## MangaJaNai — Multi-source manga search via Suwayomi microservice

---

## Resumen ejecutivo

Integrar Suwayomi-Server como microservicio local (puerto 4567) que expone
cientos de fuentes de manga (las mismas que Mihon/Tachiyomi en Android).
El Flask app hace peticiones HTTP a Suwayomi para búsqueda y descarga de
capítulos de cualquier fuente instalada, complementando MangaDex.

---

## Estado actual del sistema

- **Java:** NO instalado (necesita instalación)
- **RAM disponible:** ~13 GB libres — OK
- **Disco:** 929 GB libres — OK
- **CPU:** 12 cores — OK
- **curl/wget:** disponibles

---

## Dependencias a instalar ANTES de empezar

### 1. Java 21 JRE (obligatorio)
```bash
# Ubuntu/Debian (WSL2)
sudo apt update
sudo apt install -y openjdk-21-jre-headless

# Verificar:
java --version  # debe mostrar 21.x
```

### 2. Suwayomi-Server JAR
```bash
# Descargar el JAR standalone (no requiere instalador)
wget -O /Manga_Upscaler_project/suwayomi/Suwayomi-Server.jar \
  https://github.com/Suwayomi/Suwayomi-Server/releases/download/v2.1.1867/Suwayomi-Server-v2.1.1867.jar

mkdir -p /Manga_Upscaler_project/suwayomi
```

### 3. No se necesita nada más en Python
- Las llamadas a Suwayomi son HTTP puras (requests ya instalado en el venv)

---

## Arquitectura de integración

```
Browser
  │
  ▼
Flask app (puerto 5001)
  │
  ├── /api/mangadex/*  →  MangaDex API (existente)
  │
  └── /api/sources/*   →  Suwayomi (puerto 4567) [NUEVO]
                              │
                              ├── Fuente 1: MangaSee
                              ├── Fuente 2: Bato.to
                              ├── Fuente 3: MangaPlus
                              └── Fuente N: ...
```

Flask actúa como proxy: el frontend solo habla con Flask, que internamente
decide si usar MangaDex o Suwayomi.

---

## Configuración de Suwayomi (headless, sin GUI)

Archivo: `/Manga_Upscaler_project/suwayomi/server.conf`
```hocon
server.port = 4567
server.ip = "127.0.0.1"
server.webUIEnabled = true
server.initialOpenInBrowserEnabled = false
server.systemTrayEnabled = false
server.downloadAsCbz = false

# Directorio de datos
server.rootDir = "/Manga_Upscaler_project/suwayomi/data"

# Extension repos (Tachiyomi-compatible)
server.extensionRepos = [
  "https://raw.githubusercontent.com/suwayomi/tachiyomi-extension/repo/index.min.json"
]
```

Script de arranque: `/Manga_Upscaler_project/suwayomi/start.sh`
```bash
#!/bin/bash
java -Xmx512m \
  -Dsuwayomi.tachidesk.server.rootDir=/Manga_Upscaler_project/suwayomi/data \
  -jar /Manga_Upscaler_project/suwayomi/Suwayomi-Server.jar \
  > /tmp/suwayomi.log 2>&1 &
echo $! > /tmp/suwayomi.pid
echo "Suwayomi started (PID $(cat /tmp/suwayomi.pid))"
```

---

## API de Suwayomi — Endpoints clave

**NOTA:** La REST v1 está deprecated. Suwayomi usa GraphQL como API primaria.
URL: `http://localhost:4567/api/graphql`

### Buscar manga en una fuente
```graphql
query SearchManga($sourceId: LongString!, $query: String!, $page: Int!) {
  fetchSourceManga(sourceId: $sourceId, type: SEARCH, query: $query, page: $page) {
    mangas {
      id
      title
      thumbnailUrl
      inLibrary
    }
    hasNextPage
  }
}
```

### Listar fuentes instaladas
```graphql
query {
  sources {
    nodes {
      id
      name
      lang
      iconUrl
      isNsfw
    }
  }
}
```

### Obtener capítulos de un manga
```graphql
query GetChapters($mangaId: Int!) {
  chapters(condition: { mangaId: $mangaId }) {
    nodes {
      id
      name
      chapterNumber
      uploadDate
      scanlator
      pageCount
    }
  }
}
```

### Obtener páginas de un capítulo
```graphql
query GetPages($chapterId: Int!) {
  fetchChapterPages(chapterId: $chapterId) {
    pages
  }
}
# Retorna lista de URLs relativas → http://localhost:4567{url}
```

### Fetch/cargar manga desde fuente (necesario antes de ver capítulos)
```graphql
mutation FetchMangaDetails($id: Int!) {
  fetchManga(input: { id: $id }) {
    manga {
      id
      title
      description
      genre
      status
      author
    }
  }
}
```

---

## Nuevo blueprint Flask: `src/api/sources.py`

### Endpoints a implementar:

| Endpoint Flask | Acción | Llama a Suwayomi |
|---|---|---|
| `GET /api/sources/list` | Listar fuentes instaladas | `sources { nodes }` |
| `GET /api/sources/search?q=...&source=...` | Buscar manga | `fetchSourceManga` |
| `GET /api/sources/<id>/chapters` | Capítulos de un manga | `chapters` |
| `GET /api/sources/<id>/pages/<ch_id>` | Páginas de capítulo | `fetchChapterPages` |
| `POST /api/sources/install` | Instalar extensión | mutation `installExternalExtension` |

### Helper GraphQL en Python:
```python
import requests

SUWAYOMI_URL = "http://localhost:4567/api/graphql"

def gql(query: str, variables: dict = None):
    resp = requests.post(SUWAYOMI_URL, json={
        "query": query,
        "variables": variables or {}
    }, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(data["errors"])
    return data["data"]
```

---

## Cambios en el frontend (app.js + index.html)

### Nueva pestaña en el header: "Fuentes"
- Lista de fuentes instaladas con su flag de idioma
- Buscador con selector de fuente activa
- Cards de resultados (mismo estilo que MangaDex explore)
- Al abrir un manga: capítulos, descarga y upscale igual que ahora

### Estado nuevo en app.js:
```javascript
const sources = ref([])           // fuentes instaladas
const activeSource = ref(null)    // fuente seleccionada
const sourceResults = ref([])     // resultados de búsqueda
const sourcesLoading = ref(false)
```

### Flujo de descarga desde fuente externa:
1. Usuario busca en fuente X → ve capítulos
2. Click "Descargar" → Flask llama `fetchChapterPages` en Suwayomi → obtiene URLs de imágenes
3. Flask descarga imágenes desde esas URLs → las guarda en `MANGA_DIR/{title}/`
4. Resto del pipeline (upscale, lector, export) funciona idéntico al actual

---

## Pasos de implementación (en orden)

### Fase 1 — Infraestructura (prerequisites del USUARIO)
```
1. sudo apt install -y openjdk-21-jre-headless
2. mkdir -p /Manga_Upscaler_project/suwayomi
3. Descargar Suwayomi-Server.jar
4. Crear server.conf y start.sh
5. Arrancar Suwayomi y verificar http://localhost:4567
6. Desde la WebUI de Suwayomi: instalar extensiones deseadas
   (abrir http://localhost:4567 en browser, ir a Extensions, instalar)
```

### Fase 2 — Backend Flask
```
1. Crear src/api/sources.py con helper GQL + endpoints
2. Registrar blueprint en app.py (/api/sources)
3. Añadir función de descarga desde URLs externas en download.py
   (actualmente solo descarga de MangaDex)
```

### Fase 3 — Frontend
```
1. Añadir tab "Fuentes" en header nav (junto a Biblioteca/Explorar/Seguidos)
2. Vista de sources: lista de fuentes instaladas, search bar
3. Resultados: mismos cards que MangaDex
4. Modal de manga: reutilizar modal existente, añadir sourceId al contexto
5. Descarga: adaptar downloadChapter() para fuentes externas
```

### Fase 4 — Integración avanzada (opcional)
```
- Auto-arranque de Suwayomi cuando inicia Flask
- Health check: Flask verifica si Suwayomi está corriendo
- Caché de resultados de búsqueda
- Filtros por idioma en búsqueda de fuentes
```

---

## Estimación de esfuerzo

| Fase | Complejidad | Archivos |
|---|---|---|
| Fase 1 (infra) | Baja — solo comandos | server.conf, start.sh |
| Fase 2 (backend) | Media | sources.py, download.py, app.py |
| Fase 3 (frontend) | Media-Alta | app.js, index.html, styles.css |
| Fase 4 (avanzado) | Baja | app.py, sources.py |

---

## Notas importantes

- **Suwayomi GraphQL vs REST:** La API REST v1 está marcada como deprecated.
  Usar GraphQL exclusivamente. El endpoint es `POST /api/graphql`.
- **Páginas de imágenes:** Suwayomi sirve las imágenes en proxy desde su propio
  servidor. Las URLs de páginas son relativas: `http://localhost:4567{relativeUrl}`
- **Primera descarga de manga:** Antes de poder listar capítulos, hay que hacer
  `fetchManga` (mutation) para que Suwayomi cargue los metadatos desde la fuente.
- **Extensiones populares para manga japonés:**
  - MangaSee
  - Batoto (Bato.to)
  - MangaPlus (Shueisha)
  - Mangakakalot
  - Dynasty Scans
- **Memoria:** Suwayomi con `-Xmx512m` es suficiente para uso normal.
  Con muchas fuentes activas simultaneas puede necesitar `-Xmx1g`.
