"""Arma avisos de webhook iguales a los que manda Meta, para la demo y las pruebas.

Los formatos siguen la documentación de Meta (Messenger Platform, Instagram Messaging y WhatsApp Cloud API).
Si Meta cambia algún campo, actualizar acá y en bandeja/normalizar.py.
"""

import json
import time
import uuid

from bandeja.normalizar import firmar


def _ms(ts: float | None) -> int:
    return int((ts if ts is not None else time.time()) * 1000)


def _messaging(objeto: str, cuenta_id: str, contacto_id: str, texto: str, mid: str | None,
               ts: float | None, adjuntos: list[dict] | None, eco: bool) -> dict:
    mensaje: dict = {"mid": mid or f"m_{uuid.uuid4().hex}"}
    if texto:
        mensaje["text"] = texto
    if adjuntos:
        mensaje["attachments"] = [{"type": a.get("tipo", "image"), "payload": {"url": a.get("url")}} for a in adjuntos]
    if eco:
        mensaje["is_echo"] = True
    emisor, receptor = (cuenta_id, contacto_id) if eco else (contacto_id, cuenta_id)
    return {"object": objeto, "entry": [{
        "id": cuenta_id, "time": _ms(ts),
        "messaging": [{"sender": {"id": emisor}, "recipient": {"id": receptor},
                       "timestamp": _ms(ts), "message": mensaje}],
    }]}


def evento_messenger(page_id: str, psid: str, texto: str, mid: str | None = None, ts: float | None = None,
                     adjuntos: list[dict] | None = None, eco: bool = False) -> dict:
    return _messaging("page", page_id, psid, texto, mid, ts, adjuntos, eco)


def evento_instagram(ig_id: str, igsid: str, texto: str, mid: str | None = None, ts: float | None = None,
                     adjuntos: list[dict] | None = None, eco: bool = False) -> dict:
    return _messaging("instagram", ig_id, igsid, texto, mid, ts, adjuntos, eco)


def evento_whatsapp(phone_number_id: str, numero: str, texto: str, nombre: str | None = None,
                    wamid: str | None = None, ts: float | None = None, tipo: str = "text",
                    extra: dict | None = None) -> dict:
    mensaje: dict = {"from": numero, "id": wamid or f"wamid.{uuid.uuid4().hex}",
                     "timestamp": str(int(ts if ts is not None else time.time())), "type": tipo}
    if tipo == "text":
        mensaje["text"] = {"body": texto}
    elif extra is not None:
        mensaje[tipo] = extra
    return {"object": "whatsapp_business_account", "entry": [{
        "id": "WABA_ID",
        "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "5491100000000", "phone_number_id": phone_number_id},
            "contacts": [{"profile": {"name": nombre or numero}, "wa_id": numero}],
            "messages": [mensaje],
        }}],
    }]}


def estado_whatsapp(phone_number_id: str, wamid: str, estado: str, numero: str = "") -> dict:
    return {"object": "whatsapp_business_account", "entry": [{
        "id": "WABA_ID",
        "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "5491100000000", "phone_number_id": phone_number_id},
            "statuses": [{"id": wamid, "status": estado, "timestamp": str(int(time.time())), "recipient_id": numero}],
        }}],
    }]}


def cuerpo_firmado(aviso: dict, app_secret: str) -> tuple[bytes, dict]:
    """Devuelve el cuerpo crudo y los encabezados con la firma X-Hub-Signature-256, como lo manda Meta."""
    cuerpo = json.dumps(aviso, ensure_ascii=False).encode("utf-8")
    return cuerpo, {"Content-Type": "application/json", "X-Hub-Signature-256": firmar(cuerpo, app_secret)}
