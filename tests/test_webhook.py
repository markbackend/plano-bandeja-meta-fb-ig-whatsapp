"""Webhook único: verificación, firma, guardado de los tres canales y duplicados."""

import json

from conftest import AUTH, mandar, conversaciones
from simulador.eventos import estado_whatsapp, evento_instagram, evento_messenger, evento_whatsapp


def test_verificacion_hub_challenge(cliente):
    r = cliente.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verificame", "hub.challenge": "12345"})
    assert r.status_code == 200 and r.text == "12345"
    r = cliente.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "otro", "hub.challenge": "12345"})
    assert r.status_code == 403


def test_firma_invalida_o_faltante_rechaza(cliente):
    aviso = evento_messenger("PAGE1", "PSID1", "hola")
    assert mandar(cliente, aviso, secreto="otro").status_code == 403
    assert cliente.post("/webhook", json=aviso).status_code == 403
    assert conversaciones(cliente) == []


def test_firma_se_calcula_sobre_el_cuerpo_crudo(cliente, reloj):
    # Mismo JSON con otro espaciado: la firma del cuerpo original no sirve para el reformateado
    aviso = evento_messenger("PAGE1", "PSID1", "hola", ts=reloj.ts())
    crudo = json.dumps(aviso, indent=2).encode()
    from bandeja.normalizar import firmar
    r = cliente.post("/webhook", content=crudo, headers={"X-Hub-Signature-256": firmar(crudo, "secreto-de-prueba")})
    assert r.status_code == 200
    r = cliente.post("/webhook", content=json.dumps(aviso).encode(), headers={"X-Hub-Signature-256": firmar(crudo, "secreto-de-prueba")})
    assert r.status_code == 403


def test_json_invalido_con_firma_valida(cliente):
    from bandeja.normalizar import firmar
    r = cliente.post("/webhook", content=b"no es json", headers={"X-Hub-Signature-256": firmar(b"no es json", "secreto-de-prueba")})
    assert r.status_code == 400


def test_los_tres_canales_en_una_bandeja(cliente, reloj):
    assert mandar(cliente, evento_messenger("PAGE1", "PSID1", "hola fb", ts=reloj.ts())).status_code == 200
    assert mandar(cliente, evento_instagram("IG1", "IGSID1", "hola ig", ts=reloj.ts())).status_code == 200
    assert mandar(cliente, evento_whatsapp("PHONE1", "5491100000001", "hola wa", nombre="Martín", ts=reloj.ts())).status_code == 200
    convs = conversaciones(cliente)
    assert sorted(c["canal"] for c in convs) == ["instagram", "messenger", "whatsapp"]
    assert all(c["estado"] == "abierta" and c["no_leidos"] == 1 for c in convs)
    wa = conversaciones(cliente, canal="whatsapp")[0]
    assert wa["contacto_nombre"] == "Martín" and wa["ultimo_texto"] == "hola wa"
    # Nombre de Messenger e Instagram: se lee del perfil en la Graph API
    nombres = {c["canal"]: c["contacto_nombre"] for c in convs}
    assert nombres["messenger"] == "Lucía Gómez" and nombres["instagram"] == "Tienda Sol"


def test_duplicados_no_se_guardan_ni_disparan_nada(cliente, graph, reloj):
    aviso = evento_whatsapp("PHONE1", "549111", "hola", wamid="wamid.REPETIDO", ts=reloj.ts())
    assert mandar(cliente, aviso).json() == {"recibidos": 1}
    envios_antes = len(graph.envios())
    assert mandar(cliente, aviso).json() == {"recibidos": 0}  # Meta reintentó el mismo aviso
    assert len(graph.envios()) == envios_antes                 # no se repite la bienvenida
    conv = conversaciones(cliente)[0]
    hilo = cliente.get(f"/api/conversaciones/{conv['id']}", headers=AUTH).json()
    assert [m["texto"] for m in hilo["mensajes"] if m["direccion"] == "entrante"] == ["hola"]


def test_mismo_contacto_suma_mensajes_a_la_misma_conversacion(cliente, reloj):
    for i in range(3):
        mandar(cliente, evento_messenger("PAGE1", "PSID1", f"mensaje {i}", ts=reloj.ts() + i))
    convs = conversaciones(cliente)
    assert len(convs) == 1 and convs[0]["no_leidos"] == 3 and convs[0]["ultimo_texto"] == "mensaje 2"


def test_eco_de_otra_herramienta_se_guarda_como_saliente(cliente, graph, reloj):
    mandar(cliente, evento_messenger("PAGE1", "PSID1", "hola", ts=reloj.ts()))
    envios = len(graph.envios())
    mandar(cliente, evento_messenger("PAGE1", "PSID1", "contesté desde Meta Business Suite", mid="m_eco1", eco=True, ts=reloj.ts()))
    assert len(graph.envios()) == envios  # un eco no dispara automáticas
    conv = conversaciones(cliente)[0]
    hilo = cliente.get(f"/api/conversaciones/{conv['id']}", headers=AUTH).json()
    eco = [m for m in hilo["mensajes"] if m["meta_id"] == "m_eco1"][0]
    assert eco["direccion"] == "saliente"


def test_estado_de_entrega_de_whatsapp_actualiza_el_mensaje(cliente, graph, reloj):
    mandar(cliente, evento_whatsapp("PHONE1", "549111", "hola", ts=reloj.ts()))
    conv = conversaciones(cliente)[0]
    r = cliente.post(f"/api/conversaciones/{conv['id']}/responder", json={"texto": "Hola!", "autor": "Ana"}, headers=AUTH)
    meta_id = r.json()["meta_id"]
    mandar(cliente, estado_whatsapp("PHONE1", meta_id, "read"))
    hilo = cliente.get(f"/api/conversaciones/{conv['id']}", headers=AUTH).json()
    assert [m["estado"] for m in hilo["mensajes"] if m["meta_id"] == meta_id] == ["read"]


def test_conversacion_resuelta_se_reabre_si_el_cliente_vuelve(cliente, reloj):
    mandar(cliente, evento_instagram("IG1", "IGSID1", "hola", ts=reloj.ts()))
    conv = conversaciones(cliente)[0]
    cliente.post(f"/api/conversaciones/{conv['id']}/estado", json={"estado": "resuelta"}, headers=AUTH)
    assert conversaciones(cliente, estado="abierta") == []
    mandar(cliente, evento_instagram("IG1", "IGSID1", "otra consulta", ts=reloj.ts() + 5))
    assert conversaciones(cliente, estado="abierta")[0]["id"] == conv["id"]


def test_salud_sin_clave(cliente):
    r = cliente.get("/salud")
    assert r.status_code == 200 and r.json()["canales_activos"] == ["messenger", "instagram", "whatsapp"]
