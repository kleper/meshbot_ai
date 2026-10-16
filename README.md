# Meshtastic AI Bot v3.0 — Canal Comunitario `PlayaRica`

Bot inteligente, autónomo y *offline-first* para redes LoRa **Meshtastic**, diseñado para brindar asistencia comunitaria rural, pronósticos meteorológicos agrícolas, directorio veredal con **RAG Local (`SQLite FTS5`)**, telemetría de red e **Inteligencia Artificial ejecutada localmente sobre la NPU Hailo-10H (`qwen3:1.7b`)** en una **Raspberry Pi 5**.

Incluye un servidor web y API REST con **FastAPI + HTMX + Jinja2** (puerto `8002`) con una **Interfaz Web 100% Responsive** preparada para celulares y computadores en la red local sin depender de CDNs ni conexión a internet.

---

## 1. Arquitectura Modular del Proyecto (`v3.0`)

```text
meshtastic-bot/
├── app/
│   ├── __init__.py                 # Versión del paquete (3.0.0)
│   ├── config.py                   # Configuración tipada, constantes LoRa (228B), prompts y KNOWN_LOCATIONS
│   ├── formatter.py                # Limpieza de texto, corte en oraciones completas y partición <= 228 bytes UTF-8
│   ├── mesh_service.py             # Cliente Meshtastic TCP/Serial, Worker Queue asíncrona y reconexión automática
│   ├── router.py                   # Enrutador de comandos (!info, !guardar, !clima, !gps, !telem) y lenguaje natural
│   ├── api.py                      # Servidor FastAPI (API JSON REST /kb, /health, /nodes, /messages, /simulate, /send)
│   ├── ui_routes.py                # Enrutador Web modular con Jinja2 + fragmentos parciales HTMX
│   ├── services/
│   │   ├── knowledge.py            # Motor RAG Local Híbrido: SQLite FTS5 (BM25) + Sinónimos Rurales + Sync YAML
│   │   ├── llm.py                  # Cliente Hailo-Ollama (qwen3:1.7b), semáforo NPU (Lock) y caché LRU en RAM
│   │   ├── weather.py              # Geocodificación, pronóstico por jornadas y caché meteorológico TTL (15 min)
│   │   ├── telemetry.py            # Distancias Haversine, rumbos cardinales, GPS, telemetría y estado de malla
│   │   └── search.py               # Consultas a Wikipedia en español y DuckDuckGo Lite (sin API keys)
│   ├── templates/
│   │   ├── base.html               # Layout principal responsive y barra de navegación de 3 pestañas HTMX
│   │   └── partials/
│   │       ├── status_bar.html     # Indicador en vivo de conexión LoRa, modelo NPU y total de fichas (cada 15s)
│   │       ├── tab_directory.html  # Pestaña 1: Buscador instantáneo FTS5 y filtros por categoría
│   │       ├── kb_cards.html       # Grid de tarjetas de conocimiento local con acciones HTMX
│   │       ├── tab_form.html       # Pestaña 2: Formulario de alta/edición con contador de bytes LoRa en vivo
│   │       ├── tab_simulator.html  # Pestaña 3: Simulador interactivo del bot sin transmitir por RF
│   │       └── sim_result.html     # Vista previa de paquetes LoRa simulados, bytes UTF-8 y latencia
│   └── static/
│       ├── css/styles.css          # Hoja de estilos responsive mobile-first (Grid de 3 tabs en móvil)
│       └── js/
│           ├── htmx.min.js         # HTMX 2.0.4 empaquetado localmente (100% funcional sin internet)
│           └── app.js              # Helper ligero para contador de bytes UTF-8 LoRa y estado de pestaña activa
├── data/
│   ├── conocimiento_local.yaml     # Semilla y respaldo legible sincronizado automáticamente con SQLite
│   └── local_kb.sqlite             # Base de datos SQLite + índice virtual FTS5 (persistida en volumen Docker)
├── tests/
│   ├── test_formatter.py           # Pruebas de límite de 228 bytes UTF-8 y preservación de oraciones completas
│   ├── test_knowledge.py           # Pruebas de SQLite FTS5, sinónimos rurales, comandos !guardar y UI HTMX
│   └── test_router_and_api.py      # Pruebas de geocodificación, caché meteorológico, síntesis rural y API REST
├── bot.py                          # Punto de entrada principal (levanta FastAPI + hilos Meshtastic)
├── Dockerfile                      # Imagen Python 3.12-slim con HEALTHCHECK nativo en puerto 8002
├── docker-compose.yml              # Despliegue con network_mode: host y volumen persistente ./data:/app/data
├── requirements.txt
└── .env.example
```

---

## 2. Características Principales

