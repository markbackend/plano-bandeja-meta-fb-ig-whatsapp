"""Piezas comunes de las pruebas: configuración de prueba, Graph API falsa (transporte httpx) y reloj fijo.

Ninguna prueba usa internet ni claves reales: todo pedido HTTP de la bandeja pasa por GraphFalsa.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bandeja.app import crear_app  # noqa: E402
from bandeja.config import Config  # noqa: E402
from simulador.eventos import cuerpo_firmado  # noqa: E402

SECRETO = "secreto-de-prueba"
CLAVE = "clave-de-prueba"
AUTH = {"Authorization": f"Bearer {CLAVE}"}
GRAPH = "https://graph.test"
# Miércoles 18/11/2026 15:00 UTC = 12:00 en Buenos Aires
AHORA = datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc)

REGLAS_DE_PRUEBA = {
    "bienvenida": {"activa": True, "texto": {"todos": "Bienvenido", "whatsapp": "Bienvenido a WhatsApp"}},
    "palabras_clave": [{"palabras": ["envío", "envios"], "respuesta": "Enviamos a todo el país", "canales": ["todos"]}],
}


class GraphFalsa:
    """Imita la Graph API de Meta, Telegram y el webhook de avisos. Anota cada pedido que recibe."""

    def __init__(self):
        self.pedidos: list[httpx.Request] = []
        self.respuestas_forzadas: list[httpx.Response | Exception] = []  # se usan antes que las normales
        self.perfiles = {"PSID1": {"first_name": "Lucía", "last_name": "Gómez", "id": "PSID1"},
                         "IGSID1": {"name": "Tienda Sol", "username": "tienda.sol", "id": "IGSID1"}}
        self._enviados = 0

    def __call__(self, pedido: httpx.Request) -> httpx.Response:
        self.pedidos.append(pedido)
        if self.respuestas_forzadas:
            forzada = self.respuestas_forzadas.pop(0)
            if isinstance(forzada, Exception):
                raise forzada
            return forzada
        ruta = pedido.url.path
        if pedido.method == "POST" and ruta.endswith("/messages"):
            self._enviados += 1
            datos = json.loads(pedido.content)
            if datos.get("messaging_product") == "whatsapp":
                return httpx.Response(200, json={"messaging_product": "whatsapp",
                                                 "messages": [{"id": f"wamid.SAL{self._enviados}"}]})
            return httpx.Response(200, json={"recipient_id": datos["recipient"]["id"],
                                             "message_id": f"m_SAL{self._enviados}"})
        if pedido.method == "POST":  # avisos (webhook propio o Telegram)
            return httpx.Response(200, json={"ok": True})
        contacto = ruta.rsplit("/", 1)[-1]
        if contacto in self.perfiles:
            return httpx.Response(200, json=self.perfiles[contacto])
        return httpx.Response(404, json={"error": {"message": "Objeto no encontrado", "code": 100}})

    def envios(self) -> list[tuple[str, dict]]:
        """(url, cuerpo JSON) de cada envío de mensaje a Meta, en orden."""
        return [(str(p.url), json.loads(p.content)) for p in self.pedidos
                if p.method == "POST" and p.url.path.endswith("/messages")]

    def a(self, prefijo: str) -> list[httpx.Request]:
        """Pedidos cuya URL empieza con el prefijo."""
        return [p for p in self.pedidos if str(p.url).startswith(prefijo)]


class Reloj:
    """Reloj controlable: la bandeja lo usa para la ventana de 24 h y el horario."""

    def __init__(self, ahora: datetime = AHORA):
        self.ahora = ahora

    def __call__(self) -> datetime:
        return self.ahora

    def ts(self, horas_atras: float = 0) -> float:
        return (self.ahora - timedelta(hours=horas_atras)).timestamp()

    def adelantar(self, **delta) -> None:
        self.ahora += timedelta(**delta)


def hacer_config(tmp_path: Path, **cambios) -> Config:
    reglas = tmp_path / "reglas.json"
    if not reglas.exists():
        reglas.write_text(json.dumps(REGLAS_DE_PRUEBA, ensure_ascii=False), encoding="utf-8")
    datos = dict(
        app_secret=SECRETO, verify_token="verificame", clave_bandeja=CLAVE, agentes=["Ana", "Bruno"],
        graph_url=GRAPH, api_version="v21.0",
        fb_page_id="PAGE1", fb_token="tok-fb", ig_account_id="IG1", ig_token="tok-ig",
        wa_phone_number_id="PHONE1", wa_token="tok-wa",
        db_path=tmp_path / "bandeja.db", reglas_path=reglas, consultar_perfiles=True,
    )
    datos.update(cambios)
    return Config(**datos)


@pytest.fixture
def graph() -> GraphFalsa:
    return GraphFalsa()


@pytest.fixture
def reloj() -> Reloj:
    return Reloj()


@pytest.fixture
def armar(tmp_path, graph, reloj):
    """Crea la bandeja de prueba (con cambios de configuración opcionales) y devuelve un TestClient."""
    apps = []

    def _armar(**cambios) -> TestClient:
        app = crear_app(hacer_config(tmp_path, **cambios), http=httpx.Client(transport=httpx.MockTransport(graph)),
                        reloj=reloj)
        apps.append(app)
        return TestClient(app)

    yield _armar
    for app in apps:
        app.state.bandeja.almacen.cerrar()


@pytest.fixture
def cliente(armar) -> TestClient:
    return armar()


def mandar(cliente: TestClient, aviso: dict, secreto: str = SECRETO) -> httpx.Response:
    """Manda un aviso al webhook firmado como lo hace Meta."""
    cuerpo, encabezados = cuerpo_firmado(aviso, secreto)
    return cliente.post("/webhook", content=cuerpo, headers=encabezados)


def conversaciones(cliente: TestClient, estado: str = "", **filtros) -> list[dict]:
    r = cliente.get("/api/conversaciones", params={"estado": estado, **filtros}, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()
