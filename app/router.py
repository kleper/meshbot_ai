"""
Enrutador de comandos directos (!clima, !gps, !telem, !wiki, !buscar, !nodos, !hora)
y detector de intenciones en lenguaje natural para el bot Meshtastic.
"""

import datetime
import re
from app.config import DEFAULT_LOCATION, KNOWN_LOCATIONS, PACKET_SEP
from app.services.search import fetch_wikipedia_summary, search_duckduckgo
from app.services.weather import (
    _normalize_key,
    clean_location_query,
    fetch_weather,
    fetch_weather_bundle,
    geocode_location,
    get_cached_weather_packets,
    set_cached_weather_packets,
)


class MessageRouter:
    """Analiza y despacha mensajes entrantes hacia el servicio correspondiente."""

    def __init__(self, bot_ref):
        self.bot = bot_ref

    def build_full_weather_packets(self, lat: float, lon: float, label: str, use_cache: bool = True) -> str:
        """
        Construye o recupera de caché (TTL 15 min) los 3 bloques de clima:
        [1/3] Actual+Horas, [2/3] Mañana/Tarde/Noche, [3/3] Lectura Campo LLM.
        """
        if use_cache:
            cached = get_cached_weather_packets(lat, lon, label)
            if cached:
                return cached

        part1, part2, rural_brief = fetch_weather_bundle(lat, lon, label)
        if not part1 or not part2:
            return ""
        part3 = self.bot.generate_field_weather_reading(rural_brief)
        full_payload = f"{part1} {PACKET_SEP} {part2} {PACKET_SEP} {part3}"
        set_cached_weather_packets(lat, lon, label, full_payload)
        return full_payload

    def get_weather_response(
        self,
        target: str = "",
        sender_id: str = "",
        sender_num: int | None = None,
    ) -> str:
        """
        Obtiene el reporte meteorológico extendido + lectura en lenguaje sencillo para el campo.
        Soporta palabras clave de nodo ('local', 'yo', 'SRF2') y ubicaciones geográficas.
        """
        clean_target = clean_location_query(target)
        loc_lat, loc_lon = self.bot.get_local_coordinates()
        ref_lat = loc_lat if loc_lat is not None else 6.4062
        ref_lon = loc_lon if loc_lon is not None else -75.0161

        # 1. Si pide el clima del nodo local ('aqui', 'local', 'nodo') o del remitente ('yo')
        if clean_target.lower() in ("local", "aqui", "aquí", "este", "nodo", "bot", "yo", "mi"):
            node, label = self.bot.find_node(clean_target, sender_id=sender_id, sender_num=sender_num)
            pos = node.get("position", {}) if node else {}
            n_lat = pos.get("latitude") or (pos.get("latitudeI", 0) * 1e-7 if "latitudeI" in pos else None)
            n_lon = pos.get("longitude") or (pos.get("longitudeI", 0) * 1e-7 if "longitudeI" in pos else None)
            if n_lat and n_lon and (n_lat != 0 or n_lon != 0):
                short_name = node.get("user", {}).get("shortName") or label
                w = self.build_full_weather_packets(float(n_lat), float(n_lon), f"Nodo {short_name}")
                if w:
                    return w

        # 2. Si el argumento coincide exactamente con un nodo de la malla con GPS (ej: !clima SRF2)
        if clean_target and len(clean_target.split()) == 1 and _normalize_key(clean_target) not in KNOWN_LOCATIONS:
            node, _ = self.bot.find_node(clean_target, sender_id=sender_id, sender_num=sender_num)
            if node:
                pos = node.get("position", {})
                n_lat = pos.get("latitude") or (pos.get("latitudeI", 0) * 1e-7 if "latitudeI" in pos else None)
                n_lon = pos.get("longitude") or (pos.get("longitudeI", 0) * 1e-7 if "longitudeI" in pos else None)
                if n_lat and n_lon and (n_lat != 0 or n_lon != 0):
                    short_name = node.get("user", {}).get("shortName") or clean_target
                    w = self.build_full_weather_packets(float(n_lat), float(n_lon), f"Nodo {short_name}")
                    if w:
                        return w

        # 3. Búsqueda geográfica por nombre de zona/corregimiento/municipio
        query_loc = clean_target or DEFAULT_LOCATION
        lat, lon, label = geocode_location(query_loc, ref_lat=ref_lat, ref_lon=ref_lon)
        if lat is not None and lon is not None:
            w = self.build_full_weather_packets(lat, lon, label)
            if w:
                return w

        w_fallback = fetch_weather(query_loc, ref_lat=ref_lat, ref_lon=ref_lon)
        if w_fallback:
            return w_fallback
        return f"No se pudo obtener el pronóstico del clima para {query_loc}."

    def process_message(self, text: str, sender_id: str = "", sender_num: int | None = None) -> str:
        """Analiza la intención del mensaje y despacha al comando o herramienta correspondiente."""
        raw = text.strip()
        lower = raw.lower()

        # 1. Comandos directos
        if lower in ("!ayuda", "/ayuda", "?ayuda", "!help"):
            return (
                "Comandos: !clima [lugar/nodo], !pronostico [lugar], !gps [nodo/lista], "
                "!telem [nodo/red], !wiki [tema], !buscar [texto], !nodos, !hora, o pregúntame."
            )

        if lower in ("!hora", "/hora", "!time"):
            now_str = datetime.datetime.now().strftime("%H:%M:%S (%d/%m)")
            return f"Hora local: {now_str}."

        # Comandos GPS y Ubicación
        if (
            lower.startswith("!gps")
            or lower.startswith("/gps")
            or lower.startswith("!ubicacion")
            or lower.startswith("/ubicacion")
            or lower.startswith("!pos")
            or lower.startswith("/pos")
            or lower.startswith("!posicion")
        ):
            parts = raw.split(maxsplit=1)
            target = parts[1].strip() if len(parts) > 1 else ""
            if target.lower() in ("lista", "nodos", "todos"):
                return self.bot.get_nodes_gps_list()
            return self.bot.get_gps_response(target, sender_id=sender_id, sender_num=sender_num)

        # Comandos de Telemetría y Batería
        if (
            lower.startswith("!telem")
            or lower.startswith("/telem")
            or lower.startswith("!bateria")
            or lower.startswith("/bateria")
            or lower.startswith("!telemetria")
            or lower.startswith("/telemetria")
            or lower.startswith("!bat")
        ):
            parts = raw.split(maxsplit=1)
            target = parts[1].strip() if len(parts) > 1 else ""
            if target.lower() in ("red", "malla", "mesh", "stats", "general"):
                return self.bot.get_mesh_telemetry_summary()
            return self.bot.get_telemetry_response(target, sender_id=sender_id, sender_num=sender_num)

        # Estado de la malla y nodos
        if lower in ("!nodos", "/nodos", "!malla", "!mesh", "!estado", "!red"):
            return self.bot.get_mesh_telemetry_summary()

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
                    return self.bot.query_llm(f"Resume qué es {topic}", context=w_info)
                return f"No se encontró información en Wikipedia sobre {topic}."
            return "Uso: !wiki [tema a consultar]."

        if lower.startswith("!buscar") or lower.startswith("/buscar") or lower.startswith("!search"):
            parts = raw.split(maxsplit=1)
            if len(parts) > 1:
                query = parts[1]
                snippets = search_duckduckgo(query)
                if snippets:
                    return self.bot.query_llm(f"Responde la consulta: {query}", context=snippets)
                return f"No se encontraron resultados para {query}."
            return "Uso: !buscar [consulta]."

        # 2. Detección Inteligente en Lenguaje Natural
        if any(
            w in lower
            for w in [
                "donde estas",
                "dónde estás",
                "donde esta",
                "dónde está",
                "donde queda",
                "dónde queda",
                "ubicacion",
                "ubicación",
                "coordenadas",
                "posicion",
                "posición",
                "gps",
            ]
        ):
            m_target = re.search(
                r"(?:donde esta|dónde está|ubicacion de|ubicación de|posicion de|posición de|coordenadas de)\s+([a-zA-Z0-9_!]+)",
                lower,
            )
            if m_target:
                candidate = m_target.group(1).strip()
                if candidate not in ("tu", "este", "el", "este_nodo", "este nodo", "nodo", "gps", "la", "mi", "un"):
                    target_node, _ = self.bot.find_node(candidate, sender_id=sender_id, sender_num=sender_num)
                    if target_node:
                        return self.bot.get_gps_response(candidate, sender_id=sender_id, sender_num=sender_num)

            if (
                any(
                    w in lower
                    for w in [
                        "donde estas",
                        "dónde estás",
                        "tu ubicacion",
                        "tu ubicación",
                        "tu posicion",
                        "tu posición",
                        "este nodo",
                        "del nodo",
                        "que tienes",
                        "exacta",
                        "coordenadas",
                        "dame la ubicacion",
                        "dame tu ubicacion",
                        "dame la ubicación",
                        "dame tu ubicación",
                        "cual es tu",
                        "cuál es tu",
                        "posicion gps",
                        "posición gps",
                        "ubicacion gps",
                        "ubicación gps",
                    ]
                )
                or "gps" in lower
            ):
                return self.bot.get_gps_response("", sender_id=sender_id, sender_num=sender_num)

        # Detección de Batería / Telemetría
        if any(k in lower for k in ["bateria", "batería", "telemetria", "telemetría", "voltaje", "carga"]):
            m_bat = re.search(
                r"(?:bateria de|batería de|telemetria de|telemetría de|voltaje de)\s+([a-zA-Z0-9_!]+)",
                lower,
            )
            if m_bat:
                candidate = m_bat.group(1).strip()
                if candidate not in ("tu", "este", "el", "este_nodo", "este nodo", "la", "mi", "un", "nodo"):
                    target_node, _ = self.bot.find_node(candidate, sender_id=sender_id, sender_num=sender_num)
                    if target_node:
                        return self.bot.get_telemetry_response(candidate, sender_id=sender_id, sender_num=sender_num)
            return self.bot.get_telemetry_response("", sender_id=sender_id, sender_num=sender_num)

        # Detección de Estado general de la red
        if any(
            k in lower
            for k in ["estado de la malla", "estado de la red", "como esta la red", "cómo está la red", "cuantos nodos"]
        ):
            return self.bot.get_mesh_telemetry_summary()

        # Detección de Clima y Pronóstico en Lenguaje Natural
        if any(
            w in lower
            for w in [
                "clima",
                "temperatura",
                "va a llover",
                "llueve",
                "lluvia",
                "pronóstico",
                "pronostico",
                "tiempo en",
                "que tal el clima",
                "qué tal el clima",
            ]
        ):
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
            if w_info and not w_info.startswith("No se pudo"):
                return w_info

        # Detección de Definición / Pregunta Enciclopédica
        if (
            lower.startswith("que es ")
            or lower.startswith("qué es ")
            or lower.startswith("quien es ")
            or lower.startswith("quién es ")
            or lower.startswith("quien fue ")
            or lower.startswith("quién fue ")
        ):
            term = (
                re.sub(r"^(que es|qué es|quien es|quién es|quien fue|quién fue)\s+", "", lower)
                .replace("?", "")
                .strip()
            )
            w_info = fetch_wikipedia_summary(term)
            if w_info:
                return self.bot.query_llm(raw, context=w_info)

        # Detección de Búsqueda Web / Actualidad
        if any(w in lower for w in ["noticias", "últimas noticias", "ultimas noticias", "busca ", "buscar "]):
            snippets = search_duckduckgo(raw)
            if snippets:
                return self.bot.query_llm(raw, context=snippets)

        # 3. Consulta General a la IA (con inyección contextual de hardware si es relevante)
        if any(
            k in lower
            for k in ["gps", "ubicac", "posic", "coordenad", "altitud", "altura", "bater", "voltaj", "nodo", "malla"]
        ):
            hw_context_items = []
            loc_gps = self.bot.get_gps_response()
            if "no reporta" not in loc_gps and "pendiente" not in loc_gps:
                hw_context_items.append(loc_gps)
            loc_telem = self.bot.get_telemetry_response()
            if "no tiene" not in loc_telem:
                hw_context_items.append(loc_telem)
            if hw_context_items:
                return self.bot.query_llm(raw, context=" ".join(hw_context_items))

        return self.bot.query_llm(raw)
