"""
Servidor HTTP ligero con FastAPI (puerto 8002 por defecto) para:
- / y /ui: Interfaz Web Local 100% Responsive para gestionar el Conocimiento Comunitario (RAG Local)
- /kb: API CRUD y búsqueda FTS5 BM25 sobre la Base de Conocimiento Local
- /health: Monitoreo real de salud del socket TCP Meshtastic, hilo _rxThread y caché (Docker HEALTHCHECK)
- /nodes: Estado en vivo de los nodos de la malla LoRa (GPS, batería, SNR, distancia) en JSON
- /messages: Últimos 50 mensajes recibidos y respuestas enviadas desde el ring buffer en memoria
- /simulate: Simulador dry-run de comandos y lenguaje natural sin transmitir por radio LoRa
- /send: Envío de alertas o notificaciones externas al canal LoRa PlayaRica vía HTTP
- /cache/weather: Estadísticas y limpieza manual del caché meteorológico TTL (15 min)
"""

from contextlib import asynccontextmanager
import os
import time
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import __version__
from app.config import MAX_PACKET_BYTES
from app.formatter import split_into_packets
from app.mesh_service import MeshtasticAIBot
from app.services.weather import clear_weather_cache, get_weather_cache_stats
from app.ui_routes import create_ui_router

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


class SimulateRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=500, description="Mensaje o comando a simular (ej. '!clima San Roque Antioquia Playa Rica')")
    sender_id: str = Field(default="!sim0001", description="ID simulado del nodo remitente")
    sender_num: int | None = Field(default=None, description="Número opcional de nodo remitente")


class SendMessageRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=650, description="Texto a transmitir al canal LoRa")


class KnowledgeItemRequest(BaseModel):
    id: int | None = Field(default=None, description="ID opcional para actualizar una ficha existente")
    categoria: str = Field(default="directorio", max_length=40, description="Categoría: transporte, directorio, emergencias, avisos, agro")
    titulo: str = Field(..., min_length=2, max_length=120, description="Título corto del registro comunitario")
    respuesta: str = Field(..., min_length=3, max_length=550, description="Información o respuesta que entregará el bot")
    keywords: str = Field(default="", max_length=250, description="Palabras clave o sinónimos opcionales")
    autor: str = Field(default="web", max_length=60, description="Autor u origen del registro")


