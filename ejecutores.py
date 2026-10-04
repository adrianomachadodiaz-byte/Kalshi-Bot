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


TOPE_TP = 0.99      # el TP nunca se pone por encima de esto


def log_cada(clave, msg, seg=30):
    if time.time() - _ultimo_log.get(clave, 0) >= seg:
        _ultimo_log[clave] = time.time()
        log(msg)


def nueva_operacion(activo, f: Foto, lado, entrada, n, cfg: Config, comision_total, shard=None):
    return Operacion(activo=activo, ticker=f.ticker, lado=lado, entrada=round(entrada, 4), contratos=n, cierre=f.cierre,
                     objetivo=round(min(entrada + cfg.tp, 1.0), 4),
                     sin_tp=(entrada + cfg.tp) > TOPE_TP + 1e-9, ts_entrada=f.ts, delta=f.delta(),
                     atraso=f.atraso, comision_entrada=comision_total, shard=shard)


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
        if op.sin_tp:
            return False
        op.salir(op.abiertos(), op.objetivo, 0.0, "TP", f.ts)
        return True

    def stop(self, op, f):
        bid = f.bid(op.lado) or 0.0
        n = op.abiertos()
        op.salir(n, bid, comision(bid) * n, "Exit", f.ts)


class EjecutorReal:
    modo = "real"

    def __init__(self, k: Kalshi, contratos, desliz_entrada=0.02, n_activos=1, espera_entrada=2.0):
        # contratos: dict activo -> nº de contratos (antes era un único número para todos)
        self.k = k
        self.contratos = contratos
        self.desliz = desliz_entrada
        self.espera = espera_entrada   # segundos que la orden de entrada se queda puesta (0 = IOC)
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
        # cada activo puede llevar un nº de contratos distinto: se reserva la suma de todos
        porc = self.contratos if isinstance(self.contratos, dict) else {}
        contratos = porc.get(activo, 1.0) if porc else self.contratos
        necesario = sum(porc.values()) if porc else contratos * self.n_activos
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


    # ------------------------------------------------------------------ precio límite de la entrada
    def limite_entrada(self, ask, cfg):
        """(límite de la orden, si cruzó el spread). Separado para poder probarlo sin tocar Kalshi."""
        # Cuánto se puede pagar de más para asegurar el llenado (como NightShark, pero acotado).
        #
        # Solo se cruza el spread en UN caso: cuando el TP cae JUSTO en el tope de 0.99
        # (entrada_max + TP == 0.99). Ahí el TP se queda en 0.99 pase lo que pase, así que pagar
        # 1-2 c más no cambia si ganas, solo cuánto ganas, y conviene no quedarse fuera.
        #
        # NO se cruza cuando entrada_max + TP PASA de 0.99, porque entonces la operación es `sin_tp`:
        # no hay orden de venta y se cobra 1.00 al cerrar el bloque. Ahí cada centavo que pagas de más
        # sale directo de una ganancia de 1-2 centavos. Medido sobre 38 días (entrada 0.98, Delta 140,
        # últimos 5 min, 549 operaciones con 5 fallos):
        #   pagar 0.98 (sin cruce) -> +0.95 c/op, margen +0.95 puntos de winrate
        #   pagar 0.99 (cruce +1c) -> +0.02 c/op, margen +0.02   <- se come toda la ventaja
        #   pagar 1.00 (cruce +2c) -> -0.91 c/op, margen -100     <- pérdida garantizada
        #
        # Por debajo del tope, pagar de más SUBE el TP y baja el acierto, así que tampoco se cruza.
        # Medido en el backtest de 30 días (BTC, 100 contratos, TP topado en 0.99):
        #   pagar +2 c siempre     -> +2.43 c/op (-$835); harían falta 2.3 llenados extra al día
        #   pagar +2 c solo arriba -> +3.68 c/op (-$252); harían falta 0.7 llenados extra al día
        #   no pagar de más        -> +4.22 c/op
        sin_tp_cfg = (cfg.entrada_max + cfg.tp) > TOPE_TP + 1e-9      # no habrá orden de TP
        tp_justo_en_el_tope = (not sin_tp_cfg) and (cfg.entrada_max + cfg.tp) >= TOPE_TP - 1e-9
        cruza = tp_justo_en_el_tope and ask >= cfg.entrada_max - 1e-9
        if cruza:
            limite = ask + self.desliz                       # el TP ya está en el tope: cruzo para llenar
        else:
            limite = min(ask + self.desliz, cfg.entrada_max)  # nunca paga más que el máximo del EntryRange
        limite = min(limite, TOPE_TP)                        # jamás una orden de entrada a 1.00
        return limite, cruza

    # ------------------------------------------------------------------ entrada
    def entrar(self, activo, f, lado, ask, n, cfg):
        m = self.info[f.ticker]
        if not self.fondos.get(f.ticker):
            log_cada(f"fondos{f.ticker}", f"{activo} señal de entrada pero sin saldo en el shard: no entro")
            return None
        limite, cruza = self.limite_entrada(ask, cfg)
        if lado == "yes":
            libro, precio_yes = "bid", ajustar_tick(limite, m["rangos"], "arriba")
        else:
            libro, precio_yes = "ask", ajustar_tick(1 - limite, m["rangos"], "abajo")
        # Con espera > 0 la orden se queda puesta ese rato (como NightShark, que la deja 2 s) para
        # atrapar la liquidez que llega enseguida; después se cancela lo que quede. Con espera 0 es IOC.
        tif = "good_till_canceled" if self.espera > 0 else "immediate_or_cancel"
        try:
            r = self.k.crear_orden(f.ticker, libro, precio_yes, n, tif, m["exchange_index"])
        except ErrorKalshi as e:
            if e.status < 500:
                log_cada(f"entrada{f.ticker}", f"{activo} orden de entrada rechazada: {e}")
                return None
            return self._entrada_dudosa(activo, f, lado, ask, cfg, m, e)
        except requests.RequestException as e:
            return self._entrada_dudosa(activo, f, lado, ask, cfg, m, e)

        llenos, comision = r["llenos"], r["comision_media"] * r["llenos"]
        # OJO con los dos sistemas de precio:
        #   crear_orden -> average_fill_price, que SIEMPRE es del libro YES (hay que invertirlo para NO)
        #   llenados()  -> precio del LADO comprado (ya viene en los términos de la operación)
        # Mezclarlos hizo que una compra de NO a 0.86 se anotara como 0.14, con el TP en 0.27: la orden
        # del TP salía por debajo del mercado y vendía la posición al instante (-$0.06 en vez de +$0.11).
        py = r["precio_yes"]                                  # del libro YES, o None
        entrada = (py if lado == "yes" else 1 - py) if py is not None else None
        if tif == "good_till_canceled" and r["order_id"]:
            llenos, entrada, comision = self._descansar_y_cerrar(activo, f, r["order_id"], lado, m, n)
            if llenos is None:                  # no se pudo dejar la orden en un estado seguro
                return self._entrada_dudosa(activo, f, lado, ask, cfg, m, "no pude cerrar la orden de entrada")

        if llenos <= 0:
            log_cada(f"nolleno{f.ticker}", f"{activo} {lado.upper()} a {ask:.3f}: la orden no se llenó, reintento", 10)
            return None
        if entrada is None:
            entrada = ask
        # Red de seguridad: un precio de entrada fuera del EntryRange significa que algo se contabilizó mal.
        # Antes que abrir con un TP disparatado, se avisa y se revisa la posición a mano.
        tope_entrada = (cfg.entrada_max + self.desliz) if cruza else cfg.entrada_max
        if not (cfg.entrada_min - 0.05 <= entrada <= tope_entrada + 1e-9):
            log(f"{activo} ATENCIÓN: el precio de entrada que devolvió Kalshi ({entrada:.3f}) está fuera de "
                f"{cfg.entrada_min - 0.05:.2f}-{tope_entrada:.2f}. No abro la operación; revisa la posición a mano.")
            return None
        # average_fee_paid es por contrato (NightShark lo multiplica por la cantidad)
        op = nueva_operacion(activo, f, lado, entrada, llenos, cfg, comision, m["exchange_index"])
        if llenos < n:
            log(f"{activo} entrada parcial: {llenos:g} de {n:g} contratos")
        self._poner_tp(op)
        return op

    def _descansar_y_cerrar(self, activo, f, oid, lado, m, n):
        """Deja la orden puesta hasta `self.espera` s, la cancela y devuelve lo REALMENTE ejecutado.

        Devuelve (contratos, precio medio DEL LADO COMPRADO, comisiones $) — el precio ya viene en los
        términos de la operación, NO del libro YES, así que quien lo use no debe invertirlo.
        Devuelve (None, None, None) si no se puede confirmar que la orden dejó de estar abierta:
        ahí no se acepta la posición a ciegas.
        """
        shard = m["exchange_index"]
        fin = time.time() + self.espera
        while time.time() < fin:
            time.sleep(min(0.25, max(0.05, fin - time.time())))
            try:
                o = self.k.orden(oid, shard)
            except Exception:  # noqa: BLE001
                continue                        # si no puedo mirarla, sigo esperando y luego cancelo igual
            if o["restantes"] <= 1e-9:
                break                           # ya se llenó entera: no hay nada que cancelar
        # Congelar lo ejecutado: cancelar lo que quede ANTES de aceptar el costo de entrada.
        cerrada = False
        for intento in range(3):
            try:
                self.k.cancelar(oid, shard)
            except Exception as e:  # noqa: BLE001
                log(f"{activo} no pude cancelar el resto de la entrada ({e}), reintento {intento + 1}/3")
            try:
                o = self.k.orden(oid, shard)
                if o["estado"] != "resting" or o["restantes"] <= 1e-9:
                    cerrada = True
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.4)
        if not cerrada:
            log(f"{activo} ATENCIÓN: la orden de entrada {oid} puede seguir abierta en Kalshi; "
                f"revísala a mano. No abro la operación para no contar mal la posición.")
            return None, None, None
        try:                                    # los fills mandan: traen precio medio y comisiones reales
            c, precio, fee = self.k.llenados(oid, lado)
            return c, precio, fee
        except Exception as e:  # noqa: BLE001
            log(f"{activo} no pude leer los llenados de la entrada ({e}); uso lo que reportó la orden")
            try:
                o = self.k.orden(oid, shard)
                return o["llenos"], None, o["comision"]
            except Exception:  # noqa: BLE001
                return None, None, None

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
        if op.sin_tp:          # entrada + TP pasa de 0.99: no hay orden, se cobra al cerrar el bloque
            return
        m = self.info.get(op.ticker, {})
        self.ultimo_tp[op.ticker] = time.time()
        if op.lado == "yes":
            libro, precio_yes = "ask", ajustar_tick(op.objetivo, m.get("rangos"), "cerca")
        else:
            libro, precio_yes = "bid", ajustar_tick(1 - op.objetivo, m.get("rangos"), "cerca")
        try:
            # Kalshi solo acepta reduce_only en órdenes IOC: el TP (que queda puesto) va sin él.
            # Por eso el stop no vende hasta confirmar que el TP quedó cancelado (ver stop()).
            r = self.k.crear_orden(op.ticker, libro, precio_yes, op.abiertos(), "good_till_canceled", op.shard)
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
        if (not op.sin_tp and op.abiertos() > 0 and not op.saliendo and time.time() < op.cierre
                and time.time() - self.ultimo_tp.get(op.ticker, 0) > 5):
            self._poner_tp(op)
            return True
        return False

    def tp(self, op, f):
        """El bid llegó al objetivo. Con la orden del TP puesta, Kalshi la llena solo; sin ella, se vende ya."""
        if op.tp_orden or op.sin_tp:
            return False
        return self._vender(op, op.objetivo, "TP", f.ts)

    # ------------------------------------------------------------------ stop
    def stop(self, op, f):
        if op.tp_orden:
            try:
                self.k.cancelar(op.tp_orden, op.shard)
            except Exception as e:  # noqa: BLE001
                log(f"{op.activo} no pude cancelar el TP: {e}")
            self.revisar(op)                   # registra lo que el TP haya llenado y ve si ya no está puesta
            if op.tp_orden:
                # el TP sigue puesto: vender ahora podría dejar una posición al revés si después se llena
                log(f"{op.activo} el TP todavía figura puesto; reintento cancelarlo en el segundo siguiente")
                return
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