### 2.1. RAG Comunitario Local de 2 Niveles (`SQLite FTS5` + Sinónimos Rurales)
Diseñado para responder preguntas cotidianas de la comunidad de **Playa Rica (San Roque, Antioquia)** —como horarios de la chiva/escalera, teléfonos de vecinos, expresos en moto, fontanero, venta de gas, juntas o emergencias— **sin sobrecargar la Raspberry Pi ni la NPU Hailo-10H**:

* **Nivel 1 — Modo Determinista Instantáneo (`< 2 ms`, 0% uso de NPU)**:
  * Utiliza el motor nativo **SQLite FTS5** con tokenizador `unicode61 remove_diacritics 2` (insensible a tildes/mayúsculas) y ranking **BM25** ponderado (`título = 5.0`, `keywords = 4.0`, `respuesta = 2.0`).
  * Incluye un **Diccionario de Sinónimos Rurales y Colombianos** (`chiva` ↔ `escalera` ↔ `bus`, `telefono` ↔ `celular` ↔ `numero` ↔ `whatsapp`, `medico` ↔ `hospital` ↔ `salud`, `luz` ↔ `energia` ↔ `epm`, `gas` ↔ `pipeta`, `acarreo` ↔ `flete` ↔ `moto`).
  * Cuando hay coincidencia directa o se usa `!info`, entrega la ficha exacta en **menos de 2 milisegundos** sin pasar por el LLM, garantizando **0% de riesgo de alucinación en números de teléfono u horarios**.
* **Nivel 2 — Modo RAG Asistido por IA (`qwen3:1.7b`)**:
  * Ante preguntas abiertas o compuestas en lenguaje natural, recupera las fichas locales más relevantes y las inyecta en `qwen3:1.7b` bajo un prompt de **Directorio Comunitario Público Autorizado**.
* **Doble Persistencia Sincronizada (`SQLite` + `YAML`)**:
  * Cada cambio realizado desde la Interfaz Web, la API REST o un comando en el canal LoRa se guarda en `data/local_kb.sqlite` y actualiza automáticamente `data/conocimiento_local.yaml`.

---

### 2.2. Interfaz Web Local 100% Responsive con HTMX (`http://<IP_RPI>:8002/`)
Panel de administración ligero, rápido y adaptado a pantallas móviles y de escritorio, construido con **HTMX + Jinja2 + CSS modular** sin depender de librerías externas en la nube:

1. **Barra Superior de Estado en Vivo**:
   * Muestra el estado del enlace LoRa (`LoRa OK (PlayaRica)`), el modelo activo en la NPU (`🧠 qwen3:1.7b`) y el total de fichas registradas (`📚 X fichas`), actualizándose automáticamente cada 15 segundos vía HTMX.
2. **Navegación en 3 Pestañas (Tabs) 100% Visibles en Móvil y Escritorio**:
   * En dispositivos móviles (`< 640px`), las 3 pestañas se organizan en un **Grid de 3 columnas iguales** (`📇 Directorio` | `➕ Agregar` | `🧪 Probar`) para que siempre estén visibles sin scroll horizontal:
   * **Pestaña 1 — `📇 Base de Conocimiento` (`/ui/directorio`)**:
     * Buscador instantáneo en vivo (`hx-get="/ui/partials/kb-cards" hx-trigger="input changed delay:180ms"`).
     * Filtros rápidos por categoría (`Todas`, `🚌 Transporte`, `📇 Directorio`, `🚨 Emergencias`, `📢 Avisos`, `🌾 Agro`).
     * Tarjetas con botones para **✏️ Editar**, **🧪 Probar en Bot** y **🗑️ Borrar**.
   * **Pestaña 2 — `➕ Agregar Contenido` (`/ui/agregar`)**:
     * Formulario dedicado para crear o editar fichas locales.
     * **Contador en vivo de bytes UTF-8 y paquetes LoRa (`<= 228 bytes`)** que calcula en tiempo real cuántos paquetes ocupará la respuesta en Meshtastic.
     * Guía integrada con ejemplos de comandos para agregar datos desde la radio.
   * **Pestaña 3 — `🧪 Simulador y Red LoRa` (`/ui/simulador`)**:
     * Permite escribir cualquier pregunta o comando (`¿A qué hora sale la chiva?`, `!info emergencias`, `!clima Playa Rica`) y ver al instante los paquetes LoRa generados, su tamaño exacto en bytes UTF-8 y la latencia en milisegundos **sin transmitir por radiofrecuencia**.

---

### 2.3. Agregar y Consultar Conocimiento Local desde el Canal LoRa
Cualquier vecino o administrador conectado al canal `PlayaRica` puede alimentar y consultar la base de conocimiento directamente desde su radio Meshtastic:

