#!/usr/bin/env python3
"""
Meshtastic AI Bot v2.4 (Canal PlayaRica)
Bot inteligente para redes LoRa Meshtastic con:
- Ubicación y coordenadas GPS en vivo del nodo local y nodos de la malla
- Cálculo de distancias geodésicas (Haversine) y rumbos cardinales (N, SE, etc.)
- Telemetría de hardware (batería, voltaje, uptime, utilización de canal LoRa, sensores)
- Telemetría global y estadísticas agregadas de la red de malla
- Clima y pronóstico extendido (Open-Meteo + Nominatim + wttr.in, 100% sin API key):
  * Condición actual en español, temperatura, sensación térmica, mín/máx, humedad, viento, UV y lluvia (%/mm)
  * Pronóstico de las próximas horas (+1h a +7h)
  * Pronóstico detallado para Mañana, Tarde, Noche y Día siguiente
  * Soporte para corregimientos/veredas (ej. San Roque Antioquia Playa Rica) y nodos GPS de la malla
- Respuestas de ideas completas (terminadas en punto final, sin oraciones cortadas a la mitad)
- Protección estricta contra "Data payload too big" (MTU LoRa <= 228 bytes en UTF-8)
- Multiparte inteligente ([1/2], [2/2]) para respuestas de hasta 420 caracteres
- Búsqueda en Internet en tiempo real (DuckDuckGo Lite, 100% sin API key)
- Consulta enciclopédica (Wikipedia API abierta, 100% sin API key)
- Inferencia local con NPU Hailo-10H (Hailo Ollama: qwen2.5:1.5b)
- Control de airtime, cooldown anti-spam y reconexión TCP automática ante caídas del socket
"""

import os
import sys
import time
import logging
import argparse
import re
import datetime
import math
import unicodedata
import requests
from pubsub import pub
import meshtastic
import meshtastic.tcp_interface
import meshtastic.serial_interface

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("MeshtasticBot")

# Configuración por defecto
DEFAULT_HOST = os.getenv("MESHTASTIC_HOST", "192.168.68.68")
DEFAULT_PORT = os.getenv("MESHTASTIC_PORT", "")
DEFAULT_OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:8000/api/generate")
DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b")
DEFAULT_CHANNEL_NAME = os.getenv("MESHTASTIC_CHANNEL", "PlayaRica")
DEFAULT_LOCATION = os.getenv("DEFAULT_LOCATION", "San Roque Playa Rica")
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "5"))
MAX_CHARS = int(os.getenv("MAX_CHARS", "620"))
MAX_PACKET_BYTES = 228  # Límite seguro inferior al MTU de Meshtastic (DATA_PAYLOAD_LEN = 233)
DEFAULT_MAX_PARTS = int(os.getenv("MAX_PARTS", "3"))
PACKET_SEP = "||PKT||"
PREFIX = "🤖 "

SYSTEM_PROMPT = (
    "Eres el bot de IA del canal PlayaRica en la red LoRa Meshtastic. "
    "Responde siempre en español de forma directa, útil, amigable y concisa. "
    "REGLAS ESTRICTAS: "
    "1. Expresa 1 o 2 oraciones completas de máximo 300 caracteres. "
    "2. Concluye SIEMPRE tu respuesta con punto final. NUNCA dejes oraciones incompletas ni a medias. "
    "3. No uses formato markdown (sin asteriscos ni almohadillas), sin comillas dobles y sin rodeos."
)

# =====================================================================
# DICCIONARIOS Y CACHÉ METEOROLÓGICO (OPEN-METEO + NOMINATIM + WTTR.IN)
# =====================================================================

WMO_WEATHER_CODES = {
    0: "Despejado",
    1: "Mayormente despejado",
    2: "Parcial nublado",
    3: "Nublado",
    45: "Niebla",
    48: "Niebla densa",
    51: "Llovizna leve",
    53: "Llovizna",
    55: "Llovizna fuerte",
    56: "Llovizna helada",
    57: "Llovizna helada",
    61: "Lluvia leve",
    63: "Lluvia mod.",
    65: "Lluvia fuerte",
    66: "Lluvia helada",
    67: "Lluvia helada",
    71: "Nevada leve",
    73: "Nevada mod.",
    75: "Nevada fuerte",
    77: "Granizo",
    80: "Chubascos",
    81: "Aguacero",
    82: "Aguacero fuerte",
    85: "Chubasco nieve",
    86: "Chubasco nieve",
    95: "Tormenta",
    96: "Tormenta/granizo",
    99: "Tormenta severa",
}

# Coordenadas precargadas de referencia para respuesta instantánea (0ms) en zonas frecuentes de la malla
KNOWN_LOCATIONS = {
    "playa rica": (6.4062, -75.0161, "Playa Rica (San Roque)"),
    "san roque playa rica": (6.4062, -75.0161, "Playa Rica (San Roque)"),
    "playa rica san roque": (6.4062, -75.0161, "Playa Rica (San Roque)"),
    "san roque antioquia playa rica": (6.4062, -75.0161, "Playa Rica (San Roque)"),
    "playa rica san roque antioquia": (6.4062, -75.0161, "Playa Rica (San Roque)"),
    "san roque": (6.4851, -75.0195, "San Roque, Ant."),
    "san roque antioquia": (6.4851, -75.0195, "San Roque, Ant."),
    "cisneros": (6.5382, -75.0871, "Cisneros, Ant."),
    "cisneros antioquia": (6.5382, -75.0871, "Cisneros, Ant."),
    "santo domingo": (6.4728, -75.1653, "Sto. Domingo, Ant."),
    "santo domingo antioquia": (6.4728, -75.1653, "Sto. Domingo, Ant."),
    "maceo": (6.5519, -74.7872, "Maceo, Ant."),
    "caracoli": (6.4092, -74.7567, "Caracolí, Ant."),
    "puerto berrio": (6.4906, -74.4025, "Pto. Berrío, Ant."),
    "yolombo": (6.5972, -75.0117, "Yolombó, Ant."),
    "alejandria": (6.3769, -75.1411, "Alejandría, Ant."),
    "concepcion": (6.3944, -75.2581, "Concepción, Ant."),
    "guatape": (6.2325, -75.1586, "Guatapé, Ant."),
    "el penol": (6.2186, -75.2431, "El Peñol, Ant."),
    "penol": (6.2186, -75.2431, "El Peñol, Ant."),
    "marinilla": (6.1736, -75.3361, "Marinilla, Ant."),
    "rionegro": (6.1553, -75.3736, "Rionegro, Ant."),
    "medellin": (6.2442, -75.5812, "Medellín"),
    "medellin antioquia": (6.2442, -75.5812, "Medellín"),
    "bello": (6.3373, -75.5579, "Bello"),
    "envigado": (6.1696, -75.5875, "Envigado"),
    "itagui": (6.1726, -75.6096, "Itagüí"),
    "sabaneta": (6.1515, -75.6166, "Sabaneta"),
    "bogota": (4.7110, -74.0721, "Bogotá"),
    "cali": (3.4516, -76.5320, "Cali"),
    "barranquilla": (10.9685, -74.7813, "Barranquilla"),
    "cartagena": (10.3910, -75.4794, "Cartagena"),
    "bucaramanga": (7.1193, -73.1227, "Bucaramanga"),
    "pereira": (4.8133, -75.6961, "Pereira"),
    "manizales": (5.0689, -75.5174, "Manizales"),
}

_GEOCODE_CACHE: dict[str, tuple[float, float, str]] = {}


def _normalize_key(text: str) -> str:
    """Normaliza texto en minúsculas sin tildes ni signos para búsqueda en caché."""
    t = unicodedata.normalize("NFD", text.lower().strip())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return " ".join(t.split())


def clean_location_query(raw_loc: str) -> str:
    """Limpia palabras de relleno temporales, conectores o signos de una consulta de ubicación."""
    q = raw_loc.strip().strip("?!.,;:")
    # 1. Eliminar expresiones temporales en cualquier posición de la frase
    q = re.sub(
        r"\b(?:por\s+la\s+manana|por\s+la\s+mañana|por\s+la\s+tarde|por\s+la\s+noche|"
        r"en\s+la\s+manana|en\s+la\s+mañana|en\s+la\s+tarde|en\s+la\s+noche|"
        r"esta\s+manana|esta\s+mañana|esta\s+tarde|esta\s+noche|"
        r"para\s+hoy|para\s+manana|para\s+mañana|el\s+dia\s+de\s+hoy|el\s+día\s+de\s+hoy|"
        r"en\s+este\s+momento|por\s+favor|hoy|ahora|manana|mañana|actual)\b",
        " ",
        q,
        flags=re.IGNORECASE,
    )
    q = " ".join(q.split()).strip("?!.,;:")
    # 2. Si contiene conectores como "... en <lugar>" o "... de <lugar>", extraer el lugar
    m_conn = re.search(
        r"(?:clima\s+en|clima\s+de|clima\s+para|pronostico\s+en|pronóstico\s+en|"
        r"pronostico\s+de|pronóstico\s+de|tiempo\s+en|tiempo\s+de|temperatura\s+en|"
        r"llover\s+en|llueve\s+en|\ben|\bde|\bpara)\s+(.+)$",
        q,
        flags=re.IGNORECASE,
    )
    if m_conn:
        candidate = m_conn.group(1).strip()
        # Evitar cortar nombres propios como "San Juan de Urabá" si la consulta ya empezaba por el lugar
        if q.lower().startswith(("el ", "la ", "como ", "cómo ", "que ", "qué ", "dame ", "cual ", "cuál ", "va ", "llueve", "clima", "pronostico", "pronóstico", "tiempo", "temperatura", "en ", "de ", "para ")):
            q = candidate
    return " ".join(q.split()).strip("?!.,;:")


