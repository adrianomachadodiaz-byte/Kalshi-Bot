"""Aplica la estrategia bloque a bloque: entra, sale, liquida y anota cada operación.

No habla con Kalshi directamente: usa un ejecutor (simulado o real, ver ejecutores.py).
"""
import csv
import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from estrategia import Estrategia, Foto, Operacion

CDT = ZoneInfo("America/Chicago")
COLUMNAS = ["fecha", "hora_entrada", "hora_salida", "activo", "mercado", "lado", "entrada", "salida",
            "precio_salida", "contratos", "min_restantes", "delta", "atraso_s", "pnl_c", "pnl_usd", "resultado", "modo"]


LOGS = deque(maxlen=500)   # últimas líneas, para el panel


def log(msg):
    linea = f"[{datetime.now(CDT):%Y-%m-%d %H:%M:%S} CDT] {msg}"
    print(linea, flush=True)
    LOGS.append(linea)


def hora(ts):
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(CDT)


class Estado:
    """Lo que tiene que sobrevivir a un reinicio: operaciones abiertas, bloques ya operados y si está encendido.
    La primera vez arranca APAGADO: se enciende desde el panel."""

    def __init__(self, carpeta: Path):
        self.ruta = carpeta / "estado.json"
        self.abiertas = {}      # activo -> Operacion
        self.pendientes = []    # operaciones con el bloque cerrado, esperando el resultado oficial
        self.operado = {}       # activo -> ticker del último bloque en que entró
        self.pausado = True     # True = apagado (no abre operaciones nuevas)
        self.encendido_desde = None
        if self.ruta.exists():
            try:
                d = json.loads(self.ruta.read_text(encoding="utf-8"))
                self.abiertas = {a: Operacion.de_dict(o) for a, o in d.get("abiertas", {}).items()}
                self.pendientes = [Operacion.de_dict(o) for o in d.get("pendientes", [])]
                self.operado = d.get("operado", {})
                self.pausado = d.get("pausado", True)
                self.encendido_desde = d.get("encendido_desde")
                log(f"estado recuperado: {len(self.abiertas)} abiertas, {len(self.pendientes)} por liquidar, "
                    f"{'APAGADO' if self.pausado else 'ENCENDIDO'}")
            except Exception as e:  # noqa: BLE001
                log(f"no pude leer {self.ruta.name} ({e}); empiezo de cero")

    def guardar(self):
        d = dict(abiertas={a: o.a_dict() for a, o in self.abiertas.items()},
                 pendientes=[o.a_dict() for o in self.pendientes], operado=self.operado, pausado=self.pausado,
                 encendido_desde=self.encendido_desde)
        tmp = self.ruta.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
        os.replace(tmp, self.ruta)


class Registro:
    """operaciones.csv (una fila por operación terminada) + resumen en memoria para la página."""

    def __init__(self, carpeta: Path, modo: str):
        self.ruta = carpeta / "operaciones.csv"
        self.modo = modo
        self.filas = []
        if self.ruta.exists():
            with open(self.ruta, encoding="utf-8", newline="") as fh:
                self.filas = list(csv.DictReader(fh))

    def anotar(self, op: Operacion):
        pnl = op.pnl()
        n_sal = sum(s["n"] for s in op.salidas) or 1
        motivos = []
        for s in op.salidas:
            if s["motivo"] not in motivos:
                motivos.append(s["motivo"])
        fila = dict(
            fecha=hora(op.ts_entrada).strftime("%Y-%m-%d"),
            hora_entrada=hora(op.ts_entrada).strftime("%H:%M:%S"),
            hora_salida=hora(max(s["ts"] for s in op.salidas)).strftime("%H:%M:%S") if op.salidas else "",
            activo=op.activo, mercado=op.ticker, lado=op.lado.upper(), entrada=round(op.entrada, 4),
            salida="+".join(motivos), precio_salida=round(sum(s["n"] * s["precio"] for s in op.salidas) / n_sal, 4),
            contratos=op.contratos, min_restantes=round((op.cierre - op.ts_entrada) / 60, 1),
            delta=round(op.delta, 1) if op.delta is not None else "",
            atraso_s=round(op.atraso, 1) if op.atraso is not None else "",
            pnl_c=round(pnl / op.contratos * 100, 2) if op.contratos else 0, pnl_usd=round(pnl, 2),
            resultado="GANA" if pnl > 0 else "PIERDE", modo=self.modo)
        nuevo = not self.ruta.exists()
        with open(self.ruta, "a", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNAS)
            if nuevo:
                w.writeheader()
            w.writerow(fila)
        self.filas.append({k: str(v) for k, v in fila.items()})
        return fila

    def resumen(self, fecha=None):
        filas = [f for f in self.filas if f.get("modo") == self.modo and (fecha is None or f["fecha"] == fecha)]
        pnl = [float(f["pnl_usd"]) for f in filas]
        g = [p for p in pnl if p > 0]
        p_ = [p for p in pnl if p <= 0]
        return dict(ops=len(pnl), ganadas=len(g), perdidas=len(p_), usd=round(sum(pnl), 2),
                    wr=round(100 * len(g) / len(pnl), 1) if pnl else None,
                    media_ganada=round(sum(g) / len(g), 2) if g else None,
                    media_perdida=round(sum(p_) / len(p_), 2) if p_ else None)