def create_app(bot: MeshtasticAIBot, start_mesh_on_boot: bool = True) -> FastAPI:
    """Crea y configura la instancia FastAPI vinculada al servicio MeshtasticAIBot."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_mesh_on_boot:
            bot.start_background()
        yield
        if start_mesh_on_boot:
            bot.stop()

    app = FastAPI(
        title="Meshtastic AI Bot API (Canal PlayaRica)",
        description="Interfaz Web Local (HTMX + Jinja2), RAG Comunitario SQLite FTS5, simulador sin airtime y control del Bot LoRa Meshtastic + Hailo-Ollama.",
        version=__version__,
        lifespan=lifespan,
    )

    if os.path.isdir(STATIC_DIR):
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    app.include_router(create_ui_router(bot))

    @app.get("/health", summary="Estado de salud del bot y del socket TCP Meshtastic")
    def get_health():
        healthy = bot._is_interface_healthy()
        uptime = round(time.time() - bot.started_at, 1)
        return {
            "status": "healthy" if healthy else "reconnecting",
            "version": __version__,
            "connected": bot.connected,
            "interface_healthy": healthy,
            "meshtastic_host": bot.host if not bot.serial_port else bot.serial_port,
            "channel_name": bot.channel_name,
            "channel_index": bot.target_channel_index,
            "my_node_num": bot.my_node_num,
            "ollama_model": bot.model,
            "queue_size": bot.rx_queue.qsize(),
            "history_count": len(bot.message_history),
            "weather_cache": get_weather_cache_stats(),
            "knowledge_base": bot.knowledge.get_stats(),
            "uptime_seconds": uptime,
        }

    # ------------------------------------------------------------------
    # Endpoints CRUD de la Base de Conocimiento Comunitario (/kb)
    # ------------------------------------------------------------------
    @app.get("/kb", summary="Listar o buscar fichas de conocimiento comunitario local")
    def list_or_search_kb(
        q: str = Query(default="", description="Consulta de búsqueda FTS5 con sinónimos rurales"),
        categoria: str = Query(default="", description="Filtrar por categoría (transporte, directorio, emergencias, avisos, agro)"),
        limit: int = Query(default=100, ge=1, le=500),
    ):
        total_all = len(bot.knowledge.list_items())
        if q.strip():
            items = bot.knowledge.search(q.strip(), limit=limit, categoria=categoria.strip())
        else:
            items = bot.knowledge.list_items(categoria=categoria.strip())[:limit]
        return {
            "total_count": total_all,
            "count": len(items),
            "query": q,
            "categoria": categoria,
            "items": items,
        }

    @app.get("/kb/{item_id}", summary="Obtener una ficha de conocimiento local por ID")
    def get_kb_item(item_id: int):
        item = bot.knowledge.get_item(item_id)
        if not item:
            raise HTTPException(status_code=404, detail=f"No existe la ficha #{item_id}")
        return item

    @app.post("/kb", summary="Crear o actualizar una ficha de conocimiento local")
    def create_or_update_kb_item(req: KnowledgeItemRequest):
        try:
            saved = bot.knowledge.add_or_update_item(
                titulo=req.titulo,
                respuesta=req.respuesta,
                categoria=req.categoria,
                keywords=req.keywords,
                autor=req.autor,
                item_id=req.id,
            )
            return {"status": "saved", "item": saved}
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.put("/kb/{item_id}", summary="Actualizar una ficha de conocimiento local existente")
    def update_kb_item(item_id: int, req: KnowledgeItemRequest):
        try:
            saved = bot.knowledge.add_or_update_item(
                titulo=req.titulo,
                respuesta=req.respuesta,
                categoria=req.categoria,
                keywords=req.keywords,
                autor=req.autor,
                item_id=item_id,
            )
            return {"status": "updated", "item": saved}
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.delete("/kb/{item_id}", summary="Eliminar una ficha de conocimiento local por ID")
    def delete_kb_item(item_id: int):
        deleted = bot.knowledge.delete_item(item_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"No existe la ficha #{item_id}")
        return {"status": "deleted", "id": item_id}

    @app.get("/nodes", summary="Lista de nodos de la malla con GPS, batería y SNR")
    def get_nodes():
        return bot.telemetry.get_nodes_json()

    @app.get("/messages", summary="Historial en memoria de los últimos 50 mensajes procesados")
    def get_messages():
        return {
            "count": len(bot.message_history),
            "max_items": bot.message_history.maxlen,
            "messages": list(bot.message_history),
        }

    @app.post("/simulate", summary="Simula un mensaje o comando sin transmitir por radio LoRa")
    def simulate_message(req: SimulateRequest):
        t0 = time.time()
        raw_reply = bot.process_message(
            req.text,
            sender_id=req.sender_id,
            sender_num=req.sender_num,
        )
        trimmed = bot.format_output(raw_reply) if raw_reply else ""
        packets = (
            split_into_packets(
                trimmed,
                max_packet_bytes=MAX_PACKET_BYTES,
                max_parts=bot.max_parts,
            )
            if trimmed
            else []
        )
        latency_ms = round((time.time() - t0) * 1000.0, 1)
        bot.record_history(
            source="simulate",
            sender_id=req.sender_id,
            text=req.text,
            packets=packets,
            latency_ms=latency_ms,
        )
        return {
            "input": req.text,
            "raw_response": raw_reply,
            "packets": packets,
            "packet_bytes": [len(p.encode("utf-8")) for p in packets],
            "packet_chars": [len(p) for p in packets],
            "packet_count": len(packets),
            "latency_ms": latency_ms,
            "transmitted_over_lora": False,
        }

    @app.post("/send", summary="Envía un mensaje o alerta directamente al canal LoRa configurado")
    def send_to_mesh(req: SendMessageRequest):
        if not bot._is_interface_healthy():
            raise HTTPException(
                status_code=503,
                detail="El bot no tiene conexión activa con el nodo Meshtastic en este momento.",
            )
        t0 = time.time()
        packets = bot.send_response(req.text)
        latency_ms = round((time.time() - t0) * 1000.0, 1)
        bot.record_history(
            source="api_send",
            sender_id="api",
            text=req.text,
            packets=packets,
            latency_ms=latency_ms,
        )
        return {
            "status": "sent",
            "channel": bot.channel_name,
            "packets": packets,
            "packet_bytes": [len(p.encode("utf-8")) for p in packets],
            "packet_count": len(packets),
        }

    @app.get("/cache/weather", summary="Ver estado del caché meteorológico en memoria")
    def view_weather_cache():
        return get_weather_cache_stats()

    @app.delete("/cache/weather", summary="Limpiar caché meteorológico y de geocodificación")
    def purge_weather_cache():
        return clear_weather_cache()

    return app

