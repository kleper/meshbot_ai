"""
Servicio meteorológico y geocodificación (Open-Meteo + Nominatim OSM + wttr.in) con caché TTL en memoria.
"""

import datetime
import logging
import re
import time
import unicodedata
import requests
from app.config import (
    DEFAULT_LOCATION,
    KNOWN_LOCATIONS,
    MAX_PACKET_BYTES,
    PREFIX,
    WEATHER_CACHE_TTL,
    WMO_WEATHER_CODES,
)
from app.services.telemetry import haversine_km

logger = logging.getLogger("MeshtasticBot.Weather")

# Caché en memoria de coordenadas y de reportes completos (Clima + Lectura LLM)
_GEOCODE_CACHE: dict[str, tuple[float, float, str]] = {}
_WEATHER_CACHE: dict[str, dict] = {}


def _normalize_key(text: str) -> str:
    """Normaliza texto en minúsculas sin tildes ni signos para búsqueda en caché."""
    t = unicodedata.normalize("NFD", text.lower().strip())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return " ".join(t.split())


def get_cached_weather_packets(lat: float, lon: float, label: str, ttl: int = WEATHER_CACHE_TTL) -> str | None:
    """Devuelve el paquete triple de clima + LLM si existe en caché y no ha expirado."""
    key = f"{round(lat, 3)}:{round(lon, 3)}:{_normalize_key(label)}"
    entry = _WEATHER_CACHE.get(key)
    if not entry:
        return None
    age = time.time() - entry["ts"]
    if age > ttl:
        _WEATHER_CACHE.pop(key, None)
        return None
    entry["hits"] += 1
    logger.info(f"⚡ Clima servido desde caché en memoria para '{label}' (edad: {int(age)}s, hits: {entry['hits']})")
    return entry["payload"]


def set_cached_weather_packets(lat: float, lon: float, label: str, payload: str) -> None:
    """Almacena el reporte triple de clima + LLM en caché con marca de tiempo actual."""
    if not payload:
        return
    key = f"{round(lat, 3)}:{round(lon, 3)}:{_normalize_key(label)}"
    _WEATHER_CACHE[key] = {
        "label": label,
        "lat": round(lat, 4),
        "lon": round(lon, 4),
        "payload": payload,
        "ts": time.time(),
        "hits": 0,
    }


def clear_weather_cache() -> dict:
    """Limpia la caché de clima y geocodificación bajo demanda."""
    w_count = len(_WEATHER_CACHE)
    g_count = len(_GEOCODE_CACHE)
    _WEATHER_CACHE.clear()
    _GEOCODE_CACHE.clear()
    return {"cleared_weather_entries": w_count, "cleared_geocode_entries": g_count}


def get_weather_cache_stats(ttl: int = WEATHER_CACHE_TTL) -> dict:
    """Devuelve estadísticas del estado actual del caché meteorológico."""
    now = time.time()
    active_items = []
    for k, v in list(_WEATHER_CACHE.items()):
        age = now - v["ts"]
        if age <= ttl:
            active_items.append(
                {
                    "key": k,
                    "label": v["label"],
                    "age_seconds": round(age, 1),
                    "expires_in_seconds": round(ttl - age, 1),
                    "hits": v["hits"],
                }
            )
    return {
        "ttl_seconds": ttl,
        "active_weather_entries": len(active_items),
        "geocode_cached_entries": len(_GEOCODE_CACHE),
        "entries": active_items,
    }


def clean_location_query(raw_loc: str) -> str:
    """Limpia palabras de relleno temporales, conectores o signos de una consulta de ubicación."""
    q = raw_loc.strip().strip("?!.,;:")
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
    m_conn = re.search(
        r"(?:clima\s+en|clima\s+de|clima\s+para|pronostico\s+en|pronóstico\s+en|"
        r"pronostico\s+de|pronóstico\s+de|tiempo\s+en|tiempo\s+de|temperatura\s+en|"
        r"llover\s+en|llueve\s+en|\ben|\bde|\bpara)\s+(.+)$",
        q,
        flags=re.IGNORECASE,
    )
    if m_conn:
        candidate = m_conn.group(1).strip()
        if q.lower().startswith(
            (
                "el ",
                "la ",
                "como ",
                "cómo ",
                "que ",
                "qué ",
                "dame ",
                "cual ",
                "cuál ",
                "va ",
                "llueve",
                "clima",
                "pronostico",
                "pronóstico",
                "tiempo",
                "temperatura",
                "en ",
                "de ",
                "para ",
            )
        ):
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

    # 1. Intentar Nominatim OpenStreetMap
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": clean_q,
                "format": "json",
                "limit": 5,
                "accept-language": "es",
            },
            headers={"User-Agent": "MeshtasticBot-PlayaRica/3.0"},
            timeout=6,
        )
        if r.status_code == 200:
            items = r.json()
            if items:
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
                    dist = haversine_km(ref_lat, ref_lon, lat, lon)
                    score -= min(dist * 0.05, 250.0)
                    if score > best_score:
                        best_score = score
                        best = (lat, lon, disp)
                if best:
                    lat, lon, disp = best
                    parts = [p.strip() for p in disp.split(",") if p.strip()]
                    short_label = parts[0]
                    if (
                        len(parts) > 1
                        and parts[1].lower() not in parts[0].lower()
                        and not parts[1].startswith("Comuna")
                    ):
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

        temp = round(cur.get("temperature_2m", 0))
        app_temp = round(cur.get("apparent_temperature", temp))
        hum = round(cur.get("relative_humidity_2m", 0))
        w_spd = round(cur.get("wind_speed_10m", 0))
        w_dir = wind_deg_to_cardinal(cur.get("wind_direction_10m"))
        uv = round(cur.get("uv_index", 0))
        cond_now = wmo_to_spanish(cur.get("weather_code", 0))

        d_min = round((daily.get("temperature_2m_min") or [temp])[0])
        d_max = round((daily.get("temperature_2m_max") or [temp])[0])
        d_prob = round((daily.get("precipitation_probability_max") or [0])[0])
        d_mm = (daily.get("precipitation_sum") or [0.0])[0] or 0.0
        rain_today_str = f"{d_prob}%({d_mm:.0f}mm)" if d_mm >= 0.5 else f"{d_prob}%"

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
    """Consulta el clima actual y pronóstico con fallback a wttr.in en español y métrico."""
    loc = clean_location_query(location) or DEFAULT_LOCATION
    lat, lon, label = geocode_location(loc, ref_lat=ref_lat, ref_lon=ref_lon)
    if lat is not None and lon is not None:
        report = fetch_weather_by_coords(lat, lon, label)
        if report:
            return report

    try:
        url = f"https://wttr.in/{loc}?m&lang=es&format=%C+%t+(ST:%f)+Viento:%w+Hum:%h+Lluvia:%p"
        r = requests.get(url, headers={"User-Agent": "curl/8.0"}, timeout=6)
        if r.status_code == 200 and "Unknown" not in r.text and len(r.text.strip()) > 3:
            return f"🌦 Clima {loc}: {r.text.strip()}."
    except Exception as e:
        logger.warning(f"Error consultando fallback wttr.in: {e}")
    return ""
