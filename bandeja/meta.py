"""Cliente de la Graph API de Meta: enviar respuestas por Messenger, Instagram o WhatsApp y leer perfiles."""

import logging

import httpx

from .config import Config

log = logging.getLogger("bandeja.meta")

# Largo máximo de texto que acepta cada canal (Meta corta o rechaza si se pasa)
LIMITE_TEXTO = {"messenger": 2000, "instagram": 1000, "whatsapp": 4096}

# Errores de Meta que significan "pasaron las 24 horas" (verificar en la documentación vigente)
ERRORES_VENTANA = {
    ("messenger", 10, 2018278), ("instagram", 10, 2018278),
    ("whatsapp", 131047, None),
}


class ErrorMeta(Exception):
    """La Graph API rechazó el pedido o no respondió."""

    def __init__(self, mensaje: str, status: int | None = None, codigo: int | None = None):
        super().__init__(mensaje)
        self.status = status
        self.codigo = codigo


class FueraDeVentana(Exception):
    """Pasaron más de 24 h desde el último mensaje del cliente: Meta no deja mandar texto libre."""


def explicar_ventana(canal: str, horas: int = 24) -> str:
    if canal == "whatsapp":
        return (f"Pasaron más de {horas} h desde el último mensaje del cliente. WhatsApp solo permite enviar "
                "una plantilla aprobada por Meta hasta que el cliente vuelva a escribir. No se envió nada.")
    return (f"Pasaron más de {horas} h desde el último mensaje del cliente. En {canal.capitalize()} hace falta "
            "una etiqueta de mensaje (por ejemplo HUMAN_AGENT, si la app tiene ese permiso aprobado) o esperar "
            "a que el cliente vuelva a escribir. No se envió nada.")


class ClienteMeta:
    def __init__(self, config: Config, http: httpx.Client | None = None):
        self.config = config
        self.http = http or httpx.Client(timeout=15)

    # ------------------------------------------------------------ armado de pedidos

    def _url_y_token(self, canal: str) -> tuple[str, str]:
        c = self.config
        if canal == "messenger":
            return f"{c.graph_url}/{c.api_version}/{c.fb_page_id or 'me'}/messages", c.fb_token
        if canal == "instagram":
            base = c.ig_graph_url or c.graph_url
            return f"{base}/{c.api_version}/me/messages", c.ig_token
        if canal == "whatsapp":
            return f"{c.graph_url}/{c.api_version}/{c.wa_phone_number_id}/messages", c.wa_token
        raise ValueError(f"Canal desconocido: {canal}")

    @staticmethod
    def cuerpo(canal: str, contacto_id: str, texto: str) -> dict:
        if len(texto) > LIMITE_TEXTO[canal]:
            log.warning("El texto para %s tiene %d caracteres: se recorta a %d", canal, len(texto), LIMITE_TEXTO[canal])
            texto = texto[:LIMITE_TEXTO[canal]]
        if canal == "whatsapp":
            return {"messaging_product": "whatsapp", "recipient_type": "individual", "to": contacto_id,
                    "type": "text", "text": {"preview_url": False, "body": texto}}
        # Messenger e Instagram usan la misma Send API. RESPONSE = respuesta dentro de la ventana de 24 h.
        return {"recipient": {"id": contacto_id}, "messaging_type": "RESPONSE", "message": {"text": texto}}

    # ------------------------------------------------------------ envío

    def enviar_texto(self, canal: str, contacto_id: str, texto: str) -> str | None:
        """Envía un texto y devuelve el id que le asignó Meta. Reintenta una vez si Meta está saturado."""
        url, token = self._url_y_token(canal)
        if not token:
            raise ErrorMeta(f"El canal {canal} no tiene token configurado (revisá el .env)")
        cuerpo = self.cuerpo(canal, contacto_id, texto)
        encabezados = {"Authorization": f"Bearer {token}"}
        for intento in range(2):
            try:
                r = self.http.post(url, json=cuerpo, headers=encabezados)
            except httpx.HTTPError as error:
                if intento == 0:
                    log.warning("Error de red enviando por %s (%s), reintento", canal, error)
                    continue
                raise ErrorMeta(f"No se pudo conectar con Meta: {error}") from error
            if r.status_code < 400:
                datos = r.json()
                if canal == "whatsapp":
                    return (datos.get("messages") or [{}])[0].get("id")
                return datos.get("message_id")
            if r.status_code in (429, 500, 502, 503, 504) and intento == 0:
                log.warning("Meta respondió %s enviando por %s, reintento", r.status_code, canal)
                continue
            self._lanzar_error(canal, r)
        return None  # inalcanzable: el bucle siempre devuelve o lanza

    @staticmethod
    def _lanzar_error(canal: str, r: httpx.Response):
        try:
            error = r.json().get("error") or {}
        except ValueError:
            error = {}
        codigo, subcodigo = error.get("code"), error.get("error_subcode")
        mensaje = error.get("message") or r.text[:300] or f"HTTP {r.status_code}"
        if (canal, codigo, subcodigo) in ERRORES_VENTANA or (canal, codigo, None) in ERRORES_VENTANA:
            raise FueraDeVentana(explicar_ventana(canal))
        log.error("Meta rechazó el envío por %s: %s (código %s/%s)", canal, mensaje, codigo, subcodigo)
        raise ErrorMeta(f"Meta rechazó el envío: {mensaje}", status=r.status_code, codigo=codigo)

    # ------------------------------------------------------------ perfiles

    def perfil(self, canal: str, contacto_id: str) -> str | None:
        """Nombre del cliente de Messenger/Instagram (el webhook no lo trae). None si no se puede leer."""
        if canal == "whatsapp":
            return None  # WhatsApp ya manda el nombre en el webhook
        c = self.config
        if canal == "messenger":
            base, token, campos = c.graph_url, c.fb_token, "first_name,last_name"
        else:
            base, token, campos = c.ig_graph_url or c.graph_url, c.ig_token, "name,username"
        if not token:
            return None
        try:
            r = self.http.get(f"{base}/{c.api_version}/{contacto_id}", params={"fields": campos},
                              headers={"Authorization": f"Bearer {token}"})
            if r.status_code >= 400:
                log.info("No se pudo leer el perfil %s de %s (HTTP %s)", contacto_id, canal, r.status_code)
                return None
            d = r.json()
        except (httpx.HTTPError, ValueError) as error:
            log.info("No se pudo leer el perfil %s de %s: %s", contacto_id, canal, error)
            return None
        if canal == "messenger":
            nombre = " ".join(p for p in (d.get("first_name"), d.get("last_name")) if p)
        else:
            nombre = d.get("name") or (f"@{d['username']}" if d.get("username") else "")
        return nombre or None
