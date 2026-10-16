# Meshtastic AI Bot v3.0 (Canal PlayaRica)

Bot inteligente y autónomo para redes LoRa **Meshtastic**, con arquitectura modular en Python (`app/`), servidor HTTP **FastAPI** integrado (puerto `8002`), cola de trabajo asíncrona (*Worker Queue*), caché meteorológico en memoria y aceleración local con la NPU **Hailo-10H** en una Raspberry Pi 5.

---

## Arquitectura Modular (`v3.0`)

```text
meshtastic-bot/
├── app/
│   ├── __init__.py          # Versión del paquete (3.0.0)
│   ├── config.py            # Variables de entorno tipadas, constantes LoRa y KNOWN_LOCATIONS
│   ├── formatter.py         # clean_text, trim_to_complete_sentences y split_into_packets (<= 228 bytes UTF-8)
│   ├── mesh_service.py      # Cliente Meshtastic TCP/Serial, Worker Queue no bloqueante e historial en memoria
│   ├── router.py            # Despachador de comandos (!clima, !gps, !telem, !wiki) y lenguaje natural
│   ├── api.py               # Servidor FastAPI en puerto 8002 (/health, /nodes, /messages, /simulate, /send)
│   └── services/
│       ├── llm.py           # Cliente Hailo-Ollama (qwen3:1.7b) y lectura rural del clima
│       ├── weather.py       # Geocodificación (Nominatim/Open-Meteo), pronóstico extendido y caché TTL (15 min)
│       ├── telemetry.py     # Haversine, rumbos cardinales, GPS, telemetría y exportación JSON (/nodes)
│       └── search.py        # Wikipedia API y DuckDuckGo HTML Lite (100% sin API keys)
├── tests/
│   ├── test_formatter.py    # Tests unitarios de límite LoRa (<= 228 bytes UTF-8) y oraciones completas
│   └── test_router_and_api.py # Tests de geocodificación, caché TTL, síntesis rural y endpoints FastAPI
├── bot.py                   # Punto de entrada principal (levanta FastAPI + bucle Meshtastic con lifespan)
├── Dockerfile               # Python 3.12-slim con HEALTHCHECK nativo contra http://localhost:8002/health
├── docker-compose.yml       # Servicio meshtastic-ai-bot (network_mode: host)
├── requirements.txt
└── .env.example
```

---

## Características Principales

* **Clima, Pronóstico Extendido y Lectura Rural con IA (3 paquetes + Caché TTL 15 min):**
  * **Paquete `[1/3]` (Estado actual + próximas horas):** Condición del cielo en español, temperatura, sensación térmica (`ST`), mínima/máxima del día, humedad, viento con dirección cardinal, índice UV, probabilidad/acumulado de lluvia (`mm`) y evolución por horas (`Próx`).
  * **Paquete `[2/3]` (Jornadas del día):** Resumen climático detallado para **`Mañana` (06-12h)**, **`Tarde` (12-18h)**, **`Noche` (18-24h)** y **`Mañ.sig`** (día siguiente).
  * **Paquete `[3/3]` (`🌾 Campo:` Lectura IA para trabajadores del campo):** Los datos meteorológicos se pasan por el LLM local (`qwen3:1.7b`) para entregar una explicación en lenguaje cotidiano sin tecnicismos (si hará frío, calor o bochorno, en qué momento lloverá suave o caerá aguacero, y recomendaciones prácticas para labores agrícolas/ganaderas).
  * **Caché inteligente en memoria (TTL 15 min):** Si varios usuarios consultan la misma ubicación dentro de una ventana de 15 minutos, el bot responde en `<5ms` sin volver a consultar Internet ni ocupar la NPU Hailo.
* **Cola de Trabajo Asíncrona (*Worker Queue*):** El callback `on_receive` filtra y encola paquetes en `<1ms` sin bloquear jamás el hilo lector TCP (`_rxThread`) de la librería `meshtastic`.
* **Servidor HTTP FastAPI Integrado (Puerto `8002`):**
  * `GET /health`: Estado del socket TCP Meshtastic, hilo lector, cola de mensajes y caché (usado por el `HEALTHCHECK` de Docker).
  * `GET /nodes`: Lista JSON en tiempo real de todos los nodos de la malla con batería, voltaje, SNR, GPS y distancia Haversine.
  * `GET /messages`: Últimos 50 mensajes recibidos y respuestas enviadas guardados en un *ring buffer* en memoria (sin desgaste de tarjeta SD/SSD).
  * `POST /simulate`: Simulador *dry-run* para probar cualquier comando o frase (`{"text": "!clima San Roque Antioquia Playa Rica"}`) y verificar los paquetes y bytes generados **sin gastar airtime de radio LoRa**.
  * `POST /send`: Webhook para transmitir alertas o mensajes directamente al canal `PlayaRica`.
  * `DELETE /cache/weather`: Limpieza manual del caché meteorológico.
  * `GET /docs`: Interfaz interactiva Swagger UI en `http://<IP_RPI>:8002/docs`.

