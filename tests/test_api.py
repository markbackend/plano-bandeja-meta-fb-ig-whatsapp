"""API de la bandeja: clave, filtros, estados, asignación y notas."""

from conftest import AUTH, CLAVE, conversaciones, mandar
from simulador.eventos import evento_instagram, evento_messenger, evento_whatsapp


def _cargar(cliente, reloj):
    mandar(cliente, evento_messenger("PAGE1", "PSID1", "consulta por talles", ts=reloj.ts()))
    mandar(cliente, evento_instagram("IG1", "IGSID1", "hola", ts=reloj.ts() + 1))
    mandar(cliente, evento_whatsapp("PHONE1", "549111", "pedido 123", nombre="Martín", ts=reloj.ts() + 2))
    return {c["canal"]: c for c in conversaciones(cliente)}


def test_la_api_pide_clave(cliente):
    assert cliente.get("/api/conversaciones").status_code == 401
    assert cliente.get("/api/conversaciones", headers={"Authorization": "Bearer mal"}).status_code == 401
    assert cliente.get("/api/config", headers={"Authorization": f"Bearer {CLAVE}"}).json()["agentes"] == ["Ana", "Bruno"]
    assert cliente.post("/api/conversaciones/1/estado", json={"estado": "resuelta"}).status_code == 401


def test_pagina_html_se_sirve(cliente):
    r = cliente.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "Bandeja de mensajes" in r.text


def test_orden_y_filtros(cliente, reloj):
    _cargar(cliente, reloj)
    assert [c["canal"] for c in conversaciones(cliente)] == ["whatsapp", "instagram", "messenger"]  # más reciente primero
    assert len(conversaciones(cliente, canal="instagram")) == 1
    assert conversaciones(cliente, buscar="talles")[0]["canal"] == "messenger"
    assert conversaciones(cliente, buscar="Martín")[0]["canal"] == "whatsapp"
    assert cliente.get("/api/conversaciones", params={"canal": "telegram"}, headers=AUTH).status_code == 400


def test_ver_hilo_marca_leida(cliente, reloj):
    convs = _cargar(cliente, reloj)
    conv_id = convs["messenger"]["id"]
    assert convs["messenger"]["no_leidos"] == 1
    d = cliente.get(f"/api/conversaciones/{conv_id}", headers=AUTH).json()
    assert d["conversacion"]["ventana_abierta"] is True and d["aviso_ventana"] is None
    assert d["mensajes"][0]["texto"] == "consulta por talles"
    assert conversaciones(cliente, canal="messenger")[0]["no_leidos"] == 0
    assert cliente.get("/api/conversaciones/999", headers=AUTH).status_code == 404


def test_resolver_y_reabrir(cliente, reloj):
    conv_id = _cargar(cliente, reloj)["whatsapp"]["id"]
    r = cliente.post(f"/api/conversaciones/{conv_id}/estado", json={"estado": "resuelta"}, headers=AUTH)
    assert r.status_code == 200
    assert [c["id"] for c in conversaciones(cliente, estado="resuelta")] == [conv_id]
    assert conv_id not in [c["id"] for c in conversaciones(cliente, estado="abierta")]
    cliente.post(f"/api/conversaciones/{conv_id}/estado", json={"estado": "abierta"}, headers=AUTH)
    assert conversaciones(cliente, estado="resuelta") == []
    assert cliente.post(f"/api/conversaciones/{conv_id}/estado", json={"estado": "borrada"}, headers=AUTH).status_code == 422


def test_asignar_agente(cliente, reloj):
    conv_id = _cargar(cliente, reloj)["instagram"]["id"]
    assert cliente.post(f"/api/conversaciones/{conv_id}/asignar", json={"agente": "Bruno"}, headers=AUTH).status_code == 200
    assert conversaciones(cliente, agente="Bruno")[0]["id"] == conv_id
    assert len(conversaciones(cliente, agente="-")) == 2  # sin asignar
    r = cliente.post(f"/api/conversaciones/{conv_id}/asignar", json={"agente": "Intruso"}, headers=AUTH)
    assert r.status_code == 400
    cliente.post(f"/api/conversaciones/{conv_id}/asignar", json={"agente": None}, headers=AUTH)
    assert conversaciones(cliente, agente="Bruno") == []


def test_notas_internas_no_se_envian_al_cliente(cliente, graph, reloj):
    conv_id = _cargar(cliente, reloj)["messenger"]["id"]
    envios = len(graph.envios())
    r = cliente.post(f"/api/conversaciones/{conv_id}/notas", json={"texto": "Cliente mayorista", "autor": "Ana"}, headers=AUTH)
    assert r.status_code == 200
    d = cliente.get(f"/api/conversaciones/{conv_id}", headers=AUTH).json()
    assert [(n["autor"], n["texto"]) for n in d["notas"]] == [("Ana", "Cliente mayorista")]
    assert len(graph.envios()) == envios
    assert cliente.post("/api/conversaciones/999/notas", json={"texto": "x"}, headers=AUTH).status_code == 404
