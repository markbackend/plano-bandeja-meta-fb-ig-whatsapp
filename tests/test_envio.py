"""Respuestas por el canal correcto y ventana de 24 horas."""

import httpx

from conftest import AUTH, GRAPH, conversaciones, mandar
from simulador.eventos import evento_instagram, evento_messenger, evento_whatsapp


def _sin_automaticas(armar, tmp_path):
    (tmp_path / "reglas.json").write_text("{}", encoding="utf-8")
    return armar()


def _responder(cliente, conv_id, texto="Hola, ¿en qué te ayudo?"):
    return cliente.post(f"/api/conversaciones/{conv_id}/responder", json={"texto": texto, "autor": "Ana"}, headers=AUTH)


def test_messenger_usa_send_api_con_token_de_pagina(armar, tmp_path, graph, reloj):
    cliente = _sin_automaticas(armar, tmp_path)
    mandar(cliente, evento_messenger("PAGE1", "PSID1", "hola", ts=reloj.ts()))
    conv = conversaciones(cliente)[0]
    r = _responder(cliente, conv["id"])
    assert r.status_code == 200 and r.json()["meta_id"] == "m_SAL1"
    pedido = [p for p in graph.pedidos if p.method == "POST"][-1]
    assert str(pedido.url) == f"{GRAPH}/v21.0/PAGE1/messages"
    assert pedido.headers["Authorization"] == "Bearer tok-fb"
    url, cuerpo = graph.envios()[-1]
    assert cuerpo == {"recipient": {"id": "PSID1"}, "messaging_type": "RESPONSE",
                      "message": {"text": "Hola, ¿en qué te ayudo?"}}
    hilo = cliente.get(f"/api/conversaciones/{conv['id']}", headers=AUTH).json()
    saliente = hilo["mensajes"][-1]
    assert (saliente["direccion"], saliente["autor"], saliente["meta_id"]) == ("saliente", "Ana", "m_SAL1")


def test_instagram_usa_su_token(armar, tmp_path, graph, reloj):
    cliente = _sin_automaticas(armar, tmp_path)
    mandar(cliente, evento_instagram("IG1", "IGSID1", "hola", ts=reloj.ts()))
    _responder(cliente, conversaciones(cliente)[0]["id"])
    pedido = [p for p in graph.pedidos if p.method == "POST"][-1]
    assert str(pedido.url) == f"{GRAPH}/v21.0/me/messages"
    assert pedido.headers["Authorization"] == "Bearer tok-ig"


def test_whatsapp_usa_cloud_api(armar, tmp_path, graph, reloj):
    cliente = _sin_automaticas(armar, tmp_path)
    mandar(cliente, evento_whatsapp("PHONE1", "5491100000001", "hola", ts=reloj.ts()))
    r = _responder(cliente, conversaciones(cliente)[0]["id"], "x" * 5000)
    assert r.status_code == 200 and r.json()["meta_id"].startswith("wamid.")
    url, cuerpo = graph.envios()[-1]
    assert url == f"{GRAPH}/v21.0/PHONE1/messages"
    assert cuerpo["to"] == "5491100000001" and cuerpo["type"] == "text"
    assert len(cuerpo["text"]["body"]) == 4096  # se recorta al máximo de WhatsApp


def test_fuera_de_ventana_no_envia_y_explica(armar, tmp_path, graph, reloj):
    cliente = _sin_automaticas(armar, tmp_path)
    mandar(cliente, evento_whatsapp("PHONE1", "549111", "hola", ts=reloj.ts(horas_atras=25)))
    mandar(cliente, evento_messenger("PAGE1", "PSID1", "hola", ts=reloj.ts(horas_atras=30)))
    for conv in conversaciones(cliente):
        assert conv["ventana_abierta"] is False
        r = _responder(cliente, conv["id"])
        assert r.status_code == 409
        if conv["canal"] == "whatsapp":
            assert "plantilla" in r.json()["detail"]
        else:
            assert "etiqueta" in r.json()["detail"]
        hilo = cliente.get(f"/api/conversaciones/{conv['id']}", headers=AUTH).json()
        assert hilo["aviso_ventana"] and all(m["direccion"] == "entrante" for m in hilo["mensajes"])
    assert graph.envios() == []


def test_ventana_se_cierra_con_el_paso_del_tiempo(armar, tmp_path, graph, reloj):
    cliente = _sin_automaticas(armar, tmp_path)
    mandar(cliente, evento_messenger("PAGE1", "PSID1", "hola", ts=reloj.ts()))
    conv_id = conversaciones(cliente)[0]["id"]
    reloj.adelantar(hours=23, minutes=59)
    assert _responder(cliente, conv_id).status_code == 200
    reloj.adelantar(minutes=2)
    assert _responder(cliente, conv_id).status_code == 409
    # El cliente vuelve a escribir: se abre de nuevo la ventana
    mandar(cliente, evento_messenger("PAGE1", "PSID1", "sigo acá", ts=reloj.ts()))
    assert _responder(cliente, conv_id).status_code == 200


def test_error_de_ventana_de_meta_se_traduce(armar, tmp_path, graph, reloj):
    cliente = _sin_automaticas(armar, tmp_path)
    mandar(cliente, evento_whatsapp("PHONE1", "549111", "hola", ts=reloj.ts()))
    graph.respuestas_forzadas.append(httpx.Response(400, json={"error": {"message": "Re-engagement message", "code": 131047}}))
    r = _responder(cliente, conversaciones(cliente)[0]["id"])
    assert r.status_code == 409 and "plantilla" in r.json()["detail"]


def test_error_de_meta_se_muestra_y_no_se_guarda(armar, tmp_path, graph, reloj):
    cliente = _sin_automaticas(armar, tmp_path)
    mandar(cliente, evento_messenger("PAGE1", "PSID1", "hola", ts=reloj.ts()))
    conv_id = conversaciones(cliente)[0]["id"]
    graph.respuestas_forzadas.append(httpx.Response(400, json={"error": {"message": "Invalid OAuth access token", "code": 190}}))
    r = _responder(cliente, conv_id)
    assert r.status_code == 502 and "Invalid OAuth access token" in r.json()["detail"]
    hilo = cliente.get(f"/api/conversaciones/{conv_id}", headers=AUTH).json()
    assert [m["direccion"] for m in hilo["mensajes"]] == ["entrante"]


def test_reintenta_una_vez_si_meta_esta_saturado(armar, tmp_path, graph, reloj):
    cliente = _sin_automaticas(armar, tmp_path)
    mandar(cliente, evento_messenger("PAGE1", "PSID1", "hola", ts=reloj.ts()))
    graph.respuestas_forzadas.append(httpx.Response(503, text="ocupado"))
    r = _responder(cliente, conversaciones(cliente)[0]["id"])
    assert r.status_code == 200
    assert len([p for p in graph.pedidos if p.url.path.endswith("/messages")]) == 2


def test_canal_sin_token(armar, tmp_path, reloj):
    (tmp_path / "reglas.json").write_text("{}", encoding="utf-8")
    cliente = armar(wa_token="")
    mandar(cliente, evento_whatsapp("PHONE1", "549111", "hola", ts=reloj.ts()))
    r = _responder(cliente, conversaciones(cliente)[0]["id"])
    assert r.status_code == 502 and "token" in r.json()["detail"]


def test_validaciones_de_respuesta(cliente):
    assert cliente.post("/api/conversaciones/999/responder", json={"texto": "hola"}, headers=AUTH).status_code == 404
    assert cliente.post("/api/conversaciones/1/responder", json={"texto": ""}, headers=AUTH).status_code == 422
