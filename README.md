# Bandeja única: Messenger + Instagram + WhatsApp

Una sola bandeja web para atender los mensajes de **Facebook Messenger**, **Instagram Direct** y **WhatsApp**
(Cloud API) usando las APIs oficiales de Meta. Python 3.12 + FastAPI + SQLite + httpx.

## Qué resuelve

- **Un solo webhook** para los tres canales: verificación (`hub.challenge`), firma `X-Hub-Signature-256`
  calculada sobre el cuerpo crudo y lectura de los avisos de Messenger (`object: page`), Instagram
  (`object: instagram`) y WhatsApp (`object: whatsapp_business_account`) a un formato común
  (canal, contacto, texto, adjuntos, fecha, id). Los avisos repetidos se ignoran por id de mensaje.
- **Responder por el canal correcto**: Send API de Messenger/Instagram con el token de la página y
  `/{phone_number_id}/messages` de WhatsApp. Respeta la **ventana de 24 horas**: si pasó, no envía y avisa
  que hace falta una plantilla (WhatsApp) o una etiqueta de mensaje (Messenger/Instagram).
- **Bandeja web** protegida con clave: conversaciones de los tres canales con su etiqueta (FB / IG / WA),
  hilo, responder, marcar como resuelta, asignar a un agente, notas internas, filtros y búsqueda.
  Se actualiza sola cada 4 segundos.
- **Respuestas automáticas** opcionales por canal: bienvenida, fuera de horario y palabras clave, en
  `reglas.json` (se relee solo al guardarlo).
- **Aviso al equipo** de conversaciones nuevas o reabiertas por webhook propio (JSON) y/o Telegram.
- **Simulador local** de Meta para la demo: manda avisos firmados de los tres canales y recibe los envíos.
- Guarda los ecos (mensajes contestados desde otra herramienta, por ejemplo Meta Business Suite) y los
  estados de entrega de WhatsApp (enviado, entregado, leído, fallido).

## Pedidos de Workana que cubre

- "Integración de mensajes de Facebook e Instagram con WhatsApp para centralizar la atención."
- "Necesito que todos los mensajes de Instagram, Facebook y WhatsApp lleguen a un solo lugar y que
  varios vendedores puedan responder."
- "Conectar la API oficial de WhatsApp Business (Cloud API) con nuestro sistema y responder desde una web."
- "Bot de respuesta automática fuera de horario para WhatsApp e Instagram."
- "Webhook de Meta que guarde los mensajes en una base de datos y avise al equipo por Telegram."

## Estructura

```
bandeja/
  config.py        variables de entorno
  normalizar.py    firma del webhook y lectura de los avisos de los tres canales
  meta.py          cliente de la Graph API (envíos, perfiles, errores de ventana)
  almacen.py       SQLite: conversaciones, mensajes (sin duplicados) y notas
  automaticas.py   bienvenida, fuera de horario y palabras clave (reglas.json)
  avisos.py        aviso al equipo por webhook o Telegram
  servicio.py      lógica: guardar, reaccionar, responder respetando las 24 h
  app.py           FastAPI: /webhook, /api/... y la página
  estatico/bandeja.html
simulador/         Meta falsa (clientes + Graph API) para la demo
demo.py            levanta bandeja + simulador
reglas.json        respuestas automáticas
tests/             pruebas (sin internet ni claves)
```

