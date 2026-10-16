"""
Cliente principal Meshtastic LoRa con:
- Reconexión automática TCP/Serial y vigilancia del hilo lector (_rxThread)
- Cola de trabajo asíncrona (Worker Queue) para nunca bloquear el hilo lector TCP
- Historial circular en memoria (ring buffer de 50 mensajes) para observabilidad vía FastAPI
"""

from collections import deque
import datetime
import logging
import queue
import threading
import time
import meshtastic
import meshtastic.serial_interface
import meshtastic.tcp_interface
from pubsub import pub

from app.config import (
    COOLDOWN_SECONDS,
    DEFAULT_CHANNEL_NAME,
    DEFAULT_HOST,
    DEFAULT_MAX_PARTS,
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_URL,
    DEFAULT_PORT,
    HISTORY_MAX_ITEMS,
    MAX_CHARS,
    MAX_PACKET_BYTES,
    PACKET_PAUSE_SECONDS,
    PREFIX,
)
from app.formatter import clean_text, format_output, split_into_packets
from app.router import MessageRouter
from app.services.llm import LLMService
from app.services.telemetry import TelemetryService

logger = logging.getLogger("MeshtasticBot.Mesh")


class MeshtasticAIBot:
    """Coordinador principal del Bot Meshtastic + Cola de Procesamiento + Servicios."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        serial_port: str = DEFAULT_PORT,
        ollama_url: str = DEFAULT_OLLAMA_URL,
        model: str = DEFAULT_MODEL,
        channel_name: str = DEFAULT_CHANNEL_NAME,
        max_chars: int = MAX_CHARS,
        max_parts: int = DEFAULT_MAX_PARTS,
        cooldown_seconds: int = COOLDOWN_SECONDS,
        packet_pause_seconds: float = PACKET_PAUSE_SECONDS,
    ):
        self.host = host
        self.serial_port = serial_port
        self.ollama_url = ollama_url
        self.model = model
        self.channel_name = channel_name
        self.max_chars = max_chars
        self.max_parts = max_parts
        self.cooldown_seconds = cooldown_seconds
        self.packet_pause_seconds = packet_pause_seconds

        self.interface = None
        self.target_channel_index = None
        self.my_node_num = None
        self.last_reply_time = 0.0
        self.processed_packet_ids: deque[int] = deque(maxlen=500)
        self.message_history: deque[dict] = deque(maxlen=HISTORY_MAX_ITEMS)
        self.connected = False
        self.started_at = time.time()
        self._stop_event = threading.Event()
        self._pubsub_subscribed = False

        # Cola de trabajo para desacoplar la recepción TCP del procesamiento HTTP/LLM y transmisión
        self.rx_queue: queue.Queue = queue.Queue(maxsize=100)
        self._worker_thread: threading.Thread | None = None
        self._connection_thread: threading.Thread | None = None

        # Sub-servicios modulares
        self.llm = LLMService(ollama_url=self.ollama_url, model=self.model)
        self.telemetry = TelemetryService(self)
        self.router = MessageRouter(self)

    # ------------------------------------------------------------------
    # Métodos delegados para compatibilidad total con v2.4
    # ------------------------------------------------------------------
    def clean_text(self, text: str) -> str:
        return clean_text(text)

    def format_output(self, reply: str) -> str:
        return format_output(reply, max_chars=self.max_chars)

    def query_llm(self, prompt_text: str, context: str = "") -> str:
        return self.llm.query_llm(prompt_text, context=context)

    def generate_field_weather_reading(self, rural_brief: str) -> str:
        return self.llm.generate_field_weather_reading(rural_brief)

    def get_local_node_info(self) -> dict:
        return self.telemetry.get_local_node_info()

    def get_local_coordinates(self) -> tuple[float | None, float | None]:
        return self.telemetry.get_local_coordinates()

    def find_node(self, target: str, sender_id: str = "", sender_num: int | None = None) -> tuple[dict, str]:
        return self.telemetry.find_node(target, sender_id=sender_id, sender_num=sender_num)

    def get_gps_response(self, target: str = "", sender_id: str = "", sender_num: int | None = None) -> str:
        return self.telemetry.get_gps_response(target, sender_id=sender_id, sender_num=sender_num)

    def get_nodes_gps_list(self) -> str:
        return self.telemetry.get_nodes_gps_list()

    def get_telemetry_response(self, target: str = "", sender_id: str = "", sender_num: int | None = None) -> str:
        return self.telemetry.get_telemetry_response(target, sender_id=sender_id, sender_num=sender_num)

    def get_mesh_telemetry_summary(self) -> str:
        return self.telemetry.get_mesh_telemetry_summary()

    def get_mesh_status(self) -> str:
        return self.telemetry.get_mesh_telemetry_summary()

    def _build_full_weather_packets(self, lat: float, lon: float, label: str) -> str:
        return self.router.build_full_weather_packets(lat, lon, label)

    def get_weather_response(self, target: str = "", sender_id: str = "", sender_num: int | None = None) -> str:
        return self.router.get_weather_response(target, sender_id=sender_id, sender_num=sender_num)

    def process_message(self, text: str, sender_id: str = "", sender_num: int | None = None) -> str:
        return self.router.process_message(text, sender_id=sender_id, sender_num=sender_num)

    # ------------------------------------------------------------------
    # Registro en historial en memoria y transmisión LoRa
    # ------------------------------------------------------------------
    def record_history(
        self,
        source: str,
        sender_id: str,
        text: str,
        packets: list[str],
        latency_ms: float,
    ) -> dict:
        """Guarda un registro en el ring buffer en memoria (últimos 50 mensajes)."""
        entry = {
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
            "source": source,
            "sender_id": sender_id,
            "input_text": text,
            "packets": packets,
            "packet_bytes": [len(p.encode("utf-8")) for p in packets],
            "packet_count": len(packets),
            "latency_ms": round(latency_ms, 1),
        }
        self.message_history.appendleft(entry)
        return entry

    def send_response(self, reply: str) -> list[str]:
        """
        Transmite la respuesta a la malla LoRa garantizando que ningún paquete
        supere MAX_PACKET_BYTES (228 bytes en UTF-8).
        """
        trimmed = self.format_output(reply)
        packets = split_into_packets(
            trimmed,
            max_packet_bytes=MAX_PACKET_BYTES,
            max_parts=self.max_parts,
        )

        if not packets:
            return []

        total = len(packets)
        for i, pkt in enumerate(packets):
            if i > 0:
                time.sleep(self.packet_pause_seconds)

            pkt_bytes = len(pkt.encode("utf-8"))
            logger.info(
                f"📤 Transmitiendo paquete {i+1}/{total} "
                f"({pkt_bytes} bytes UTF-8, {len(pkt)} chars): '{pkt}'"
            )
            try:
                if self.interface and self.target_channel_index is not None:
                    self.interface.sendText(
                        pkt,
                        channelIndex=self.target_channel_index,
                    )
                    logger.info(f"✅ Paquete {i+1}/{total} transmitido con éxito a la malla LoRa.")
                else:
                    logger.warning("⚠️ Interfaz Meshtastic no disponible al intentar transmitir.")
                    self.connected = False
                    break
            except Exception as e:
                logger.error(f"Error transmitiendo paquete Meshtastic: {e}", exc_info=True)
                self.connected = False
                break

        self.last_reply_time = time.time()
        return packets

    # ------------------------------------------------------------------
    # Recepción no bloqueante y Worker Thread
    # ------------------------------------------------------------------
    def on_connection_lost(self, interface=None, topic=pub.AUTO_TOPIC):
        """Callback invocado cuando Meshtastic detecta desconexión del nodo."""
        logger.warning("⚠️ Evento de desconexión Meshtastic detectado. Marcando para reconexión...")
        self.connected = False

    def on_receive(self, packet, interface):
        """
        Manejador rápido de paquetes recibidos de Meshtastic.
        Filtra y encola en <1ms para NO bloquear el hilo lector TCP (_rxThread).
        """
        try:
            decoded = packet.get("decoded", {})
            if decoded.get("portnum") != "TEXT_MESSAGE_APP":
                return

            sender_num = packet.get("from")
            if sender_num == self.my_node_num:
                return

            channel_idx = packet.get("channel", 0)
            if channel_idx != self.target_channel_index:
                return

            text = decoded.get("text", "").strip()
            if not text:
                return

            pkt_id = packet.get("id")
            if pkt_id:
                if pkt_id in self.processed_packet_ids:
                    return
                self.processed_packet_ids.append(pkt_id)

            sender_id = packet.get("fromId", hex(sender_num) if sender_num else "desconocido")
            logger.info(f"📥 [Canal {self.channel_name}] Mensaje de {sender_id}: '{text}'")

            if text.startswith("[Bot]") or text.startswith(PREFIX) or text.startswith("🤖"):
                return

            now = time.time()
            if (now - self.last_reply_time) < self.cooldown_seconds:
                logger.warning(
                    f"Cooldown activo ({self.cooldown_seconds}s). Descartando respuesta inmediata para proteger airtime."
                )
                return

            try:
                self.rx_queue.put_nowait(
                    {
                        "text": text,
                        "sender_id": sender_id,
                        "sender_num": sender_num,
                        "received_at": now,
                    }
                )
            except queue.Full:
                logger.warning("⚠️ Cola de mensajes llena; descartando paquete entrante.")

        except Exception as e:
            logger.error(f"Error en on_receive: {e}", exc_info=True)

    def _worker_loop(self):
        """Hilo trabajador dedicado que procesa mensajes de la cola y transmite respuestas."""
        logger.info("⚙️ Hilo trabajador (Worker Queue) iniciado.")
        while not self._stop_event.is_set():
            try:
                item = self.rx_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            try:
                t0 = time.time()
                text = item["text"]
                sender_id = item["sender_id"]
                sender_num = item["sender_num"]

                if (t0 - self.last_reply_time) < self.cooldown_seconds:
                    logger.warning("Cooldown activo al desencolar mensaje. Omitiendo.")
                    continue

                raw_response = self.process_message(text, sender_id=sender_id, sender_num=sender_num)
                if raw_response:
                    packets = self.send_response(raw_response)
                    latency_ms = (time.time() - t0) * 1000.0
                    self.record_history(
                        source="lora",
                        sender_id=sender_id,
                        text=text,
                        packets=packets,
                        latency_ms=latency_ms,
                    )
            except Exception as e:
                logger.error(f"Error procesando mensaje en worker: {e}", exc_info=True)
            finally:
                self.rx_queue.task_done()

    def _ensure_worker_started(self):
        if self._worker_thread is None or not self._worker_thread.is_alive():
            self._worker_thread = threading.Thread(
                target=self._worker_loop,
                name="MeshtasticWorker",
                daemon=True,
            )
            self._worker_thread.start()

    # ------------------------------------------------------------------
    # Gestión de conexión TCP/Serial y salud del socket
    # ------------------------------------------------------------------
    def find_channel_index(self) -> int:
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
        self._ensure_worker_started()

        if self.serial_port:
            logger.info(f"Conectando a Meshtastic por puerto serial {self.serial_port}...")
            self.interface = meshtastic.serial_interface.SerialInterface(devPath=self.serial_port)
        else:
            logger.info(f"Conectando a Meshtastic por TCP en {self.host}...")
            self.interface = meshtastic.tcp_interface.TCPInterface(self.host)

        self.my_node_num = self.interface.myInfo.my_node_num
        logger.info(f"Conectado a nodo ID: {self.interface.myInfo.my_node_num}")

        self.target_channel_index = self.find_channel_index()
        if not self._pubsub_subscribed:
            pub.subscribe(self.on_receive, "meshtastic.receive")
            pub.subscribe(self.on_connection_lost, "meshtastic.connection.lost")
            self._pubsub_subscribed = True

        self.connected = True
        logger.info(
            f"🤖 Bot v3.0 escuchando en canal '{self.channel_name}' (idx {self.target_channel_index}). "
            f"Max Chars: {self.max_chars}, Max Parts: {self.max_parts}, Model: {self.model}, "
            f"Airtime Cooldown: {self.cooldown_seconds}s"
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
        self._ensure_worker_started()
        while not self._stop_event.is_set():
            try:
                self.connect()
                while not self._stop_event.is_set() and self._is_interface_healthy():
                    time.sleep(2)
                if self._stop_event.is_set():
                    break
                logger.warning("Reconexión requerida: cerrando interfaz previa y reintentando en 8s...")
                if self.interface:
                    try:
                        self.interface.close()
                    except Exception:
                        pass
                time.sleep(8)
            except KeyboardInterrupt:
                logger.info("Deteniendo bot por solicitud del usuario...")
                self.stop()
                break
            except Exception as e:
                logger.error(f"Conexión con el nodo perdida ({e}). Reconectando en 10s...")
                if self.interface:
                    try:
                        self.interface.close()
                    except Exception:
                        pass
                time.sleep(10)

    def start_background(self):
        """Inicia el bucle de conexión Meshtastic y el Worker en hilos daemon (para FastAPI lifespan)."""
        self._stop_event.clear()
        self._ensure_worker_started()
        if self._connection_thread is None or not self._connection_thread.is_alive():
            self._connection_thread = threading.Thread(
                target=self.run,
                name="MeshtasticConnectionManager",
                daemon=True,
            )
            self._connection_thread.start()

    def stop(self):
        """Detiene limpiamente los hilos y cierra el socket Meshtastic."""
        self._stop_event.set()
        self.connected = False
        if self.interface:
            try:
                self.interface.close()
            except Exception:
                pass