def wind_deg_to_cardinal(deg: float | None) -> str:
    """Convierte grados de dirección de viento a punto cardinal."""
    if deg is None:
        return ""
    cardinals = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"]
    return cardinals[round(float(deg) / 45) % 8]


def wmo_to_spanish(code: int | None) -> str:
    """Traduce el código WMO de Open-Meteo a descripción concisa en español."""
    if code is None:
        return "Variable"
    return WMO_WEATHER_CODES.get(int(code), "Nublado")


def geocode_location(
    location: str,
    ref_lat: float = 6.4062,
    ref_lon: float = -75.0161,
) -> tuple[float | None, float | None, str]:
    """
    Resuelve el nombre de una zona/corregimiento/ciudad a (lat, lon, nombre_corto)
    usando: 1) Diccionario local + caché, 2) Nominatim OSM, 3) Open-Meteo Geocoding.
    Prioriza coincidencias en Antioquia/Colombia cuando hay ambigüedad.
    """
    clean_q = clean_location_query(location) or DEFAULT_LOCATION
    norm_key = _normalize_key(clean_q)

    if norm_key in KNOWN_LOCATIONS:
        return KNOWN_LOCATIONS[norm_key]
    if norm_key in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[norm_key]

    # 1. Intentar Nominatim OpenStreetMap (excelente para corregimientos/veredas como Playa Rica)
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": clean_q,
                "format": "json",
                "limit": 5,
                "accept-language": "es",
            },
            headers={"User-Agent": "MeshtasticBot-PlayaRica/2.4"},
            timeout=6,
        )
        if r.status_code == 200:
            items = r.json()
            if items:
                # Si hay resultados en Colombia/Antioquia, priorizar el más cercano al nodo local
                best = None
                best_score = -1e9
                for it in items:
                    disp = it.get("display_name", "")
                    lat = float(it["lat"])
                    lon = float(it["lon"])
                    disp_norm = _normalize_key(disp)
                    score = 0.0
                    if "colombia" in disp_norm:
                        score += 500.0
                    if "antioquia" in disp_norm:
                        score += 300.0
                    # Penalizar lejanía leve respecto a la referencia local
                    dist = haversine_km(ref_lat, ref_lon, lat, lon)
                    score -= min(dist * 0.05, 250.0)
                    if score > best_score:
                        best_score = score
                        best = (lat, lon, disp)
                if best:
                    lat, lon, disp = best
                    parts = [p.strip() for p in disp.split(",") if p.strip()]
                    short_label = parts[0]
                    if len(parts) > 1 and parts[1].lower() not in parts[0].lower() and not parts[1].startswith("Comuna"):
                        short_label = f"{parts[0]} ({parts[1]})"
                    if len(short_label) > 26:
                        short_label = short_label[:26].strip()
                    res_tuple = (lat, lon, short_label)
                    _GEOCODE_CACHE[norm_key] = res_tuple
                    return res_tuple
    except Exception as e:
        logger.warning(f"Error en geocodificación Nominatim para '{clean_q}': {e}")

    # 2. Fallback: Open-Meteo Geocoding API
    queries_to_try = [clean_q]
    words = clean_q.split()
    if len(words) > 2:
        queries_to_try.append(" ".join(words[:2]))
        queries_to_try.append(" ".join(words[-2:]))

    for q_try in queries_to_try:
        try:
            r = requests.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": q_try, "count": 5, "language": "es"},
                timeout=5,
            )
            if r.status_code == 200:
                results = r.json().get("results") or []
                if results:
                    best = None
                    best_score = -1e9
                    for it in results:
                        lat = float(it["latitude"])
                        lon = float(it["longitude"])
                        country = (it.get("country") or "").lower()
                        admin1 = (it.get("admin1") or "").lower()
                        score = 0.0
                        if "colombia" in country or it.get("country_code") == "CO":
                            score += 500.0
                        if "antioquia" in admin1:
                            score += 300.0
                        dist = haversine_km(ref_lat, ref_lon, lat, lon)
                        score -= min(dist * 0.05, 250.0)
                        if score > best_score:
                            best_score = score
                            best = it
                    if best:
                        lat = float(best["latitude"])
                        lon = float(best["longitude"])
                        name = best.get("name", clean_q)
                        adm = best.get("admin1", "")
                        short_label = f"{name} ({adm[:4]}.)" if adm and adm.lower() not in name.lower() else name
                        res_tuple = (lat, lon, short_label)
                        _GEOCODE_CACHE[norm_key] = res_tuple
                        return res_tuple
        except Exception as e:
            logger.warning(f"Error en Open-Meteo Geocoding para '{q_try}': {e}")

    return None, None, clean_q


def _summarize_period(
    temps: list[float],
    probs: list[int],
    mms: list[float],
    codes: list[int],
    compact: bool = False,
) -> str:
    """Genera resumen compacto de un periodo (Mañana/Tarde/Noche): condición, rango °C, % lluvia y mm."""
    if not temps:
        return "S/D"
    t_min = round(min(temps))
    t_max = round(max(temps))
    t_str = f"{t_min}°C" if t_min == t_max else f"{t_min}-{t_max}°C"
    max_prob = max(probs) if probs else 0
    sum_mm = sum(mms) if mms else 0.0
    if not codes:
        rep_code = 0
    elif max(codes) >= 80 or sum_mm >= 2.5:
        # Destacar aguaceros o tormentas si ocurren en el periodo
        rep_code = max(codes)
    elif max_prob >= 40 or sum_mm >= 0.5:
        rain_codes = [c for c in codes if c >= 50]
        rep_code = max(set(rain_codes), key=rain_codes.count) if rain_codes else max(codes)
    else:
        non_rain = [c for c in codes if c < 50] or codes
        rep_code = max(set(non_rain), key=non_rain.count)
    cond = wmo_to_spanish(rep_code)

    rain_lbl = "" if compact else " lluvia"
    if sum_mm >= 0.5:
        sep = " " if compact else ", "
        return f"{cond} {t_str} ({max_prob}%{rain_lbl}{sep}{sum_mm:.0f}mm)"
    return f"{cond} {t_str} ({max_prob}%{rain_lbl})"


def _build_rural_weather_brief(
    d_min: int,
    d_max: int,
    app_temp: int,
    uv: int,
    d_prob: int,
    d_mm: float,
    periods_data: list[tuple[str, list[float], list[int], list[float], list[int]]],
) -> str:
    """
    Construye una síntesis en lenguaje cotidiano del clima del día (frío/calor, lluvias por jornada
    y consejo práctico para labores del campo) para alimentar al LLM y garantizar precisión rural.
    """
    # 1. Sensación térmica del día y de la noche/madrugada
    if d_max >= 30 or app_temp >= 30:
        heat_str = f"hará mucho calor y bochorno fuerte al mediodía (hasta {d_max}°C)"
    elif d_max >= 25 or app_temp >= 26:
        heat_str = f"hará calor bochornoso al mediodía ({d_max}°C)"
    elif d_max >= 20:
        heat_str = f"el clima estará templado y agradable en el día ({d_max}°C)"
    else:
        heat_str = f"hará frío durante el día (máximo {d_max}°C)"

    if d_min <= 13:
        cold_str = f"con bastante frío en la madrugada y noche ({d_min}°C)"
    elif d_min <= 19:
        cold_str = f"y fresco en la noche y madrugada ({d_min}°C)"
    else:
        cold_str = f"y noche templada ({d_min}°C)"

    # 2. Análisis de lluvia por jornada (Mañana, Tarde, Noche)
    heavy_periods = []
    light_periods = []
    for p_name, _, probs, mms, codes in periods_data:
        p_prob = max(probs) if probs else 0
        p_mm = sum(mms) if mms else 0.0
        p_code = max(codes) if codes else 0
        clean_name = p_name.lower().replace("(sig)", " siguiente")
        if p_code >= 80 or p_mm >= 3.5 or (p_prob >= 80 and p_mm >= 1.5):
            heavy_periods.append(clean_name)
        elif p_prob >= 45 or p_mm >= 0.4 or p_code >= 51:
            light_periods.append(clean_name)

    if heavy_periods and light_periods:
        rain_str = (
            f"Habrá llovizna suave en {', '.join(light_periods)} "
            f"y caerá aguacero fuerte o tormenta en {', '.join(heavy_periods)}."
        )
    elif heavy_periods:
        rain_str = f"Se espera aguacero fuerte o tormenta en {', '.join(heavy_periods)}."
    elif light_periods:
        rain_str = f"Habrá lluvias o lloviznas suaves en {', '.join(light_periods)}."
    else:
        rain_str = "No se esperan lluvias importantes hoy, predominará el tiempo seco."

    # 3. Consejo práctico para trabajadores del campo
    if heavy_periods:
        advice_str = "Lleve poncho o carpa y resguarde animales, cosecha y herramientas antes del aguacero."
    elif light_periods:
        advice_str = "Lleve impermeable al trabajo de campo y proteja cargas, abonos o café de la humedad."
    elif d_max >= 28 or uv >= 7:
        advice_str = "Aproveche el tiempo seco para labores y secado, hidrátese bien y cúbrase del sol fuerte."
    else:
        advice_str = "Buen tiempo para trabajar tranquilo en el campo, siembra y mantenimiento de cultivos."

    return f"Hoy {heat_str} {cold_str}. {rain_str} {advice_str}"


