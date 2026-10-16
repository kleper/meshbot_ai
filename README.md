# Meshtastic AI Bot v2.4 (Canal PlayaRica)

Bot inteligente y autónomo para redes LoRa **Meshtastic**, desplegado en contenedores **Docker** y acelerado localmente con la NPU **Hailo-10H** en una Raspberry Pi 5.

---

## Características Principales

* **Clima, Pronóstico Extendido y Lectura Rural con IA (3 paquetes):** Integración multicapa con **Open-Meteo**, **Nominatim OpenStreetMap**, **wttr.in** y **Hailo-Ollama (`qwen2.5:1.5b`)**:
  * **Paquete `[1/3]` (Estado actual + próximas horas):** Condición del cielo en español, temperatura, sensación térmica (`ST`), mínima/máxima del día, humedad, viento con dirección cardinal, índice UV, probabilidad/acumulado de lluvia (`mm`) y evolución por horas (`Próx`).
  * **Paquete `[2/3]` (Jornadas del día):** Resumen climático detallado para **`Mañana` (06-12h)**, **`Tarde` (12-18h)**, **`Noche` (18-24h)** y **`Mañ.sig`** (día siguiente).
  * **Paquete `[3/3]` (`🌾 Campo:` Lectura IA para trabajadores del campo):** Los datos meteorológicos se pasan por el LLM local (`qwen2.5:1.5b`) para entregar una explicación en lenguaje cotidiano sin tecnicismos (si hará frío, calor o bochorno, en qué momento lloverá suave o caerá aguacero, y recomendaciones prácticas para labores agrícolas/ganaderas).
  * **Geocodificación rural/urbana y por nodo GPS:** Soporta corregimientos y veredas (ej. `San Roque Antioquia Playa Rica`), municipios y coordenadas GPS en vivo de cualquier nodo de la malla (`!clima local`, `!clima SRF2`).
* **Posicionamiento GPS en vivo:** Coordenadas de latitud y longitud, altitud sobre el nivel del mar, satélites visibles y enlace directo a Google Maps del nodo local conectado y de nodos en la malla.
* **Geolocalización y rumbos de la malla:** Cálculo de distancia ortodrómica geodésica (**fórmula de Haversine** en km/m) y rumbo cardinal relativo (N, S, SE, etc.) respecto al nodo local.
* **Telemetría de hardware y red:** Monitoreo en tiempo real de batería (%, voltaje), tiempo activo (*uptime*), utilización de canal LoRa (%), tiempo de transmisión al aire (*AirTX* %) y sensores ambientales.
* **Inferencia Local Acelerada con Hailo-10H:** Respuestas conversacionales rápidas y privadas vía NPU PCIe (`qwen2.5:1.5b` en `hailo-ollama`), con inyección contextual de datos de hardware.
* **Protección Estricta de MTU LoRa:** Garantiza que ningún paquete supere el límite de carga de Meshtastic (`DATA_PAYLOAD_LEN = 233` bytes), limitando cada trama a `<= 228` bytes en UTF-8.
* **Multiparte Inteligente ([1/3], [2/3], [3/3]):** División automática en hasta 3 partes numeradas con pausa entre paquetes si la respuesta supera los 228 bytes en UTF-8 (hasta 620 caracteres).
* **Recorte de Oraciones Completas:** Asegura que ninguna idea quede cortada a la mitad y siempre concluya con signo de puntuación.
* **Monitoreo de Salud TCP y Reconexión Automática:** Detecta caídas de socket (`ConnectionResetError` / `_rxThread` inactivo) o eventos `meshtastic.connection.lost` y reconecta automáticamente.

---

## Comandos Disponibles en la Radio LoRa

