"""
Tests de geocodificación local, caché TTL de clima, ruteo de comandos y endpoints FastAPI.
"""

from fastapi.testclient import TestClient

from app.api import create_app
from app.mesh_service import MeshtasticAIBot
from app.services.weather import (
    _build_rural_weather_brief,
    clean_location_query,
    clear_weather_cache,
    geocode_location,
    get_cached_weather_packets,
    set_cached_weather_packets,
)


def test_known_locations_san_roque_playa_rica():
    lat, lon, label = geocode_location("San Roque Antioquia Playa Rica")
    assert lat == 6.4062
    assert lon == -75.0161
    assert label == "Playa Rica (San Roque)"


def test_clean_location_query_strips_temporal_words():
    q = "clima en San Roque Antioquia Playa Rica para hoy por la tarde"
    assert clean_location_query(q).lower() == "san roque antioquia playa rica"


def test_weather_ttl_cache_store_and_clear():
    clear_weather_cache()
    assert get_cached_weather_packets(6.4062, -75.0161, "Playa Rica (San Roque)") is None

    sample = "🤖 [1/3] Clima ||PKT|| 📅 Pronóstico ||PKT|| 🌾 Campo: Lleve poncho."
    set_cached_weather_packets(6.4062, -75.0161, "Playa Rica (San Roque)", sample)

    cached = get_cached_weather_packets(6.4062, -75.0161, "Playa Rica (San Roque)")
    assert cached == sample

    stats = clear_weather_cache()
    assert stats["cleared_weather_entries"] == 1
    assert get_cached_weather_packets(6.4062, -75.0161, "Playa Rica (San Roque)") is None


def test_rural_weather_brief_detects_storm_and_heat():
    periods = [
        ("Mañana", [19.0, 22.0], [30, 40], [0.0, 0.2], [2, 3]),
        ("Tarde", [25.0, 28.0], [85, 90], [4.0, 6.0], [81, 95]),
        ("Noche", [18.0, 19.0], [20, 20], [0.0, 0.0], [2, 2]),
    ]
    brief = _build_rural_weather_brief(
        d_min=18,
        d_max=28,
        app_temp=30,
        uv=8,
        d_prob=90,
        d_mm=10.2,
        periods_data=periods,
    )
    assert "calor" in brief.lower()
    assert "aguacero" in brief.lower() or "tormenta" in brief.lower()
    assert "poncho" in brief.lower()


def test_fastapi_endpoints_and_simulator():
    bot = MeshtasticAIBot()
    app = create_app(bot, start_mesh_on_boot=False)
    client = TestClient(app)

    # 1. /health
    r_health = client.get("/health")
    assert r_health.status_code == 200
    data_h = r_health.json()
    assert data_h["channel_name"] == "PlayaRica"
    assert "weather_cache" in data_h

    # 2. /simulate con !ayuda y !hora
    r_sim = client.post("/simulate", json={"text": "!ayuda", "sender_id": "!test1234"})
    assert r_sim.status_code == 200
    sim_data = r_sim.json()
    assert sim_data["transmitted_over_lora"] is False
    assert sim_data["packet_count"] == 1
    assert "!clima" in sim_data["raw_response"]

    # 3. /messages verifica que la simulación quedó registrada en el ring buffer
    r_msgs = client.get("/messages")
    assert r_msgs.status_code == 200
    msgs_data = r_msgs.json()
    assert msgs_data["count"] >= 1
    assert msgs_data["messages"][0]["input_text"] == "!ayuda"

    # 4. /nodes
    r_nodes = client.get("/nodes")
    assert r_nodes.status_code == 200
    assert "nodes" in r_nodes.json()
