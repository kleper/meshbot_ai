"""
Suite de pruebas para el RAG Local (SQLite FTS5 + Sinónimos Rurales),
comandos de canal LoRa (!guardar, !info, !borrar_info) e Interfaz Web / API CRUD (/kb).
"""

from fastapi.testclient import TestClient
from app.api import create_app
from app.config import MAX_PACKET_BYTES
from app.formatter import split_into_packets
from app.mesh_service import MeshtasticAIBot
from app.services.knowledge import LocalKnowledgeService


def test_sqlite_fts5_synonyms_and_diacritics(tmp_path):
    kb = LocalKnowledgeService(data_dir=str(tmp_path))
    kb.add_or_update_item(
        titulo="Chiva Playa Rica - San Roque",
        respuesta="Sale a las 6:00 AM y 1:00 PM con Don Pedro (Cel: 310-555-1234).",
        categoria="transporte",
    )
    kb.add_or_update_item(
        titulo="Puesto de Salud y Médico",
        respuesta="Atención miércoles y viernes de 8am a 4pm en la vereda.",
        categoria="emergencias",
    )

    # Búsqueda usando sinónimo rural ('escalera' -> 'chiva')
    res_syn = kb.search("horario escalera")
    assert len(res_syn) >= 1
    assert "Chiva" in res_syn[0]["titulo"]

    # Búsqueda sin tildes ('medico' -> 'Médico')
    res_no_accent = kb.search("donde atiende el medico")
    assert len(res_no_accent) >= 1
    assert "Salud" in res_no_accent[0]["titulo"]


def test_mesh_channel_commands_save_query_delete(tmp_path):
    bot = MeshtasticAIBot(kb_data_dir=str(tmp_path))
    bot.query_llm = lambda prompt, context="", is_public_directory=False: f"LLM_RAG: {context}"

    # 1. Guardar desde comando en el canal LoRa con formato estándar
    save_reply = bot.process_message(
        "!guardar Moto Don Luis: Cel 311-222-3344 hace expresos a San Roque y Cisneros.",
        sender_id="!a1b2c3d4",
    )
    assert "✅ Guardado en Info Local" in save_reply
    assert "Moto Don Luis" in save_reply

    # 2. Consultar con comando !info
    info_reply = bot.process_message("!info Don Luis")
    assert "📌" in info_reply
    assert "311-222-3344" in info_reply

    # 3. Consultar en lenguaje natural (Modo Determinista Instantáneo <2ms)
    nl_reply = bot.process_message("¿Cuál es el celular de Don Luis el de la moto?")
    assert "311-222-3344" in nl_reply

    # Verificar que los paquetes resultantes cumplan el límite de 228 bytes UTF-8
    packets = split_into_packets(bot.format_output(nl_reply), max_packet_bytes=MAX_PACKET_BYTES)
    assert len(packets) >= 1
    for pkt in packets:
        assert len(pkt.encode("utf-8")) <= MAX_PACKET_BYTES

    # 4. Guardar con categoría explícita y texto libre sin ':'
    save_free = bot.process_message("!agregar [avisos] Convite domingo 8am para arreglar via escuela")
    assert "✅ Guardado en Info Local" in save_free
    assert "avisos" in save_free

    # 5. Borrar ficha por ID desde comando LoRa
    del_reply = bot.process_message("!borrar_info 1")
    assert "🗑️" in del_reply


def test_web_ui_and_kb_crud_endpoints(tmp_path):
    bot = MeshtasticAIBot(kb_data_dir=str(tmp_path))
    app = create_app(bot, start_mesh_on_boot=False)
    client = TestClient(app)

    # 1. Verificar que la Interfaz Web con Tabs HTMX y recursos estáticos carguen correctamente
    r_ui = client.get("/")
    assert r_ui.status_code == 200
    assert "PlayaRica Mesh · Conocimiento Local" in r_ui.text
    assert "/static/js/htmx.min.js" in r_ui.text
    assert "/static/css/styles.css" in r_ui.text

    # Verificar que las pestañas separadas (/ui/agregar y /ui/simulador) respondan tanto en carga completa como vía HTMX
    r_tab_add = client.get("/ui/agregar", headers={"HX-Request": "true"})
    assert r_tab_add.status_code == 200
    assert 'data-active-tab="agregar"' in r_tab_add.text

    r_tab_sim = client.get("/ui/simulador", headers={"HX-Request": "true"})
    assert r_tab_sim.status_code == 200
    assert 'data-active-tab="simulador"' in r_tab_sim.text

    # Verificar envío de formulario HTMX (POST /ui/kb)
    r_htmx_post = client.post(
        "/ui/kb",
        data={
            "categoria": "transporte",
            "titulo": "Expreso Moto Don Jairo",
            "respuesta": "Viajes a San Roque desde las 5am. Cel: 314-111-2233.",
            "keywords": "moto jairo expreso",
            "item_id": "",
        },
        headers={"HX-Request": "true"},
    )
    assert r_htmx_post.status_code == 200
    assert "Expreso Moto Don Jairo" in r_htmx_post.text

    # 2. Crear una ficha por POST /kb (JSON API)
    r_post = client.post(
        "/kb",
        json={
            "categoria": "directorio",
            "titulo": "Doña Marta - Venta de Gas",
            "respuesta": "Pipetas de gas propano a domicilio en Playa Rica. Cel: 312-999-8877.",
            "keywords": "gas pipeta cilindro marta",
            "autor": "web",
        },
    )
    assert r_post.status_code == 200
    created_item = r_post.json()["item"]
    item_id = created_item["id"]
    assert created_item["titulo"] == "Doña Marta - Venta de Gas"

    # 3. Buscar por GET /kb?q=pipeta
    r_search = client.get("/kb?q=pipeta")
    assert r_search.status_code == 200
    items = r_search.json()["items"]
    assert len(items) == 1
    assert "312-999-8877" in items[0]["respuesta"]

    # 4. Actualizar por PUT /kb/{item_id}
    r_put = client.put(
        f"/kb/{item_id}",
        json={
            "categoria": "directorio",
            "titulo": "Doña Marta - Venta de Gas",
            "respuesta": "Pipetas de gas propano de 30 y 40 lb. Cel: 312-999-0000.",
            "autor": "web",
        },
    )
    assert r_put.status_code == 200
    assert "312-999-0000" in r_put.json()["item"]["respuesta"]

    # 5. Probar desde /simulate que el bot responda con el dato actualizado
    r_sim = client.post("/simulate", json={"text": "¿Quién vende pipeta de gas en la vereda?"})
    assert r_sim.status_code == 200
    sim_data = r_sim.json()
    assert "312-999-0000" in sim_data["raw_response"]
    assert all(b <= MAX_PACKET_BYTES for b in sim_data["packet_bytes"])

    # 6. Eliminar por DELETE /kb/{item_id}
    r_del = client.delete(f"/kb/{item_id}")
    assert r_del.status_code == 200
    assert client.get(f"/kb/{item_id}").status_code == 404
