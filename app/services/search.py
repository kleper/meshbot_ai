"""
Servicios de consulta enciclopédica (Wikipedia API) y búsqueda web (DuckDuckGo Lite) sin API keys.
"""

import logging
import re
import requests

logger = logging.getLogger("MeshtasticBot.Search")


def fetch_wikipedia_summary(query: str) -> str:
    """Busca y extrae el extracto principal de Wikipedia en español sin API key."""
    clean_query = query.strip()
    if not clean_query:
        return ""
    try:
        search_url = "https://es.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "list": "search",
            "srsearch": clean_query,
            "format": "json",
            "utf8": 1,
        }
        r = requests.get(
            search_url,
            params=params,
            headers={"User-Agent": "MeshtasticBot/3.0"},
            timeout=6,
        )
        if r.status_code != 200:
            return ""
        results = r.json().get("query", {}).get("search", [])
        if not results:
            return ""
        title = results[0]["title"]

        sum_url = f"https://es.wikipedia.org/api/rest_v1/page/summary/{title}"
        r_sum = requests.get(
            sum_url,
            headers={"User-Agent": "MeshtasticBot/3.0"},
            timeout=6,
        )
        if r_sum.status_code == 200:
            extract = r_sum.json().get("extract", "")
            return extract.strip()
    except Exception as e:
        logger.warning(f"Error consultando Wikipedia: {e}")
    return ""


def search_duckduckgo(query: str) -> str:
    """Busca en DuckDuckGo HTML Lite y devuelve snippets limpios sin API key."""
    clean_query = query.strip()
    if not clean_query:
        return ""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"
        }
        r = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": clean_query},
            headers=headers,
            timeout=7,
        )
        if r.status_code == 200:
            snippets = re.findall(r'class="result__snippet[^>]*>(.*?)</a>', r.text)
            clean_snippets = [re.sub(r"<[^>]+>", "", s).strip() for s in snippets]
            if clean_snippets:
                return " ".join(clean_snippets[:2])
    except Exception as e:
        logger.warning(f"Error consultando DuckDuckGo: {e}")
    return ""
