"""Respuestas automáticas: bienvenida, palabra clave, fuera de horario y recarga de reglas.json."""

import json
import os
from datetime import datetime, timedelta, timezone

from bandeja.automaticas import Reglas, normalizar_texto
from conftest import AUTH, conversaciones, mandar
from simulador.eventos import evento_instagram, evento_messenger, evento_whatsapp

MIERCOLES_MEDIODIA = datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc)   # 12:00 en Buenos Aires
MIERCOLES_NOCHE = datetime(2026, 11, 19, 2, 0, tzinfo=timezone.utc)      # 23:00 en Buenos Aires
DOMINGO_MEDIODIA = datetime(2026, 11, 22, 15, 0, tzinfo=timezone.utc)


def _reglas(tmp_path, datos) -> Reglas:
    ruta = tmp_path / "reglas.json"
    ruta.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    return Reglas(ruta)


HORARIO = {"zona_horaria": "America/Argentina/Buenos_Aires", "horario": {"dias": [0, 1, 2, 3, 4], "desde": "09:00", "hasta": "18:00"},
           "fuera_de_horario": {"activa": True, "repetir_cada_horas": 12, "texto": {"todos": "Cerrado", "instagram": "Cerrado IG"}}}


def test_normalizar_texto():
    assert normalizar_texto("ENVÍO Ñandú") == "envio nandu"


def test_horario(tmp_path):
    r = _reglas(tmp_path, HORARIO)
    assert r.en_horario(MIERCOLES_MEDIODIA)
    assert not r.en_horario(MIERCOLES_NOCHE)
    assert not r.en_horario(DOMINGO_MEDIODIA)
    assert _reglas(tmp_path, {}).en_horario(MIERCOLES_NOCHE)  # sin horario = siempre abierto


def test_fuera_de_horario_una_vez_cada_tanto(tmp_path):
    r = _reglas(tmp_path, HORARIO)
    assert [x.texto for x in r.respuestas_para("instagram", "hola", False, MIERCOLES_NOCHE)] == ["Cerrado IG"]
    assert r.respuestas_para("messenger", "hola", False, MIERCOLES_NOCHE, MIERCOLES_NOCHE - timedelta(hours=1)) == []
    assert len(r.respuestas_para("messenger", "hola", False, MIERCOLES_NOCHE, MIERCOLES_NOCHE - timedelta(hours=13))) == 1
    assert r.respuestas_para("messenger", "hola", False, MIERCOLES_MEDIODIA) == []


def test_palabras_clave_sin_tildes_y_palabra_entera(tmp_path):
    r = _reglas(tmp_path, {"palabras_clave": [
        {"palabras": ["envío", "cuánto sale"], "respuesta": "Enviamos", "canales": ["todos"]},
        {"palabras": ["catalogo"], "respuesta": {"todos": "Catálogo", "whatsapp": "Catálogo WA"}, "canales": ["whatsapp"]},
    ]})
    textos = lambda canal, t: [x.texto for x in r.respuestas_para(canal, t, False, MIERCOLES_MEDIODIA)]  # noqa: E731
    assert textos("messenger", "Hacen ENVIO a Rosario?") == ["Enviamos"]
    assert textos("instagram", "cuanto sale??") == ["Enviamos"]
    assert textos("messenger", "reenvío") == []                 # no matchea dentro de otra palabra
    assert textos("whatsapp", "pasame el catálogo") == ["Catálogo WA"]
    assert textos("messenger", "pasame el catálogo") == []      # regla solo para WhatsApp


def test_reglas_se_releen_y_un_json_roto_no_rompe(tmp_path):
    r = _reglas(tmp_path, {"bienvenida": {"activa": True, "texto": "Hola 1"}})
    assert r.respuestas_para("messenger", "x", True, MIERCOLES_MEDIODIA)[0].texto == "Hola 1"
    ruta = tmp_path / "reglas.json"
    ruta.write_text(json.dumps({"bienvenida": {"activa": True, "texto": "Hola 2"}}), encoding="utf-8")
    os.utime(ruta, (1, 1_900_000_000))  # forzar mtime distinto aunque el sistema de archivos sea lento
    assert r.respuestas_para("messenger", "x", True, MIERCOLES_MEDIODIA)[0].texto == "Hola 2"
    ruta.write_text("{ esto no es json", encoding="utf-8")
    os.utime(ruta, (1, 1_900_000_100))
    assert r.respuestas_para("messenger", "x", True, MIERCOLES_MEDIODIA)[0].texto == "Hola 2"  # sigue con las anteriores
    ruta.unlink()
    assert r.respuestas_para("messenger", "x", True, MIERCOLES_MEDIODIA) == []


