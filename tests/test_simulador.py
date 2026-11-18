"""Simulador de Meta + bandeja de punta a punta, sin red: los dos servidores se hablan por TestClient."""

import pytest
from fastapi.testclient import TestClient

from bandeja.app import crear_app
from conftest import AUTH, SECRETO, conversaciones, hacer_config
from simulador.app import CONTACTOS, crear_simulador


@pytest.fixture
def par(tmp_path, reloj):
    simulador = crear_simulador("http://bandeja", SECRETO, page_id="PAGE1", ig_id="IG1", wa_phone_id="PHONE1")
    config = hacer_config(tmp_path, graph_url="http://simulador", aviso_webhook_url="http://simulador/aviso-equipo",
                          telegram_token="1:demo", telegram_chat_id="9", telegram_api_url="http://simulador")
    # El simulador real usa la hora de la máquina para los avisos: la bandeja también
    bandeja_app = crear_app(config, http=TestClient(simulador, base_url="http://simulador"))
    bandeja = TestClient(bandeja_app, base_url="http://bandeja")
    simulador.state.http = bandeja
    yield TestClient(simulador), bandeja, simulador
    bandeja_app.state.bandeja.almacen.cerrar()


def test_cliente_escribe_por_los_tres_canales_y_recibe_respuesta(par):
    sim, bandeja, app_sim = par
    for c in CONTACTOS:
        r = sim.post("/api/simular", json={"canal": c["canal"], "contacto_id": c["id"], "texto": "hacen envío?"})
        assert r.status_code == 200 and r.json()["webhook_status"] == 200
    convs = conversaciones(bandeja)
    assert sorted(c["canal"] for c in convs) == ["instagram", "messenger", "whatsapp"]
    # Los nombres de Messenger e Instagram salen del perfil que contesta la Graph API simulada
    assert {c["contacto_nombre"] for c in convs} == {c["nombre"] for c in CONTACTOS}

    registros = app_sim.state.registros
    automaticas = [r for r in registros if r["tipo"] == "respuesta"]
    assert len(automaticas) == 3  # UN mensaje por canal: bienvenida + palabra clave juntas
    assert {r["canal"] for r in automaticas} == {"messenger", "instagram", "whatsapp"}
    assert len([r for r in registros if r["tipo"] == "aviso"]) == 6  # webhook + Telegram por conversación nueva

    wa = conversaciones(bandeja, canal="whatsapp")[0]
    r = bandeja.post(f"/api/conversaciones/{wa['id']}/responder", json={"texto": "Te paso el costo", "autor": "Ana"},
                     headers=AUTH)
    assert r.status_code == 200
    ultimo = app_sim.state.registros[-1]
    assert (ultimo["tipo"], ultimo["canal"], ultimo["texto"]) == ("respuesta", "whatsapp", "Te paso el costo")


def test_mensaje_viejo_del_simulador_queda_fuera_de_ventana(par):
    sim, bandeja, app_sim = par
    sim.post("/api/simular", json={"canal": "messenger", "contacto_id": "PSID-viejo", "texto": "hola",
                                   "horas_atras": 30})
    conv = conversaciones(bandeja)[0]
    assert conv["ventana_abierta"] is False
    r = bandeja.post(f"/api/conversaciones/{conv['id']}/responder", json={"texto": "hola"}, headers=AUTH)
    assert r.status_code == 409
    assert not [r for r in app_sim.state.registros if r["tipo"] == "respuesta"]


def test_simulador_valida_lo_que_recibe(par):
    sim, _, _ = par
    assert sim.post("/api/simular", json={"canal": "telegram", "contacto_id": "1", "texto": "x"}).status_code == 422
    assert sim.post("/api/simular", json={"canal": "messenger", "contacto_id": "1", "texto": ""}).status_code == 400
    assert sim.post("/v21.0/PAGE1/messages", json={"recipient": {"id": "1"}}).status_code == 401
    assert sim.get("/").status_code == 200
