"""Demo en 1 minuto, sin cuentas ni claves de Meta.

    .venv/Scripts/python.exe demo.py             -> levanta bandeja (8000) y simulador (8001)
    .venv/Scripts/python.exe demo.py --verificar -> lo mismo, comprueba que todo responde y sale

Bandeja:    http://127.0.0.1:8000   (clave: demo)
Simulador:  http://127.0.0.1:8001   (escribís como cliente de Messenger, Instagram o WhatsApp)
"""

import argparse
import logging
import os
import sys
import threading
import time

import httpx
import uvicorn

from bandeja.__main__ import configurar_logs
from bandeja.app import crear_app
from bandeja.config import RAIZ, Config
from simulador.app import CONTACTOS, crear_simulador

log = logging.getLogger("demo")

PUERTO_BANDEJA = int(os.environ.get("DEMO_PUERTO_BANDEJA", "8000"))
PUERTO_SIMULADOR = int(os.environ.get("DEMO_PUERTO_SIMULADOR", "8001"))
URL_BANDEJA = f"http://127.0.0.1:{PUERTO_BANDEJA}"
URL_SIMULADOR = f"http://127.0.0.1:{PUERTO_SIMULADOR}"
SECRETO = "secreto-de-la-demo"

MENSAJES_INICIALES = [
    ("messenger", CONTACTOS[0]["id"], "Hola! Quería saber el precio del kit completo", 0),
    ("instagram", CONTACTOS[1]["id"], "Buenas, hacen envíos a Córdoba?", 0),
    ("whatsapp", CONTACTOS[2]["id"], "Hola, quiero saber el horario de atención", 0),
    ("whatsapp", "5491166660002", "Les escribí ayer y nadie me contestó", 30),  # fuera de la ventana de 24 h
]


def config_demo() -> Config:
    db = RAIZ / "datos" / "demo.db"
    for sufijo in ("", "-wal", "-shm"):
        try:
            os.remove(str(db) + sufijo)
        except FileNotFoundError:
            pass
    return Config(
        app_secret=SECRETO, verify_token="verificar-demo", clave_bandeja="demo",
        agentes=["Ana", "Bruno", "Carla"],
        graph_url=URL_SIMULADOR, api_version="v21.0",
        fb_page_id="PAGE_DEMO", fb_token="token-demo-pagina",
        ig_account_id="IG_DEMO", ig_token="token-demo-instagram",
        wa_phone_number_id="WA_DEMO", wa_token="token-demo-whatsapp",
        db_path=db, reglas_path=RAIZ / "reglas.json",
        aviso_webhook_url=f"{URL_SIMULADOR}/aviso-equipo",
        telegram_token="123456:demo", telegram_chat_id="999", telegram_api_url=URL_SIMULADOR,
        url_publica=URL_BANDEJA,
    )


def esperar(url: str, segundos: float = 15) -> None:
    limite = time.time() + segundos
    while time.time() < limite:
        try:
            if httpx.get(url, timeout=1).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"No arrancó {url}")


def servidor(app, puerto: int) -> uvicorn.Server:
    return uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=puerto, log_level="warning"))


def sembrar() -> None:
    """Manda unos mensajes de ejemplo de los tres canales, como si escribieran clientes reales."""
    with httpx.Client(timeout=15) as http:
        for canal, contacto, texto, horas in MENSAJES_INICIALES:
            r = http.post(f"{URL_SIMULADOR}/api/simular",
                          json={"canal": canal, "contacto_id": contacto, "texto": texto, "horas_atras": horas})
            r.raise_for_status()
            log.info("Simulado %s de %s: %r -> webhook %s", canal, contacto, texto, r.json()["webhook_status"])


def verificar() -> bool:
    """Comprueba de punta a punta: mensajes guardados, respuesta enviada y ventana de 24 h respetada."""
    ok = True
    with httpx.Client(timeout=15, headers={"Authorization": "Bearer demo"}) as http:
        convs = http.get(f"{URL_BANDEJA}/api/conversaciones", params={"estado": ""}).json()
        canales = sorted({c["canal"] for c in convs})
        log.info("Conversaciones en la bandeja: %d (canales: %s)", len(convs), ", ".join(canales))
        ok &= len(convs) == len(MENSAJES_INICIALES) and canales == ["instagram", "messenger", "whatsapp"]
        reciente = next(c for c in convs if c["contacto_id"] == CONTACTOS[0]["id"])
        r = http.post(f"{URL_BANDEJA}/api/conversaciones/{reciente['id']}/responder",
                      json={"texto": "Hola Lucía, el kit completo sale $25.000.", "autor": "Ana"})
        log.info("Respuesta por Messenger: HTTP %s", r.status_code)
        ok &= r.status_code == 200
        vieja = next(c for c in convs if c["contacto_id"] == "5491166660002")
        r = http.post(f"{URL_BANDEJA}/api/conversaciones/{vieja['id']}/responder", json={"texto": "Hola", "autor": "Ana"})
        log.info("Respuesta fuera de las 24 h: HTTP %s (%s)", r.status_code, r.json().get("detail", "")[:70])
        ok &= r.status_code == 409
    estado = httpx.get(f"{URL_SIMULADOR}/api/estado").json()["registros"]
    respuestas = [r for r in estado if r["tipo"] == "respuesta"]
    avisos = [r for r in estado if r["tipo"] == "aviso"]
    log.info("El simulador recibió %d envíos de la bandeja y %d avisos al equipo", len(respuestas), len(avisos))
    ok &= any(r["texto"].startswith("Hola Lucía") for r in respuestas) and len(avisos) > 0
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="Demo de la bandeja con el simulador de Meta")
    parser.add_argument("--verificar", action="store_true", help="arranca, prueba de punta a punta y sale")
    args = parser.parse_args()
    configurar_logs()

    simulador = crear_simulador(URL_BANDEJA, SECRETO, page_id="PAGE_DEMO", ig_id="IG_DEMO", wa_phone_id="WA_DEMO")
    bandeja = crear_app(config_demo())
    srv_sim, srv_bandeja = servidor(simulador, PUERTO_SIMULADOR), servidor(bandeja, PUERTO_BANDEJA)
    threading.Thread(target=srv_sim.run, daemon=True).start()
    hilo_bandeja = threading.Thread(target=srv_bandeja.run, daemon=True)
    hilo_bandeja.start()
    esperar(f"{URL_SIMULADOR}/api/estado")
    esperar(f"{URL_BANDEJA}/salud")
    sembrar()
    time.sleep(1)  # las automáticas y avisos corren en segundo plano

    if args.verificar:
        ok = verificar()
        srv_bandeja.should_exit = srv_sim.should_exit = True
        hilo_bandeja.join(5)
        print("\nDEMO OK" if ok else "\nDEMO CON ERRORES")
        sys.exit(0 if ok else 1)

    print(f"""
==============================================================
 Bandeja:   {URL_BANDEJA}    (clave: demo)
 Simulador: {URL_SIMULADOR}  (escribí como cliente)
 Ctrl+C para salir.
==============================================================
""")
    try:
        while hilo_bandeja.is_alive():
            hilo_bandeja.join(0.5)
    except KeyboardInterrupt:
        srv_bandeja.should_exit = srv_sim.should_exit = True
        print("Demo terminada.")


if __name__ == "__main__":
    main()