def fetch_weather_bundle(lat: float, lon: float, label: str) -> tuple[str, str, str]:
    """
    Consulta Open-Meteo para (lat, lon) y devuelve (part1, part2, rural_brief):
    - part1: Clima actual + pronóstico próximas horas.
    - part2: Pronóstico detallado para Mañana, Tarde, Noche y Día siguiente.
    - rural_brief: Síntesis para trabajadores del campo lista para pasar por el LLM.
    """
    try:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": round(lat, 4),
            "longitude": round(lon, 4),
            "current": (
                "temperature_2m,relative_humidity_2m,apparent_temperature,"
                "precipitation,weather_code,wind_speed_10m,wind_direction_10m,uv_index"
            ),
            "hourly": "temperature_2m,precipitation_probability,precipitation,weather_code",
            "daily": (
                "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_sum,precipitation_probability_max,uv_index_max"
            ),
            "timezone": "America/Bogota",
            "forecast_days": 2,
        }
        r = requests.get(url, params=params, timeout=7)
        if r.status_code != 200:
            return "", "", ""
        data = r.json()
        cur = data.get("current", {})
        daily = data.get("daily", {})
        hourly = data.get("hourly", {})

        # 1. Datos actuales
        temp = round(cur.get("temperature_2m", 0))
        app_temp = round(cur.get("apparent_temperature", temp))
        hum = round(cur.get("relative_humidity_2m", 0))
        w_spd = round(cur.get("wind_speed_10m", 0))
        w_dir = wind_deg_to_cardinal(cur.get("wind_direction_10m"))
        uv = round(cur.get("uv_index", 0))
        cond_now = wmo_to_spanish(cur.get("weather_code", 0))

        # 2. Resumen diario (hoy)
        d_min = round((daily.get("temperature_2m_min") or [temp])[0])
        d_max = round((daily.get("temperature_2m_max") or [temp])[0])
        d_prob = round((daily.get("precipitation_probability_max") or [0])[0])
        d_mm = (daily.get("precipitation_sum") or [0.0])[0] or 0.0
        rain_today_str = f"{d_prob}%({d_mm:.0f}mm)" if d_mm >= 0.5 else f"{d_prob}%"

        # 3. Pronóstico de las próximas horas (cada 2h a partir de la hora siguiente)
        h_times = hourly.get("time", [])
        h_temps = hourly.get("temperature_2m", [])
        h_probs = hourly.get("precipitation_probability", [])
        h_mms = hourly.get("precipitation", [])
        h_codes = hourly.get("weather_code", [])

        now_dt = datetime.datetime.now()
        cur_hour_idx = now_dt.hour
        cur_iso_prefix = now_dt.strftime("%Y-%m-%dT%H:")
        for idx, ts in enumerate(h_times):
            if ts.startswith(cur_iso_prefix):
                cur_hour_idx = idx
                break

        next_slots = []
        for offset in (1, 3, 5, 7):
            idx = cur_hour_idx + offset
            if idx < len(h_times):
                hh = h_times[idx].split("T")[-1][:2]
                t_val = round(h_temps[idx])
                p_val = round(h_probs[idx]) if idx < len(h_probs) and h_probs[idx] is not None else 0
                next_slots.append(f"{hh}h:{t_val}°C({p_val}%)")

        max_body_bytes = MAX_PACKET_BYTES - len(f"{PREFIX}[1/3] ".encode("utf-8"))
        base_p1 = (
            f"🌦 {label}: {cond_now} {temp}°C (ST:{app_temp}°C) "
            f"Min:{d_min}°/Max:{d_max}°C Hum:{hum}% Viento:{w_spd}km/h {w_dir} "
            f"UV:{uv} Lluvia:{rain_today_str}"
        )
        prox_str = f" | Próx: {' '.join(next_slots)}" if next_slots else ""
        part1 = f"{base_p1}{prox_str}."
        if len(part1.encode("utf-8")) > max_body_bytes and len(next_slots) > 3:
            part1 = f"{base_p1} | Próx: {' '.join(next_slots[:3])}."

        # 4. Pronóstico por periodos del día (Mañana 06-12h, Tarde 12-18h, Noche 18-24h)
        hour_now = now_dt.hour
        if hour_now >= 19 and len(h_temps) >= 48:
            periods_order = [
                ("Noche", slice(18, 24)),
                ("Mañ(sig)", slice(30, 36)),
                ("Tar(sig)", slice(36, 42)),
            ]
        else:
            periods_order = [
                ("Mañana", slice(6, 12)),
                ("Tarde", slice(12, 18)),
                ("Noche", slice(18, 24)),
            ]

        periods_data = []
        for p_name, sl in periods_order:
            periods_data.append((p_name, h_temps[sl], h_probs[sl], h_mms[sl], h_codes[sl]))

        def _build_part2(compact_mode: bool, include_tomorrow: bool) -> str:
            p_list = []
            for p_name, p_t, p_p, p_m, p_c in periods_data:
                p_sum = _summarize_period(
                    p_t,
                    p_p,
                    p_m,
                    p_c,
                    compact=compact_mode,
                )
                p_list.append(f"{p_name}: {p_sum}")
            if include_tomorrow and hour_now < 19 and len(daily.get("temperature_2m_max", [])) > 1:
                tm_min = round(daily["temperature_2m_min"][1])
                tm_max = round(daily["temperature_2m_max"][1])
                tm_prob = round((daily.get("precipitation_probability_max") or [0, 0])[1] or 0)
                tm_code = (daily.get("weather_code") or [0, 0])[1]
                tm_cond = wmo_to_spanish(tm_code)
                p_list.append(f"Mañ.sig: {tm_cond} {tm_min}-{tm_max}°C ({tm_prob}%)")
            return "📅 " + " | ".join(p_list) + "."

        part2 = _build_part2(compact_mode=False, include_tomorrow=True)
        if len(part2.encode("utf-8")) > max_body_bytes:
            part2 = _build_part2(compact_mode=True, include_tomorrow=True)
        if len(part2.encode("utf-8")) > max_body_bytes:
            part2 = _build_part2(compact_mode=True, include_tomorrow=False)

        rural_brief = _build_rural_weather_brief(
            d_min=d_min,
            d_max=d_max,
            app_temp=app_temp,
            uv=uv,
            d_prob=d_prob,
            d_mm=d_mm,
            periods_data=periods_data,
        )

        return part1, part2, rural_brief

    except Exception as e:
        logger.warning(f"Error consultando Open-Meteo para {label} ({lat},{lon}): {e}")
        return "", "", ""


def fetch_weather_by_coords(lat: float, lon: float, label: str) -> str:
    """Compatibilidad directa: devuelve part1 y part2 unidos."""
    p1, p2, _ = fetch_weather_bundle(lat, lon, label)
    if p1 and p2:
        return f"{p1} {p2}"
    return ""


def fetch_weather(
    location: str = DEFAULT_LOCATION,
    ref_lat: float = 6.4062,
    ref_lon: float = -75.0161,
) -> str:
    """
    Consulta el clima actual y el pronóstico extendido (próximas horas + Mañana, Tarde y Noche).
    Usa geocodificación inteligente + Open-Meteo (100% sin API key), con fallback a wttr.in en español y métrico.
    """
    loc = clean_location_query(location) or DEFAULT_LOCATION
    lat, lon, label = geocode_location(loc, ref_lat=ref_lat, ref_lon=ref_lon)
    if lat is not None and lon is not None:
        report = fetch_weather_by_coords(lat, lon, label)
        if report:
            return report

    # Fallback a wttr.in en español y unidades métricas (?m&lang=es)
    try:
        url = f"https://wttr.in/{loc}?m&lang=es&format=%C+%t+(ST:%f)+Viento:%w+Hum:%h+Lluvia:%p"
        r = requests.get(url, headers={"User-Agent": "curl/8.0"}, timeout=6)
        if r.status_code == 200 and "Unknown" not in r.text and len(r.text.strip()) > 3:
            return f"🌦 Clima {loc}: {r.text.strip()}."
    except Exception as e:
        logger.warning(f"Error consultando fallback wttr.in: {e}")
    return ""


def fetch_wikipedia_summary(query: str) -> str:
    """Busca y extrae el extracto principal de Wikipedia en español sin API key."""
    clean_query = query.strip()
    if not clean_query:
        return ""
    try:
        # 1. Buscar el título más relevante
        search_url = "https://es.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "list": "search",
            "srsearch": clean_query,
            "format": "json",
            "utf8": 1,
        }
        r = requests.get(search_url, params=params, headers={"User-Agent": "MeshtasticBot/2.1"}, timeout=6)
        if r.status_code != 200:
            return ""
        results = r.json().get("query", {}).get("search", [])
        if not results:
            return ""
        title = results[0]["title"]

        # 2. Obtener resumen de la página
        sum_url = f"https://es.wikipedia.org/api/rest_v1/page/summary/{title}"
        r_sum = requests.get(sum_url, headers={"User-Agent": "MeshtasticBot/2.1"}, timeout=6)
        if r_sum.status_code == 200:
            extract = r_sum.json().get("extract", "")
            return extract.strip()
    except Exception as e:
        logger.warning(f"Error consultando Wikipedia: {e}")
    return ""