## Instalar

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt     # Windows
# en Linux/Mac: .venv/bin/python -m pip install -r requirements.txt
```

## Probar en 1 minuto (sin cuentas ni claves)

```bash
.venv/Scripts/python.exe demo.py
```

- Bandeja: http://127.0.0.1:8000 (clave `demo`)
- Simulador: http://127.0.0.1:8001: escribís como cliente de Messenger, Instagram o WhatsApp y ves las
  respuestas (las del agente y las automáticas) y los avisos al equipo.

Arranca con 4 conversaciones de ejemplo, una de ellas de hace 30 horas para ver el bloqueo de la ventana
de 24 h. Con `demo.py --verificar` hace la prueba de punta a punta y sale (imprime `DEMO OK`).

## Pruebas

```bash
.venv/Scripts/python.exe -m pytest
```

58 pruebas. No usan internet: la Graph API, Telegram y el webhook de avisos se simulan con un transporte
httpx falso (`tests/conftest.py`), y la prueba de punta a punta conecta el simulador con la bandeja por
`TestClient`. Cubren firma, verificación, los tres formatos, duplicados, ecos, estados de WhatsApp, envío
por cada canal, ventana de 24 h (propia y error de Meta), reintentos, API con clave, asignación, notas,
automáticas, horario, recarga de reglas y avisos.

## Configurar con Meta (producción)

1. Copiar `.env.example` a `.env` y completar `META_APP_SECRET`, `META_VERIFY_TOKEN` y `BANDEJA_CLAVE`.
2. Crear una app de tipo **Empresa** en developers.facebook.com y agregar los productos **Messenger**,
   **Instagram** y **WhatsApp**.
3. Webhook: URL `https://tu-dominio/webhook`, token de verificación = `META_VERIFY_TOKEN`.
   - Messenger: suscribir la página a `messages`, `messaging_postbacks` y `message_echoes`.
   - Instagram: suscribir a `messages` (y `messaging_postbacks` si se usan botones).
   - WhatsApp: suscribir la cuenta de WhatsApp Business al campo `messages`.
4. Tokens: `FB_PAGE_TOKEN` (token de la página, mejor de un usuario del sistema para que no venza),
   `IG_TOKEN` (vacío = usa el de la página), `WA_PHONE_NUMBER_ID` y `WA_TOKEN`.
5. Arrancar: `.venv/Scripts/python.exe -m bandeja` (o `uvicorn bandeja.app:crear_app --factory`).

### Permisos de Meta que hacen falta

| Canal | Permisos |
|---|---|
| Messenger | `pages_messaging`, `pages_manage_metadata`, `pages_show_list`, `pages_read_engagement` |
| Instagram (login de Facebook) | `instagram_basic`, `instagram_manage_messages`, `pages_manage_metadata`, `pages_show_list` |
| Instagram (login de Instagram) | `instagram_business_basic`, `instagram_business_manage_messages` |
| WhatsApp | `whatsapp_business_messaging`, `whatsapp_business_management` |
| Responder después de 24 h en Messenger | función **Human Agent** (etiqueta `HUMAN_AGENT`, hasta 7 días) |

- **Modo desarrollo**: la app funciona solo con usuarios que tengan rol en la app (administradores,
  desarrolladores, testers). Sirve para probar con el equipo del cliente.
- **Revisión de la app (App Review)**: para atender a clientes reales hay que pedir **Acceso avanzado** a los
  permisos, con video de uso y explicación, y tener la **verificación del negocio** hecha en el
  Business Manager. Suele tardar de unos días a un par de semanas, y a veces rechazan y hay que reenviar.
- **WhatsApp**: el número de prueba que da Meta sirve para desarrollar. Para producción hay que registrar un
  número propio, verificar el negocio y aprobar el nombre visible; los mensajes fuera de las 24 h solo
  con **plantillas aprobadas** (y tienen costo por conversación o por mensaje según la tarifa vigente).

> **Importante:** permisos, nombres de campos, versión de la Graph API, códigos de error, tiempos de
> revisión y precios cambian seguido. Verificarlos en la documentación vigente de Meta
> (developers.facebook.com/docs) antes de presupuestar y de desplegar.

## Desplegar

- Necesita **HTTPS público** para que Meta mande el webhook: un VPS con Caddy o Nginx delante, o Render,
  Railway, Fly.io. Para probar desde la PC: un túnel (cloudflared, ngrok).
