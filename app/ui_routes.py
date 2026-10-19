"""
Enrutador modular de Interfaz Web con FastAPI + Jinja2 + HTMX.
Separa en pestañas dedicadas:
1. /ui/directorio (y /): Ver, buscar y filtrar la Base de Conocimiento Local
2. /ui/agregar: Formulario limpio para crear o editar fichas locales
3. /ui/simulador: Probador interactivo de consultas y comandos LoRa
"""

import os
import time
from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import MAX_PACKET_BYTES
from app.formatter import split_into_packets
from app.mesh_service import MeshtasticAIBot

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

CATEGORY_LABELS = {
    "transporte": "Transporte",
    "directorio": "Directorio",
    "emergencias": "Emergencias",
    "avisos": "Avisos",
    "agro": "Agro",
}


def create_ui_router(bot: MeshtasticAIBot) -> APIRouter:
    """Crea el APIRouter con todas las vistas HTML y fragmentos parciales HTMX."""
    router = APIRouter()

    def _base_context(request: Request, active_tab: str, tab_template: str, extra: dict | None = None) -> dict:
        ctx = {
            "request": request,
            "active_tab": active_tab,
            "tab_template": tab_template,
            "interface_healthy": bot._is_interface_healthy(),
            "channel_name": bot.channel_name,
            "ollama_model": bot.model,
            "total_kb_items": len(bot.knowledge.list_items()),
            "category_labels": CATEGORY_LABELS,
        }
        if extra:
            ctx.update(extra)
        return ctx

    def _render_tab_or_full(request: Request, active_tab: str, tab_template: str, extra: dict | None = None) -> HTMLResponse:
        ctx = _base_context(request, active_tab, tab_template, extra)
        is_htmx = request.headers.get("HX-Request") == "true"
        template_name = tab_template if is_htmx else "base.html"
        return templates.TemplateResponse(request, template_name, ctx)

    # ------------------------------------------------------------------
    # Pestaña 1: Base de Conocimiento (Directorio y Búsqueda FTS5)
    # ------------------------------------------------------------------
    @router.get("/", response_class=HTMLResponse, include_in_schema=False)
    @router.get("/ui", response_class=HTMLResponse, include_in_schema=False)
    @router.get("/ui/directorio", response_class=HTMLResponse, summary="Vista Web: Base de Conocimiento Local")
    def view_directory(
        request: Request,
        q: str = Query(default=""),
        categoria: str = Query(default=""),
    ):
        items = (
            bot.knowledge.search(q.strip(), limit=100, categoria=categoria.strip())
            if q.strip()
            else bot.knowledge.list_items(categoria=categoria.strip())
        )
        return _render_tab_or_full(
            request,
            active_tab="directorio",
            tab_template="partials/tab_directory.html",
            extra={
                "items": items,
                "query": q,
                "selected_categoria": categoria,
                "flash_message": "",
            },
        )

    # ------------------------------------------------------------------
    # Pestaña 2: Agregar o Editar Contenido Local
    # ------------------------------------------------------------------
    @router.get("/ui/agregar", response_class=HTMLResponse, summary="Vista Web: Agregar o Editar Contenido Local")
    def view_add_or_edit_form(
        request: Request,
        edit_id: int | None = Query(default=None),
    ):
        edit_item = bot.knowledge.get_item(edit_id) if edit_id is not None else None
        return _render_tab_or_full(
            request,
            active_tab="agregar",
            tab_template="partials/tab_form.html",
            extra={"edit_item": edit_item},
        )

    # ------------------------------------------------------------------
    # Pestaña 3: Simulador de Consultas y Comandos LoRa
    # ------------------------------------------------------------------
    @router.get("/ui/simulador", response_class=HTMLResponse, summary="Vista Web: Simulador del Bot LoRa")
    def view_simulator(
        request: Request,
        q: str = Query(default=""),
    ):
        sim_result = None
        if q.strip():
            sim_result = _run_simulation(q.strip())
        return _render_tab_or_full(
            request,
            active_tab="simulador",
            tab_template="partials/tab_simulator.html",
            extra={
                "initial_query": q.strip(),
                "sim_result": sim_result,
            },
        )

    # ------------------------------------------------------------------
    # Fragmentos Parciales HTMX (Búsqueda en vivo, Estado, CRUD, Simulación)
    # ------------------------------------------------------------------
    @router.get("/ui/partials/status", response_class=HTMLResponse, include_in_schema=False)
    def htmx_status_bar(request: Request):
        ctx = _base_context(request, active_tab="", tab_template="partials/status_bar.html")
        return templates.TemplateResponse(request, "partials/status_bar.html", ctx)

    @router.get("/ui/partials/kb-cards", response_class=HTMLResponse, include_in_schema=False)
    def htmx_kb_cards(
        request: Request,
        q: str = Query(default=""),
        categoria: str = Query(default=""),
    ):
        items = (
            bot.knowledge.search(q.strip(), limit=100, categoria=categoria.strip())
            if q.strip()
            else bot.knowledge.list_items(categoria=categoria.strip())
        )
        ctx = {
            "request": request,
            "items": items,
            "category_labels": CATEGORY_LABELS,
        }
        return templates.TemplateResponse(request, "partials/kb_cards.html", ctx)

    @router.post("/ui/kb", response_class=HTMLResponse, include_in_schema=False)
    def htmx_save_kb_item(
        request: Request,
        titulo: str = Form(...),
        respuesta: str = Form(...),
        categoria: str = Form(default="directorio"),
        keywords: str = Form(default=""),
        item_id: str = Form(default=""),
    ):
        parsed_id = int(item_id) if item_id.strip().isdigit() else None
        saved = bot.knowledge.add_or_update_item(
            titulo=titulo,
            respuesta=respuesta,
            categoria=categoria,
            keywords=keywords,
            autor="web",
            item_id=parsed_id,
        )
        items = bot.knowledge.list_items()
        flash_msg = f"Ficha #{saved['id']} ({saved['titulo']}) guardada correctamente."
        return _render_tab_or_full(
            request,
            active_tab="directorio",
            tab_template="partials/tab_directory.html",
            extra={
                "items": items,
                "query": "",
                "selected_categoria": "",
                "flash_message": flash_msg,
            },
        )

    @router.delete("/ui/kb/{item_id}", response_class=HTMLResponse, include_in_schema=False)
    def htmx_delete_kb_item(request: Request, item_id: int):
        bot.knowledge.delete_item(item_id)
        items = bot.knowledge.list_items()
        ctx = {
            "request": request,
            "items": items,
            "category_labels": CATEGORY_LABELS,
        }
        return templates.TemplateResponse(request, "partials/kb_cards.html", ctx)

    def _run_simulation(text: str) -> dict:
        t0 = time.time()
        raw_reply = bot.process_message(text, sender_id="!web_sim")
        trimmed = bot.format_output(raw_reply) if raw_reply else ""
        packets = (
            split_into_packets(trimmed, max_packet_bytes=MAX_PACKET_BYTES, max_parts=bot.max_parts)
            if trimmed
            else []
        )
        latency_ms = round((time.time() - t0) * 1000.0, 1)
        bot.record_history(
            source="web_simulate",
            sender_id="!web_sim",
            text=text,
            packets=packets,
            latency_ms=latency_ms,
        )
        return {
            "input": text,
            "raw_response": raw_reply,
            "packets": packets,
            "packet_bytes": [len(p.encode("utf-8")) for p in packets],
            "packet_count": len(packets),
            "latency_ms": latency_ms,
        }

    @router.post("/ui/simulate", response_class=HTMLResponse, include_in_schema=False)
    def htmx_simulate(request: Request, text: str = Form(...)):
        sim_result = _run_simulation(text.strip())
        ctx = {"request": request, "sim_result": sim_result}
        return templates.TemplateResponse(request, "partials/sim_result.html", ctx)

    return router
