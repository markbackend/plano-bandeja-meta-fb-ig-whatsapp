"""Configuración por variables de entorno (archivo .env en la raíz del proyecto)."""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
log = logging.getLogger("bandeja.config")

CANALES = ("messenger", "instagram", "whatsapp")


class ErrorConfig(Exception):
    """Falta o está mal una variable de entorno obligatoria."""


def _bool(valor: str | None, defecto: bool = False) -> bool:
    if valor is None or valor.strip() == "":
        return defecto
    return valor.strip().lower() in ("1", "si", "sí", "true", "yes", "on")


def _ruta(valor: str) -> Path:
    ruta = Path(valor)
    return ruta if ruta.is_absolute() else RAIZ / ruta


@dataclass
class Config:
    # Webhook de Meta (los tres canales llegan por la misma app de Meta)
    app_secret: str
    verify_token: str
    # Bandeja web
    clave_bandeja: str
    agentes: list[str] = field(default_factory=list)
    # Graph API
    graph_url: str = "https://graph.facebook.com"
    api_version: str = "v21.0"
    # Facebook Messenger (token de acceso de la página)
    fb_page_id: str = ""
    fb_token: str = ""
    # Instagram Direct (cuenta profesional conectada a la página; por defecto usa el token de la página)
    ig_account_id: str = ""
    ig_token: str = ""
    ig_graph_url: str = ""
    # WhatsApp Cloud API
    wa_phone_number_id: str = ""
    wa_token: str = ""
    # Datos
    db_path: Path = field(default_factory=lambda: RAIZ / "datos" / "bandeja.db")
    reglas_path: Path = field(default_factory=lambda: RAIZ / "reglas.json")
    # Avisos al equipo
    aviso_webhook_url: str = ""
    telegram_token: str = ""
    telegram_chat_id: str = ""
    telegram_api_url: str = "https://api.telegram.org"
    avisar_cada_mensaje: bool = False
    url_publica: str = ""
    # Otros
    consultar_perfiles: bool = True
    ventana_horas: int = 24

    @classmethod
    def desde_entorno(cls, cargar_env: bool = True) -> "Config":
        if cargar_env:
            load_dotenv(RAIZ / ".env")
        e = os.environ.get
        try:
            ventana = int(e("VENTANA_HORAS", "24"))
        except ValueError as error:
            raise ErrorConfig("VENTANA_HORAS tiene que ser un número entero") from error
        fb_token = e("FB_PAGE_TOKEN", "")
        config = cls(
            app_secret=e("META_APP_SECRET", ""),
            verify_token=e("META_VERIFY_TOKEN", ""),
            clave_bandeja=e("BANDEJA_CLAVE", ""),
            agentes=[a.strip() for a in e("AGENTES", "").split(",") if a.strip()],
            graph_url=e("META_GRAPH_URL", "https://graph.facebook.com").rstrip("/"),
            api_version=e("META_API_VERSION", "v21.0"),
            fb_page_id=e("FB_PAGE_ID", ""),
            fb_token=fb_token,
            ig_account_id=e("IG_ACCOUNT_ID", ""),
            ig_token=e("IG_TOKEN", "") or fb_token,
            ig_graph_url=e("IG_GRAPH_URL", "").rstrip("/"),
            wa_phone_number_id=e("WA_PHONE_NUMBER_ID", ""),
            wa_token=e("WA_TOKEN", ""),
            db_path=_ruta(e("DB_PATH", "datos/bandeja.db")),
            reglas_path=_ruta(e("REGLAS_PATH", "reglas.json")),
            aviso_webhook_url=e("AVISO_WEBHOOK_URL", ""),
            telegram_token=e("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=e("TELEGRAM_CHAT_ID", ""),
            telegram_api_url=e("TELEGRAM_API_URL", "https://api.telegram.org").rstrip("/"),
            avisar_cada_mensaje=_bool(e("AVISAR_CADA_MENSAJE"), False),
            url_publica=e("BANDEJA_URL_PUBLICA", "").rstrip("/"),
            consultar_perfiles=_bool(e("CONSULTAR_PERFILES"), True),
            ventana_horas=ventana,
        )
        config.validar()
        return config

    def validar(self) -> None:
        faltan = [nombre for nombre, valor in (
            ("META_APP_SECRET", self.app_secret),
            ("META_VERIFY_TOKEN", self.verify_token),
            ("BANDEJA_CLAVE", self.clave_bandeja),
        ) if not valor]
        if faltan:
            raise ErrorConfig("Faltan variables de entorno obligatorias: " + ", ".join(faltan)
                              + ". Copiá .env.example a .env y completalas.")
        if len(self.clave_bandeja) < 8:
            log.warning("BANDEJA_CLAVE es muy corta: usá al menos 12 caracteres en producción.")
        if not self.canales_activos():
            log.warning("No hay ningún canal con token configurado: se reciben mensajes pero no se puede responder.")

    def canales_activos(self) -> list[str]:
        activos = []
        if self.fb_token:
            activos.append("messenger")
        if self.ig_token:
            activos.append("instagram")
        if self.wa_token and self.wa_phone_number_id:
            activos.append("whatsapp")
        return activos
