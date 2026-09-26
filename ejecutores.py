"""Cómo se ejecutan las decisiones.

EjecutorSim:  no manda órdenes (solo lo usa probar_con_datos.py). Llena como el backtest: compra al ask,
              TP al objetivo cuando el bid llega, stop al bid de ese segundo, comisión taker en la entrada y en el stop.
EjecutorReal: manda órdenes a Kalshi (es el que usa el bot).
  - Entrada: orden IOC al ask + DESLIZ_ENTRADA, con tope en el máximo del EntryRange (se llena al mejor precio
    disponible; si no se llena, reintenta en el segundo siguiente mientras siga cumpliéndose todo).
  - TP: orden límite que queda puesta (reduce_only) en entrada + TP, igual que el backtest (TP límite, sin comisión taker).
  - Stop: cancela el TP y vende lo que quede con IOC reduce_only a 0.01 (se llena al mejor bid), como NightShark.
"""
import time

import requests

from estrategia import Config, Foto, Operacion, comision
from kalshi import ErrorKalshi, Kalshi, ajustar_tick
from operador import log

_ultimo_log = {}


def log_cada(clave, msg, seg=30):
    if time.time() - _ultimo_log.get(clave, 0) >= seg:
        _ultimo_log[clave] = time.time()
        log(msg)


def nueva_operacion(activo, f: Foto, lado, entrada, n, cfg: Config, comision_total, shard=None):
    return Operacion(activo=activo, ticker=f.ticker, lado=lado, entrada=round(entrada, 4), contratos=n, cierre=f.cierre,
                     objetivo=round(min(entrada + cfg.tp, 0.99), 4), ts_entrada=f.ts, delta=f.delta(),
                     comision_entrada=comision_total, shard=shard)


class EjecutorSim:
    modo = "simulacion"

    def __init__(self):
        self.info = {}

    def preparar_bloque(self, activo, m, estado):
        return "ok"

    def entrar(self, activo, f, lado, ask, n, cfg):
        return nueva_operacion(activo, f, lado, ask, n, cfg, comision(ask) * n)

    def revisar(self, op):
        return False

    def tp(self, op, f):
        op.salir(op.abiertos(), op.objetivo, 0.0, "TP", f.ts)
        return True

    def stop(self, op, f):
        bid = f.bid(op.lado) or 0.0
        n = op.abiertos()
        op.salir(n, bid, comision(bid) * n, "Exit", f.ts)