| Comando / Frase | Acción | Ejemplo de Respuesta |
|---|---|---|
| `!ayuda` | Lista los comandos disponibles | `🤖 Comandos: !clima [lugar/nodo], !pronostico [lugar], !gps [nodo/lista], !telem [nodo/red], !wiki [tema], !buscar [texto], !nodos, !hora, o pregúntame.` |
| `!clima` o `!clima <lugar>` | Clima actual + próximas horas + Mañana/Tarde/Noche + Lectura IA para el campo (3 paquetes) | `[1/3] 🌦 Playa Rica (San Roque): Nublado 24°C (ST:27°C) Min:19°/Max:27°C Hum:79% Viento:6km/h SE UV:4 Lluvia:96%(18mm) \| Próx: 11h:24°C(69%) 13h:27°C(88%) 15h:24°C(82%) 17h:21°C(79%).`<br>`[2/3] 📅 Mañana: Llovizna leve 19-24°C (79% lluvia, 2mm) \| Tarde: Llovizna leve 21-27°C (68% lluvia, 1mm) \| Noche: Tormenta 20-21°C (82% lluvia, 7mm) \| Mañ.sig: Aguacero 18-26°C (98%).`<br>`[3/3] 🌾 Campo: Hoy hará calor bochornoso al mediodía y fresco en la noche, con lloviznas en el día y aguacero fuerte en la noche. Lleve poncho y resguarde animales y herramientas temprano.` |
| `!clima <nodo>` / `!clima local` | Pronóstico extendido + lectura de campo usando coordenadas GPS de un nodo | `[1/3] 🌦 Nodo SRF4: ...` / `[2/3] 📅 Mañana: ...` / `[3/3] 🌾 Campo: ...` |
| `!gps` o `!ubicacion` | GPS del nodo local conectado | `🤖 📍 GPS SRF4 (!49b70f54): 6.39751,-75.00602 Alt:1325m (GPS interno) maps.google.com/?q=6.39751,-75.00602.` |
| `!gps lista` | Nodos con GPS en la malla y distancias | `🤖 📍 Nodos con posición GPS (3): SRF4 (Local), SRF2 (3.1km S), MFC1 (2.9km N).` |
| `!gps <nodo>` | GPS de un nodo específico por nombre o ID | `🤖 📍 GPS SRF2 (!1d64df18): 6.37010,-74.99940 Alt:1400m (Manual) Dist:3.1km al S maps.google.com/?q=6.37010,-74.99940.` |
| `!gps yo` | GPS del nodo que envió el mensaje | `🤖 📍 GPS SRF2 (!1d64df18): 6.37010,-74.99940 Alt:1400m (Manual) Dist:3.1km al S maps.google.com/?q=6.37010,-74.99940.` |
| `!telem` o `!bateria` | Telemetría del nodo local | `🤖 📊 Telem SRF4: Bat:Externa (4.31V) \| Canal:5.4% \| AirTX:2.6% \| Uptime:22h 42m.` |
| `!telem <nodo>` | Telemetría de un nodo remoto | `🤖 📊 Telem MFC1 (!6e7f72a3): Bat:84% (4.01V) \| Canal:6.9% \| AirTX:0.3% \| Uptime:411d 21h.` |
| `!telem red` o `!nodos` | Resumen de telemetría y nodos de la red | `🤖 📊 Red LoRa: 46 nodos (5 activos hoy). Con GPS: 4. Con telemetría: 3. Canal prom: 5.6%. Canal: PlayaRica.` |
| `!wiki <tema>` | Resumen sintetizado de Wikipedia | `🤖 LoRa (Long Range) es una tecnología de modulación por radio de bajo consumo...` |
| `!buscar <texto>` | Búsqueda web en vivo (DuckDuckGo) | `🤖 Según los últimos reportes...` |
| `!hora` | Hora local del servidor | `🤖 Hora local: 15:31:56 (12/10)` |
| *Lenguaje Natural:* `"cómo estará el clima en San Roque Antioquia Playa Rica"`, `"dame la ubicación de GPS"`, `"cuánta batería tienes"` | Detección directa de intención | Respuesta instantánea y precisa. |
| *Preguntas libres a la IA* | Consulta a Hailo-Ollama con contexto de hardware | `🤖 [Respuesta concisa <= 300 caracteres con punto final]` |

---

## Requisitos y Arquitectura

* **Docker & Docker Compose** (versión 2.20+ recomendada).
* **Nodo Meshtastic:** Conexión TCP (ej: Heltec V3/V4 o T-Beam por WiFi) o puerto serial (`/dev/ttyUSB0` o `/dev/ttyACM0`).
* **Hailo-Ollama / Ollama:** Endpoint HTTP disponible en `http://localhost:8000/api/generate` (o en la red).
* **Modo de red:** `network_mode: host` para comunicarse sin NAT con el hardware local y la radio Meshtastic.

---

## Despliegue con Docker Compose

### 1. Clonar y configurar variables de entorno
```bash
cp .env.example .env
nano .env
```

Ajusta los valores principales en `.env`:
```ini
MESHTASTIC_HOST=192.168.68.68
MESHTASTIC_CHANNEL=PlayaRica
OLLAMA_URL=http://localhost:8000/api/generate
OLLAMA_MODEL=qwen2.5:1.5b
DEFAULT_LOCATION=San Roque Playa Rica
COOLDOWN_SECONDS=5
MAX_CHARS=620
MAX_PARTS=3
TZ=America/Bogota
```

### 2. Construir e iniciar el contenedor
```bash
docker compose up -d --build
```

### 3. Ver logs en tiempo real
```bash
docker compose logs -f
```

### 4. Detener o reiniciar
```bash
docker compose restart
docker compose down
```
