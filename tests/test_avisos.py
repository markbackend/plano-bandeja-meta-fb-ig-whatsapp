"""Aviso al equipo por webhook propio y Telegram."""

import json

import httpx

from conftest import AUTH, conversaciones, mandar
from simulador.eventos import evento_messenger, evento_whatsapp

CON_AVISOS = dict(aviso_webhook_url="https://avisos.example/hook", telegram_token="123:ABC", telegram_chat_id="42",
                  url_publica="https://bandeja.example")


def test_conversacion_nueva_avisa_por_webhook_y_telegram(armar, graph, reloj):
    cliente = armar(**CON_AVISOS)
    mandar(cliente, evento_whatsapp("PHONE1", "549111", "quiero comprar", nombre="Martín", ts=reloj.ts()))
    hook = graph.a("https://avisos.example/hook")
    tg = graph.a("https://api.telegram.org/bot123:ABC/sendMessage")
    assert len(hook) == 1 and len(tg) == 1
    datos = json.loads(hook[0].content)
    assert datos["evento"] == "mensaje_nuevo" and datos["canal"] == "whatsapp" and datos["contacto"] == "Martín"
    assert datos["texto"] == "quiero comprar" and datos["enlace"].startswith("https://bandeja.example/#c")
    texto_tg = json.loads(tg[0].content)
    assert texto_tg["chat_id"] == "42" and "[WhatsApp] Conversación nueva: Martín" in texto_tg["text"]


def test_solo_avisa_conversaciones_nuevas_o_reabiertas(armar, graph, reloj):
    cliente = armar(**CON_AVISOS)
    mandar(cliente, evento_messenger("PAGE1", "PSID1", "hola", ts=reloj.ts()))
    mandar(cliente, evento_messenger("PAGE1", "PSID1", "otra cosa", ts=reloj.ts() + 1))
    assert len(graph.a("https://avisos.example/hook")) == 1
    conv = conversaciones(cliente)[0]
    cliente.post(f"/api/conversaciones/{conv['id']}/estado", json={"estado": "resuelta"}, headers=AUTH)
    mandar(cliente, evento_messenger("PAGE1", "PSID1", "volví", ts=reloj.ts() + 2))
    avisos = graph.a("https://avisos.example/hook")
    assert len(avisos) == 2 and json.loads(avisos[-1].content)["motivo"] == "Volvió a escribir"
    # el nombre se completó desde el perfil antes de avisar
    assert json.loads(avisos[-1].content)["contacto"] == "Lucía Gómez"


def test_avisar_cada_mensaje(armar, graph, reloj):
    cliente = armar(**CON_AVISOS, avisar_cada_mensaje=True)
    for i in range(3):
        mandar(cliente, evento_messenger("PAGE1", "PSID1", f"m{i}", ts=reloj.ts() + i))
    assert len(graph.a("https://avisos.example/hook")) == 3


def test_aviso_que_falla_no_frena_la_bandeja(armar, graph, reloj):
    cliente = armar(aviso_webhook_url="https://avisos.example/hook")
    original = graph.__call__

    def con_falla(pedido):
        if pedido.url.host == "avisos.example":
            raise httpx.ConnectError("sin conexión")
        return original(pedido)

    cliente.app.state.bandeja.avisador.http = httpx.Client(transport=httpx.MockTransport(con_falla))
    r = mandar(cliente, evento_messenger("PAGE1", "PSID1", "hola", ts=reloj.ts()))
    assert r.status_code == 200
    assert len(conversaciones(cliente)) == 1
    assert len(graph.envios()) == 1  # la bienvenida salió igual


def test_sin_configurar_no_avisa(cliente, graph, reloj):
    mandar(cliente, evento_messenger("PAGE1", "PSID1", "hola", ts=reloj.ts()))
    assert not graph.a("https://api.telegram.org")
