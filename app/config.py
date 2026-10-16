"""
Configuración centralizada, constantes de protocolo LoRa y diccionario geográfico local.
"""

import os
from dataclasses import dataclass

# Constantes de protocolo LoRa Meshtastic
MAX_PACKET_BYTES = 228  # Límite seguro inferior al MTU de Meshtastic (DATA_PAYLOAD_LEN = 233)
PACKET_SEP = "||PKT||"
PREFIX = "🤖 "

# Configuración por defecto leída de variables de entorno
DEFAULT_HOST = os.getenv("MESHTASTIC_HOST", "192.168.68.68")
DEFAULT_PORT = os.getenv("MESHTASTIC_PORT", "")
DEFAULT_OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:8000/api/generate")
DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b")
DEFAULT_CHANNEL_NAME = os.getenv("MESHTASTIC_CHANNEL", "PlayaRica")
DEFAULT_LOCATION = os.getenv("DEFAULT_LOCATION", "San Roque Playa Rica")
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "5"))
MAX_CHARS = int(os.getenv("MAX_CHARS", "620"))
DEFAULT_MAX_PARTS = int(os.getenv("MAX_PARTS", "3"))
PACKET_PAUSE_SECONDS = float(os.getenv("PACKET_PAUSE_SECONDS", "4.0"))

# Configuración de FastAPI y Caché
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8002"))
WEATHER_CACHE_TTL = int(os.getenv("WEATHER_CACHE_TTL", "900"))  # 15 minutos en segundos
HISTORY_MAX_ITEMS = int(os.getenv("HISTORY_MAX_ITEMS", "50"))


@dataclass
class BotConfig:
    """Configuración tipada de ejecución para MeshtasticAIBot."""

    host: str = DEFAULT_HOST
    serial_port: str = DEFAULT_PORT
    ollama_url: str = DEFAULT_OLLAMA_URL
    model: str = DEFAULT_MODEL
    channel_name: str = DEFAULT_CHANNEL_NAME
    default_location: str = DEFAULT_LOCATION
    cooldown_seconds: int = COOLDOWN_SECONDS
    max_chars: int = MAX_CHARS
    max_parts: int = DEFAULT_MAX_PARTS
    packet_pause_seconds: float = PACKET_PAUSE_SECONDS
    api_host: str = API_HOST
    api_port: int = API_PORT
    weather_cache_ttl: int = WEATHER_CACHE_TTL
    history_max_items: int = HISTORY_MAX_ITEMS


SYSTEM_PROMPT = (
    "Eres el bot de IA del canal PlayaRica en la red LoRa Meshtastic. "
    "Responde siempre en español de forma directa, útil, amigable y concisa. "
    "REGLAS ESTRICTAS: "
    "1. Expresa 1 o 2 oraciones completas de máximo 300 caracteres. "
    "2. Concluye SIEMPRE tu respuesta con punto final. NUNCA dejes oraciones incompletas ni a medias. "
    "3. No uses formato markdown (sin asteriscos ni almohadillas), sin comillas dobles y sin rodeos."
)

RURAL_WEATHER_SYSTEM_PROMPT = (
    "Eres un asistente rural para campesinos y trabajadores del campo. "
    "Habla claro, sencillo y directo en español. Sin tecnicismos. "
    "Máximo 25 palabras en total, terminando siempre con punto final."
)

WMO_WEATHER_CODES: dict[int, str] = {
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
KNOWN_LOCATIONS: dict[str, tuple[float, float, str]] = {
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
