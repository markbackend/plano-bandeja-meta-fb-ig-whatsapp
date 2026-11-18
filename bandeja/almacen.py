"""Base de datos SQLite: conversaciones, mensajes (sin duplicados) y notas internas."""

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .normalizar import Mensaje

ESQUEMA = """
CREATE TABLE IF NOT EXISTS conversaciones (
    id INTEGER PRIMARY KEY,
    canal TEXT NOT NULL,
    cuenta_id TEXT NOT NULL DEFAULT '',
    contacto_id TEXT NOT NULL,
    contacto_nombre TEXT,
    estado TEXT NOT NULL DEFAULT 'abierta' CHECK (estado IN ('abierta', 'resuelta')),
    agente TEXT,
    ultimo_entrante TEXT,
    ultima_actividad TEXT NOT NULL,
    ultimo_texto TEXT NOT NULL DEFAULT '',
    no_leidos INTEGER NOT NULL DEFAULT 0,
    aviso_fuera_horario TEXT,
    creada TEXT NOT NULL,
    UNIQUE (canal, contacto_id)
);
CREATE TABLE IF NOT EXISTS mensajes (
    id INTEGER PRIMARY KEY,
    conversacion_id INTEGER NOT NULL REFERENCES conversaciones(id),
    meta_id TEXT UNIQUE,
    direccion TEXT NOT NULL CHECK (direccion IN ('entrante', 'saliente')),
    texto TEXT NOT NULL DEFAULT '',
    adjuntos TEXT NOT NULL DEFAULT '[]',
    fecha TEXT NOT NULL,
    autor TEXT,
    estado TEXT
);
CREATE INDEX IF NOT EXISTS ix_mensajes_conversacion ON mensajes (conversacion_id, fecha);
CREATE TABLE IF NOT EXISTS notas (
    id INTEGER PRIMARY KEY,
    conversacion_id INTEGER NOT NULL REFERENCES conversaciones(id),
    autor TEXT NOT NULL,
    texto TEXT NOT NULL,
    fecha TEXT NOT NULL
);
"""


def a_texto(fecha: datetime) -> str:
    return fecha.astimezone(timezone.utc).isoformat(timespec="seconds")


def de_texto(valor: str | None) -> datetime | None:
    return datetime.fromisoformat(valor) if valor else None


@dataclass
class Guardado:
    """Resultado de guardar un mensaje que llegó por el webhook."""
    nuevo: bool                  # False si el mensaje ya estaba (Meta reintenta avisos)
    conversacion_id: int
    conversacion_nueva: bool = False
    reabierta: bool = False      # estaba resuelta y el cliente volvió a escribir