def search_duckduckgo(query: str) -> str:
    """Busca en DuckDuckGo HTML Lite y devuelve snippets limpios sin API key."""
    clean_query = query.strip()
    if not clean_query:
        return ""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"
        }
        r = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": clean_query},
            headers=headers,
            timeout=7,
        )
        if r.status_code == 200:
            snippets = re.findall(r'class="result__snippet[^>]*>(.*?)</a>', r.text)
            clean_snippets = [re.sub(r"<[^>]+>", "", s).strip() for s in snippets]
            if clean_snippets:
                return " ".join(clean_snippets[:2])
    except Exception as e:
        logger.warning(f"Error consultando DuckDuckGo: {e}")
    return ""


# =====================================================================
# RECORTADOR INTELIGENTE DE ORACIONES COMPLETAS
# =====================================================================

def trim_to_complete_sentences(text: str, max_allowed: int) -> str:
    """
    Recorta el texto asegurando que SIEMPRE termine en una idea/oración completa
    (con punto '.', '!' o '?') y nunca corte una frase o palabra a la mitad.
    """
    clean = text.strip()
    if not clean:
        return ""

    # Si ya cabe y termina en puntuación válida (. ! ?)
    if len(clean) <= max_allowed and clean.endswith((".", "!", "?")):
        return clean

    candidate = clean[:max_allowed].strip()

    # Buscar todos los terminadores de oración (. ! ?)
    terminators = [m.start() for m in re.finditer(r"[\.\!\?](?:\s|$)", candidate)]
    if terminators:
        last_term = terminators[-1]
        # Si hay un terminador válido y la oración tiene un largo razonable,
        # conservamos hasta ese punto y descartamos cualquier fragmento posterior incompleto
        if last_term >= 20:
            return candidate[:last_term + 1].strip()

    # Si NO hay terminadores válidos antes del corte:
    # Caso 1: Si todo el texto original cabía dentro del límite
    if len(clean) <= max_allowed:
        if clean.startswith("¿") and not clean.endswith("?"):
            return clean.rstrip(" ,;:-¡") + "?"
        return clean.rstrip(" ,;:-¿¡") + "."

    # Caso 2: El texto original superaba max_allowed y no tiene ningún punto antes de max_allowed
    last_space = candidate.rfind(" ")
    if last_space > 20:
        return candidate[:last_space].rstrip(" ,;:-¿¡") + "."

    return candidate.rstrip(" ,;:-¿¡") + "."


def split_into_packets(
    text: str,
    max_packet_bytes: int = MAX_PACKET_BYTES,
    max_parts: int = DEFAULT_MAX_PARTS,
) -> list[str]:
    """
    Divide un texto en paquetes seguros para LoRa Meshtastic (<= max_packet_bytes en UTF-8).
    Si contiene bloques separados por PACKET_SEP ('||PKT||'), asigna cada bloque a su propio paquete.
    Si cabe en 1 paquete, lo devuelve tal cual con prefijo.
    Si excede el límite y max_parts > 1, divide en oraciones completas numeradas ([1/N], [2/N]).
    """
    clean = " ".join(text.strip().split())
    if not clean:
        return []

    if clean.startswith(PREFIX):
        clean = clean[len(PREFIX):].strip()

    # 0. Si el texto trae bloques explícitos separados por PACKET_SEP (ej. Clima [1/3], [2/3], [3/3])
    if PACKET_SEP in clean:
        parts = [b.strip() for b in clean.split(PACKET_SEP) if b.strip()][:max_parts]
        total = len(parts)
        formatted = []
        for i, p in enumerate(parts, 1):
            p_clean = p.strip()
            if not p_clean.endswith((".", "!", "?")):
                p_clean = p_clean.rstrip(" ,;:-¿¡") + "."
            hdr = f"{PREFIX}[{i}/{total}] " if total > 1 else PREFIX
            pkt = f"{hdr}{p_clean}"
            while len(pkt.encode("utf-8")) > max_packet_bytes:
                last_sp = p_clean.rfind(" ")
                if last_sp == -1:
                    break
                p_clean = p_clean[:last_sp].rstrip(" ,;:-¿¡") + "."
                pkt = f"{hdr}{p_clean}"
            formatted.append(pkt)
        return formatted

    # 1. Si cabe en un solo paquete con prefijo normal
    single_msg = f"{PREFIX}{clean}"
    if len(single_msg.encode("utf-8")) <= max_packet_bytes:
        return [single_msg]

    # 2. Si se fuerza máximo 1 parte (modo paquete único estricto)
    if max_parts <= 1:
        sentences = re.split(r"(?<=[\.\!\?])\s+", clean)
        accum = ""
        for s in sentences:
            test = f"{accum} {s}".strip() if accum else s
            if len(f"{PREFIX}{test}".encode("utf-8")) <= max_packet_bytes:
                accum = test
            else:
                break
        if accum:
            return [f"{PREFIX}{accum}"]

        candidate = clean
        while len(f"{PREFIX}{candidate}".encode("utf-8")) > max_packet_bytes:
            last_space = candidate.rfind(" ")
            if last_space == -1:
                encoded = candidate.encode("utf-8")
                max_txt_bytes = max_packet_bytes - len(PREFIX.encode("utf-8")) - 1
                candidate = encoded[:max_txt_bytes].decode("utf-8", errors="ignore")
                break
            candidate = candidate[:last_space].rstrip(" ,;:-¿¡") + "."
        return [f"{PREFIX}{candidate}"]

    # 3. Multiparte (hasta max_parts)
    sentences = re.split(r"(?<=[\.\!\?])\s+", clean)
    sentences = [s.strip() for s in sentences if s.strip()]

    parts = []
    current_part = ""
    prefix_sample = f"{PREFIX}[1/{max_parts}] "
    max_text_bytes_per_part = max_packet_bytes - len(prefix_sample.encode("utf-8"))

    for s in sentences:
        test_part = f"{current_part} {s}".strip() if current_part else s
        if len(test_part.encode("utf-8")) <= max_text_bytes_per_part:
            current_part = test_part
        else:
            if current_part:
                parts.append(current_part)
                current_part = s
            else:
                words = s.split()
                sub = ""
                for w in words:
                    test_sub = f"{sub} {w}".strip() if sub else w
                    if len(test_sub.encode("utf-8")) <= max_text_bytes_per_part:
                        sub = test_sub
                    else:
                        if sub:
                            parts.append(sub.rstrip(" ,;:-¿¡") + ".")
                            sub = w
                        else:
                            encoded_w = w.encode("utf-8")[:max_text_bytes_per_part].decode("utf-8", errors="ignore")
                            parts.append(encoded_w)
                            sub = ""
                if sub:
                    current_part = sub

    if current_part:
        parts.append(current_part)

    if len(parts) > max_parts:
        parts = parts[:max_parts]

    if len(parts) == 1:
        p_clean = parts[0].strip()
        if not p_clean.endswith((".", "!", "?")):
            p_clean = p_clean.rstrip(" ,;:-¿¡") + "."
        single = f"{PREFIX}{p_clean}"
        if len(single.encode("utf-8")) <= max_packet_bytes:
            return [single]
        while len(f"{PREFIX}{p_clean}".encode("utf-8")) > max_packet_bytes:
            last_sp = p_clean.rfind(" ")
            if last_sp == -1:
                break
            p_clean = p_clean[:last_sp].rstrip(" ,;:-¿¡") + "."
        return [f"{PREFIX}{p_clean}"]

    total = len(parts)
    formatted = []
    for i, p in enumerate(parts, 1):
        p_clean = p.strip()
        if not p_clean.endswith((".", "!", "?")):
            p_clean = p_clean.rstrip(" ,;:-¿¡") + "."
        pkt = f"{PREFIX}[{i}/{total}] {p_clean}"
        while len(pkt.encode("utf-8")) > max_packet_bytes:
            last_sp = p_clean.rfind(" ")
            if last_sp == -1:
                break
            p_clean = p_clean[:last_sp].rstrip(" ,;:-¿¡") + "."
            pkt = f"{PREFIX}[{i}/{total}] {p_clean}"
        formatted.append(pkt)

    return formatted


