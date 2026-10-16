"""
Tests unitarios del formateador y particionador de paquetes LoRa (<= 228 bytes UTF-8).
"""

from app.config import MAX_PACKET_BYTES, PACKET_SEP
from app.formatter import clean_text, format_output, split_into_packets, trim_to_complete_sentences


def test_clean_text_removes_markdown_and_urls():
    raw = 'Hola *mundo* #test "cita" https://example.com/ruta\nSegunda línea.'
    cleaned = clean_text(raw)
    assert "*" not in cleaned
    assert "#" not in cleaned
    assert '"' not in cleaned
    assert "http" not in cleaned
    assert "\n" not in cleaned


def test_trim_to_complete_sentences_ends_with_punctuation():
    txt = "Primera oración completa con suficiente longitud. Segunda oración que queda cortada a la mitad"
    res = trim_to_complete_sentences(txt, 60)
    assert res == "Primera oración completa con suficiente longitud."
    assert res.endswith(".")


def test_split_into_packets_with_explicit_packet_sep():
    p1 = "🌦 Playa Rica (San Roque): Nublado 24°C (ST:28°C) Min:19°/Max:27°C Hum:78% Viento:7km/h SE UV:4."
    p2 = "📅 Mañana: Llovizna leve 19-24°C (85% lluvia, 2mm) | Tarde: Llovizna 21-27°C (87%) | Noche: Tormenta."
    p3 = "🌾 Campo: Hoy hará calor al mediodía y caerá aguacero en la noche. Prepare poncho y proteja cosecha."
    combined = f"{p1} {PACKET_SEP} {p2} {PACKET_SEP} {p3}"
    formatted = format_output(combined)
    packets = split_into_packets(formatted, max_packet_bytes=MAX_PACKET_BYTES, max_parts=3)

    assert len(packets) == 3
    assert packets[0].startswith("🤖 [1/3] 🌦")
    assert packets[1].startswith("🤖 [2/3] 📅")
    assert packets[2].startswith("🤖 [3/3] 🌾 Campo:")
    for pkt in packets:
        assert len(pkt.encode("utf-8")) <= MAX_PACKET_BYTES
        assert pkt.endswith((".", "!", "?"))


def test_split_into_packets_never_exceeds_228_bytes_with_emojis_and_accents():
    long_text = (
        "En San Roque Antioquia corregimiento Playa Rica la situación meteorológica presenta "
        "precipitaciones intensas con ráfagas de viento del sureste y sensación térmica elevada. "
        "Durante la jornada de la tarde se recomienda resguardar herramientas agrícolas, ganado "
        "y cosechas de café o cacao ante posibles tormentas eléctricas severas en la montaña. "
        "Por la noche la temperatura bajará considerablemente generando ambiente frío y húmedo."
    )
    packets = split_into_packets(long_text, max_packet_bytes=MAX_PACKET_BYTES, max_parts=3)
    assert 1 <= len(packets) <= 3
    for pkt in packets:
        assert len(pkt.encode("utf-8")) <= MAX_PACKET_BYTES
        assert pkt.endswith((".", "!", "?"))