class Operador:
    """Lleva las operaciones de todos los activos. `procesar` se llama una vez por segundo y activo."""

    def __init__(self, estrategias: dict, ejecutor, estado: Estado, registro: Registro,
                 contratos: float, max_perdida_dia: float = 0, meta_dia: float = 0):
        self.estrategias = estrategias        # activo -> Estrategia
        self.ej = ejecutor
        self.estado = estado
        self.registro = registro
        self.contratos = contratos
        self.max_perdida_dia = max_perdida_dia
        self.meta_dia = meta_dia
        self.lock = threading.RLock()
        self.aviso_limite = None

    # ------------------------------------------------------------------ entrada / salida
    def procesar(self, activo: str, f: Foto):
        with self.lock:
            op = self.estado.abiertas.get(activo)
            if op and (f.ticker != op.ticker or f.ts >= op.cierre):
                self._bloque_cerrado(activo)
                op = None
            if op:
                self._gestionar(op, f)
                return
            if self.estado.operado.get(activo) == f.ticker or not self.puede_entrar():
                return
            est: Estrategia = self.estrategias[activo]
            senal = est.entrada(f)
            if not senal:
                return
            lado, ask = senal
            op = self.ej.entrar(activo, f, lado, ask, self.contratos, est.cfg)
            if op is None:
                return                        # no se llenó: se vuelve a intentar en la foto siguiente
            self.estado.operado[activo] = f.ticker
            self.estado.abiertas[activo] = op
            self.estado.guardar()
            log(f"{activo} ENTRA {lado.upper()} {op.contratos:g} a {op.entrada:.3f} | TP {op.objetivo:.3f} | "
                f"delta {op.delta:.1f} | quedan {(op.cierre - f.ts) / 60:.1f} min | {f.ticker}")

    def _gestionar(self, op: Operacion, f: Foto):
        est = self.estrategias[op.activo]
        cambio = self.ej.revisar(op)                     # modo real: ¿se llenó la orden del TP?
        if op.abiertos() > 0:
            if not op.saliendo:
                decision = est.salida(op, f)
                if decision == "stop":
                    op.saliendo = True
                    log(f"{op.activo} STOP: bid {f.bid(op.lado):.3f} (entrada {op.entrada:.3f})")
                    self.ej.stop(op, f)
                    cambio = True
                elif decision == "tp":
                    cambio = self.ej.tp(op, f) or cambio
            else:
                self.ej.stop(op, f)                      # stop a medias: sigue vendiendo lo que quede
                cambio = True
        if op.abiertos() <= 0:
            del self.estado.abiertas[op.activo]
            self._terminar(op)
        elif cambio:
            self.estado.guardar()

    def _bloque_cerrado(self, activo):
        op = self.estado.abiertas.pop(activo)
        self.estado.pendientes.append(op)
        self.estado.guardar()
        log(f"{activo} bloque cerrado con {op.abiertos():g} contratos abiertos: espero el resultado de {op.ticker}")

    # ------------------------------------------------------------------ liquidación
    def liquidar(self, op: Operacion, ganador: str):
        """Cierre del bloque: lo que quede abierto paga 1 o 0, sin comisión."""
        with self.lock:
            self.ej.revisar(op)
            n = op.abiertos()
            if n > 0:
                op.salir(n, 1.0 if ganador == op.lado else 0.0, 0.0, "Cierre", op.cierre)
            if op in self.estado.pendientes:
                self.estado.pendientes.remove(op)
            self._terminar(op)

    def _terminar(self, op: Operacion):
        fila = self.registro.anotar(op)
        self.estado.guardar()
        hoy = self.registro.resumen(datetime.now(CDT).strftime("%Y-%m-%d"))
        log(f"{op.activo} {fila['resultado']} {fila['pnl_usd']:+.2f} $ ({fila['salida']} a {fila['precio_salida']}) | "
            f"hoy {hoy['ops']} ops, {hoy['ganadas']}G/{hoy['perdidas']}P, {hoy['usd']:+.2f} $")

    # ------------------------------------------------------------------ controles
    def puede_entrar(self):
        if self.estado.pausado:
            return False
        if self.max_perdida_dia > 0 or self.meta_dia > 0:
            hoy = datetime.now(CDT).strftime("%Y-%m-%d")
            usd = self.registro.resumen(hoy)["usd"]
            motivo = None
            if self.max_perdida_dia > 0 and usd <= -self.max_perdida_dia:
                motivo = f"hoy se perdieron {-usd:.2f} $ (límite {self.max_perdida_dia:g} $)"
            elif self.meta_dia > 0 and usd >= self.meta_dia:
                motivo = f"hoy se ganaron {usd:.2f} $ (meta {self.meta_dia:g} $)"
            if motivo:
                if self.aviso_limite != hoy:
                    self.aviso_limite = hoy
                    log(f"LÍMITE DIARIO: {motivo}; no entro hasta mañana (CDT)")
                return False
        return True

    def limite_diario(self):
        return self.aviso_limite == datetime.now(CDT).strftime("%Y-%m-%d")

    def pausar(self, si: bool):
        with self.lock:
            self.estado.pausado = si
            self.estado.encendido_desde = None if si else time.time()
            self.estado.guardar()
        log("APAGADO: no abre operaciones nuevas (las abiertas se siguen cuidando hasta que terminen)" if si
            else "ENCENDIDO: busca entradas")
