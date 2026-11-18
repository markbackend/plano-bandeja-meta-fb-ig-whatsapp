"""Simulador de Meta para la demo.

Hace de las dos puntas que en producción pone Meta:
1. Los CLIENTES: arma avisos de Messenger, Instagram y WhatsApp, los firma con el secreto de la app y los
   manda al webhook de la bandeja.
2. La GRAPH API: recibe las respuestas que manda la bandeja (/{version}/{id}/messages), contesta los
   perfiles (/{version}/{psid}) y recibe los avisos al equipo (webhook propio y Telegram).
"""

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .eventos import cuerpo_firmado, evento_instagram, evento_messenger, evento_whatsapp

log = logging.getLogger("simulador")

CONTACTOS = [
    {"canal": "messenger", "id": "7001000000000001", "nombre": "Lucía Gómez"},
    {"canal": "instagram", "id": "1784100000000002", "nombre": "tienda.sol", "usuario": "tienda.sol"},
    {"canal": "whatsapp", "id": "5491155550001", "nombre": "Martín Pérez"},
]


class Simulacion(BaseModel):
    canal: str = Field(pattern="^(messenger|instagram|whatsapp)$")
    contacto_id: str = Field(min_length=1, max_length=40)
    texto: str = Field(default="", max_length=2000)
    nombre: str | None = Field(default=None, max_length=60)
    horas_atras: float = Field(default=0, ge=0, le=24 * 30)  # para probar la ventana de 24 h
    adjunto_url: str | None = None


def crear_simulador(bandeja_url: str, app_secret: str, page_id: str = "PAGE_DEMO", ig_id: str = "IG_DEMO",
                    wa_phone_id: str = "WA_DEMO", http: httpx.Client | None = None) -> FastAPI:
    app = FastAPI(title="Simulador de Meta", docs_url=None, redoc_url=None)
    app.state.http = http or httpx.Client(timeout=15)
    registros: list[dict] = []
    canal_de: dict[str, str] = {c["id"]: c["canal"] for c in CONTACTOS}
    nombres: dict[str, str] = {c["id"]: c["nombre"] for c in CONTACTOS}
    lock = threading.Lock()
    app.state.registros = registros

    def anotar(**datos) -> None:
        with lock:
            registros.append({"n": len(registros) + 1, "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                              **datos})

    # ------------------------------------------------------------ página y estado

    @app.get("/", include_in_schema=False)
    def pagina():
        return FileResponse(Path(__file__).parent / "estatico" / "simulador.html",
                            media_type="text/html; charset=utf-8")

    @app.get("/api/estado")
    def estado(desde: int = 0):
        with lock:
            return {"contactos": CONTACTOS, "registros": [r for r in registros if r["n"] > desde]}

    # ------------------------------------------------------------ clientes -> webhook de la bandeja

    @app.post("/api/simular")
    def simular(s: Simulacion):
        if not s.texto and not s.adjunto_url:
            raise HTTPException(400, "Escribí un texto o poné un adjunto")
        ts = time.time() - s.horas_atras * 3600
        adjuntos = [{"tipo": "image", "url": s.adjunto_url}] if s.adjunto_url else None
        canal_de[s.contacto_id] = s.canal
        if s.nombre:
            nombres[s.contacto_id] = s.nombre
        if s.canal == "messenger":
            aviso = evento_messenger(page_id, s.contacto_id, s.texto, ts=ts, adjuntos=adjuntos)
        elif s.canal == "instagram":
            aviso = evento_instagram(ig_id, s.contacto_id, s.texto, ts=ts, adjuntos=adjuntos)
        else:
            aviso = evento_whatsapp(wa_phone_id, s.contacto_id, s.texto, ts=ts,
                                    nombre=s.nombre or nombres.get(s.contacto_id))
        cuerpo, encabezados = cuerpo_firmado(aviso, app_secret)
        try:
            r = app.state.http.post(f"{bandeja_url}/webhook", content=cuerpo, headers=encabezados)
        except httpx.HTTPError as error:
            raise HTTPException(502, f"La bandeja no responde en {bandeja_url}: {error}")
        anotar(tipo="cliente", canal=s.canal, contacto_id=s.contacto_id, texto=s.texto or "[imagen]",
               respuesta_webhook=r.status_code)
        return {"webhook_status": r.status_code, "webhook_respuesta": r.text[:300]}

    # ------------------------------------------------------------ Graph API falsa

    @app.post("/{version}/{cuenta}/messages")
    async def recibir_envio(version: str, cuenta: str, request: Request):
        if not request.headers.get("authorization", "").startswith("Bearer "):
            return _error_graph(401, 190, "Falta el token de acceso")
        datos = await request.json()
        if cuenta == wa_phone_id:
            if datos.get("messaging_product") != "whatsapp" or not datos.get("to"):
                return _error_graph(400, 100, "Cuerpo de WhatsApp inválido")
            wamid = f"wamid.{uuid.uuid4().hex}"
            anotar(tipo="respuesta", canal="whatsapp", contacto_id=datos["to"],
                   texto=(datos.get("text") or {}).get("body", ""), meta_id=wamid)
            return {"messaging_product": "whatsapp", "contacts": [{"input": datos["to"], "wa_id": datos["to"]}],
                    "messages": [{"id": wamid}]}
        destino = (datos.get("recipient") or {}).get("id")
        if not destino or "message" not in datos:
            return _error_graph(400, 100, "Cuerpo de Send API inválido")
        mid = f"m_{uuid.uuid4().hex}"
        anotar(tipo="respuesta", canal=canal_de.get(destino, "messenger"), contacto_id=destino,
               texto=(datos.get("message") or {}).get("text", ""), meta_id=mid)
        return {"recipient_id": destino, "message_id": mid}

    @app.post("/aviso-equipo")
    async def aviso_equipo(request: Request):
        datos = await request.json()
        anotar(tipo="aviso", medio="webhook", canal=datos.get("canal"), contacto_id=str(datos.get("contacto")),
               texto=f"{datos.get('motivo')}: {datos.get('texto')}")
        return {"ok": True}

    @app.post("/bot{token}/sendMessage")
    async def aviso_telegram(token: str, request: Request):
        datos = await request.json()
        anotar(tipo="aviso", medio="telegram", canal="", contacto_id=str(datos.get("chat_id")), texto=datos.get("text"))
        return {"ok": True, "result": {"message_id": len(registros)}}

    @app.get("/{version}/{contacto_id}")
    def perfil(version: str, contacto_id: str, fields: str = ""):
        nombre = nombres.get(contacto_id)
        if not nombre:
            return _error_graph(404, 100, "Perfil no encontrado")
        if "first_name" in fields:
            partes = nombre.split(" ", 1)
            return {"first_name": partes[0], "last_name": partes[1] if len(partes) > 1 else "", "id": contacto_id}
        return {"name": nombre, "username": nombre.replace(" ", ".").lower(), "id": contacto_id}

    return app


def _error_graph(status: int, codigo: int, mensaje: str):
    return JSONResponse({"error": {"message": mensaje, "type": "OAuthException", "code": codigo}}, status_code=status)