* **Guardar una nueva ficha desde el canal (`!guardar`, `!agregar`, `!anotar`, `!nuevo`)**:
  * **Formato estándar (`Título: Información`)**:
    ```text
    !guardar Moto Don Luis: Cel 311-222-3344 hace expresos a San Roque y Cisneros
    ```
    *(El bot detecta automáticamente la categoría `🚌 transporte` y genera las palabras clave).*
  * **Formato con categoría explícita (`[categoria] Título: Información`)**:
    ```text
    !guardar [avisos] Convite JAC: Domingo 8:00 AM arreglo de vía en la escuela
    ```
  * **Formato libre (con `|` o `-` o texto corrido)**:
    ```text
    !guardar Doña Marta Gas | Vende pipetas de 30 y 40 lb cel 312-999-8877
    ```
* **Consultar fichas desde el canal (`!info`, `!directorio`, `!local` o lenguaje natural)**:
  * `!info chiva` / `!directorio Don Luis` / `!info #1` / `!info` (lista resumen del directorio).
  * Pregunta directa en el canal sin comando: *"¿A qué hora sale la chiva para San Roque?"* o *"¿Cuál es el celular de Don Luis el de la moto?"*.
* **Eliminar una ficha por su ID**:
  * `!borrar_info 8` (o `!eliminar_info 8`).

---

### 2.4. Clima, Pronóstico Extendido y Lectura de Campo con IA (3 Paquetes + Caché TTL 15 min)
Cuando un usuario solicita `!clima` (o pregunta *"¿va a llover hoy en Playa Rica?"*), el bot entrega hasta **3 paquetes LoRa** cuidadosamente ajustados a `<= 228 bytes UTF-8`:

1. **Paquete `[1/3]` (Condición actual + evolución por horas)**:
   * Estado del cielo en español, temperatura actual, sensación térmica (`ST`), mínima y máxima del día, humedad, viento con rumbo cardinal, índice UV, probabilidad y milímetros de lluvia (`mm`), más el pronóstico de las próximas horas (`Próx`).
2. **Paquete `[2/3]` (Pronóstico por jornadas del día)**:
   * Resumen para **`Mañana` (06–12h)**, **`Tarde` (12–18h)**, **`Noche` (18–24h)** y **`Mañ.sig`** (día siguiente).
3. **Paquete `[3/3]` (`🌾 Campo:` Lectura generada por IA para trabajadores rurales)**:
   * Pasa el resumen meteorológico por el modelo local **`qwen3:1.7b`** en la NPU Hailo-10H para traducir los datos técnicos en un consejo breve y claro para labores del campo (si hará frío o bochorno, si viene llovizna o aguacero fuerte y qué precaución tomar con cosechas, secaderos o ganado).

---

### 2.5. Rendimiento y Protección de la NPU Hailo-10H (`qwen3:1.7b`)
* **Modelo activo**: `qwen3:1.7b` ejecutado en `hailo-ollama` sobre la NPU **Hailo-10H** (Raspberry Pi 5).
* **Velocidad medida en hardware real (`rpi5fct`)**:
  * **Velocidad de decodificación**: **`~4.91 – 4.96 tokens/segundo`** sostenidos (`~202 ms/token`).
  * **Time To First Token (TTFT)**: **`~842 ms`**.
* **Protección de concurrencia (`_NPU_LOCK`) y Caché LRU (`_LLM_CACHE`)**:
  * `app/services/llm.py` serializa las peticiones mediante un `threading.Lock()` para evitar colisiones en el contexto único de la NPU y almacena en memoria RAM (TTL 30 min) las respuestas recientes para responder preguntas repetidas en `< 1 ms`.

---

## 3. Tabla Resumen de Comandos en la Radio LoRa

