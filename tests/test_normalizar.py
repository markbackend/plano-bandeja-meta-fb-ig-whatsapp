"""Lectura de los avisos de los tres canales al formato común."""

from bandeja.normalizar import firma_valida, firmar, leer_evento
from simulador.eventos import estado_whatsapp, evento_instagram, evento_messenger, evento_whatsapp


def test_messenger_texto_y_adjunto():
    aviso = evento_messenger("PAGE1", "PSID1", "hola", mid="m_1", ts=1_700_000_000,
                             adjuntos=[{"tipo": "image", "url": "https://cdn.example/foto.jpg"}])
    mensajes, estados = leer_evento(aviso)
    assert estados == []
    m = mensajes[0]
    assert (m.id, m.canal, m.cuenta_id, m.contacto_id, m.texto) == ("m_1", "messenger", "PAGE1", "PSID1", "hola")
    assert m.adjuntos == ({"tipo": "image", "url": "https://cdn.example/foto.jpg"},)
    assert m.fecha.timestamp() == 1_700_000_000
    assert m.saliente is False


def test_instagram_se_reconoce_por_object():
    mensajes, _ = leer_evento(evento_instagram("IG1", "IGSID1", "buenas", mid="ig_1"))
    assert mensajes[0].canal == "instagram"
    assert mensajes[0].contacto_id == "IGSID1"


def test_eco_de_la_pagina_es_saliente_y_el_contacto_es_el_destinatario():
    mensajes, _ = leer_evento(evento_messenger("PAGE1", "PSID1", "respuesta desde Meta", mid="m_eco", eco=True))
    assert mensajes[0].saliente is True
    assert mensajes[0].contacto_id == "PSID1"


def test_solo_adjunto_sin_texto():
    aviso = evento_instagram("IG1", "IGSID1", "", adjuntos=[{"tipo": "audio", "url": "https://x/a.mp4"}])
    m = leer_evento(aviso)[0][0]
    assert m.texto == "" and m.adjuntos[0]["tipo"] == "audio"


def test_eventos_que_no_son_mensajes_se_ignoran():
    aviso = {"object": "page", "entry": [{"id": "PAGE1", "messaging": [
        {"sender": {"id": "PSID1"}, "recipient": {"id": "PAGE1"}, "timestamp": 1, "read": {"watermark": 1}},
        {"sender": {"id": "PSID1"}, "recipient": {"id": "PAGE1"}, "timestamp": 1, "message": {"mid": "x", "is_deleted": True}},
    ]}]}
    assert leer_evento(aviso) == ([], [])
    assert leer_evento({"object": "user", "entry": []}) == ([], [])
    assert leer_evento({}) == ([], [])


def test_postback_de_boton():
    aviso = {"object": "page", "entry": [{"id": "PAGE1", "messaging": [
        {"sender": {"id": "PSID1"}, "recipient": {"id": "PAGE1"}, "timestamp": 1_700_000_000_000,
         "postback": {"mid": "m_pb", "title": "Ver precios", "payload": "PRECIOS"}}]}]}
    m = leer_evento(aviso)[0][0]
    assert (m.id, m.texto) == ("m_pb", "Ver precios")


def test_whatsapp_texto_con_nombre():
    mensajes, _ = leer_evento(evento_whatsapp("PHONE1", "5491155550001", "hola", nombre="Martín", wamid="wamid.1",
                                              ts=1_700_000_000))
    m = mensajes[0]
    assert (m.canal, m.cuenta_id, m.contacto_id, m.contacto_nombre, m.texto) == (
        "whatsapp", "PHONE1", "5491155550001", "Martín", "hola")
    assert m.fecha.timestamp() == 1_700_000_000


def test_whatsapp_imagen_ubicacion_y_botones():
    img = evento_whatsapp("PHONE1", "549", "", tipo="image", extra={"id": "MEDIA1", "mime_type": "image/jpeg", "caption": "mirá"})
    m = leer_evento(img)[0][0]
    assert m.texto == "mirá" and m.adjuntos[0] == {"tipo": "image", "id": "MEDIA1", "mime": "image/jpeg"}

    ubi = evento_whatsapp("PHONE1", "549", "", tipo="location", extra={"latitude": -34.6, "longitude": -58.4, "name": "Local"})
    m = leer_evento(ubi)[0][0]
    assert "[ubicación] Local" in m.texto and m.adjuntos[0]["url"].startswith("https://maps.google.com/")

    boton = evento_whatsapp("PHONE1", "549", "", tipo="interactive",
                            extra={"type": "button_reply", "button_reply": {"id": "si", "title": "Sí, quiero"}})
    assert leer_evento(boton)[0][0].texto == "Sí, quiero"

    raro = evento_whatsapp("PHONE1", "549", "", tipo="unsupported", extra={})
    assert leer_evento(raro)[0][0].texto == "[unsupported no soportado]"


def test_whatsapp_reacciones_se_ignoran_y_estados_se_leen():
    reaccion = evento_whatsapp("PHONE1", "549", "", tipo="reaction", extra={"message_id": "x", "emoji": "+1"})
    assert leer_evento(reaccion) == ([], [])
    _, estados = leer_evento(estado_whatsapp("PHONE1", "wamid.SAL1", "read"))
    assert estados[0].id == "wamid.SAL1" and estados[0].estado == "read"


def test_firma():
    cuerpo = b'{"object":"page"}'
    firma = firmar(cuerpo, "secreto")
    assert firma_valida(cuerpo, firma, "secreto")
    assert not firma_valida(cuerpo + b" ", firma, "secreto")      # cuerpo alterado
    assert not firma_valida(cuerpo, firma, "otro")                # otro secreto
    assert not firma_valida(cuerpo, None, "secreto")
    assert not firma_valida(cuerpo, firma.removeprefix("sha256="), "secreto")
    assert not firma_valida(cuerpo, firma, "")                    # sin secreto configurado nunca es válida
