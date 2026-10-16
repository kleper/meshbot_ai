"""
Cálculos geodésicos (Haversine, rumbos cardinales) y telemetría de nodos y malla LoRa Meshtastic.
"""

import math
import time


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


class TelemetryService:
    """Consultor de nodos, GPS y telemetría de la interfaz Meshtastic."""

    def __init__(self, bot_ref):
        """Recibe referencia al bot/cliente para acceder dinámicamente a interface y my_node_num."""
        self.bot = bot_ref

    @property
    def interface(self):
        return self.bot.interface

    @property
    def my_node_num(self):
        return self.bot.my_node_num

    @property
    def channel_name(self):
        return self.bot.channel_name

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

    def find_node(self, target: str, sender_id: str = "", sender_num: int | None = None) -> tuple[dict, str]:
        """Localiza un nodo en la base de datos de la malla por identificador flexible."""
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

    def get_gps_response(self, target: str = "", sender_id: str = "", sender_num: int | None = None) -> str:
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

    def get_telemetry_response(self, target: str = "", sender_id: str = "", sender_num: int | None = None) -> str:
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

    def get_nodes_json(self) -> dict:
        """Devuelve un resumen estructurado en JSON de los nodos de la malla para FastAPI (/nodes)."""
        nodes = getattr(self.interface, "nodes", {}) or {}
        loc_lat, loc_lon = self.get_local_coordinates()
        now = time.time()
        node_list = []
        active_24h = 0
        with_gps = 0
        with_telemetry = 0

        for nid, n in nodes.items():
            user = n.get("user", {})
            pos = n.get("position", {})
            dev = n.get("deviceMetrics", {})
            last_heard = n.get("lastHeard", 0)
            if last_heard and (now - last_heard) < 86400:
                active_24h += 1

            lat = pos.get("latitude") or (pos.get("latitudeI", 0) * 1e-7 if "latitudeI" in pos else None)
            lon = pos.get("longitude") or (pos.get("longitudeI", 0) * 1e-7 if "longitudeI" in pos else None)
            has_pos = bool(lat and lon and (lat != 0 or lon != 0))
            if has_pos:
                with_gps += 1
            if dev:
                with_telemetry += 1

            dist_km = None
            bearing = None
            if has_pos and loc_lat and loc_lon and n.get("num") != self.my_node_num:
                dist_km = round(haversine_km(loc_lat, loc_lon, float(lat), float(lon)), 2)
                bearing = bearing_to_cardinal(loc_lat, loc_lon, float(lat), float(lon))

            node_list.append(
                {
                    "id": nid,
                    "num": n.get("num"),
                    "short_name": user.get("shortName"),
                    "long_name": user.get("longName"),
                    "hw_model": user.get("hwModel"),
                    "is_local": n.get("num") == self.my_node_num,
                    "snr": n.get("snr"),
                    "hops_away": n.get("hopsAway"),
                    "last_heard": last_heard,
                    "battery_level": dev.get("batteryLevel"),
                    "voltage": dev.get("voltage"),
                    "channel_utilization": dev.get("channelUtilization"),
                    "air_util_tx": dev.get("airUtilTx"),
                    "latitude": float(lat) if has_pos else None,
                    "longitude": float(lon) if has_pos else None,
                    "altitude": pos.get("altitude") if has_pos else None,
                    "distance_km": dist_km,
                    "bearing": bearing,
                }
            )

        node_list.sort(key=lambda x: x.get("last_heard") or 0, reverse=True)
        return {
            "channel": self.channel_name,
            "total_nodes": len(node_list),
            "active_24h": active_24h,
            "with_gps": with_gps,
            "with_telemetry": with_telemetry,
            "nodes": node_list,
        }