| Comando / Frase en el Canal | Acción | Latencia Típica |
| :--- | :--- | :--- |
| `!ayuda` o `/ayuda` | Lista todos los comandos disponibles en el bot | `< 2 ms` |
| `!info` o `!directorio` | Muestra el resumen de fichas guardadas en el Directorio Local | `< 2 ms` |
| `!info <búsqueda>` | Busca por título, palabra clave o sinónimo rural en la base local (`!info chiva`, `!info medico`) | `< 2 ms` (0% NPU) |
| `!guardar <Título>: <Dato>` | Agrega o actualiza una ficha en la Base de Conocimiento Local desde la radio | `~15 ms` |
| `!guardar [cat] <Título>: <Dato>` | Guarda especificando categoría (`transporte`, `directorio`, `emergencias`, `avisos`, `agro`) | `~15 ms` |
| `!borrar_info <ID>` | Elimina una ficha local por su número de ID (`!borrar_info 8`) | `~15 ms` |
| `!clima` o `!clima <lugar>` | Pronóstico extendido en 3 paquetes (Actual+Horas, Jornadas y Lectura de Campo IA) | `~8.8 s` (`< 2 ms` en caché) |
| `!clima local` o `!clima <nodo>` | Clima extendido usando las coordenadas GPS reportadas por un nodo de la malla | `~8.8 s` |
| `!gps` o `!gps <nodo>` | Coordenadas GPS, altitud, distancia Haversine, rumbo y enlace a Google Maps | `< 5 ms` |
| `!gps lista` | Lista todos los nodos de la malla que reportan posición GPS y su distancia | `< 5 ms` |
| `!telem` o `!telem <nodo>` | Batería (`%`), voltaje (`V`), ocupación de canal (`%`), AirTX y tiempo encendido | `< 5 ms` |
| `!nodos` o `!telem red` | Resumen general de nodos vistos, activos hoy, con GPS y ocupación promedio del canal | `< 5 ms` |
| `!wiki <tema>` | Consulta Wikipedia en español y resume en 1–2 oraciones con `qwen3:1.7b` | `~6–9 s` |
| `!buscar <consulta>` | Busca en DuckDuckGo Lite y sintetiza la respuesta con `qwen3:1.7b` | `~7–10 s` |
| `!hora` | Devuelve la hora y fecha local del servidor (`America/Bogota`) | `< 1 ms` |
| *Lenguaje natural* | Detecta automáticamente si es consulta de directorio local, clima, GPS, batería o pregunta abierta para la IA | `< 2 ms` a `~8 s` |

---

## 4. Endpoints del Servidor Web y API REST (Puerto `8002`)

### Vistas Web HTML / HTMX
* `GET /` o `GET /ui/directorio`: Interfaz Web Responsive — Pestaña **📇 Base de Conocimiento**.
* `GET /ui/agregar`: Interfaz Web Responsive — Pestaña **➕ Agregar / Editar Contenido Local**.
* `GET /ui/simulador`: Interfaz Web Responsive — Pestaña **🧪 Simulador de Consultas y Comandos LoRa**.
* `GET /docs`: Documentación interactiva OpenAPI / Swagger UI.

### API JSON REST
* `GET /health`: Estado de salud del socket Meshtastic, hilo lector `_rxThread`, modelo NPU, caché de clima y estadísticas de `local_kb.sqlite`.
* `GET /kb`: Lista todas las fichas locales o busca mediante FTS5 (`GET /kb?q=chiva&categoria=transporte`).
* `GET /kb/{item_id}`: Obtiene el detalle JSON de una ficha por su ID.
* `POST /kb`: Crea o actualiza una ficha local (`{"categoria": "directorio", "titulo": "...", "respuesta": "..."}`).
* `PUT /kb/{item_id}`: Actualiza una ficha existente por ID.
* `DELETE /kb/{item_id}`: Elimina una ficha local por ID.
* `POST /simulate`: Simula cualquier comando o pregunta (`{"text": "¿A qué hora sale la chiva?"}`) devolviendo los paquetes LoRa y su tamaño en bytes sin transmitir por RF.
* `POST /send`: Transmite un mensaje o alerta externa directamente al canal LoRa `PlayaRica`.
* `GET /nodes`: Lista JSON de todos los nodos de la malla con GPS, batería, SNR y distancia.
* `GET /messages`: Últimos 50 mensajes procesados en el *ring buffer* en memoria.

---

## 5. Configuración, Despliegue en Docker y Pruebas

### Variables de Entorno (`.env`)
```ini
MESHTASTIC_HOST=192.168.68.68
MESHTASTIC_PORT=
MESHTASTIC_CHANNEL=PlayaRica
OLLAMA_URL=http://localhost:8000/api/generate
OLLAMA_MODEL=qwen3:1.7b
DEFAULT_LOCATION=San Roque Playa Rica
COOLDOWN_SECONDS=5
MAX_CHARS=620
MAX_PARTS=3
PACKET_PAUSE_SECONDS=4.0
API_HOST=0.0.0.0
API_PORT=8002
```

### Comandos de Operación con Docker Compose
```bash
# 1. Construir y levantar el contenedor en segundo plano (con volumen persistente ./data:/app/data)
docker compose up -d --build

# 2. Ejecutar la suite completa de pruebas automatizadas (12 tests) dentro del contenedor
docker exec meshtastic-ai-bot pytest -v

# 3. Verificar estado de salud del bot y de la Base de Conocimiento Local
curl -s http://localhost:8002/health | python3 -m json.tool

# 4. Probar una consulta de conocimiento local por API sin gastar aire LoRa
curl -s -X POST http://localhost:8002/simulate \
  -H "Content-Type: application/json" \
  -d '{"text": "¿A qué hora sale la chiva para San Roque?"}' | python3 -m json.tool
```
