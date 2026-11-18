"""Lectura de los avisos del webhook de Meta y conversión a un formato común.

Los tres canales llegan al mismo webhook pero con formatos distintos:
- Messenger:  {"object": "page",       "entry": [{"id": PAGE_ID, "messaging": [...]}]}
- Instagram:  {"object": "instagram",  "entry": [{"id": IG_ID,   "messaging": [...]}]}
- WhatsApp:   {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {...}}]}]}
"""

import hashlib
import hmac
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

log = logging.getLogger("bandeja.normalizar")

OBJETO_A_CANAL = {"page": "messenger", "instagram": "instagram", "whatsapp_business_account": "whatsapp"}


@dataclass(frozen=True)
class Mensaje:
    """Mensaje en formato común, sin importar de qué canal vino."""
    id: str                      # id de Meta (mid en Messenger/Instagram, wamid en WhatsApp): evita duplicados
    canal: str                   # messenger | instagram | whatsapp
    cuenta_id: str               # página, cuenta de Instagram o número de WhatsApp que recibió el mensaje
    contacto_id: str             # PSID, IGSID o número de WhatsApp del cliente
    contacto_nombre: str | None
    texto: str
    adjuntos: tuple[dict, ...] = field(default_factory=tuple)  # [{"tipo": "image", "url": ..., "id": ...}]
    fecha: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    saliente: bool = False       # eco de un mensaje que mandó la página (desde otra herramienta)


@dataclass(frozen=True)
class EstadoEntrega:
    """Estado de un mensaje enviado (WhatsApp avisa sent / delivered / read / failed)."""
    id: str
    estado: str
    error: str | None = None


def firma_valida(cuerpo: bytes, firma: str | None, app_secret: str) -> bool:
    """Meta firma cada aviso con HMAC-SHA256 del cuerpo crudo usando el secreto de la app."""
    if not firma or not firma.startswith("sha256=") or not app_secret:
        return False
    esperada = hmac.new(app_secret.encode(), cuerpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperada, firma.removeprefix("sha256="))


def firmar(cuerpo: bytes, app_secret: str) -> str:
    """Genera el encabezado X-Hub-Signature-256 (lo usan el simulador y las pruebas)."""
    return "sha256=" + hmac.new(app_secret.encode(), cuerpo, hashlib.sha256).hexdigest()