---

## Comandos Disponibles en la Radio LoRa

| Comando / Frase | Acción | Ejemplo de Respuesta |
|---|---|---|
| `!ayuda` | Lista los comandos disponibles | `🤖 Comandos: !clima [lugar/nodo], !pronostico [lugar], !gps [nodo/lista], !telem [nodo/red], !wiki [tema], !buscar [texto], !nodos, !hora, o pregúntame.` |
| `!clima` o `!clima <lugar>` | Clima actual + próximas horas + Mañana/Tarde/Noche + Lectura IA para el campo (3 paquetes) | `[1/3] 🌦 Playa Rica (San Roque): Nublado 24°C (ST:28°C) Min:19°/Max:27°C Hum:78% Viento:7km/h SE UV:4 Lluvia:93%(18mm) \| Próx: 11h:24°C(63%) 13h:27°C(84%) 15h:24°C(86%) 17h:21°C(78%).`<br>`[2/3] 📅 Mañana: Llovizna leve 19-24°C (85% lluvia, 2mm) \| Tarde: Llovizna leve 21-27°C (87% lluvia, 1mm) \| Noche: Tormenta 20-21°C (82% lluvia, 7mm) \| Mañ.sig: Aguacero 18-26°C (99%).`<br>`[3/3] 🌾 Campo: Hoy será caluroso, fresco y lluvioso. Tenga poncho o carpa para protegerse del aguacero. Prepare cosecha y herramientas antes de la tormenta.` |
| `!clima <nodo>` / `!clima local` | Pronóstico extendido + lectura de campo usando coordenadas GPS de un nodo | `[1/3] 🌦 Nodo SRF4: ...` / `[2/3] 📅 Mañana: ...` / `[3/3] 🌾 Campo: ...` |
| `!gps` o `!ubicacion` | GPS del nodo local conectado | `🤖 📍 GPS SRF4 (!49b70f54): 6.39751,-75.00602 Alt:1325m (GPS interno) maps.google.com/?q=6.39751,-75.00602.` |
| `!gps lista` | Nodos con GPS en la malla y distancias | `🤖 📍 Nodos con posición GPS (3): SRF4 (Local), SRF2 (3.1km S), MFC1 (2.9km N).` |
| `!gps <nodo>` | GPS de un nodo específico por nombre o ID | `🤖 📍 GPS SRF2 (!1d64df18): 6.37010,-74.99940 Alt:1400m (Manual) Dist:3.1km al S maps.google.com/?q=6.37010,-74.99940.` |
| `!telem` o `!bateria` | Telemetría del nodo local | `🤖 📊 Telem SRF4: Bat:Externa (4.31V) \| Canal:5.4% \| AirTX:2.6% \| Uptime:22h 42m.` |
| `!telem <nodo>` | Telemetría de un nodo remoto | `🤖 📊 Telem MFC1 (!6e7f72a3): Bat:84% (4.01V) \| Canal:6.9% \| AirTX:0.3% \| Uptime:411d 21h.` |
| `!telem red` o `!nodos` | Resumen de telemetría y nodos de la red | `🤖 📊 Red LoRa: 46 nodos (5 activos hoy). Con GPS: 4. Con telemetría: 3. Canal prom: 5.6%. Canal: PlayaRica.` |
| `!wiki <tema>` | Resumen sintetizado de Wikipedia | `🤖 LoRa (Long Range) es una tecnología de modulación por radio de bajo consumo...` |
| `!buscar <texto>` | Búsqueda web en vivo (DuckDuckGo) | `🤖 Según los últimos reportes...` |
| `!hora` | Hora local del servidor | `🤖 Hora local: 15:31:56 (16/10).` |

---

## Despliegue y Pruebas con Docker Compose

```bash
# Construir e iniciar el contenedor
docker compose up -d --build

# Ejecutar suite de tests unitarios (pytest)
docker exec meshtastic-ai-bot pytest -v

# Consultar salud y simular comando sin gastar radio LoRa
curl -s http://localhost:8002/health | python3 -m json.tool
curl -s -X POST http://localhost:8002/simulate \
  -H "Content-Type: application/json" \
  -d '{"text": "!clima San Roque Antioquia Playa Rica"}' | python3 -m json.tool
```
