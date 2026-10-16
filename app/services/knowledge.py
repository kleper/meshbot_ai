"""
Servicio de Conocimiento Comunitario Local (RAG Híbrido Ultraligero de 2 Niveles).
Utiliza SQLite FTS5 (BM25 + unicode61 remove_diacritics 2) + Diccionario de Sinónimos Rurales.
Permite agregar, editar, buscar y eliminar fichas desde:
1. Interfaz Web Local 100% Responsive (GET /) y API REST (/kb)
2. Mensajes en el canal LoRa Meshtastic (!guardar, !agregar, !info, !directorio)
3. Archivo semilla / respaldo sincronizado (data/conocimiento_local.yaml)
"""

import datetime
import logging
import os
import re
import sqlite3
import threading
import unicodedata
import yaml

from app.config import KB_DATA_DIR

logger = logging.getLogger("MeshtasticBot.Knowledge")

VALID_CATEGORIES = ("transporte", "directorio", "emergencias", "avisos", "agro")

CATEGORY_EMOJIS = {
    "transporte": "🚌",
    "directorio": "📇",
    "emergencias": "🚨",
    "avisos": "📢",
    "agro": "🌾",
}

# Diccionario de sinónimos rurales y colombianos para expansión léxica sin costo de CPU/NPU
RURAL_SYNONYMS: dict[str, list[str]] = {
    "chiva": ["chiva", "escalera", "bus", "transporte", "horario", "pasaje", "flota"],
    "escalera": ["chiva", "escalera", "bus", "transporte", "horario"],
    "bus": ["bus", "chiva", "escalera", "flota", "transporte", "horario"],
    "transporte": ["transporte", "chiva", "escalera", "bus", "moto", "mototaxi", "expreso", "acarreo"],
    "horario": ["horario", "hora", "sale", "pasa", "salida", "regresa", "turno", "chiva", "bus"],
    "telefono": ["telefono", "celular", "numero", "contacto", "whatsapp", "llamar", "cel", "tel"],
    "celular": ["celular", "telefono", "numero", "contacto", "whatsapp", "cel", "tel"],
    "numero": ["numero", "telefono", "celular", "contacto", "linea", "cel"],
    "contacto": ["contacto", "telefono", "celular", "numero", "whatsapp"],
    "medico": ["medico", "hospital", "salud", "puesto", "urgencias", "enfermera", "ambulancia", "doctor"],
    "salud": ["salud", "hospital", "medico", "urgencias", "enfermera", "ambulancia", "drogueria"],
    "hospital": ["hospital", "medico", "salud", "urgencias", "ambulancia"],
    "emergencia": ["emergencia", "emergencias", "bomberos", "policia", "urgencia", "rescate", "socorro"],
    "bomberos": ["bomberos", "emergencias", "incendio", "rescate", "ambulancia"],
    "policia": ["policia", "emergencias", "seguridad", "estacion", "cuadrante"],
    "luz": ["luz", "energia", "epm", "apagon", "poste", "transformador", "corte"],
    "energia": ["energia", "luz", "epm", "apagon", "poste", "transformador"],
    "epm": ["epm", "energia", "luz", "apagon", "poste", "daño"],
    "agua": ["agua", "acueducto", "fontanero", "plomero", "tuberia", "corte"],
    "acueducto": ["acueducto", "agua", "fontanero", "plomero", "tuberia"],
    "fontanero": ["fontanero", "plomero", "acueducto", "agua", "tuberia"],
    "plomero": ["plomero", "fontanero", "agua", "acueducto", "tuberia"],
    "junta": ["junta", "jac", "comunal", "presidente", "reunion", "convite", "caseta"],
    "jac": ["jac", "junta", "comunal", "presidente", "reunion", "convite", "caseta"],
    "convite": ["convite", "junta", "jac", "reunion", "comunal", "trabajo"],
    "gas": ["gas", "pipeta", "cilindro", "propano", "domicilio"],
    "pipeta": ["pipeta", "gas", "cilindro", "propano"],
    "acarreo": ["acarreo", "flete", "expreso", "camioneta", "carga", "moto", "transporte"],
    "flete": ["flete", "acarreo", "expreso", "camioneta", "carga"],
    "moto": ["moto", "mototaxi", "expreso", "domicilio", "transporte"],
    "mototaxi": ["mototaxi", "moto", "expreso", "transporte"],
    "cafe": ["cafe", "cana", "panela", "trapiche", "abono", "cultivo", "agro"],
    "cana": ["cana", "panela", "trapiche", "molienda", "cafe", "agro"],
    "trapiche": ["trapiche", "cana", "panela", "molienda", "agro"],
}