def _fecha_ms(valor) -> datetime:
    try:
        return datetime.fromtimestamp(int(valor) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def _fecha_s(valor) -> datetime:
    try:
        return datetime.fromtimestamp(int(valor), tz=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def leer_evento(aviso: dict) -> tuple[list[Mensaje], list[EstadoEntrega]]:
    """Devuelve los mensajes y estados de entrega que trae un aviso. Ignora lo que no entiende."""
    canal = OBJETO_A_CANAL.get(aviso.get("object", ""))
    if canal is None:
        log.info("Aviso ignorado: object=%r no es de Messenger, Instagram ni WhatsApp", aviso.get("object"))
        return [], []
    mensajes: list[Mensaje] = []
    estados: list[EstadoEntrega] = []
    for entrada in aviso.get("entry") or []:
        if canal == "whatsapp":
            m, e = _leer_whatsapp(entrada)
            mensajes += m
            estados += e
        else:
            mensajes += _leer_messaging(canal, entrada)
    return mensajes, estados


# ---------------------------------------------------------------- Messenger e Instagram

def _leer_messaging(canal: str, entrada: dict) -> list[Mensaje]:
    cuenta = str(entrada.get("id", ""))
    salida = []
    for item in entrada.get("messaging") or []:
        emisor = str((item.get("sender") or {}).get("id", ""))
        receptor = str((item.get("recipient") or {}).get("id", ""))
        fecha = _fecha_ms(item.get("timestamp"))
        if "message" in item:
            m = item["message"] or {}
            if m.get("is_deleted") or m.get("is_unsupported"):
                continue
            if not m.get("mid"):
                log.warning("Mensaje de %s sin mid, se ignora", canal)
                continue
            eco = bool(m.get("is_echo"))
            adjuntos = tuple(
                {"tipo": a.get("type", "archivo"), "url": (a.get("payload") or {}).get("url")}
                for a in m.get("attachments") or []
            )
            texto = m.get("text") or ""
            if not texto and not adjuntos:
                texto = "[mensaje sin texto]"
            salida.append(Mensaje(
                id=m["mid"], canal=canal, cuenta_id=cuenta,
                contacto_id=receptor if eco else emisor, contacto_nombre=None,
                texto=texto, adjuntos=adjuntos, fecha=fecha, saliente=eco,
            ))
        elif "postback" in item:  # el cliente tocó un botón
            p = item["postback"] or {}
            salida.append(Mensaje(
                id=p.get("mid") or f"postback-{emisor}-{item.get('timestamp')}", canal=canal, cuenta_id=cuenta,
                contacto_id=emisor, contacto_nombre=None,
                texto=p.get("title") or p.get("payload") or "[botón]", fecha=fecha,
            ))
        # read, delivery, reaction, optin, etc.: no son mensajes nuevos
    return salida


# ---------------------------------------------------------------- WhatsApp

TIPOS_ARCHIVO_WA = ("image", "audio", "video", "document", "sticker")


def _texto_whatsapp(m: dict) -> tuple[str, tuple[dict, ...]]:
    tipo = m.get("type")
    if tipo == "text":
        return (m.get("text") or {}).get("body", ""), ()
    if tipo in TIPOS_ARCHIVO_WA:
        datos = m.get(tipo) or {}
        adjunto = {"tipo": tipo, "id": datos.get("id"), "mime": datos.get("mime_type")}
        if datos.get("filename"):
            adjunto["nombre"] = datos["filename"]
        return datos.get("caption") or "", (adjunto,)
    if tipo == "location":
        u = m.get("location") or {}
        nombre = u.get("name") or u.get("address") or ""
        return f"[ubicación] {nombre} {u.get('latitude')},{u.get('longitude')}".replace("  ", " "), (
            {"tipo": "location", "url": f"https://maps.google.com/?q={u.get('latitude')},{u.get('longitude')}"},)
    if tipo == "interactive":
        i = m.get("interactive") or {}
        r = i.get("button_reply") or i.get("list_reply") or {}
        return r.get("title") or "[respuesta interactiva]", ()
    if tipo == "button":
        return (m.get("button") or {}).get("text") or "[botón]", ()
    if tipo == "contacts":
        return "[contacto compartido]", ()
    return f"[{tipo or 'mensaje'} no soportado]", ()


def _leer_whatsapp(entrada: dict) -> tuple[list[Mensaje], list[EstadoEntrega]]:
    mensajes, estados = [], []
    for cambio in entrada.get("changes") or []:
        if cambio.get("field") not in (None, "messages"):
            continue
        valor = cambio.get("value") or {}
        cuenta = str((valor.get("metadata") or {}).get("phone_number_id", ""))
        nombres = {c.get("wa_id"): (c.get("profile") or {}).get("name") for c in valor.get("contacts") or []}
        for m in valor.get("messages") or []:
            if m.get("type") == "reaction" or not m.get("id") or not m.get("from"):
                continue
            texto, adjuntos = _texto_whatsapp(m)
            mensajes.append(Mensaje(
                id=m["id"], canal="whatsapp", cuenta_id=cuenta,
                contacto_id=m["from"], contacto_nombre=nombres.get(m["from"]),
                texto=texto, adjuntos=adjuntos, fecha=_fecha_s(m.get("timestamp")),
            ))
        for s in valor.get("statuses") or []:
            if not s.get("id"):
                continue
            error = None
            if s.get("errors"):
                e = s["errors"][0]
                error = f"{e.get('code')}: {e.get('title') or e.get('message')}"
            estados.append(EstadoEntrega(id=s["id"], estado=s.get("status", "desconocido"), error=error))
    return mensajes, estados