class EjecutorReal:
    modo = "real"

    def __init__(self, k: Kalshi, contratos, desliz_entrada=0.02, n_activos=1):
        self.k = k
        self.contratos = contratos
        self.desliz = desliz_entrada
        self.n_activos = n_activos
        self.info = {}          # ticker -> info del mercado (shard, ticks)
        self.fondos = {}        # ticker -> True si el shard tiene saldo para operar
        self.ultimo_tp = {}     # ticker -> último intento de poner el TP

    # ------------------------------------------------------------------ inicio de cada bloque
    def preparar_bloque(self, activo, m, estado):
        """Al empezar un bloque: no operar si ya hay una posición que el bot no conoce, y dejar saldo en el shard."""
        ticker, shard = m["ticker"], m["exchange_index"]
        op = estado.abiertas.get(activo)
        if not (op and op.ticker == ticker):
            n, _ = self.k.posicion(ticker, shard)
            if n:
                log(f"{activo} ya hay una posición de {n:g} en {ticker} que el bot no abrió: no opero este bloque")
                return "ocupado"
        self.fondos[ticker] = self._asegurar_fondos(activo, shard)
        return "ok"

    def _asegurar_fondos(self, activo, shard):
        """Cada contrato puede costar hasta 1 $: el shard del mercado necesita CONTRATOS x 1 $ por activo."""
        contratos = self.contratos
        necesario = contratos * self.n_activos
        disponible = self.k.saldo(shard)
        if disponible >= necesario - 1e-6:
            return True
        if not shard:
            log(f"{activo} saldo insuficiente: {disponible:.2f} $ (hacen falta ~{necesario:g} $)")
            return disponible >= contratos
        falta = necesario - disponible
        origen = self.k.saldo(0)
        if origen < falta:
            log(f"{activo} el shard {shard} tiene {disponible:.2f} $ y el shard 0 solo {origen:.2f} $: no alcanza para "
                f"{necesario:g} $")
            return disponible >= contratos
        try:
            self.k.transferir_a_shard(falta, shard)
            log(f"{activo} paso {falta:.2f} $ del shard 0 al shard {shard}")
        except Exception as e:  # noqa: BLE001
            log(f"{activo} no pude pasar saldo al shard {shard}: {e}")
        for _ in range(20):                     # la transferencia tarda un poco
            time.sleep(0.5)
            if self.k.saldo(shard) >= necesario - 1e-6:
                return True
        return self.k.saldo(shard) >= contratos

    # ------------------------------------------------------------------ entrada
    def entrar(self, activo, f, lado, ask, n, cfg):
        m = self.info[f.ticker]
        if not self.fondos.get(f.ticker):
            log_cada(f"fondos{f.ticker}", f"{activo} señal de entrada pero sin saldo en el shard: no entro")
            return None
        limite = min(ask + self.desliz, cfg.entrada_max)     # nunca paga más que el máximo del EntryRange
        if lado == "yes":
            libro, precio_yes = "bid", ajustar_tick(limite, m["rangos"], "arriba")
        else:
            libro, precio_yes = "ask", ajustar_tick(1 - limite, m["rangos"], "abajo")
        try:
            r = self.k.crear_orden(f.ticker, libro, precio_yes, n, "immediate_or_cancel", m["exchange_index"])
        except ErrorKalshi as e:
            if e.status < 500:
                log_cada(f"entrada{f.ticker}", f"{activo} orden de entrada rechazada: {e}")
                return None
            return self._entrada_dudosa(activo, f, lado, ask, cfg, m, e)
        except requests.RequestException as e:
            return self._entrada_dudosa(activo, f, lado, ask, cfg, m, e)
        if r["llenos"] <= 0:
            log_cada(f"nolleno{f.ticker}", f"{activo} {lado.upper()} a {ask:.3f}: la orden IOC no se llenó, reintento", 10)
            return None
        py = r["precio_yes"]
        entrada = (py if lado == "yes" else 1 - py) if py is not None else ask
        # average_fee_paid es por contrato (NightShark lo multiplica por la cantidad)
        op = nueva_operacion(activo, f, lado, entrada, r["llenos"], cfg, r["comision_media"] * r["llenos"],
                             m["exchange_index"])
        if r["llenos"] < n:
            log(f"{activo} entrada parcial: {r['llenos']:g} de {n:g} contratos")
        self._poner_tp(op)
        return op

    def _entrada_dudosa(self, activo, f, lado, ask, cfg, m, err):
        """No se sabe si la orden entró: se mira la posición antes de hacer nada más."""
        log(f"{activo} respuesta dudosa al entrar ({err}); reviso la posición")
        time.sleep(1)
        try:
            n, costo = self.k.posicion(f.ticker, m["exchange_index"])
        except Exception as e:  # noqa: BLE001
            log(f"{activo} no pude ver la posición ({e}); no opero este bloque")
            self.fondos[f.ticker] = False     # no volver a intentar en este bloque
            return None
        if not n:
            return None
        lado_real = "yes" if n > 0 else "no"
        entrada = costo / abs(n) if costo else ask
        op = nueva_operacion(activo, f, lado_real, entrada, abs(n), cfg, comision(entrada) * abs(n), m["exchange_index"])
        log(f"{activo} la orden sí entró: {abs(n):g} {lado_real.upper()} a ~{entrada:.3f}")
        self._poner_tp(op)
        return op

    # ------------------------------------------------------------------ TP
    def _poner_tp(self, op: Operacion):
        m = self.info.get(op.ticker, {})
        self.ultimo_tp[op.ticker] = time.time()
        if op.lado == "yes":
            libro, precio_yes = "ask", ajustar_tick(op.objetivo, m.get("rangos"), "cerca")
        else:
            libro, precio_yes = "bid", ajustar_tick(1 - op.objetivo, m.get("rangos"), "cerca")
        try:
            r = self.k.crear_orden(op.ticker, libro, precio_yes, op.abiertos(), "good_till_canceled", op.shard,
                                   reduce_only=True)
            op.tp_orden, op.tp_llenos, op.tp_comision = r["order_id"], 0.0, 0.0
            self.revisar(op)
        except Exception as e:  # noqa: BLE001
            log(f"{op.activo} no pude poner el TP ({e}); lo reintento y mientras tanto lo vigilo con el bid")

    def revisar(self, op: Operacion):
        """¿Se llenó (parte de) la orden del TP? Si falta la orden, se vuelve a poner cada 5 s."""
        if op.tp_orden:
            try:
                o = self.k.orden(op.tp_orden, op.shard)
            except Exception as e:  # noqa: BLE001
                log_cada(f"orden{op.ticker}", f"{op.activo} no pude leer la orden del TP: {e}")
                return False
            nuevos = round(o["llenos"] - op.tp_llenos, 2)
            cambio = False
            if nuevos > 0:
                op.salir(min(nuevos, op.abiertos()), op.objetivo, max(o["comision"] - op.tp_comision, 0.0), "TP", time.time())
                op.tp_llenos, op.tp_comision = o["llenos"], o["comision"]
                cambio = True
                log(f"{op.activo} TP lleno: {nuevos:g} contratos a {op.objetivo:.3f}")
            if o["estado"] != "resting":
                op.tp_orden = None
                cambio = True
            return cambio
        if (op.abiertos() > 0 and not op.saliendo and time.time() < op.cierre
                and time.time() - self.ultimo_tp.get(op.ticker, 0) > 5):
            self._poner_tp(op)
            return True
        return False

    def tp(self, op, f):
        """El bid llegó al objetivo. Con la orden del TP puesta, Kalshi la llena solo; sin ella, se vende ya."""
        if op.tp_orden:
            return False
        return self._vender(op, op.objetivo, "TP", f.ts)

    # ------------------------------------------------------------------ stop
    def stop(self, op, f):
        if op.tp_orden:
            try:
                self.k.cancelar(op.tp_orden, op.shard)
            except Exception as e:  # noqa: BLE001
                log(f"{op.activo} no pude cancelar el TP: {e}")
            self.revisar(op)                   # registra lo que el TP haya llenado antes de cancelarse
            op.tp_orden = None
        if op.abiertos() > 0:
            self._vender(op, 0.01, "Exit", f.ts)

    def _vender(self, op, precio_lado, motivo, ts):
        """Venta IOC reduce_only de lo que quede abierto. precio_lado 0.01 = a cualquier precio (mejor bid)."""
        m = self.info.get(op.ticker, {})
        n = op.abiertos()
        if op.lado == "yes":
            libro, precio_yes = "ask", ajustar_tick(precio_lado, m.get("rangos"), "abajo")
        else:
            libro, precio_yes = "bid", ajustar_tick(1 - precio_lado, m.get("rangos"), "arriba")
        try:
            r = self.k.crear_orden(op.ticker, libro, precio_yes, n, "immediate_or_cancel", op.shard, reduce_only=True)
        except Exception as e:  # noqa: BLE001
            log(f"{op.activo} falló la venta ({motivo}): {e}; reintento en el segundo siguiente")
            return False
        if r["llenos"] <= 0:
            log(f"{op.activo} la venta ({motivo}) no se llenó; reintento en el segundo siguiente")
            return False
        py = r["precio_yes"]
        precio = (py if op.lado == "yes" else 1 - py) if py is not None else precio_lado
        op.salir(min(r["llenos"], n), round(precio, 4), r["comision_media"] * r["llenos"], motivo, ts)
        log(f"{op.activo} vendidos {r['llenos']:g} a {precio:.3f} ({motivo})")
        return True