STOPWORDS_ES = {
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "al", "a",
    "en", "con", "por", "para", "sin", "sobre", "y", "o", "u", "e", "que", "quien",
    "quienes", "cual", "cuales", "como", "donde", "cuando", "cuanto", "es", "son",
    "esta", "estan", "hay", "tiene", "tienen", "sabe", "saben", "me", "te", "se",
    "nos", "le", "les", "mi", "tu", "su", "sus", "dar", "da", "dame", "regala",
    "regalame", "decir", "dice", "dime", "porfa", "favor", "hola", "buenos",
    "buenas", "dias", "tardes", "noches", "bot", "info", "informacion", "dato",
    "datos", "local", "vereda", "playa", "rica",
}

# Patrones que indican que el usuario pregunta por información local/comunitaria en lenguaje natural
LOCAL_INTENT_KEYWORDS = (
    "chiva",
    "escalera",
    "horario",
    "a que hora sale",
    "a qué hora sale",
    "a que hora pasa",
    "a qué hora pasa",
    "telefono de",
    "teléfono de",
    "celular de",
    "numero de",
    "número de",
    "contacto de",
    "whatsapp de",
    "como llamo a",
    "cómo llamo a",
    "quien vende",
    "quién vende",
    "quien arregla",
    "quién arregla",
    "quien hace",
    "quién hace",
    "donde queda la caseta",
    "junta de accion",
    "junta de acción",
    "fontanero",
    "plomero",
    "acueducto",
    "bomberos",
    "hospital de san roque",
    "puesto de salud",
    "daño de energia",
    "daño de energía",
    "daños epm",
    "pipeta de gas",
    "vende gas",
    "acarreo",
    "mototaxi",
    "convite",
    "don ",
    "doña ",
    "dona ",
)


def strip_accents(text: str) -> str:
    """Elimina tildes y diacríticos manteniendo caracteres alfanuméricos."""
    normalized = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def normalize_tokens(text: str) -> list[str]:
    """Extrae tokens alfanuméricos en minúscula sin tildes ni stopwords."""
    clean = strip_accents(text.lower())
    raw_tokens = re.findall(r"[a-z0-9]{2,}", clean)
    tokens = [t for t in raw_tokens if t not in STOPWORDS_ES]
    return tokens if tokens else raw_tokens


def infer_category(text: str) -> str:
    """Infiere automáticamente la categoría según el contenido del mensaje."""
    low = strip_accents(text.lower())
    if any(k in low for k in ("chiva", "escalera", "bus", "flota", "moto", "mototaxi", "acarreo", "flete", "expreso", "pasaje", "sale a las")):
        return "transporte"
    if any(k in low for k in ("emergencia", "bombero", "policia", "hospital", "salud", "urgencia", "ambulancia", "epm", "incendi")):
        return "emergencias"
    if any(k in low for k in ("reunion", "convite", "jornada", "vacunacion", "evento", "aviso", "mercado", "asamblea")):
        return "avisos"
    if any(k in low for k in ("cafe", "cana", "trapiche", "panela", "abono", "cultivo", "ganado", "cosecha")):
        return "agro"
    return "directorio"