# =====================================================================
# AUXILIARES DE GEODATA Y TELEMETRÍA DE MALLA
# =====================================================================

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calcula la distancia ortodrómica en kilómetros entre dos coordenadas GPS (Haversine)."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def bearing_to_cardinal(lat1: float, lon1: float, lat2: float, lon2: float) -> str:
    """Calcula el rumbo cardinal aproximado (N, NE, E, SE, S, SW, W, NW) de punto 1 hacia punto 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    bearing = (math.degrees(math.atan2(y, x)) + 360) % 360
    cardinals = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return cardinals[round(bearing / 45) % 8]


def format_uptime(seconds: int) -> str:
    """Formatea segundos en una cadena amigable como '2d 4h' o '3h 15m'."""
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


# =====================================================================
# CLASE PRINCIPAL DEL BOT MESHTASTIC
# =====================================================================

class MeshtasticAIBot:
    def __init__(
        self,
        host=DEFAULT_HOST,
        serial_port=DEFAULT_PORT,
        ollama_url=DEFAULT_OLLAMA_URL,
        model=DEFAULT_MODEL,
        channel_name=DEFAULT_CHANNEL_NAME,
        max_chars=MAX_CHARS,
        max_parts=DEFAULT_MAX_PARTS,
    ):
        self.host = host
        self.serial_port = serial_port
        self.ollama_url = ollama_url
        self.model = model
        self.channel_name = channel_name
        self.max_chars = max_chars
        self.max_parts = max_parts
        self.interface = None
        self.target_channel_index = None
        self.my_node_num = None
        self.last_reply_time = 0
        self.processed_packet_ids = set()
        self.connected = False

    def clean_text(self, text: str) -> str:
        """Limpia el texto de formato Markdown, comillas y saltos para radio LoRa."""
        t = text.strip()
        t = t.replace("\n", " ").replace("\r", " ")
        t = t.replace('"', "").replace("'", "").replace("*", "").replace("#", "")
        t = re.sub(r"https?://\S+", "", t)  # eliminar URLs
        t = " ".join(t.split())  # normalizar espacios
        return t

    def format_output(self, reply: str) -> str:
        """Limpia y garantiza oraciones completas bajo max_chars o por bloque PACKET_SEP."""
        if PACKET_SEP in reply:
            blocks = [self.clean_text(b) for b in reply.split(PACKET_SEP) if b.strip()]
            trimmed_blocks = [trim_to_complete_sentences(b, 205) for b in blocks if b]
            return f" {PACKET_SEP} ".join(trimmed_blocks)
        cleaned = self.clean_text(reply)
        available_chars = self.max_chars - len(PREFIX)
        return trim_to_complete_sentences(cleaned, available_chars)

    def send_response(self, reply: str):
        """
        Transmite la respuesta a la malla LoRa garantizando que ningún paquete
        supere el límite estricto de Meshtastic (DATA_PAYLOAD_LEN = 233 bytes).
        Si la respuesta excede MAX_PACKET_BYTES (228 bytes en UTF-8), se divide
        inteligentemente en máximo self.max_parts paquetes ([1/2], [2/2]).
        """
        trimmed = self.format_output(reply)
        packets = split_into_packets(
            trimmed,
            max_packet_bytes=MAX_PACKET_BYTES,
            max_parts=self.max_parts,
        )

        if not packets:
            return

        total = len(packets)
        for i, pkt in enumerate(packets):
            if i > 0:
                # Pausa de seguridad entre transmisiones para evitar colisiones en la radio LoRa
                time.sleep(2.5)

            pkt_bytes = len(pkt.encode("utf-8"))
            logger.info(
                f"📤 Transmitiendo paquete {i+1}/{total} "
                f"({pkt_bytes} bytes UTF-8, {len(pkt)} chars): '{pkt}'"
            )
            try:
                self.interface.sendText(
                    pkt,
                    channelIndex=self.target_channel_index,
                )
                logger.info(f"✅ Paquete {i+1}/{total} transmitido con éxito a la malla LoRa.")
            except Exception as e:
                logger.error(f"Error transmitiendo paquete Meshtastic: {e}", exc_info=True)
                self.connected = False
                break

        self.last_reply_time = time.time()

    def query_llm(self, prompt_text: str, context: str = "") -> str:
        """Llama a Hailo Ollama para generar la respuesta con suficientes tokens."""
        if context:
            user_prompt = (
                f"Información verídica de referencia:\n{context}\n\n"
                f"Pregunta del usuario: {prompt_text}\n"
                "Instrucción: Responde en 1 o 2 oraciones breves y completas basadas en la referencia. Termina con punto."
            )
        else:
            user_prompt = (
                f"{prompt_text}\n"
                "(Responde en 1 o 2 oraciones breves y completas, terminando siempre con punto final)."
            )

        logger.info(f"Consultando AI ({self.model})...")
        payload = {
            "model": self.model,
            "system": SYSTEM_PROMPT,
            "prompt": user_prompt,
            "stream": False,
            "options": {
                "num_predict": 95,  # Tokens suficientes para 1-2 oraciones completas
                "temperature": 0.35,
            },
        }
        try:
            r = requests.post(
                self.ollama_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=25,
            )
            if r.status_code == 200:
                data = r.json()
                raw_reply = data.get("response", "").strip()
                return self.clean_text(raw_reply)
            else:
                logger.error(f"Error HTTP {r.status_code} desde Ollama: {r.text}")
                return "Error temporal en el motor de IA."
        except Exception as e:
            logger.error(f"Excepción al consultar AI: {e}")
            return "No fue posible conectar con el motor de IA."

    def get_local_node_info(self) -> dict:
        """Devuelve el diccionario del nodo local conectado."""
        if not self.interface:
            return {}
        try:
            info = self.interface.getMyNodeInfo()
            if info:
                return info
        except Exception:
            pass
        nodes_by_num = getattr(self.interface, "nodesByNum", {}) or {}
        if self.my_node_num and self.my_node_num in nodes_by_num:
            return nodes_by_num[self.my_node_num]
        nodes = getattr(self.interface, "nodes", {}) or {}
        for n in nodes.values():
            if n.get("num") == self.my_node_num:
                return n
        return {}

    def get_local_coordinates(self) -> tuple[float | None, float | None]:
        """Obtiene las coordenadas latitud/longitud del nodo local si existen."""
        local = self.get_local_node_info()
        pos = local.get("position", {})
        lat = pos.get("latitude") or (pos.get("latitudeI", 0) * 1e-7 if "latitudeI" in pos else None)
        lon = pos.get("longitude") or (pos.get("longitudeI", 0) * 1e-7 if "longitudeI" in pos else None)
        if lat and lon and (lat != 0 or lon != 0):
            return float(lat), float(lon)
        return None, None

    def find_node(self, target: str, sender_id: str = "", sender_num: int = None) -> tuple[dict, str]:
        """
        Localiza un nodo en la base de datos de la malla por identificador flexible:
        - Vacío, 'local', 'este', 'bot', 'servidor', 'aqui' -> nodo local
        - 'yo', 'mi', 'remitente' -> nodo que envió el mensaje
        - Hexadecimal con/sin '!' (!49b70f54, 49b70f54)
        - Número decimal (1236733780)
        - Nombre corto (SRF2, SRF4, etc.) o Nombre largo (Meshtastic 0f54, etc.)
        """
        clean = target.strip()
        nodes = getattr(self.interface, "nodes", {}) or {}
        nodes_by_num = getattr(self.interface, "nodesByNum", {}) or {}

        # 1. Caso nodo local
        if not clean or clean.lower() in ("local", "este", "aqui", "aquí", "servidor", "bot", "host"):
            local = self.get_local_node_info()
            name = local.get("user", {}).get("shortName") or "Local"
            nid = local.get("user", {}).get("id") or (f"!{self.my_node_num:08x}" if self.my_node_num else "")
            return local, f"{name} ({nid})" if nid else name

        # 2. Caso nodo remitente ('yo', 'mi')
        if clean.lower() in ("yo", "mi", "mío", "mio", "remitente"):
            if sender_id and sender_id in nodes:
                n = nodes[sender_id]
                name = n.get("user", {}).get("shortName") or sender_id
                return n, f"{name} ({sender_id})"
            if sender_num and sender_num in nodes_by_num:
                n = nodes_by_num[sender_num]
                nid = n.get("user", {}).get("id") or f"!{sender_num:08x}"
                name = n.get("user", {}).get("shortName") or nid
                return n, f"{name} ({nid})"
            return {}, "Tu nodo"

        # 3. Búsqueda exacta por ID (!1d64df18 o 1d64df18)
        target_id = clean if clean.startswith("!") else f"!{clean.lower()}"
        if target_id in nodes:
            n = nodes[target_id]
            name = n.get("user", {}).get("shortName") or target_id
            return n, f"{name} ({target_id})"

        # 4. Búsqueda por número de nodo (decimal)
        if clean.isdigit():
            num = int(clean)
            if num in nodes_by_num:
                n = nodes_by_num[num]
                nid = n.get("user", {}).get("id") or f"!{num:08x}"
                name = n.get("user", {}).get("shortName") or nid
                return n, f"{name} ({nid})"

        # 5. Coincidencia por user.shortName o user.longName (case-insensitive)
        clean_lower = clean.lower()
        for nid, n in nodes.items():
            user = n.get("user", {})
            s_name = (user.get("shortName") or "").strip().lower()
            l_name = (user.get("longName") or "").strip().lower()
            if clean_lower in (s_name, l_name):
                disp = user.get("shortName") or user.get("longName") or nid
                return n, f"{disp} ({nid})"

        # 6. Coincidencia parcial
        for nid, n in nodes.items():
            user = n.get("user", {})
            s_name = (user.get("shortName") or "").lower()
            l_name = (user.get("longName") or "").lower()
            if clean_lower in s_name or clean_lower in l_name or clean_lower in nid.lower():
                disp = user.get("shortName") or user.get("longName") or nid
                return n, f"{disp} ({nid})"

        return {}, clean

    def get_gps_response(self, target: str = "", sender_id: str = "", sender_num: int = None) -> str:
        """Genera el reporte de ubicación GPS para el nodo especificado o el local."""
        node, label = self.find_node(target, sender_id, sender_num)
        if not node:
            return f"No se encontró el nodo '{label}' en la base de datos de la malla."

        pos = node.get("position", {})
        if not pos:
            return f"El nodo {label} no reporta datos de posición GPS."

        lat = pos.get("latitude") or (pos.get("latitudeI", 0) * 1e-7 if "latitudeI" in pos else None)
        lon = pos.get("longitude") or (pos.get("longitudeI", 0) * 1e-7 if "longitudeI" in pos else None)

        if not lat or not lon or (lat == 0 and lon == 0):
            return f"El nodo {label} tiene GPS activo pero aún no tiene fijación satelital (Fix pendiente)."

        lat = float(lat)
        lon = float(lon)
        alt = pos.get("altitude")
        sats = pos.get("satsInView")
        src = pos.get("locationSource", "")
        src_tag = "GPS interno" if "INTERNAL" in src or "GPS" in src else ("Manual" if "MANUAL" in src else "")

        parts = [f"📍 GPS {label}: {lat:.5f},{lon:.5f}"]
        if alt:
            parts.append(f"Alt:{alt}m")
        if sats:
            parts.append(f"Sats:{sats}")
        if src_tag:
            parts.append(f"({src_tag})")

        # Distancia respecto al nodo local si no es el mismo nodo
        loc_lat, loc_lon = self.get_local_coordinates()
        node_num = node.get("num")
        if loc_lat and loc_lon and node_num != self.my_node_num:
            d = haversine_km(loc_lat, loc_lon, lat, lon)
            card = bearing_to_cardinal(loc_lat, loc_lon, lat, lon)
            dist_str = f"{int(d * 1000)}m" if d < 1.0 else f"{d:.1f}km"
            parts.append(f"Dist:{dist_str} al {card}")

        parts.append(f"maps.google.com/?q={lat:.5f},{lon:.5f}")
        return " ".join(parts) + "."

    def get_nodes_gps_list(self) -> str:
        """Lista los nodos de la malla que reportan coordenadas GPS válidas y su distancia."""
        nodes = getattr(self.interface, "nodes", {}) or {}
        loc_lat, loc_lon = self.get_local_coordinates()
        entries = []

        for nid, n in nodes.items():
            pos = n.get("position", {})
            lat = pos.get("latitude") or (pos.get("latitudeI", 0) * 1e-7 if "latitudeI" in pos else None)
            lon = pos.get("longitude") or (pos.get("longitudeI", 0) * 1e-7 if "longitudeI" in pos else None)
            if not lat or not lon or (lat == 0 and lon == 0):
                continue
            lat = float(lat)
            lon = float(lon)
            user = n.get("user", {})
            name = user.get("shortName") or user.get("longName") or nid
            if n.get("num") == self.my_node_num:
                entries.append(f"{name} (Local)")
            elif loc_lat and loc_lon:
                d = haversine_km(loc_lat, loc_lon, lat, lon)
                card = bearing_to_cardinal(loc_lat, loc_lon, lat, lon)
                dist_str = f"{int(d * 1000)}m" if d < 1.0 else f"{d:.1f}km"
                entries.append(f"{name} ({dist_str} {card})")
            else:
                entries.append(name)

        if not entries:
            return "No hay nodos reportando coordenadas GPS en la malla en este momento."

        return f"📍 Nodos con posición GPS ({len(entries)}): {', '.join(entries[:6])}."

    def get_telemetry_response(self, target: str = "", sender_id: str = "", sender_num: int = None) -> str:
        """Genera el reporte de telemetría de un nodo (batería, voltaje, canal, uptime, sensores)."""
        node, label = self.find_node(target, sender_id, sender_num)
        if not node:
            return f"No se encontró el nodo '{label}' en la base de datos de la malla."

        dev = node.get("deviceMetrics", {})
        env = node.get("environmentMetrics", {})
        power = node.get("powerMetrics", {})

        if not dev and not env and not power:
            snr = node.get("snr")
            hops = node.get("hopsAway")
            if snr is not None or hops is not None:
                extras = []
                if snr is not None:
                    extras.append(f"SNR:{snr:.1f}dB")
                if hops is not None:
                    extras.append(f"Saltos:{hops}")
                return f"Nodo {label} sin telemetría de batería reportada ({', '.join(extras)})."
            return f"Nodo {label} no tiene datos de telemetría registrados."

        metrics = []
        if dev:
            bat = dev.get("batteryLevel")
            volt = dev.get("voltage")
            if bat is not None:
                if bat > 100:
                    metrics.append(f"Bat:Externa ({volt:.2f}V)" if volt else "Bat:Externa")
                else:
                    metrics.append(f"Bat:{bat}% ({volt:.2f}V)" if volt else f"Bat:{bat}%")

            chan = dev.get("channelUtilization")
            if chan is not None:
                metrics.append(f"Canal:{chan:.1f}%")

            air = dev.get("airUtilTx")
            if air is not None:
                metrics.append(f"AirTX:{air:.1f}%")

            uptime = dev.get("uptimeSeconds")
            if uptime:
                metrics.append(f"Uptime:{format_uptime(int(uptime))}")

        if env:
            env_items = []
            temp = env.get("temperature")
            hum = env.get("relativeHumidity")
            pres = env.get("barometricPressure")
            if temp is not None:
                env_items.append(f"{temp:.1f}°C")
            if hum is not None:
                env_items.append(f"{hum:.0f}%H")
            if pres is not None:
                env_items.append(f"{pres:.1f}hPa")
            if env_items:
                metrics.append(f"Ambiente:{', '.join(env_items)}")

        snr = node.get("snr")
        if snr is not None:
            metrics.append(f"SNR:{snr:.1f}dB")

        return f"📊 Telem {label}: " + " | ".join(metrics) + "."

    def get_mesh_telemetry_summary(self) -> str:
        """Reporte general de telemetría y estadísticas de la red LoRa."""
        nodes = getattr(self.interface, "nodes", {}) or {}
        total = len(nodes)
        now = time.time()
        recent = 0
        with_gps = 0
        with_telem = 0
        channel_utils = []

        for n in nodes.values():
            last_h = n.get("lastHeard", 0)
            if (now - last_h) < 86400:
                recent += 1
            pos = n.get("position", {})
            lat = pos.get("latitude") or (pos.get("latitudeI", 0) * 1e-7 if "latitudeI" in pos else 0)
            lon = pos.get("longitude") or (pos.get("longitudeI", 0) * 1e-7 if "longitudeI" in pos else 0)
            if lat != 0 and lon != 0:
                with_gps += 1
            dev = n.get("deviceMetrics", {})
            if dev:
                with_telem += 1
                cu = dev.get("channelUtilization")
                if cu is not None:
                    channel_utils.append(cu)

        avg_chan = f" Canal prom: {sum(channel_utils)/len(channel_utils):.1f}%." if channel_utils else ""
        return (
            f"📊 Red LoRa: {total} nodos ({recent} activos hoy). "
            f"Con GPS: {with_gps}. Con telemetría: {with_telem}.{avg_chan} "
            f"Canal: {self.channel_name}."
        )

    def get_mesh_status(self) -> str:
        """Obtiene estadísticas y telemetría de los nodos locales en la malla LoRa."""
        return self.get_mesh_telemetry_summary()

    def generate_field_weather_reading(self, rural_brief: str) -> str:
        """
        Pasa los datos meteorológicos por el LLM local (Hailo-Ollama) para generar una lectura
        en lenguaje sencillo y útil para personas que trabajan en el campo (calor/frío, lluvia y consejo).
        """
        prompt = (
            f"Datos del clima de hoy:\n{rural_brief}\n\n"
            "Instrucción: Explica este clima en lenguaje muy sencillo para un trabajador del campo "
            "en máximo 25 palabras (1 o 2 oraciones cortas), diciendo si hará frío o calor, si va a llover "
            "y qué cuidado tener en el campo. Empieza directo sin introducciones y termina con punto."
        )
        logger.info(f"Consultando AI ({self.model}) para lectura rural del clima...")
        payload = {
            "model": self.model,
            "system": (
                "Eres un asistente rural para campesinos y trabajadores del campo. "
                "Habla claro, sencillo y directo en español. Sin tecnicismos. "
                "Máximo 25 palabras en total, terminando siempre con punto final."
            ),
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_predict": 58,
                "temperature": 0.25,
            },
        }
        llm_reading = ""
        try:
            r = requests.post(
                self.ollama_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=20,
            )
            if r.status_code == 200:
                raw_reply = r.json().get("response", "").strip()
                cleaned = self.clean_text(raw_reply)
                # Quitar muletillas introductorias para aprovechar al máximo el paquete LoRa
                cleaned = re.sub(
                    r"^(?:según la información|segun la informacion|según los datos|segun los datos|"
                    r"basándome en los datos|de acuerdo con los datos|en resumen|resumen para el campo)"
                    r"[\s,:;-]*",
                    "",
                    cleaned,
                    flags=re.IGNORECASE,
                ).strip()
                if cleaned:
                    cleaned = cleaned[0].upper() + cleaned[1:]
                    llm_reading = trim_to_complete_sentences(cleaned, 185)
        except Exception as e:
            logger.warning(f"No se pudo obtener lectura rural desde LLM, usando síntesis directa: {e}")

        if not llm_reading or len(llm_reading) < 20:
            llm_reading = trim_to_complete_sentences(rural_brief, 185)

        return f"🌾 Campo: {llm_reading}"

    def _build_full_weather_packets(self, lat: float, lon: float, label: str) -> str:
        """Construye los 3 bloques de clima: [1/3] Actual+Horas, [2/3] Mañana/Tarde/Noche, [3/3] Lectura Campo LLM."""
        part1, part2, rural_brief = fetch_weather_bundle(lat, lon, label)
        if not part1 or not part2:
            return ""
        part3 = self.generate_field_weather_reading(rural_brief)
        return f"{part1} {PACKET_SEP} {part2} {PACKET_SEP} {part3}"

    def get_weather_response(self, target: str = "", sender_id: str = "", sender_num: int = None) -> str:
        """
        Obtiene el reporte meteorológico y pronóstico extendido (próximas horas + Mañana, Tarde y Noche)
        más la interpretación en lenguaje sencillo para el campo generada con el LLM.
        """
        clean_target = clean_location_query(target)
        loc_lat, loc_lon = self.get_local_coordinates()
        ref_lat = loc_lat if loc_lat is not None else 6.4062
        ref_lon = loc_lon if loc_lon is not None else -75.0161

        # 1. Si pide el clima del nodo local ('aqui', 'local', 'nodo') o del remitente ('yo')
        if clean_target.lower() in ("local", "aqui", "aquí", "este", "nodo", "bot", "yo", "mi"):
            node, label = self.find_node(clean_target, sender_id=sender_id, sender_num=sender_num)
            pos = node.get("position", {}) if node else {}
            n_lat = pos.get("latitude") or (pos.get("latitudeI", 0) * 1e-7 if "latitudeI" in pos else None)
            n_lon = pos.get("longitude") or (pos.get("longitudeI", 0) * 1e-7 if "longitudeI" in pos else None)
            if n_lat and n_lon and (n_lat != 0 or n_lon != 0):
                short_name = node.get("user", {}).get("shortName") or label
                w = self._build_full_weather_packets(float(n_lat), float(n_lon), f"Nodo {short_name}")
                if w:
                    return w

        # 2. Si el argumento coincide exactamente con un nodo de la malla con GPS (ej: !clima SRF2)
        if clean_target and len(clean_target.split()) == 1 and _normalize_key(clean_target) not in KNOWN_LOCATIONS:
            node, label = self.find_node(clean_target, sender_id=sender_id, sender_num=sender_num)
            if node:
                pos = node.get("position", {})
                n_lat = pos.get("latitude") or (pos.get("latitudeI", 0) * 1e-7 if "latitudeI" in pos else None)
                n_lon = pos.get("longitude") or (pos.get("longitudeI", 0) * 1e-7 if "longitudeI" in pos else None)
                if n_lat and n_lon and (n_lat != 0 or n_lon != 0):
                    short_name = node.get("user", {}).get("shortName") or clean_target
                    w = self._build_full_weather_packets(float(n_lat), float(n_lon), f"Nodo {short_name}")
                    if w:
                        return w

        # 3. Búsqueda geográfica por nombre de zona/corregimiento/municipio (ej. San Roque Antioquia Playa Rica)
        query_loc = clean_target or DEFAULT_LOCATION
        lat, lon, label = geocode_location(query_loc, ref_lat=ref_lat, ref_lon=ref_lon)
        if lat is not None and lon is not None:
            w = self._build_full_weather_packets(lat, lon, label)
            if w:
                return w

        w_fallback = fetch_weather(query_loc, ref_lat=ref_lat, ref_lon=ref_lon)
        if w_fallback:
            return w_fallback
        return f"No se pudo obtener el pronóstico del clima para {query_loc}."

    def process_message(self, text: str, sender_id: str = "", sender_num: int = None) -> str:
        """Analiza la intención del mensaje y despacha al comando o herramienta correspondiente."""
        raw = text.strip()
        lower = raw.lower()

        # 1. Comandos directos
        if lower in ("!ayuda", "/ayuda", "?ayuda", "!help"):
            return "Comandos: !clima [lugar/nodo], !pronostico [lugar], !gps [nodo/lista], !telem [nodo/red], !wiki [tema], !buscar [texto], !nodos, !hora, o pregúntame."

        if lower in ("!hora", "/hora", "!time"):
            now_str = datetime.datetime.now().strftime("%H:%M:%S (%d/%m)")
            return f"Hora local: {now_str}."

        # Comandos GPS y Ubicación
        if lower.startswith("!gps") or lower.startswith("/gps") or lower.startswith("!ubicacion") or lower.startswith("/ubicacion") or lower.startswith("!pos") or lower.startswith("/pos") or lower.startswith("!posicion"):
            parts = raw.split(maxsplit=1)
            target = parts[1].strip() if len(parts) > 1 else ""
            if target.lower() in ("lista", "nodos", "todos"):
                return self.get_nodes_gps_list()
            return self.get_gps_response(target, sender_id=sender_id, sender_num=sender_num)

        # Comandos de Telemetría y Batería
        if lower.startswith("!telem") or lower.startswith("/telem") or lower.startswith("!bateria") or lower.startswith("/bateria") or lower.startswith("!telemetria") or lower.startswith("/telemetria") or lower.startswith("!bat"):
            parts = raw.split(maxsplit=1)
            target = parts[1].strip() if len(parts) > 1 else ""
            if target.lower() in ("red", "malla", "mesh", "stats", "general"):
                return self.get_mesh_telemetry_summary()
            return self.get_telemetry_response(target, sender_id=sender_id, sender_num=sender_num)

        # Estado de la malla y nodos
        if lower in ("!nodos", "/nodos", "!malla", "!mesh", "!estado", "!red"):
            return self.get_mesh_telemetry_summary()

        # Comandos de Clima y Pronóstico Extendido
        if (
            lower.startswith("!clima")
            or lower.startswith("/clima")
            or lower.startswith("!pronostico")
            or lower.startswith("/pronostico")
            or lower.startswith("!pronóstico")
            or lower.startswith("!tiempo")
        ):
            parts = raw.split(maxsplit=1)
            target_loc = parts[1] if len(parts) > 1 else DEFAULT_LOCATION
            return self.get_weather_response(target_loc, sender_id=sender_id, sender_num=sender_num)

        if lower.startswith("!wiki") or lower.startswith("/wiki"):
            parts = raw.split(maxsplit=1)
            if len(parts) > 1:
                topic = parts[1]
                w_info = fetch_wikipedia_summary(topic)
                if w_info:
                    return self.query_llm(f"Resume qué es {topic}", context=w_info)
                return f"No se encontró información en Wikipedia sobre {topic}."
            return "Uso: !wiki [tema a consultar]."

        if lower.startswith("!buscar") or lower.startswith("/buscar") or lower.startswith("!search"):
            parts = raw.split(maxsplit=1)
            if len(parts) > 1:
                query = parts[1]
                snippets = search_duckduckgo(query)
                if snippets:
                    return self.query_llm(f"Responde la consulta: {query}", context=snippets)
                return f"No se encontraron resultados para {query}."
            return "Uso: !buscar [consulta]."

        # 2. Detección Inteligente en Lenguaje Natural
        # Detección de GPS / Ubicación
        if any(w in lower for w in [
            "donde estas", "dónde estás", "donde esta", "dónde está", "donde queda", "dónde queda",
            "ubicacion", "ubicación", "coordenadas", "posicion", "posición", "gps"
        ]):
            # 1. Comprobar si busca un nodo remoto específico (ej: "donde esta SRF2", "ubicacion de MFC1")
            m_target = re.search(r"(?:donde esta|dónde está|ubicacion de|ubicación de|posicion de|posición de|coordenadas de)\s+([a-zA-Z0-9_!]+)", lower)
            if m_target:
                candidate = m_target.group(1).strip()
                if candidate not in ("tu", "este", "el", "este_nodo", "este nodo", "nodo", "gps", "la", "mi", "un"):
                    target_node, _ = self.find_node(candidate, sender_id=sender_id, sender_num=sender_num)
                    if target_node:
                        return self.get_gps_response(candidate, sender_id=sender_id, sender_num=sender_num)

            # 2. Si pregunta por la ubicación del bot / nodo local
            if any(w in lower for w in [
                "donde estas", "dónde estás", "tu ubicacion", "tu ubicación", "tu posicion", "tu posición",
                "este nodo", "del nodo", "que tienes", "exacta", "coordenadas", "dame la ubicacion",
                "dame tu ubicacion", "dame la ubicación", "dame tu ubicación", "cual es tu", "cuál es tu",
                "posicion gps", "posición gps", "ubicacion gps", "ubicación gps"
            ]) or "gps" in lower:
                return self.get_gps_response("", sender_id=sender_id, sender_num=sender_num)

        # Detección de Batería / Telemetría
        if any(k in lower for k in ["bateria", "batería", "telemetria", "telemetría", "voltaje", "carga"]):
            m_bat = re.search(r"(?:bateria de|batería de|telemetria de|telemetría de|voltaje de)\s+([a-zA-Z0-9_!]+)", lower)
            if m_bat:
                candidate = m_bat.group(1).strip()
                if candidate not in ("tu", "este", "el", "este_nodo", "este nodo", "la", "mi", "un", "nodo"):
                    target_node, _ = self.find_node(candidate, sender_id=sender_id, sender_num=sender_num)
                    if target_node:
                        return self.get_telemetry_response(candidate, sender_id=sender_id, sender_num=sender_num)
            return self.get_telemetry_response("", sender_id=sender_id, sender_num=sender_num)

        # Detección de Estado general de la red
        if any(k in lower for k in ["estado de la malla", "estado de la red", "como esta la red", "cómo está la red", "cuantos nodos"]):
            return self.get_mesh_telemetry_summary()

        # Detección de Clima y Pronóstico en Lenguaje Natural
        if any(w in lower for w in [
            "clima", "temperatura", "va a llover", "llueve", "lluvia",
            "pronóstico", "pronostico", "tiempo en", "que tal el clima", "qué tal el clima"
        ]):
            loc = DEFAULT_LOCATION
            m_loc = re.search(
                r"(?:clima\s+en|clima\s+de|clima\s+para|pronostico\s+en|pronóstico\s+en|"
                r"pronostico\s+de|pronóstico\s+de|llover\s+en|llueve\s+en|temperatura\s+en|"
                r"tiempo\s+en|\ben)\s+([a-zA-Z0-9áéíóúñÁÉÍÓÚÑ\s,]+)",
                raw,
                flags=re.IGNORECASE,
            )
            if m_loc:
                extracted = clean_location_query(m_loc.group(1))
                if extracted:
                    loc = extracted
            elif lower.startswith("clima ") or lower.startswith("pronostico ") or lower.startswith("pronóstico "):
                extracted = clean_location_query(raw.split(maxsplit=1)[1])
                if extracted:
                    loc = extracted

            w_info = self.get_weather_response(loc, sender_id=sender_id, sender_num=sender_num)
            # Entregar directamente el reporte completo de 2 paquetes (actual + próximas horas + Mañana/Tarde/Noche)
            if w_info and not w_info.startswith("No se pudo"):
                return w_info

        # Detección de Definición / Pregunta Enciclopédica
        if lower.startswith("que es ") or lower.startswith("qué es ") or lower.startswith("quien es ") or lower.startswith("quién es ") or lower.startswith("quien fue ") or lower.startswith("quién fue "):
            term = re.sub(r"^(que es|qué es|quien es|quién es|quien fue|quién fue)\s+", "", lower).replace("?", "").strip()
            w_info = fetch_wikipedia_summary(term)
            if w_info:
                return self.query_llm(raw, context=w_info)

        # Detección de Búsqueda Web / Actualidad
        if any(w in lower for w in ["noticias", "últimas noticias", "ultimas noticias", "busca ", "buscar "]):
            snippets = search_duckduckgo(raw)
            if snippets:
                return self.query_llm(raw, context=snippets)

        # 3. Consulta General a la IA (con inyección contextual de hardware si es relevante)
        if any(k in lower for k in ["gps", "ubicac", "posic", "coordenad", "altitud", "altura", "bater", "voltaj", "nodo", "malla"]):
            hw_context_items = []
            loc_gps = self.get_gps_response()
            if "no reporta" not in loc_gps and "pendiente" not in loc_gps:
                hw_context_items.append(loc_gps)
            loc_telem = self.get_telemetry_response()
            if "no tiene" not in loc_telem:
                hw_context_items.append(loc_telem)
            if hw_context_items:
                return self.query_llm(raw, context=" ".join(hw_context_items))

        return self.query_llm(raw)

    def on_connection_lost(self, interface=None, topic=pub.AUTO_TOPIC):
        """Callback invocado cuando Meshtastic detecta desconexión del nodo."""
        logger.warning("⚠️ Evento de desconexión Meshtastic detectado. Marcando para reconexión...")
        self.connected = False

    def on_receive(self, packet, interface):
        """Manejador de paquetes recibidos de Meshtastic."""
        try:
            # Validar tipo de paquete
            decoded = packet.get("decoded", {})
            if decoded.get("portnum") != "TEXT_MESSAGE_APP":
                return

            # Evitar bucles: ignorar paquetes emitidos por nosotros mismos
            sender_num = packet.get("from")
            if sender_num == self.my_node_num:
                return

            # Filtrar por canal objetivo (PlayaRica)
            channel_idx = packet.get("channel", 0)
            if channel_idx != self.target_channel_index:
                return

            text = decoded.get("text", "").strip()
            if not text:
                return

            # Deduplicación de paquetes repetidos por retransmisión de malla
            pkt_id = packet.get("id")
            if pkt_id:
                if pkt_id in self.processed_packet_ids:
                    return
                self.processed_packet_ids.add(pkt_id)
                if len(self.processed_packet_ids) > 1000:
                    self.processed_packet_ids.clear()

            sender_id = packet.get("fromId", hex(sender_num) if sender_num else "desconocido")
            logger.info(f"📥 [Canal {self.channel_name}] Mensaje de {sender_id}: '{text}'")

            # Ignorar mensajes de otros bots para evitar bucles
            if text.startswith("[Bot]") or text.startswith(PREFIX) or text.startswith("🤖"):
                return

            # Control de Airtime / Cooldown
            now = time.time()
            if (now - self.last_reply_time) < COOLDOWN_SECONDS:
                logger.warning(f"Cooldown activo ({COOLDOWN_SECONDS}s). Descartando respuesta inmediata para proteger airtime.")
                return

            # Procesar el mensaje y obtener respuesta
            raw_response = self.process_message(text, sender_id=sender_id, sender_num=sender_num)
            if not raw_response:
                return

            # Transmitir respuesta a la malla garantizando MTU seguro
            self.send_response(raw_response)

        except Exception as e:
            logger.error(f"Error procesando paquete: {e}", exc_info=True)

    def find_channel_index(self):
        """Busca el índice correspondiente al nombre del canal configurado."""
        channels = getattr(self.interface.localNode, "channels", [])
        for ch in channels:
            name = getattr(ch.settings, "name", "")
            if name.lower() == self.channel_name.lower():
                logger.info(f"Canal '{self.channel_name}' encontrado en índice {ch.index}")
                return ch.index
        logger.warning(f"Canal '{self.channel_name}' no encontrado por nombre. Usando índice 1 por defecto.")
        return 1

    def connect(self):
        """Conecta al nodo Meshtastic vía TCP o Serial."""
        if self.serial_port:
            logger.info(f"Conectando a Meshtastic por puerto serial {self.serial_port}...")
            self.interface = meshtastic.serial_interface.SerialInterface(devPath=self.serial_port)
        else:
            logger.info(f"Conectando a Meshtastic por TCP en {self.host}...")
            self.interface = meshtastic.tcp_interface.TCPInterface(self.host)

        self.my_node_num = self.interface.myInfo.my_node_num
        logger.info(f"Conectado a nodo ID: {self.interface.myInfo.my_node_num}")

        self.target_channel_index = self.find_channel_index()
        pub.subscribe(self.on_receive, "meshtastic.receive")
        pub.subscribe(self.on_connection_lost, "meshtastic.connection.lost")
        self.connected = True
        logger.info(
            f"🤖 Bot v2.4 escuchando en canal '{self.channel_name}' (idx {self.target_channel_index}). "
            f"Max Chars: {self.max_chars}, Max Parts: {self.max_parts}, Model: {self.model}, Airtime Cooldown: {COOLDOWN_SECONDS}s"
        )

    def _is_interface_healthy(self) -> bool:
        """Verifica que el socket TCP y el hilo lector de Meshtastic sigan vivos."""
        if not self.connected or not self.interface:
            return False
        rx_thread = getattr(self.interface, "_rxThread", None)
        if rx_thread is not None and not rx_thread.is_alive():
            logger.warning("⚠️ El hilo lector de Meshtastic (_rxThread) terminó inesperadamente.")
            return False
        if not self.serial_port:
            sock = getattr(self.interface, "socket", None)
            if sock is None:
                logger.warning("⚠️ El socket TCP de Meshtastic está cerrado.")
                return False
        return True

    def run(self):
        """Bucle principal con monitoreo de salud de conexión y reconexión automática."""
        while True:
            try:
                self.connect()
                while self._is_interface_healthy():
                    time.sleep(2)
                logger.warning("Reconexión requerida: cerrando interfaz previa y reintentando en 8s...")
                if self.interface:
                    try:
                        self.interface.close()
                    except Exception:
                        pass
                time.sleep(8)
            except KeyboardInterrupt:
                logger.info("Deteniendo bot por solicitud del usuario...")
                if self.interface:
                    self.interface.close()
                break
            except Exception as e:
                logger.error(f"Conexión con el nodo perdida ({e}). Reconectando en 10s...")
                if self.interface:
                    try:
                        self.interface.close()
                    except Exception:
                        pass
                time.sleep(10)


def main():
    parser = argparse.ArgumentParser(description="Meshtastic AI Bot v2.4 con Clima Extendido, GPS, Telemetría y <= 420 chars")
    parser.add_argument("--host", default=DEFAULT_HOST, help="IP del nodo Meshtastic (default: 192.168.68.68)")
    parser.add_argument("--port", default=DEFAULT_PORT, help="Puerto serial alternativo (/dev/tty...)")
    parser.add_argument("--ollama", default=DEFAULT_OLLAMA_URL, help="URL de Hailo-Ollama")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Modelo LLM a usar")
    parser.add_argument("--channel", default=DEFAULT_CHANNEL_NAME, help="Nombre del canal objetivo")
    parser.add_argument("--max-chars", type=int, default=MAX_CHARS, help="Límite máximo de caracteres (default: 420)")
    parser.add_argument("--max-parts", type=int, default=DEFAULT_MAX_PARTS, help="Máximo número de paquetes por respuesta (1 para sólo 1 paquete, 2 para multiparte)")
    parser.add_argument("--cooldown", type=int, default=COOLDOWN_SECONDS, help="Segundos de enfriamiento entre mensajes")
    args = parser.parse_args()

    bot = MeshtasticAIBot(
        host=args.host,
        serial_port=args.port,
        ollama_url=args.ollama,
        model=args.model,
        channel_name=args.channel,
        max_chars=args.max_chars,
        max_parts=args.max_parts,
    )
    bot.run()


if __name__ == "__main__":
    main()
