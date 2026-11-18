"""Configuración por variables de entorno."""

import pytest

from bandeja.config import Config, ErrorConfig

BASE = {"META_APP_SECRET": "s", "META_VERIFY_TOKEN": "v", "BANDEJA_CLAVE": "una-clave-larga"}


def _limpiar(monkeypatch):
    for nombre in ("META_APP_SECRET", "META_VERIFY_TOKEN", "BANDEJA_CLAVE", "FB_PAGE_TOKEN", "IG_TOKEN", "WA_TOKEN",
                   "WA_PHONE_NUMBER_ID", "AGENTES", "VENTANA_HORAS", "AVISAR_CADA_MENSAJE"):
        monkeypatch.delenv(nombre, raising=False)


def test_faltan_obligatorias(monkeypatch):
    _limpiar(monkeypatch)
    with pytest.raises(ErrorConfig, match="META_APP_SECRET"):
        Config.desde_entorno(cargar_env=False)


def test_lee_canales_y_agentes(monkeypatch):
    _limpiar(monkeypatch)
    for k, v in {**BASE, "FB_PAGE_TOKEN": "tfb", "WA_TOKEN": "twa", "WA_PHONE_NUMBER_ID": "123",
                 "AGENTES": "Ana, Bruno ,", "AVISAR_CADA_MENSAJE": "si"}.items():
        monkeypatch.setenv(k, v)
    c = Config.desde_entorno(cargar_env=False)
    assert c.agentes == ["Ana", "Bruno"]
    assert c.ig_token == "tfb"  # Instagram usa el token de la página si no tiene uno propio
    assert c.canales_activos() == ["messenger", "instagram", "whatsapp"]
    assert c.avisar_cada_mensaje is True and c.ventana_horas == 24


def test_ventana_invalida(monkeypatch):
    _limpiar(monkeypatch)
    for k, v in {**BASE, "VENTANA_HORAS": "veinte"}.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ErrorConfig, match="VENTANA_HORAS"):
        Config.desde_entorno(cargar_env=False)
