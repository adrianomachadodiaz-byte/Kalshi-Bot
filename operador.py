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

from estrategia import BLOQUE, Estrategia, Foto, Operacion

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

    def archivar(self):
        """Empieza las estadísticas de cero. No borra nada: el historial queda al lado con la fecha."""
        n = len([f for f in self.filas if f.get("modo") == self.modo])
        copia = None
        if self.ruta.exists():
            copia = self.ruta.with_name("operaciones-" + time.strftime("%Y%m%d-%H%M%S") + ".csv")
            os.replace(self.ruta, copia)
        self.filas = []
        return n, (copia.name if copia else None)

    def resumen(self, fecha=None, activo=None, curva=False, desde=None):
        """Las cuentas del panel. `curva` solo para el total: en el resumen de hoy
        sería un único punto y multiplicaría el tamaño del JSON por nada.
        `desde` recorta a las operaciones de esa fecha en adelante.

        El panel pide ~20 resúmenes por segundo (3 periodos x 6 criptos), así que el
        resultado se guarda hasta que cambia el número de operaciones.
        """
        sello = (len(self.filas), self.modo)
        if getattr(self, "_sello", None) != sello:       # entró una operación: a recalcular
            self._sello, self._cache = sello, {}
        clave = (fecha, activo, curva, desde)
        if clave not in self._cache:
            self._cache[clave] = self._resumen(fecha, activo, curva, desde)
        return self._cache[clave]

    def _resumen(self, fecha, activo, curva, desde):
        filas = [f for f in self.filas
                 if f.get("modo") == self.modo
                 and (fecha is None or f["fecha"] == fecha)
                 and (desde is None or f["fecha"] >= desde)
                 and (activo is None or f.get("activo") == activo)]
        pnl = [float(f["pnl_usd"]) for f in filas]
        g = [p for p in pnl if p > 0]
        p_ = [p for p in pnl if p <= 0]
        # racha: cuántas seguidas, contando desde la última hacia atrás. Positiva si son
        # ganadas, negativa si son perdidas. Dice más que el porcentaje de acierto solo.
        racha = 0
        for x in reversed(pnl):
            if racha == 0:
                racha = 1 if x > 0 else -1
            elif (x > 0) == (racha > 0):
                racha += 1 if racha > 0 else -1
            else:
                break
        # la mejor racha de ganadas que ha habido, no solo la de ahora
        mejor_racha, n = 0, 0
        for x in pnl:
            n = n + 1 if x > 0 else 0
            mejor_racha = max(mejor_racha, n)
        # el bajón más hondo: cuánto se llegó a perder desde el punto más alto.
        # Es el número que dice si una racha mala te habría dejado sin saldo.
        acum, techo, bajon = 0.0, 0.0, 0.0
        for x in pnl:
            acum += x
            techo = max(techo, acum)
            bajon = min(bajon, acum - techo)
        # acierto mínimo para no perder: en Kalshi, si entras a 0,85 ganas 0,15 cuando
        # aciertas y pierdes 0,85 cuando fallas, así que el punto de equilibrio es
        # justo el precio medio de entrada.
        entradas = [float(f["entrada"]) for f in filas if f.get("entrada") not in (None, "")]
        # curva de la ganancia acumulada, un punto por día, para dibujarla de un vistazo
        porDia = {}
        for f in filas:
            porDia[f["fecha"]] = porDia.get(f["fecha"], 0.0) + float(f["pnl_usd"])
        acum, puntos = 0.0, []
        for d in sorted(porDia):
            acum += porDia[d]
            puntos.append([d, round(acum, 2)])
        # lo puesto en juego (entrada x contratos): da la rentabilidad sobre lo invertido
        def num(x):
            try:
                return float(x)
            except (TypeError, ValueError):
                return None
        invertido = sum((num(f.get("entrada")) or 0) * (num(f.get("contratos")) or 0) for f in filas)
        centavos = [c for c in (num(f.get("pnl_c")) for f in filas) if c is not None]
        # por lado (YES/NO): cuántas y qué acierto lleva cada uno
        lados = {}
        for f, x in zip(filas, pnl):
            l = lados.setdefault(f.get("lado") or "?", [0, 0])
            l[0] += 1
            l[1] += x > 0
        lados = {k: dict(ops=v[0], wr=round(100 * v[1] / v[0], 1)) for k, v in lados.items()}
        # cómo cerraron: TP, Exit (stop), al cierre del bloque... (columna "salida")
        salidas = {}
        for f in filas:
            for m in (f.get("salida") or "?").split("+"):
                salidas[m] = salidas.get(m, 0) + 1
        mins = [m for m in (num(f.get("min_restantes")) for f in filas) if m is not None]
        dias_ops = len({f["fecha"] for f in filas})
        return dict(ops=len(pnl), ganadas=len(g), perdidas=len(p_), usd=round(sum(pnl), 2),
                    invertido=round(invertido, 2),
                    roi=round(100 * sum(pnl) / invertido, 2) if invertido else None,
                    c_media=round(sum(centavos) / len(centavos), 2) if centavos else None,
                    ops_dia=round(len(pnl) / dias_ops, 1) if dias_ops else None,
                    recuperacion=round(sum(pnl) / -bajon, 2) if bajon < 0 else None,
                    lados=lados, salidas=salidas,
                    min_entrada=round(sum(mins) / len(mins), 1) if mins else None,
                    wr=round(100 * len(g) / len(pnl), 1) if pnl else None,
                    media_ganada=round(sum(g) / len(g), 2) if g else None,
                    media_perdida=round(sum(p_) / len(p_), 2) if p_ else None,
                    media=round(sum(pnl) / len(pnl), 3) if pnl else None,
                    mejor=round(max(pnl), 2) if pnl else None,
                    peor=round(min(pnl), 2) if pnl else None,
                    racha=racha, mejor_racha=mejor_racha,
                    ganado=round(sum(g), 2), perdido=round(sum(p_), 2),
                    ratio=round(sum(g) / -sum(p_), 2) if p_ and sum(p_) else None,
                    bajon=round(bajon, 2), entrada_media=round(sum(entradas) / len(entradas), 4) if entradas else None,
                    equilibrio=round(100 * sum(entradas) / len(entradas), 1) if entradas else None,
                    dias=len(puntos), curva=puntos[-60:] if curva else [])


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
        self.filtro = None            # FiltroNoticias; si es None, no se filtra nada
        self.bloqueado = {}           # activo -> por qué no entra en este bloque (lo enseña el panel)
        self.intento = {}             # activo -> lo más cerca que estuvo de entrar en el bloque actual

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
            # filtro de noticias: si el bloque cierra sobre un dato de alto impacto, no se entra
            porque = self.filtro.motivo(f.cierre, f.ticker, activo) if self.filtro else None
            if porque != self.bloqueado.get(activo):
                self.bloqueado[activo] = porque
                if porque:
                    log(f"{activo} filtro: no entro en {f.ticker} · {porque}")
            if porque:
                return
            est: Estrategia = self.estrategias[activo]
            self._anotar_intento(activo, f, est)
            senal = est.entrada(f)
            if not senal:
                return
            lado, ask = senal
            op = self.ej.entrar(activo, f, lado, ask, est.cfg.contratos, est.cfg)
            if op is None:
                return                        # no se llenó: se vuelve a intentar en la foto siguiente
            self.estado.operado[activo] = f.ticker
            self.estado.abiertas[activo] = op
            self.estado.guardar()
            meta = "sin TP, al cierre" if op.sin_tp else f"TP {op.objetivo:.3f}"
            log(f"{activo} ENTRA {lado.upper()} {op.contratos:g} a {op.entrada:.3f} | {meta} | "
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

    def _anotar_intento(self, activo, f, est):
        """Guarda lo más cerca que estuvo de entrar: para explicar luego por qué no entró."""
        c = est.cfg
        i = self.intento.get(activo)
        if not i or i["ticker"] != f.ticker:
            if i and i["vueltas"]:
                self._porque_no(activo, i)
            i = dict(ticker=f.ticker, ya=None, na=None, dmax=None, vueltas=0,
                     emin=c.entrada_min, emax=c.entrada_max, dmin=c.delta, delay=c.delay,
                     usa_delta=c.usa_delta)
            self.intento[activo] = i
        if f.segundo() < BLOQUE - c.delay:
            return                                  # fuera de la ventana no cuenta
        i["vueltas"] += 1
        d = f.delta()
        if d is not None and (i["dmax"] is None or d > i["dmax"]):
            i["dmax"] = d
        for lado in ("yes", "no"):
            a = f.ask(lado)
            k = "ya" if lado == "yes" else "na"
            if a is not None and (i[k] is None or a > i[k]):
                i[k] = a

    def _porque_no(self, activo, i):
        """Una línea en el registro diciendo qué faltó. Si no, el bloque sin operar es un misterio."""
        n = lambda x: "–" if x is None else f"{x:.3f}"
        if not i.get("usa_delta"):
            falta = (f"ningún lado llegó al rango {i['emin']:.2f}-{i['emax']:.2f} "
                     f"(lo más alto: YES {n(i['ya'])}, NO {n(i['na'])})")
        elif i["dmax"] is None:
            falta = "no hubo precio del índice"
        elif i["dmax"] < i["dmin"] - 1e-9:
            falta = f"Delta se quedó en {i['dmax']:+.1f} y pide {i['dmin']:g}"
        else:
            falta = (f"ningún lado llegó al rango {i['emin']:.2f}-{i['emax']:.2f} "
                     f"(lo más alto: YES {n(i['ya'])}, NO {n(i['na'])})")
        log(f"{activo} sin entrada en {i['ticker']}: {falta} · mirando los últimos {i['delay']:g} s")

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