def build_auto_keywords(titulo: str, respuesta: str, categoria: str) -> str:
    """Genera palabras clave enriquecidas automáticamente si el usuario no las especifica."""
    combined = f"{categoria} {titulo} {respuesta}"
    base_tokens = normalize_tokens(combined)
    expanded: list[str] = []
    seen: set[str] = set()
    for tok in base_tokens:
        if tok not in seen:
            seen.add(tok)
            expanded.append(tok)
        for syn in RURAL_SYNONYMS.get(tok, []):
            syn_clean = strip_accents(syn.lower())
            if syn_clean not in seen:
                seen.add(syn_clean)
                expanded.append(syn_clean)
    return " ".join(expanded[:25])


class LocalKnowledgeService:
    """Motor RAG Híbrido con SQLite FTS5, expansión de sinónimos y persistencia YAML + SQLite."""

    def __init__(self, data_dir: str = KB_DATA_DIR):
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)
        self.db_path = os.path.join(self.data_dir, "local_kb.sqlite")
        self.yaml_path = os.path.join(self.data_dir, "conocimiento_local.yaml")
        self._lock = threading.RLock()
        self._init_db()
        self._seed_from_yaml_if_empty()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Crea las tablas items e items_fts (FTS5 unicode61 sin diacríticos) si no existen."""
        with self._lock:
            conn = self._get_conn()
            try:
                with conn:
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS items (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            categoria TEXT NOT NULL DEFAULT 'directorio',
                            titulo TEXT NOT NULL,
                            keywords TEXT NOT NULL DEFAULT '',
                            respuesta TEXT NOT NULL,
                            autor TEXT NOT NULL DEFAULT 'local',
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        )
                        """
                    )
                    conn.execute(
                        """
                        CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
                            titulo,
                            keywords,
                            respuesta,
                            categoria,
                            tokenize='unicode61 remove_diacritics 2'
                        )
                        """
                    )
            finally:
                conn.close()

    def _rebuild_fts(self, conn: sqlite3.Connection) -> None:
        """Sincroniza la tabla virtual FTS5 con los registros de la tabla principal."""
        conn.execute("DELETE FROM items_fts")
        conn.execute(
            """
            INSERT INTO items_fts (rowid, titulo, keywords, respuesta, categoria)
            SELECT id, titulo, keywords, respuesta, categoria FROM items
            """
        )

    def _seed_from_yaml_if_empty(self) -> None:
        """Si la base SQLite está vacía y existe conocimiento_local.yaml, importa los datos iniciales."""
        with self._lock:
            conn = self._get_conn()
            try:
                count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
                if count > 0:
                    self._rebuild_fts(conn)
                    conn.commit()
                    return

                if not os.path.exists(self.yaml_path):
                    return

                with open(self.yaml_path, "r", encoding="utf-8") as f:
                    entries = yaml.safe_load(f) or []

                now = datetime.datetime.now().isoformat(timespec="seconds")
                with conn:
                    for entry in entries:
                        if not isinstance(entry, dict):
                            continue
                        titulo = str(entry.get("titulo", "")).strip()
                        respuesta = str(entry.get("respuesta", "")).strip()
                        if not titulo or not respuesta:
                            continue
                        categoria = str(entry.get("categoria", "")).strip().lower() or infer_category(f"{titulo} {respuesta}")
                        if categoria not in VALID_CATEGORIES:
                            categoria = "directorio"
                        keywords = str(entry.get("keywords", "")).strip()
                        if not keywords:
                            keywords = build_auto_keywords(titulo, respuesta, categoria)
                        autor = str(entry.get("autor", "sistema")).strip()
                        conn.execute(
                            """
                            INSERT INTO items (categoria, titulo, keywords, respuesta, autor, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (categoria, titulo, keywords, respuesta, autor, now, now),
                        )
                    self._rebuild_fts(conn)
                logger.info(f"📚 Base de conocimiento local inicializada con {len(entries)} fichas desde YAML.")
            except Exception as e:
                logger.error(f"Error inicializando conocimiento local desde YAML: {e}")
            finally:
                conn.close()

    def _export_to_yaml(self) -> None:
        """Guarda un respaldo legible en conocimiento_local.yaml tras cada cambio."""
        try:
            items = self.list_items()
            export_list = [
                {
                    "id": it["id"],
                    "categoria": it["categoria"],
                    "titulo": it["titulo"],
                    "keywords": it["keywords"],
                    "respuesta": it["respuesta"],
                    "autor": it["autor"],
                    "updated_at": it["updated_at"],
                }
                for it in items
            ]
            with open(self.yaml_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(export_list, f, allow_unicode=True, sort_keys=False)
        except Exception as e:
            logger.warning(f"No se pudo exportar respaldo YAML: {e}")

    # ------------------------------------------------------------------
    # Operaciones CRUD (Web UI, API REST y Comandos LoRa)
    # ------------------------------------------------------------------
    def list_items(self, categoria: str = "") -> list[dict]:
        """Devuelve todas las fichas ordenadas por actualización más reciente."""
        with self._lock:
            conn = self._get_conn()
            try:
                if categoria and categoria.lower() in VALID_CATEGORIES:
                    rows = conn.execute(
                        "SELECT * FROM items WHERE categoria = ? ORDER BY updated_at DESC, id DESC",
                        (categoria.lower(),),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM items ORDER BY updated_at DESC, id DESC"
                    ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def get_item(self, item_id: int) -> dict | None:
        """Obtiene una ficha por su ID numérico."""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def add_or_update_item(
        self,
        titulo: str,
        respuesta: str,
        categoria: str = "",
        keywords: str = "",
        autor: str = "web",
        item_id: int | None = None,
    ) -> dict:
        """
        Crea o actualiza una ficha de conocimiento local.
        Si no se pasa item_id pero ya existe una ficha con el mismo título normalizado, la actualiza.
        """
        titulo_clean = titulo.strip()
        respuesta_clean = respuesta.strip()
        if not titulo_clean or not respuesta_clean:
            raise ValueError("El título y la respuesta son obligatorios.")

        cat_clean = categoria.strip().lower() if categoria else infer_category(f"{titulo_clean} {respuesta_clean}")
        if cat_clean not in VALID_CATEGORIES:
            cat_clean = infer_category(f"{titulo_clean} {respuesta_clean}")

        kw_clean = keywords.strip() if keywords and keywords.strip() else build_auto_keywords(titulo_clean, respuesta_clean, cat_clean)
        now = datetime.datetime.now().isoformat(timespec="seconds")

        with self._lock:
            conn = self._get_conn()
            try:
                with conn:
                    target_id = item_id
                    if target_id is None:
                        existing = conn.execute(
                            "SELECT id FROM items WHERE lower(titulo) = lower(?)",
                            (titulo_clean,),
                        ).fetchone()
                        if existing:
                            target_id = existing["id"]

                    if target_id is not None:
                        cur = conn.execute(
                            """
                            UPDATE items
                            SET categoria = ?, titulo = ?, keywords = ?, respuesta = ?, autor = ?, updated_at = ?
                            WHERE id = ?
                            """,
                            (cat_clean, titulo_clean, kw_clean, respuesta_clean, autor, now, target_id),
                        )
                        if cur.rowcount == 0:
                            raise KeyError(f"No existe la ficha con ID #{target_id}")
                        saved_id = target_id
                    else:
                        cur = conn.execute(
                            """
                            INSERT INTO items (categoria, titulo, keywords, respuesta, autor, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (cat_clean, titulo_clean, kw_clean, respuesta_clean, autor, now, now),
                        )
                        saved_id = cur.lastrowid

                    self._rebuild_fts(conn)

                saved = self.get_item(int(saved_id))
                self._export_to_yaml()
                return saved or {}
            finally:
                conn.close()

    def delete_item(self, item_id: int) -> bool:
        """Elimina una ficha por su ID numérico y actualiza el índice FTS5."""
        with self._lock:
            conn = self._get_conn()
            try:
                with conn:
                    cur = conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
                    deleted = cur.rowcount > 0
                    if deleted:
                        self._rebuild_fts(conn)
                if deleted:
                    self._export_to_yaml()
                return deleted
            finally:
                conn.close()

    def get_stats(self) -> dict:
        """Estadísticas rápidas de la Base de Conocimiento Local."""
        items = self.list_items()
        by_cat: dict[str, int] = {}
        for it in items:
            cat = it.get("categoria", "directorio")
            by_cat[cat] = by_cat.get(cat, 0) + 1
        return {
            "total_items": len(items),
            "categories": by_cat,
            "db_path": self.db_path,
        }

    # ------------------------------------------------------------------
    # Motor de Búsqueda FTS5 (BM25 + Expansión de Sinónimos Rurales)
    # ------------------------------------------------------------------
    def _build_fts_query(self, raw_query: str) -> tuple[str, list[str]]:
        """
        Construye una expresión FTS5 segura expandiendo cada término con sinónimos rurales.
        Ejemplo: 'horario chiva' -> '(horario* OR hora* OR chiva*) OR (chiva* OR escalera* OR bus*)'
        """
        tokens = normalize_tokens(raw_query)
        if not tokens:
            return "", []

        groups: list[str] = []
        for tok in tokens[:8]:
            synonyms = RURAL_SYNONYMS.get(tok, [tok])
            group_terms: list[str] = []
            seen: set[str] = set()
            for term in [tok] + synonyms:
                clean_t = re.sub(r"[^a-z0-9]", "", strip_accents(term.lower()))
                if len(clean_t) >= 2 and clean_t not in seen:
                    seen.add(clean_t)
                    group_terms.append(f"{clean_t}*")
            if group_terms:
                groups.append("(" + " OR ".join(group_terms) + ")")

        if not groups:
            return "", tokens

        # Unimos grupos con OR para permitir coincidencias parciales y dejamos que BM25 ordene por relevancia
        return " OR ".join(groups), tokens

    def search(self, query: str, limit: int = 5, categoria: str = "") -> list[dict]:
        """
        Ejecuta búsqueda híbrida FTS5 (BM25 ponderando título=5.0, keywords=4.0, respuesta=2.0)
        y calcula cuántos tokens originales del usuario coinciden para determinar confianza.
        """
        q_clean = query.strip()
        if not q_clean:
            return self.list_items(categoria=categoria)[:limit]

        fts_expr, query_tokens = self._build_fts_query(q_clean)
        if not fts_expr:
            return []

        with self._lock:
            conn = self._get_conn()
            try:
                sql = """
                    SELECT
                        i.*,
                        bm25(items_fts, 5.0, 4.0, 2.0, 1.0) AS bm25_raw
                    FROM items_fts
                    JOIN items i ON i.id = items_fts.rowid
                    WHERE items_fts MATCH ?
                """
                params: list = [fts_expr]
                if categoria and categoria.lower() in VALID_CATEGORIES:
                    sql += " AND i.categoria = ?"
                    params.append(categoria.lower())
                sql += " ORDER BY bm25_raw ASC, i.updated_at DESC LIMIT ?"
                params.append(limit)

                rows = conn.execute(sql, params).fetchall()
                results: list[dict] = []
                for r in rows:
                    item = dict(r)
                    haystack = strip_accents(
                        f"{item['titulo']} {item['keywords']} {item['respuesta']} {item['categoria']}".lower()
                    )
                    matched_tokens = 0
                    for tok in query_tokens:
                        syns = [ strip_accents(s.lower()) for s in RURAL_SYNONYMS.get(tok, [tok]) ]
                        if any(s in haystack for s in [tok] + syns):
                            matched_tokens += 1

                    token_coverage = matched_tokens / max(1, len(query_tokens))
                    score = round(abs(float(item.pop("bm25_raw", 0.0))) * (0.5 + token_coverage), 3)
                    item["score"] = score
                    item["matched_tokens"] = matched_tokens
                    item["token_coverage"] = round(token_coverage, 2)
                    results.append(item)

                results.sort(key=lambda x: (x["matched_tokens"], x["score"]), reverse=True)
                return results
            except Exception as e:
                logger.warning(f"Fallo en consulta FTS5 ('{fts_expr}'), usando fallback LIKE: {e}")
                return self._fallback_like_search(conn, query_tokens, limit, categoria)
            finally:
                conn.close()

    def _fallback_like_search(
        self,
        conn: sqlite3.Connection,
        tokens: list[str],
        limit: int,
        categoria: str = "",
    ) -> list[dict]:
        rows = conn.execute("SELECT * FROM items ORDER BY updated_at DESC").fetchall()
        matched: list[dict] = []
        for r in rows:
            item = dict(r)
            if categoria and item["categoria"] != categoria.lower():
                continue
            haystack = strip_accents(f"{item['titulo']} {item['keywords']} {item['respuesta']}".lower())
            hits = sum(1 for t in tokens if t in haystack)
            if hits > 0:
                item["score"] = float(hits)
                item["matched_tokens"] = hits
                item["token_coverage"] = round(hits / max(1, len(tokens)), 2)
                matched.append(item)
        matched.sort(key=lambda x: x["matched_tokens"], reverse=True)
        return matched[:limit]

    # ------------------------------------------------------------------
    # Despacho de 2 Niveles (Directo <2ms vs Contexto RAG para LLM)
    # ------------------------------------------------------------------
    def format_direct_item(self, item: dict) -> str:
        """Formatea una ficha para entrega directa e instantánea por LoRa."""
        resp = item["respuesta"].strip()
        titulo = item["titulo"].strip()
        if resp.lower().startswith(titulo.lower()[:12]):
            return f"📌 {resp}"
        return f"📌 {titulo}: {resp}"

    def query_knowledge(self, query: str, is_explicit_command: bool = False) -> tuple[str, str]:
        """
        Nivel 1 y 2 del RAG Local:
        Retorna una tupla (direct_response, rag_context_for_llm):
        - Si hay alta certeza o comando explícito (!info / !directorio), retorna (direct_response, "") en <2ms.
        - Si es una pregunta abierta con coincidencia parcial/múltiple, retorna ("", rag_context_for_llm).
        - Si no hay coincidencias locales, retorna ("", "").
        """
        q_clean = query.strip()
        if not q_clean:
            items = self.list_items()
            if not items:
                return ("📌 El directorio local aún no tiene registros. Usa !guardar Titulo: Dato.", "")
            titles = ", ".join(f"ID {it['id']} {it['titulo']}" for it in items[:5])
            return (
                f"📌 Directorio Local ({len(items)} fichas): {titles}. Usa !info [tema] o !guardar Titulo: Dato.",
                "",
            )

        # Si busca por #ID exacto (ej. !info #3 o !info 3)
        m_id = re.match(r"^(?:#|id\s*)?(\d+)$", q_clean, flags=re.IGNORECASE)
        if m_id:
            item = self.get_item(int(m_id.group(1)))
            if item:
                return (self.format_direct_item(item), "")
            if is_explicit_command:
                return (f"No existe la ficha ID {m_id.group(1)} en el directorio local.", "")

        results = self.search(q_clean, limit=3)
        if not results:
            if is_explicit_command:
                return (
                    f"No encontré '{q_clean}' en Info Local. Puedes agregarlo enviando: !guardar {q_clean}: [información].",
                    "",
                )
            return ("", "")

        top = results[0]

        # Si es comando explícito (!info, !directorio, !local), siempre respondemos en Modo Determinista (<2ms)
        if is_explicit_command:
            if len(results) == 1 or top["score"] >= results[1]["score"] * 1.25:
                return (self.format_direct_item(top), "")
            # Si hay 2 resultados muy parecidos para una búsqueda genérica (ej. !info transporte)
            combined = f"{self.format_direct_item(top)} | {results[1]['respuesta'].strip()}"
            return (combined, "")

        # En lenguaje natural:
        # Modo 1: Coincidencia Fuerte / Pregunta directa de directorio -> Respuesta Determinista Instantánea (0s NPU)
        has_local_intent = any(k in q_clean.lower() for k in LOCAL_INTENT_KEYWORDS)
        if (top["token_coverage"] >= 0.65 and top["score"] >= 2.5) or (has_local_intent and top["matched_tokens"] >= 1 and top["score"] >= 2.0):
            if len(results) == 1 or top["score"] >= results[1]["score"] * 1.35:
                return (self.format_direct_item(top), "")

        # Modo 2: Coincidencia Media o pregunta compuesta -> Inyectar contexto en qwen3:1.7b
        if top["score"] >= 1.5 or (has_local_intent and top["matched_tokens"] >= 1):
            context_lines = [
                f"- [{r['categoria'].upper()}] {r['titulo']}: {r['respuesta']}"
                for r in results[:2]
            ]
            return ("", "\n".join(context_lines))

        return ("", "")

    # ------------------------------------------------------------------
    # Procesador de Comandos de Guardado desde el Canal LoRa (!guardar)
    # ------------------------------------------------------------------
    def save_from_mesh_command(self, raw_payload: str, sender_id: str = "lora") -> str:
        """
        Permite a cualquier usuario en el canal LoRa agregar un contenido local fácilmente:
        Formatos soportados:
        1. !guardar [categoria] Título: Contenido
        2. !guardar Título: Contenido
        3. !guardar Título | Contenido
        4. !guardar Texto libre (extrae título automáticamente de las primeras palabras)
        """
        body = raw_payload.strip()
        if not body:
            return "Uso: !guardar Titulo: Informacion (Ej: !guardar Moto Don Luis: Cel 3112223344 viajes a San Roque)."

        categoria = ""
        m_cat = re.match(r"^\[([a-zA-ZáéíóúñÁÉÍÓÚÑ]+)\]\s*(.+)$", body)
        if m_cat:
            cand_cat = strip_accents(m_cat.group(1).lower())
            if cand_cat in VALID_CATEGORIES:
                categoria = cand_cat
                body = m_cat.group(2).strip()

        titulo = ""
        respuesta = ""

        if ":" in body:
            parts = body.split(":", 1)
            # Evitar partir por una hora como "6:00 AM" si el ':' está entre dígitos
            if not (parts[0][-1:].isdigit() and parts[1][:1].isdigit()):
                titulo = parts[0].strip()
                respuesta = parts[1].strip()

        if not titulo and "|" in body:
            parts = body.split("|", 1)
            titulo = parts[0].strip()
            respuesta = parts[1].strip()

        if not titulo and " - " in body:
            parts = body.split(" - ", 1)
            titulo = parts[0].strip()
            respuesta = parts[1].strip()

        if not titulo or not respuesta:
            words = body.split()
            if len(words) < 3:
                return "Escribe un poco más de detalle. Ej: !guardar Don Carlos Gas: Vende pipetas cel 3120001122."
            titulo = " ".join(words[:4]).rstrip(",.;:")
            respuesta = body

        saved = self.add_or_update_item(
            titulo=titulo,
            respuesta=respuesta,
            categoria=categoria,
            autor=sender_id or "lora",
        )
        emoji = CATEGORY_EMOJIS.get(saved.get("categoria", "directorio"), "📌")
        return (
            f"✅ Guardado en Info Local (ID {saved['id']} {emoji} {saved['categoria']}): "
            f"{saved['titulo']}. Consúltalo con !info {saved['titulo']}."
        )
