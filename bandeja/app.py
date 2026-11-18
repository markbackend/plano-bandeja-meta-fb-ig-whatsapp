"""Aplicación web: webhook único de Meta + API y página de la bandeja (protegida con clave).

Arranque:  python -m bandeja      (o  uvicorn bandeja.app:crear_app --factory)
"""

import hmac
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .almacen import Almacen
from .automaticas import Reglas
from .avisos import Avisador
from .config import CANALES, Config
from .meta import ClienteMeta, ErrorMeta, FueraDeVentana, explicar_ventana
from .normalizar import firma_valida
from .servicio import Bandeja, NoEncontrada, ahora_utc

log = logging.getLogger("bandeja.app")
ESTATICOS = Path(__file__).resolve().parent / "estatico"


class Respuesta(BaseModel):
    # Cada canal tiene su largo máximo (ver meta.LIMITE_TEXTO): lo que sobra se recorta y queda en el log
    texto: str = Field(min_length=1, max_length=8000)
    autor: str = Field(default="agente", max_length=60)


class CambioEstado(BaseModel):
    estado: Literal["abierta", "resuelta"]


class Asignacion(BaseModel):
    agente: str | None = Field(default=None, max_length=60)


class Nota(BaseModel):
    texto: str = Field(min_length=1, max_length=2000)
    autor: str = Field(default="agente", max_length=60)


def crear_app(config: Config | None = None, http: httpx.Client | None = None,
              reloj: Callable[[], datetime] = ahora_utc) -> FastAPI:
    config = config or Config.desde_entorno()
    http = http or httpx.Client(timeout=15)
    almacen = Almacen(config.db_path)
    bandeja = Bandeja(config, almacen, ClienteMeta(config, http), Reglas(config.reglas_path),
                      Avisador(config, http), reloj)

    app = FastAPI(title="Bandeja Meta (Messenger, Instagram, WhatsApp)", docs_url=None, redoc_url=None)
    app.state.bandeja = bandeja
    app.state.config = config

    def exigir_clave(authorization: str = Header(default="")) -> None:
        clave = authorization.removeprefix("Bearer ").strip()
        if not hmac.compare_digest(clave.encode(), config.clave_bandeja.encode()):
            raise HTTPException(401, "Clave incorrecta")

    def buscar(conv_id: int) -> dict:
        conv = almacen.conversacion(conv_id)
        if not conv:
            raise HTTPException(404, "No existe esa conversación")
        return conv

    # ------------------------------------------------------------ webhook de Meta

    @app.get("/webhook")
    def verificar(modo: str = Query("", alias="hub.mode"), token: str = Query("", alias="hub.verify_token"),
                  desafio: str = Query("", alias="hub.challenge")):
        """Meta llama acá una vez, al configurar el webhook, para confirmar que es nuestro."""
        if modo == "subscribe" and hmac.compare_digest(token.encode(), config.verify_token.encode()):
            log.info("Webhook verificado por Meta")
            return PlainTextResponse(desafio)
        log.warning("Verificación de webhook rechazada (modo=%r)", modo)
        raise HTTPException(403, "Token de verificación incorrecto")

    @app.post("/webhook")
    async def recibir(request: Request, tareas: BackgroundTasks):
        cuerpo = await request.body()
        if not firma_valida(cuerpo, request.headers.get("x-hub-signature-256"), config.app_secret):
            log.warning("Aviso con firma inválida desde %s", request.client.host if request.client else "?")
            raise HTTPException(403, "Firma inválida")
        try:
            aviso = json.loads(cuerpo)
        except ValueError:
            raise HTTPException(400, "El cuerpo no es JSON")
        if not isinstance(aviso, dict):
            raise HTTPException(400, "El cuerpo no es un objeto JSON")
        # Guardar es rápido; lo lento (enviar automáticas, avisos, perfiles) va en segundo plano para
        # contestar 200 enseguida, como pide Meta. Si guardar falla, devolvemos 500 y Meta reintenta.
        pendientes = await run_in_threadpool(bandeja.procesar_aviso, aviso)
        if pendientes:
            tareas.add_task(bandeja.reaccionar, pendientes)
        return {"recibidos": len(pendientes)}

    # ------------------------------------------------------------ página y salud

    @app.get("/", include_in_schema=False)
    def pagina():
        return FileResponse(ESTATICOS / "bandeja.html", media_type="text/html; charset=utf-8")

    @app.get("/salud")
    def salud():
        return {"ok": True, "canales_activos": config.canales_activos()}

    # ------------------------------------------------------------ API de la bandeja

    api = [Depends(exigir_clave)]

    @app.get("/api/config", dependencies=api)
    def ver_config():
        return {"agentes": config.agentes, "canales_activos": config.canales_activos(),
                "ventana_horas": config.ventana_horas}

    @app.get("/api/conversaciones", dependencies=api)
    def listar(estado: Literal["abierta", "resuelta", ""] = "", canal: str = "", agente: str = "",
               buscar_texto: str = Query("", alias="buscar")):
        if canal and canal not in CANALES:
            raise HTTPException(400, f"Canal desconocido: {canal}")
        convs = almacen.listar(estado or None, canal or None, agente or None, buscar_texto.strip() or None)
        return [{**c, "ventana_abierta": bandeja.ventana_abierta(c)} for c in convs]

    @app.get("/api/conversaciones/{conv_id}", dependencies=api)
    def ver(conv_id: int):
        conv = buscar(conv_id)
        almacen.marcar_leida(conv_id)
        abierta = bandeja.ventana_abierta(conv)
        return {
            "conversacion": {**conv, "no_leidos": 0, "ventana_abierta": abierta},
            "aviso_ventana": None if abierta else explicar_ventana(conv["canal"], config.ventana_horas),
            "mensajes": almacen.mensajes(conv_id),
            "notas": almacen.notas(conv_id),
        }

    @app.post("/api/conversaciones/{conv_id}/responder", dependencies=api)
    def responder(conv_id: int, datos: Respuesta):
        try:
            return bandeja.responder(conv_id, datos.texto, datos.autor)
        except NoEncontrada:
            raise HTTPException(404, "No existe esa conversación")
        except ValueError as error:
            raise HTTPException(400, str(error))
        except FueraDeVentana as error:
            raise HTTPException(409, str(error))
        except ErrorMeta as error:
            raise HTTPException(502, str(error))

    @app.post("/api/conversaciones/{conv_id}/estado", dependencies=api)
    def cambiar_estado(conv_id: int, datos: CambioEstado):
        buscar(conv_id)
        almacen.cambiar_estado(conv_id, datos.estado)
        log.info("Conversación %s marcada como %s", conv_id, datos.estado)
        return {"ok": True, "estado": datos.estado}

    @app.post("/api/conversaciones/{conv_id}/asignar", dependencies=api)
    def asignar(conv_id: int, datos: Asignacion):
        buscar(conv_id)
        agente = (datos.agente or "").strip() or None
        if agente and config.agentes and agente not in config.agentes:
            raise HTTPException(400, f"{agente} no está en la lista de AGENTES")
        almacen.asignar(conv_id, agente)
        log.info("Conversación %s asignada a %s", conv_id, agente or "nadie")
        return {"ok": True, "agente": agente}

    @app.post("/api/conversaciones/{conv_id}/notas", dependencies=api)
    def agregar_nota(conv_id: int, datos: Nota):
        buscar(conv_id)
        return almacen.agregar_nota(conv_id, datos.autor.strip() or "agente", datos.texto.strip())

    return app
