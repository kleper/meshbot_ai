"""
Servicio de inferencia LLM local con Hailo-Ollama (NPU Hailo-10H).
Incluye consultas generales y traducción de datos meteorológicos a lenguaje sencillo de campo.
"""

import logging
import re
import requests
from app.config import (
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_URL,
    RURAL_WEATHER_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
)
from app.formatter import clean_text, trim_to_complete_sentences

logger = logging.getLogger("MeshtasticBot.LLM")


class LLMService:
    """Cliente para el servidor local Hailo-Ollama."""

    def __init__(self, ollama_url: str = DEFAULT_OLLAMA_URL, model: str = DEFAULT_MODEL):
        self.ollama_url = ollama_url
        self.model = model

    def query_llm(self, prompt_text: str, context: str = "") -> str:
        """Llama a Hailo-Ollama para generar una respuesta concisa de 1 a 2 oraciones."""
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
                "num_predict": 95,
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
                return clean_text(raw_reply)
            logger.error(f"Error HTTP {r.status_code} desde Ollama: {r.text}")
            return "Error temporal en el motor de IA."
        except Exception as e:
            logger.error(f"Excepción al consultar AI: {e}")
            return "No fue posible conectar con el motor de IA."

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
            "system": RURAL_WEATHER_SYSTEM_PROMPT,
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
                cleaned = clean_text(raw_reply)
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
