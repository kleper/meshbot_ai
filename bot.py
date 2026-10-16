#!/usr/bin/env python3
"""
Meshtastic AI Bot v3.0 (Canal PlayaRica) - Punto de entrada principal.
Arquitectura modular en `app/` con:
- Servidor HTTP FastAPI integrado (puerto 8002) para /health, /nodes, /messages, /simulate y /send
- Cola de trabajo asíncrona (Worker Queue) para no bloquear el hilo lector TCP (_rxThread)
- Clima extendido (Open-Meteo + Nominatim) + lectura rural con LLM (Hailo-Ollama) y caché TTL (15 min)
"""

import argparse
import logging
import uvicorn

from app.api import create_app
from app.config import (
    API_HOST,
    API_PORT,
    COOLDOWN_SECONDS,
    DEFAULT_CHANNEL_NAME,
    DEFAULT_HOST,
    DEFAULT_LOCATION,
    DEFAULT_MAX_PARTS,
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_URL,
    DEFAULT_PORT,
    KNOWN_LOCATIONS,
    MAX_CHARS,
    MAX_PACKET_BYTES,
    PACKET_SEP,
    PREFIX,
    WMO_WEATHER_CODES,
)
from app.formatter import clean_text, format_output, split_into_packets, trim_to_complete_sentences
from app.mesh_service import MeshtasticAIBot
from app.services.search import fetch_wikipedia_summary, search_duckduckgo
from app.services.telemetry import bearing_to_cardinal, format_uptime, haversine_km
from app.services.weather import (
    _build_rural_weather_brief,
    _summarize_period,
    clean_location_query,
    clear_weather_cache,
    fetch_weather,
    fetch_weather_bundle,
    fetch_weather_by_coords,
    geocode_location,
    wind_deg_to_cardinal,
    wmo_to_spanish,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("MeshtasticBot")


def main():
    parser = argparse.ArgumentParser(
        description="Meshtastic AI Bot v3.0 (Arquitectura Modular + FastAPI + Worker Queue + Clima Rural LLM)"
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="IP del nodo Meshtastic (default: 192.168.68.68)")
    parser.add_argument("--port", default=DEFAULT_PORT, help="Puerto serial alternativo (/dev/tty...)")
    parser.add_argument("--ollama", default=DEFAULT_OLLAMA_URL, help="URL de Hailo-Ollama")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Modelo LLM a usar")
    parser.add_argument("--channel", default=DEFAULT_CHANNEL_NAME, help="Nombre del canal objetivo")
    parser.add_argument("--max-chars", type=int, default=MAX_CHARS, help="Límite máximo de caracteres (default: 620)")
    parser.add_argument("--max-parts", type=int, default=DEFAULT_MAX_PARTS, help="Máximo número de paquetes por respuesta (default: 3)")
    parser.add_argument("--cooldown", type=int, default=COOLDOWN_SECONDS, help="Segundos de enfriamiento entre mensajes")
    parser.add_argument("--api-host", default=API_HOST, help="Host para el servidor FastAPI (default: 0.0.0.0)")
    parser.add_argument("--api-port", type=int, default=API_PORT, help="Puerto para el servidor FastAPI (default: 8002)")
    parser.add_argument("--no-api", action="store_true", help="Ejecutar solo el bucle LoRa sin levantar FastAPI")
    args = parser.parse_args()

    bot = MeshtasticAIBot(
        host=args.host,
        serial_port=args.port,
        ollama_url=args.ollama,
        model=args.model,
        channel_name=args.channel,
        max_chars=args.max_chars,
        max_parts=args.max_parts,
        cooldown_seconds=args.cooldown,
    )

    if args.no_api:
        bot.run()
    else:
        app = create_app(bot, start_mesh_on_boot=True)
        uvicorn.run(app, host=args.api_host, port=args.api_port, log_level="info")


if __name__ == "__main__":
    main()