- Comando: `uvicorn bandeja.app:crear_app --factory --host 0.0.0.0 --port 8000` con el `.env` cargado.
- Usar **un solo proceso** (un worker): SQLite y las tareas en segundo plano están pensadas así. Para
  mucho volumen, pasar a PostgreSQL y una cola (ver checklist).
- Hacer copia de `datos/bandeja.db` (por ejemplo un cron diario).
- Logs: el nivel se cambia con `LOG_LEVEL`. Las URLs de httpx no se loguean para no mostrar tokens.

## CÓMO ADAPTARLO A UN CLIENTE

| Qué | Archivo | Tiempo |
|---|---|---|
| Crear/ajustar la app de Meta, webhook y tokens con el cliente (pantalla compartida) | `.env` | 1-2 h |
| Textos de bienvenida, horario y palabras clave del negocio | `reglas.json` | 30 min |
| Lista de agentes y clave | `.env` (`AGENTES`, `BANDEJA_CLAVE`) | 5 min |
| Colores, logo y nombre en la bandeja | `bandeja/estatico/bandeja.html` | 30 min |
| Aviso por Telegram o al sistema del cliente | `.env` (`TELEGRAM_*`, `AVISO_WEBHOOK_URL`) | 15-30 min |
| Usuarios con clave propia por agente (en vez de una clave común) | `bandeja/app.py` (`exigir_clave`) + tabla en `almacen.py` | 2-3 h |
| Enviar plantillas de WhatsApp fuera de las 24 h | `bandeja/meta.py` (cuerpo `type: template`) + botón en el HTML | 2-3 h |
| Etiqueta `HUMAN_AGENT` en Messenger (si la tienen aprobada) | `bandeja/meta.py` (`messaging_type: MESSAGE_TAG`) | 1 h |
| Enviar imágenes/archivos o descargar los de WhatsApp (media id) | `bandeja/meta.py`, `app.py` | 2-4 h |
| Pasar a PostgreSQL | `bandeja/almacen.py` | 2-3 h |
| Despliegue en VPS/Render con HTTPS y copia de la base | servidor | 1-2 h |
| Probar con el número/página real y ajustar | — | 1 h |

Lo mínimo (Meta + reglas + despliegue) son unas 4 horas; con plantillas y usuarios por agente, 8 o más.

## Qué preguntarle al cliente

1. ¿Qué canales usan hoy y cuáles quieren en la bandeja (página de Facebook, Instagram, número de WhatsApp)?
2. ¿El número de WhatsApp ya está en la app de WhatsApp Business? (para la Cloud API hay que migrarlo o usar
   otro número; deja de funcionar en la app del celular).
3. ¿Tienen Business Manager con el negocio verificado y acceso de administrador a la página y a Instagram?
4. ¿Cuántas personas van a atender y necesitan usuario y clave propios o alcanza una clave común?
5. ¿Qué horario de atención tienen y qué textos quieren para bienvenida, fuera de horario y preguntas frecuentes?
6. ¿Necesitan escribirle a clientes después de 24 h (seguimientos, recordatorios)? Eso requiere plantillas
   aprobadas en WhatsApp y tiene costo.
7. ¿Dónde quieren que avisen los mensajes nuevos (Telegram, su sistema, mail) y dónde se va a alojar
   (servidor propio, hosting, lo contratamos nosotros)?
8. ¿Cuántos mensajes por día reciben aproximadamente?

## Limitaciones conocidas

- Solo texto saliente (los adjuntos entrantes se muestran; los de WhatsApp vienen como id de media y hay que
  descargarlos con el token).
- Una clave común para la bandeja; el nombre del agente lo escribe cada uno al entrar.
- No envía plantillas de WhatsApp ni etiquetas de Messenger: avisa y no envía (ver checklist).
- Comentarios de publicaciones no están incluidos (solo mensajes directos).

## Licencia

MIT, markbackend. Hecho con ayuda de herramientas de IA.
