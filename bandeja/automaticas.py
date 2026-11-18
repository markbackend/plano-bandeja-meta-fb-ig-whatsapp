"""Respuestas automáticas: bienvenida, fuera de horario y reglas por palabra clave.

Todo se configura en reglas.json (se puede editar con la bandeja andando: se relee al cambiar).
"""

import json
import logging
import re
import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger("bandeja.automaticas")


@dataclass(frozen=True)
class Respuesta:
    tipo: str   # bienvenida | palabra_clave | fuera_de_horario (o varias unidas con "+")
    texto: str


def combinar(respuestas: list[Respuesta]) -> Respuesta | None:
    """Une las respuestas automáticas en UN solo mensaje, para no mandarle 3 mensajes seguidos al cliente.

    Si corresponde el aviso de fuera de horario, la bienvenida se omite: suele decir "en un rato te responde
    alguien", que contradice al aviso. Orden: saludo, respuesta concreta, aviso de horario.
    """
    tipos = {r.tipo for r in respuestas}
    elegidas = [r for r in respuestas if not (r.tipo == "bienvenida" and "fuera_de_horario" in tipos)]
    if not elegidas:
        return None
    orden = {"bienvenida": 0, "palabra_clave": 1, "fuera_de_horario": 2}
    elegidas.sort(key=lambda r: orden.get(r.tipo, 9))
    return Respuesta("+".join(r.tipo for r in elegidas), "\n\n".join(r.texto for r in elegidas))


def normalizar_texto(texto: str) -> str:
    """Minúsculas y sin tildes, para que "Envío" y "envio" sean lo mismo."""
    sin_tildes = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in sin_tildes if not unicodedata.combining(c)).lower()


def _hora(valor: str) -> time:
    h, m = valor.split(":")
    return time(int(h), int(m))


class Reglas:
    def __init__(self, ruta: Path | str):
        self.ruta = Path(ruta)
        self._datos: dict = {}
        self._mtime: float | None = None
        self._lock = threading.Lock()

    def datos(self) -> dict:
        """Lee reglas.json si cambió. Si está roto, avisa en el log y sigue con las reglas anteriores."""
        with self._lock:
            try:
                mtime = self.ruta.stat().st_mtime
            except FileNotFoundError:
                if self._mtime != -1:
                    log.info("No existe %s: sin respuestas automáticas", self.ruta)
                self._datos, self._mtime = {}, -1
                return self._datos
            if mtime != self._mtime:
                try:
                    datos = json.loads(self.ruta.read_text(encoding="utf-8"))
                    if not isinstance(datos, dict):
                        raise ValueError("el archivo tiene que ser un objeto JSON")
                    self._datos = datos
                    log.info("Reglas cargadas desde %s", self.ruta)
                except (ValueError, OSError) as error:
                    log.error("reglas.json tiene un error (%s): se siguen usando las reglas anteriores", error)
                self._mtime = mtime
            return self._datos

    # ------------------------------------------------------------ horario

    def en_horario(self, ahora: datetime) -> bool:
        datos = self.datos()
        horario = datos.get("horario")
        if not horario:
            return True
        try:
            zona = ZoneInfo(datos.get("zona_horaria", "UTC"))
        except (ZoneInfoNotFoundError, ValueError):
            log.error("zona_horaria inválida en reglas.json, se usa UTC")
            zona = ZoneInfo("UTC")
        local = ahora.astimezone(zona)
        dias = horario.get("dias", [0, 1, 2, 3, 4])  # 0 = lunes
        if local.weekday() not in dias:
            return False
        try:
            desde, hasta = _hora(horario.get("desde", "00:00")), _hora(horario.get("hasta", "23:59"))
        except (ValueError, AttributeError):
            log.error("horario mal escrito en reglas.json (usar \"HH:MM\"), se considera abierto")
            return True
        return desde <= local.time() < hasta

    # ------------------------------------------------------------ respuestas

    @staticmethod
    def _texto_para(bloque: dict, canal: str) -> str | None:
        textos = bloque.get("texto")
        if isinstance(textos, str):
            return textos
        if isinstance(textos, dict):
            return textos.get(canal) or textos.get("todos")
        return None

    def _palabra_clave(self, canal: str, texto: str) -> str | None:
        limpio = normalizar_texto(texto)
        for regla in self.datos().get("palabras_clave", []):
            canales = regla.get("canales") or ["todos"]
            if "todos" not in canales and canal not in canales:
                continue
            for palabra in regla.get("palabras", []):
                patron = r"\b" + re.escape(normalizar_texto(palabra)) + r"\b"
                if re.search(patron, limpio):
                    return self._texto_para({"texto": regla.get("respuesta")}, canal)
        return None

    def respuestas_para(self, canal: str, texto: str, conversacion_nueva: bool, ahora: datetime,
                        ultimo_aviso_fuera: datetime | None = None) -> list[Respuesta]:
        """Qué contestar automáticamente a un mensaje entrante (puede ser nada)."""
        datos = self.datos()
        salida: list[Respuesta] = []

        bienvenida = datos.get("bienvenida") or {}
        if conversacion_nueva and bienvenida.get("activa"):
            t = self._texto_para(bienvenida, canal)
            if t:
                salida.append(Respuesta("bienvenida", t))

        t = self._palabra_clave(canal, texto)
        if t:
            salida.append(Respuesta("palabra_clave", t))

        fuera = datos.get("fuera_de_horario") or {}
        if fuera.get("activa") and not self.en_horario(ahora):
            cada = timedelta(hours=float(fuera.get("repetir_cada_horas", 12)))
            if ultimo_aviso_fuera is None or ahora - ultimo_aviso_fuera >= cada:
                t = self._texto_para(fuera, canal)
                if t:
                    salida.append(Respuesta("fuera_de_horario", t))
        return salida
