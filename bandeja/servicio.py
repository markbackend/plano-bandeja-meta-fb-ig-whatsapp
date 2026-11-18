"""Lógica de la bandeja: guardar lo que llega, reaccionar (automáticas, avisos) y responder respetando las 24 h."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from .almacen import Almacen, Guardado, de_texto
from .automaticas import Reglas, combinar
from .avisos import Avisador
from .config import Config
from .meta import ClienteMeta, ErrorMeta, FueraDeVentana, explicar_ventana
from .normalizar import Mensaje, leer_evento

log = logging.getLogger("bandeja.servicio")


def ahora_utc() -> datetime:
    return datetime.now(timezone.utc)


class NoEncontrada(Exception):
    """La conversación no existe."""


@dataclass
class Pendiente:
    """Mensaje recién guardado que todavía tiene que disparar automáticas y avisos."""
    mensaje: Mensaje
    guardado: Guardado


class Bandeja:
    def __init__(self, config: Config, almacen: Almacen, meta: ClienteMeta, reglas: Reglas,
                 avisador: Avisador, reloj: Callable[[], datetime] = ahora_utc):
        self.config = config
        self.almacen = almacen
        self.meta = meta
        self.reglas = reglas
        self.avisador = avisador
        self.reloj = reloj

    # ------------------------------------------------------------ entrada (webhook)

    def procesar_aviso(self, aviso: dict) -> list[Pendiente]:
        """Guarda los mensajes del aviso (sin duplicados) y devuelve los que hay que procesar después."""
        mensajes, estados = leer_evento(aviso)
        pendientes = []
        for m in mensajes:
            guardado = self.almacen.guardar_mensaje(m)
            if not guardado.nuevo:
                log.info("Mensaje %s de %s repetido, se ignora", m.id, m.canal)
                continue
            log.info("%s %s de %s (%s): %r", "Eco" if m.saliente else "Mensaje", m.id, m.canal,
                     m.contacto_id, m.texto[:80])
            if not m.saliente:
                pendientes.append(Pendiente(m, guardado))
        for e in estados:
            if self.almacen.actualizar_estado(e.id, e.estado):
                log.info("Mensaje %s: %s%s", e.id, e.estado, f" ({e.error})" if e.error else "")
        return pendientes

    def reaccionar(self, pendientes: list[Pendiente]) -> None:
        """Completa nombres, avisa al equipo y manda respuestas automáticas. Se corre en segundo plano."""
        for p in pendientes:
            for paso in (self._completar_nombre, self._avisar, self._automaticas):
                try:
                    paso(p)
                except Exception:  # un paso que falla no debe frenar a los demás
                    log.exception("Falló %s para el mensaje %s", paso.__name__, p.mensaje.id)

    def _completar_nombre(self, p: Pendiente) -> None:
        if not self.config.consultar_perfiles or p.mensaje.canal == "whatsapp":
            return
        conv = self.almacen.conversacion(p.guardado.conversacion_id)
        if conv and not conv["contacto_nombre"]:
            nombre = self.meta.perfil(conv["canal"], conv["contacto_id"])
            if nombre:
                self.almacen.poner_nombre(conv["id"], nombre)

    def _avisar(self, p: Pendiente) -> None:
        if not self.avisador.activo():
            return
        g = p.guardado
        if g.conversacion_nueva:
            motivo = "Conversación nueva"
        elif g.reabierta:
            motivo = "Volvió a escribir"
        elif self.config.avisar_cada_mensaje:
            motivo = "Mensaje nuevo"
        else:
            return
        conv = self.almacen.conversacion(g.conversacion_id)
        if conv:
            self.avisador.avisar(conv, p.mensaje.texto, motivo)

    def _automaticas(self, p: Pendiente) -> None:
        conv = self.almacen.conversacion(p.guardado.conversacion_id)
        if not conv:
            return
        ahora = self.reloj()
        r = combinar(self.reglas.respuestas_para(
            conv["canal"], p.mensaje.texto, p.guardado.conversacion_nueva, ahora,
            de_texto(conv["aviso_fuera_horario"])))
        if r is None:
            return
        try:
            self._enviar(conv, r.texto, autor=f"automática ({r.tipo})")
        except (FueraDeVentana, ErrorMeta) as error:
            log.warning("No se mandó la respuesta automática %s a %s: %s", r.tipo, conv["contacto_id"], error)
            return
        if "fuera_de_horario" in r.tipo.split("+"):
            self.almacen.marcar_aviso_fuera_horario(conv["id"], ahora)

    # ------------------------------------------------------------ salida (respuestas)

    def ventana_abierta(self, conv: dict) -> bool:
        ultimo = de_texto(conv.get("ultimo_entrante"))
        if ultimo is None:
            return False
        return self.reloj() - ultimo <= timedelta(hours=self.config.ventana_horas)

    def responder(self, conv_id: int, texto: str, autor: str) -> dict:
        """Responde desde la bandeja. Lanza FueraDeVentana sin enviar si pasaron las 24 h."""
        texto = (texto or "").strip()
        if not texto:
            raise ValueError("El mensaje está vacío")
        conv = self.almacen.conversacion(conv_id)
        if not conv:
            raise NoEncontrada(f"No existe la conversación {conv_id}")
        meta_id = self._enviar(conv, texto, autor=autor or "agente")
        return {"meta_id": meta_id, "texto": texto, "autor": autor}

    def _enviar(self, conv: dict, texto: str, autor: str) -> str | None:
        if not self.ventana_abierta(conv):
            raise FueraDeVentana(explicar_ventana(conv["canal"], self.config.ventana_horas))
        meta_id = self.meta.enviar_texto(conv["canal"], conv["contacto_id"], texto)
        self.almacen.guardar_saliente(conv["id"], meta_id, texto, autor, self.reloj(),
                                      automatica=autor.startswith("automática"))
        log.info("Enviado por %s a %s (%s): %s", conv["canal"], conv["contacto_id"], autor, meta_id)
        return meta_id