# ---------------------------------------------------------------- de punta a punta por el webhook

def test_bienvenida_solo_en_el_primer_mensaje_y_por_canal(cliente, graph, reloj):
    mandar(cliente, evento_whatsapp("PHONE1", "549111", "hola", ts=reloj.ts()))
    mandar(cliente, evento_whatsapp("PHONE1", "549111", "sigo", ts=reloj.ts() + 1))
    mandar(cliente, evento_messenger("PAGE1", "PSID1", "hola", ts=reloj.ts()))
    textos = [(url.split("/")[-2], c.get("text", {}).get("body") or c["message"]["text"]) for url, c in graph.envios()]
    assert textos == [("PHONE1", "Bienvenido a WhatsApp"), ("PAGE1", "Bienvenido")]
    conv = conversaciones(cliente, canal="whatsapp")[0]
    hilo = cliente.get(f"/api/conversaciones/{conv['id']}", headers=AUTH).json()
    assert [m["autor"] for m in hilo["mensajes"] if m["direccion"] == "saliente"] == ["automática (bienvenida)"]
    # una automática no marca la conversación como leída
    assert conv["no_leidos"] == 2


def test_palabra_clave_por_webhook(cliente, graph, reloj):
    mandar(cliente, evento_instagram("IG1", "IGSID1", "hola", ts=reloj.ts()))
    mandar(cliente, evento_instagram("IG1", "IGSID1", "hacen envío?", ts=reloj.ts() + 1))
    assert [c["message"]["text"] for _, c in graph.envios()] == ["Bienvenido", "Enviamos a todo el país"]


def test_automatica_fuera_de_ventana_no_se_envia(cliente, graph, reloj):
    mandar(cliente, evento_messenger("PAGE1", "PSID1", "hola", ts=reloj.ts(horas_atras=48)))
    assert graph.envios() == []
    assert len(conversaciones(cliente)) == 1  # el mensaje igual queda en la bandeja


def test_fuera_de_horario_por_webhook_se_marca(armar, tmp_path, graph, reloj):
    (tmp_path / "reglas.json").write_text(json.dumps(HORARIO), encoding="utf-8")
    cliente = armar()
    reloj.ahora = MIERCOLES_NOCHE
    mandar(cliente, evento_messenger("PAGE1", "PSID1", "hola", ts=reloj.ts()))
    mandar(cliente, evento_messenger("PAGE1", "PSID1", "hay alguien?", ts=reloj.ts() + 60))
    assert [c["message"]["text"] for _, c in graph.envios()] == ["Cerrado"]  # solo una vez


# ------------------------------------------------------------ revisión: un solo mensaje automático


def test_combinar_une_en_un_solo_mensaje():
    from bandeja.automaticas import Respuesta, combinar
    r = combinar([Respuesta("palabra_clave", "Abrimos de 9 a 19."), Respuesta("bienvenida", "Hola!")])
    assert r.tipo == "bienvenida+palabra_clave" and r.texto == "Hola!\n\nAbrimos de 9 a 19."


def test_combinar_fuera_de_horario_reemplaza_la_bienvenida():
    from bandeja.automaticas import Respuesta, combinar
    r = combinar([Respuesta("bienvenida", "En un rato te responde alguien"), Respuesta("palabra_clave", "Envíos a todo el país."),
                  Respuesta("fuera_de_horario", "Estamos cerrados.")])
    assert "En un rato" not in r.texto  # se contradecía con "estamos cerrados"
    assert r.texto == "Envíos a todo el país.\n\nEstamos cerrados." and r.tipo == "palabra_clave+fuera_de_horario"
    assert combinar([]) is None


def test_un_mensaje_entrante_genera_un_solo_envio(cliente, graph, reloj):
    """Primer mensaje y con palabra clave: el cliente recibe UN mensaje (saludo + respuesta), no dos."""
    mandar(cliente, evento_instagram("IG1", "IGSID1", "hola, hacen envío?", ts=reloj.ts()))
    textos = [c["message"]["text"] for _, c in graph.envios()]
    assert textos == ["Bienvenido\n\nEnviamos a todo el país"]
