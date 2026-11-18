"""Aviso al equipo cuando entra un mensaje nuevo: webhook propio (JSON) y/o Telegram."""

import logging

import httpx

from .config import Config

log = logging.getLogger("bandeja.avisos")

ETIQUETA = {"messenger": "Messenger", "instagram": "Instagram", "whatsapp": "WhatsApp"}


class Avisador:
    def __init__(self, config: Config, http: httpx.Client | None = None):
        self.config = config
        self.http = http or httpx.Client(timeout=10)

    def activo(self) -> bool:
        c = self.config
        return bool(c.aviso_webhook_url or (c.telegram_token and c.telegram_chat_id))

    def avisar(self, conversacion: dict, texto: str, motivo: str) -> list[str]:
        """Manda el aviso por los medios configurados. Nunca lanza: un aviso fallido no frena la bandeja.

        Devuelve la lista de medios por los que se avisó bien (útil para pruebas y logs).
        """
        c = self.config
        enviados = []
        canal = ETIQUETA.get(conversacion["canal"], conversacion["canal"])
        contacto = conversacion.get("contacto_nombre") or conversacion["contacto_id"]
        enlace = f"{c.url_publica}/#c{conversacion['id']}" if c.url_publica else ""

        if c.aviso_webhook_url:
            datos = {
                "evento": "mensaje_nuevo", "motivo": motivo, "canal": conversacion["canal"],
                "conversacion_id": conversacion["id"], "contacto": contacto, "texto": texto,
                "agente": conversacion.get("agente"), "enlace": enlace,
            }
            if self._post("webhook", c.aviso_webhook_url, datos):
                enviados.append("webhook")

        if c.telegram_token and c.telegram_chat_id:
            lineas = [f"[{canal}] {motivo}: {contacto}", texto[:500]]
            if enlace:
                lineas.append(enlace)
            url = f"{c.telegram_api_url}/bot{c.telegram_token}/sendMessage"
            if self._post("telegram", url, {"chat_id": c.telegram_chat_id, "text": "\n".join(lineas),
                                            "disable_web_page_preview": True}):
                enviados.append("telegram")
        return enviados

    def _post(self, medio: str, url: str, datos: dict) -> bool:
        try:
            r = self.http.post(url, json=datos)
            if r.status_code >= 400:
                log.warning("El aviso por %s falló: HTTP %s %s", medio, r.status_code, r.text[:200])
                return False
            return True
        except httpx.HTTPError as error:
            log.warning("El aviso por %s falló: %s", medio, error)
            return False
