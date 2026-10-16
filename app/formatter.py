"""
Formateador de texto y particionador inteligente de paquetes para LoRa Meshtastic.
Garantiza oraciones completas y límite estricto <= 228 bytes UTF-8 por paquete.
"""

import re
from app.config import DEFAULT_MAX_PARTS, MAX_CHARS, MAX_PACKET_BYTES, PACKET_SEP, PREFIX


def clean_text(text: str) -> str:
    """Limpia el texto de bloques <think>, formato Markdown, comillas y saltos para radio LoRa."""
    t = re.sub(r"<think>.*?</think>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    t = re.sub(r"</?think>", " ", t, flags=re.IGNORECASE).strip()
    t = t.replace("\n", " ").replace("\r", " ")
    t = t.replace('"', "").replace("'", "").replace("*", "").replace("#", "")
    t = re.sub(r"https?://\S+", "", t)
    t = " ".join(t.split())
    return t


def trim_to_complete_sentences(text: str, max_allowed: int) -> str:
    """
    Recorta el texto asegurando que SIEMPRE termine en una idea/oración completa
    (con punto '.', '!' o '?') y nunca corte una frase o palabra a la mitad.
    """
    clean = text.strip()
    if not clean:
        return ""

    if len(clean) <= max_allowed and clean.endswith((".", "!", "?")):
        return clean

    candidate = clean[:max_allowed].strip()

    terminators = [m.start() for m in re.finditer(r"[\.\!\?](?:\s|$)", candidate)]
    if terminators:
        last_term = terminators[-1]
        if last_term >= 20:
            return candidate[: last_term + 1].strip()

    if len(clean) <= max_allowed:
        if clean.startswith("¿") and not clean.endswith("?"):
            return clean.rstrip(" ,;:-¡") + "?"
        return clean.rstrip(" ,;:-¿¡") + "."

    last_space = candidate.rfind(" ")
    if last_space > 20:
        return candidate[:last_space].rstrip(" ,;:-¿¡") + "."

    return candidate.rstrip(" ,;:-¿¡") + "."


def format_output(reply: str, max_chars: int = MAX_CHARS) -> str:
    """Limpia y garantiza oraciones completas bajo max_chars o por bloque PACKET_SEP."""
    if PACKET_SEP in reply:
        blocks = [clean_text(b) for b in reply.split(PACKET_SEP) if b.strip()]
        trimmed_blocks = [trim_to_complete_sentences(b, 205) for b in blocks if b]
        return f" {PACKET_SEP} ".join(trimmed_blocks)
    cleaned = clean_text(reply)
    available_chars = max_chars - len(PREFIX)
    return trim_to_complete_sentences(cleaned, available_chars)


def split_into_packets(
    text: str,
    max_packet_bytes: int = MAX_PACKET_BYTES,
    max_parts: int = DEFAULT_MAX_PARTS,
) -> list[str]:
    """
    Divide un texto en paquetes seguros para LoRa Meshtastic (<= max_packet_bytes en UTF-8).
    Si contiene bloques separados por PACKET_SEP ('||PKT||'), asigna cada bloque a su propio paquete.
    Si cabe en 1 paquete, lo devuelve tal cual con prefijo.
    Si excede el límite y max_parts > 1, divide en oraciones completas numeradas ([1/N], [2/N]).
    """
    clean = " ".join(text.strip().split())
    if not clean:
        return []

    if clean.startswith(PREFIX):
        clean = clean[len(PREFIX) :].strip()

    # 0. Si el texto trae bloques explícitos separados por PACKET_SEP (ej. Clima [1/3], [2/3], [3/3])
    if PACKET_SEP in clean:
        parts = [b.strip() for b in clean.split(PACKET_SEP) if b.strip()][:max_parts]
        total = len(parts)
        formatted = []
        for i, p in enumerate(parts, 1):
            p_clean = p.strip()
            if not p_clean.endswith((".", "!", "?")):
                p_clean = p_clean.rstrip(" ,;:-¿¡") + "."
            hdr = f"{PREFIX}[{i}/{total}] " if total > 1 else PREFIX
            pkt = f"{hdr}{p_clean}"
            while len(pkt.encode("utf-8")) > max_packet_bytes:
                last_sp = p_clean.rfind(" ")
                if last_sp == -1:
                    break
                p_clean = p_clean[:last_sp].rstrip(" ,;:-¿¡") + "."
                pkt = f"{hdr}{p_clean}"
            formatted.append(pkt)
        return formatted

    # 1. Si cabe en un solo paquete con prefijo normal
    single_msg = f"{PREFIX}{clean}"
    if len(single_msg.encode("utf-8")) <= max_packet_bytes:
        return [single_msg]

    # 2. Si se fuerza máximo 1 parte (modo paquete único estricto)
    if max_parts <= 1:
        sentences = re.split(r"(?<=[\.\!\?])\s+", clean)
        accum = ""
        for s in sentences:
            test = f"{accum} {s}".strip() if accum else s
            if len(f"{PREFIX}{test}".encode("utf-8")) <= max_packet_bytes:
                accum = test
            else:
                break
        if accum:
            return [f"{PREFIX}{accum}"]

        candidate = clean
        while len(f"{PREFIX}{candidate}".encode("utf-8")) > max_packet_bytes:
            last_space = candidate.rfind(" ")
            if last_space == -1:
                encoded = candidate.encode("utf-8")
                max_txt_bytes = max_packet_bytes - len(PREFIX.encode("utf-8")) - 1
                candidate = encoded[:max_txt_bytes].decode("utf-8", errors="ignore")
                break
            candidate = candidate[:last_space].rstrip(" ,;:-¿¡") + "."
        return [f"{PREFIX}{candidate}"]

    # 3. Multiparte (hasta max_parts)
    sentences = re.split(r"(?<=[\.\!\?])\s+", clean)
    sentences = [s.strip() for s in sentences if s.strip()]

    parts = []
    current_part = ""
    prefix_sample = f"{PREFIX}[1/{max_parts}] "
    max_text_bytes_per_part = max_packet_bytes - len(prefix_sample.encode("utf-8"))

    for s in sentences:
        test_part = f"{current_part} {s}".strip() if current_part else s
        if len(test_part.encode("utf-8")) <= max_text_bytes_per_part:
            current_part = test_part
        else:
            if current_part:
                parts.append(current_part)
                current_part = s
            else:
                words = s.split()
                sub = ""
                for w in words:
                    test_sub = f"{sub} {w}".strip() if sub else w
                    if len(test_sub.encode("utf-8")) <= max_text_bytes_per_part:
                        sub = test_sub
                    else:
                        if sub:
                            parts.append(sub.rstrip(" ,;:-¿¡") + ".")
                            sub = w
                        else:
                            encoded_w = w.encode("utf-8")[:max_text_bytes_per_part].decode("utf-8", errors="ignore")
                            parts.append(encoded_w)
                            sub = ""
                if sub:
                    current_part = sub

    if current_part:
        parts.append(current_part)

    if len(parts) > max_parts:
        parts = parts[:max_parts]

    if len(parts) == 1:
        p_clean = parts[0].strip()
        if not p_clean.endswith((".", "!", "?")):
            p_clean = p_clean.rstrip(" ,;:-¿¡") + "."
        single = f"{PREFIX}{p_clean}"
        if len(single.encode("utf-8")) <= max_packet_bytes:
            return [single]
        while len(f"{PREFIX}{p_clean}".encode("utf-8")) > max_packet_bytes:
            last_sp = p_clean.rfind(" ")
            if last_sp == -1:
                break
            p_clean = p_clean[:last_sp].rstrip(" ,;:-¿¡") + "."
        return [f"{PREFIX}{p_clean}"]

    total = len(parts)
    formatted = []
    for i, p in enumerate(parts, 1):
        p_clean = p.strip()
        if not p_clean.endswith((".", "!", "?")):
            p_clean = p_clean.rstrip(" ,;:-¿¡") + "."
        pkt = f"{PREFIX}[{i}/{total}] {p_clean}"
        while len(pkt.encode("utf-8")) > max_packet_bytes:
            last_sp = p_clean.rfind(" ")
            if last_sp == -1:
                break
            p_clean = p_clean[:last_sp].rstrip(" ,;:-¿¡") + "."
            pkt = f"{PREFIX}[{i}/{total}] {p_clean}"
        formatted.append(pkt)

    return formatted