class Almacen:
    def __init__(self, ruta: Path | str):
        if str(ruta) != ":memory:":
            Path(ruta).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(ruta), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._con.execute("PRAGMA foreign_keys = ON")
            if str(ruta) != ":memory:":
                self._con.execute("PRAGMA journal_mode = WAL")
            self._con.executescript(ESQUEMA)

    def cerrar(self) -> None:
        with self._lock:
            self._con.close()

    # ------------------------------------------------------------ mensajes

    def guardar_mensaje(self, m: Mensaje) -> Guardado:
        """Guarda un mensaje del webhook (entrante o eco saliente) y actualiza su conversación."""
        with self._lock, self._con:
            fila = self._con.execute(
                "SELECT id, estado, ultimo_entrante FROM conversaciones WHERE canal = ? AND contacto_id = ?",
                (m.canal, m.contacto_id)).fetchone()
            nueva = fila is None
            if nueva:
                cur = self._con.execute(
                    "INSERT INTO conversaciones (canal, cuenta_id, contacto_id, contacto_nombre, ultima_actividad, creada)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (m.canal, m.cuenta_id, m.contacto_id, m.contacto_nombre, a_texto(m.fecha),
                     a_texto(datetime.now(timezone.utc))))
                conv_id, estado, ultimo_entrante = cur.lastrowid, "abierta", None
            else:
                conv_id, estado, ultimo_entrante = fila["id"], fila["estado"], fila["ultimo_entrante"]

            cur = self._con.execute(
                "INSERT OR IGNORE INTO mensajes (conversacion_id, meta_id, direccion, texto, adjuntos, fecha, autor)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (conv_id, m.id, "saliente" if m.saliente else "entrante", m.texto,
                 json.dumps(list(m.adjuntos), ensure_ascii=False), a_texto(m.fecha),
                 "página (otra herramienta)" if m.saliente else None))
            if cur.rowcount == 0:
                if nueva:  # no debería pasar, pero no dejamos conversaciones vacías
                    self._con.execute("DELETE FROM conversaciones WHERE id = ?", (conv_id,))
                return Guardado(nuevo=False, conversacion_id=conv_id)

            resumen = m.texto or ("[adjunto]" if m.adjuntos else "")
            if m.saliente:
                self._con.execute(
                    "UPDATE conversaciones SET ultima_actividad = max(ultima_actividad, ?), ultimo_texto = ? WHERE id = ?",
                    (a_texto(m.fecha), resumen, conv_id))
                return Guardado(nuevo=True, conversacion_id=conv_id, conversacion_nueva=nueva)

            fecha = a_texto(m.fecha)
            nuevo_ultimo = fecha if not ultimo_entrante or fecha > ultimo_entrante else ultimo_entrante
            self._con.execute(
                "UPDATE conversaciones SET ultimo_entrante = ?, ultima_actividad = max(ultima_actividad, ?),"
                " ultimo_texto = ?, no_leidos = no_leidos + 1, estado = 'abierta',"
                " contacto_nombre = coalesce(?, contacto_nombre) WHERE id = ?",
                (nuevo_ultimo, fecha, resumen, m.contacto_nombre, conv_id))
            return Guardado(nuevo=True, conversacion_id=conv_id, conversacion_nueva=nueva,
                            reabierta=(estado == "resuelta"))

    def guardar_saliente(self, conv_id: int, meta_id: str | None, texto: str, autor: str,
                         fecha: datetime | None = None, automatica: bool = False) -> None:
        """Guarda una respuesta enviada desde la bandeja. Si el eco del webhook llegó antes, completa el autor.

        Una respuesta de un agente marca la conversación como leída y pasa a ser la vista previa de la lista.
        Una automática no: así en la lista se sigue viendo lo que escribió el cliente y que nadie lo atendió.
        """
        fecha = fecha or datetime.now(timezone.utc)
        with self._lock, self._con:
            self._con.execute(
                "INSERT INTO mensajes (conversacion_id, meta_id, direccion, texto, fecha, autor, estado)"
                " VALUES (?, ?, 'saliente', ?, ?, ?, 'enviado')"
                " ON CONFLICT (meta_id) DO UPDATE SET autor = excluded.autor",
                (conv_id, meta_id, texto, a_texto(fecha), autor))
            if automatica:
                return
            self._con.execute(
                "UPDATE conversaciones SET ultima_actividad = max(ultima_actividad, ?), ultimo_texto = ?,"
                " no_leidos = 0 WHERE id = ?",
                (a_texto(fecha), texto, conv_id))

    def actualizar_estado(self, meta_id: str, estado: str) -> bool:
        with self._lock, self._con:
            cur = self._con.execute("UPDATE mensajes SET estado = ? WHERE meta_id = ?", (estado, meta_id))
            return cur.rowcount > 0

    def mensajes(self, conv_id: int) -> list[dict]:
        with self._lock:
            filas = self._con.execute(
                "SELECT id, meta_id, direccion, texto, adjuntos, fecha, autor, estado FROM mensajes"
                " WHERE conversacion_id = ? ORDER BY fecha, id", (conv_id,)).fetchall()
        return [{**dict(f), "adjuntos": json.loads(f["adjuntos"])} for f in filas]

    # ------------------------------------------------------------ conversaciones

    def conversacion(self, conv_id: int) -> dict | None:
        with self._lock:
            fila = self._con.execute("SELECT * FROM conversaciones WHERE id = ?", (conv_id,)).fetchone()
        return dict(fila) if fila else None

    def listar(self, estado: str | None = None, canal: str | None = None, agente: str | None = None,
               buscar: str | None = None, limite: int = 200) -> list[dict]:
        condiciones, parametros = [], []
        if estado:
            condiciones.append("estado = ?")
            parametros.append(estado)
        if canal:
            condiciones.append("canal = ?")
            parametros.append(canal)
        if agente == "-":
            condiciones.append("agente IS NULL")
        elif agente:
            condiciones.append("agente = ?")
            parametros.append(agente)
        if buscar:
            condiciones.append("(contacto_nombre LIKE ? OR contacto_id LIKE ? OR ultimo_texto LIKE ?)")
            parametros += [f"%{buscar}%"] * 3
        donde = ("WHERE " + " AND ".join(condiciones)) if condiciones else ""
        with self._lock:
            filas = self._con.execute(
                f"SELECT * FROM conversaciones {donde} ORDER BY ultima_actividad DESC LIMIT ?",
                (*parametros, limite)).fetchall()
        return [dict(f) for f in filas]

    def _actualizar(self, conv_id: int, sql: str, parametros: tuple = ()) -> bool:
        with self._lock, self._con:
            cur = self._con.execute(f"UPDATE conversaciones SET {sql} WHERE id = ?", (*parametros, conv_id))
            return cur.rowcount > 0

    def marcar_leida(self, conv_id: int) -> bool:
        return self._actualizar(conv_id, "no_leidos = 0")

    def cambiar_estado(self, conv_id: int, estado: str) -> bool:
        return self._actualizar(conv_id, "estado = ?", (estado,))

    def asignar(self, conv_id: int, agente: str | None) -> bool:
        return self._actualizar(conv_id, "agente = ?", (agente,))

    def poner_nombre(self, conv_id: int, nombre: str) -> bool:
        return self._actualizar(conv_id, "contacto_nombre = ?", (nombre,))

    def marcar_aviso_fuera_horario(self, conv_id: int, fecha: datetime) -> bool:
        return self._actualizar(conv_id, "aviso_fuera_horario = ?", (a_texto(fecha),))

    # ------------------------------------------------------------ notas

    def agregar_nota(self, conv_id: int, autor: str, texto: str) -> dict:
        fecha = a_texto(datetime.now(timezone.utc))
        with self._lock, self._con:
            cur = self._con.execute(
                "INSERT INTO notas (conversacion_id, autor, texto, fecha) VALUES (?, ?, ?, ?)",
                (conv_id, autor, texto, fecha))
        return {"id": cur.lastrowid, "autor": autor, "texto": texto, "fecha": fecha}

    def notas(self, conv_id: int) -> list[dict]:
        with self._lock:
            filas = self._con.execute(
                "SELECT id, autor, texto, fecha FROM notas WHERE conversacion_id = ? ORDER BY id", (conv_id,)).fetchall()
        return [dict(f) for f in filas]
