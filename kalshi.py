"""Cliente mínimo de la API de Kalshi (firma RSA-PSS, igual que kalshi-recorder y NightShark).

Órdenes con el endpoint V2 (/portfolio/events/orders): todo se cotiza del lado YES.
  bid = comprar YES, ask = vender YES. Comprar NO a p  == vender YES a 1 - p.
Los mercados 15m viven en un shard (exchange_index, hoy el 2): órdenes, saldo y posiciones van con ese índice.
"""
import base64
import math
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, urlparse

import requests

SERIES = {"BTC": "KXBTC15M", "ETH": "KXETH15M", "SOL": "KXSOL15M", "XRP": "KXXRP15M",
          "DOGE": "KXDOGE15M", "HYPE": "KXHYPE15M", "BNB": "KXBNB15M"}


class ErrorKalshi(Exception):
    def __init__(self, status, texto):
        super().__init__(f"HTTP {status}: {texto[:300]}")
        self.status = status
        self.texto = texto


def num(v):
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def epoch(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() if iso else None


def cargar_clave(pem_texto="", ruta=""):
    from cryptography.hazmat.primitives import serialization
    if pem_texto:
        data = pem_texto.replace("\\n", "\n").encode()
    elif ruta:
        data = Path(ruta).read_bytes()
    else:
        return None
    return serialization.load_pem_private_key(data, password=None)


def mejor_bid(niveles):
    """Precio más alto de una lista [precio, cantidad] (dólares como texto, o centavos)."""
    best = None
    for lv in niveles or []:
        try:
            p, q = float(lv[0]), float(lv[1])
        except (TypeError, ValueError, IndexError):
            continue
        if q <= 0:
            continue
        if p > 1:
            p /= 100
        best = p if best is None or p > best else best
    return best


def ajustar_tick(p, rangos, hacia="cerca"):
    """Lleva un precio al tick válido del mercado (0.001 bajo 0.10 y sobre 0.90, 0.01 en el medio)."""
    rangos = rangos or [(0.0, 1.0, 0.01)]
    for ini, fin, paso in rangos:
        if ini <= p < fin or (p >= fin and fin >= 1.0):
            k = (p - ini) / paso
            k = math.floor(k + 1e-9) if hacia == "abajo" else math.ceil(k - 1e-9) if hacia == "arriba" else round(k)
            p = ini + k * paso
            break
    minimo = rangos[0][2]
    return round(min(max(p, minimo), 1 - rangos[-1][2]), 4)


class Kalshi:
    def __init__(self, rest_url, key_id="", clave=None, min_intervalo=0.06):
        self.base = rest_url.rstrip("/")
        self.key_id = key_id
        self.clave = clave
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "kalshi-bot/1.0"
        self.lock = threading.Lock()
        self.min_intervalo = min_intervalo
        self.proximo = 0.0

    @property
    def autenticado(self):
        return bool(self.key_id and self.clave)

    # ------------------------------------------------------------------ http
    def _firma(self, metodo, url):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        ts = str(int(time.time() * 1000))
        sig = self.clave.sign((ts + metodo + urlparse(url).path).encode(),
                              padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
                              hashes.SHA256())
        return {"KALSHI-ACCESS-KEY": self.key_id, "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
                "KALSHI-ACCESS-TIMESTAMP": ts}

    def _pedir(self, metodo, ruta, params=None, cuerpo=None, firmar=None, reintentos=3):
        url = self.base + ruta + ("?" + urlencode(params) if params else "")
        firmar = self.autenticado if firmar is None else firmar
        for intento in range(reintentos):
            with self.lock:                                  # espacia los pedidos (límite de Kalshi)
                espera = self.proximo - time.monotonic()
                if espera > 0:
                    time.sleep(espera)
                self.proximo = time.monotonic() + self.min_intervalo
            headers = self._firma(metodo, url) if firmar else {}
            r = self.s.request(metodo, url, json=cuerpo, headers=headers, timeout=6)
            if r.status_code == 429 and intento < reintentos - 1:
                time.sleep(0.3 * (intento + 1))
                continue
            if r.status_code >= 400:
                raise ErrorKalshi(r.status_code, r.text)
            return r.json() if r.content else {}
        raise ErrorKalshi(429, "demasiados pedidos")

    # ------------------------------------------------------------------ mercado (público)
    def mercado_abierto(self, activo):
        """El bloque de 15 min abierto ahora para el activo (el que cierra primero)."""
        d = self._pedir("GET", "/markets", {"series_ticker": SERIES[activo], "status": "open", "limit": 10}, firmar=False)
        ahora = time.time()
        abiertos = []
        for m in d.get("markets", []):
            cierre, apertura = epoch(m.get("close_time")), epoch(m.get("open_time"))
            if m.get("ticker") and cierre and cierre > ahora and (apertura is None or apertura <= ahora + 1):
                abiertos.append((cierre, m))
        if not abiertos:
            return None
        cierre, m = min(abiertos, key=lambda x: x[0])
        return self._info_mercado(m)

    @staticmethod
    def _info_mercado(m):
        rangos = [(float(r["start"]), float(r["end"]), float(r["step"])) for r in m.get("price_ranges") or []]
        idx = m.get("exchange_index")
        return dict(ticker=m["ticker"], cierre=epoch(m.get("close_time")), referencia=num(m.get("floor_strike")),
                    exchange_index=int(idx) if idx is not None else None, rangos=rangos,
                    resultado=(m.get("result") or "").lower())

    def mercado(self, ticker):
        return self._info_mercado(self._pedir("GET", f"/markets/{ticker}", firmar=False)["market"])

    def libro(self, ticker):
        """Mejor bid de cada lado; el ask de un lado es 1 - bid del otro. Lado vacío: bid 0 y ask del otro 1.0."""
        d = self._pedir("GET", f"/markets/{ticker}/orderbook", {"depth": 5}, firmar=False)
        ob = d.get("orderbook_fp") or d.get("orderbook") or {}
        yb = mejor_bid(ob.get("yes_dollars") or ob.get("yes"))
        nb = mejor_bid(ob.get("no_dollars") or ob.get("no"))
        return dict(yes_bid=yb if yb is not None else 0.0, no_bid=nb if nb is not None else 0.0,
                    yes_ask=round(1 - nb, 4) if nb is not None else 1.0,
                    no_ask=round(1 - yb, 4) if yb is not None else 1.0)

    # ------------------------------------------------------------------ cuenta
    def saldo(self, exchange_index=None):
        """Saldo disponible en dólares (de un shard si se indica). Shard nuevo sin usuario = 0."""
        params = {"exchange_index": exchange_index} if exchange_index is not None else None
        try:
            d = self._pedir("GET", "/portfolio/balance", params)
        except ErrorKalshi as e:
            if exchange_index is not None and "user_not_found" in e.texto:
                return 0.0
            raise
        b = num(d.get("balance_dollars"))
        return b if b is not None else (num(d.get("balance")) or 0) / 100

    def transferir_a_shard(self, dolares, destino):
        """Pasa saldo del shard 0 al shard del mercado (como NightShark). amount en centésimas de centavo."""
        cuerpo = {"source": "event_contract", "destination": "event_contract",
                  "amount": int(math.ceil(dolares * 10000 - 1e-6)), "source_exchange_shard": 0,
                  "destination_exchange_shard": destino, "source_subaccount": 0, "destination_subaccount": 0}
        return self._pedir("POST", "/portfolio/intra_exchange_instance_transfer", cuerpo=cuerpo, reintentos=1)

    def posicion(self, ticker, exchange_index=None):
        """(contratos, costo en $). Positivo = YES, negativo = NO."""
        params = {"ticker": ticker}
        if exchange_index is not None:
            params["exchange_index"] = exchange_index
        for p in self._pedir("GET", "/portfolio/positions", params).get("market_positions", []):
            if p.get("ticker") == ticker:
                return num(p.get("position_fp")) or 0.0, num(p.get("market_exposure_dollars")) or 0.0
        return 0.0, 0.0

    def ordenes_abiertas(self, ticker, exchange_index=None):
        params = {"ticker": ticker, "status": "resting"}
        if exchange_index is not None:
            params["exchange_index"] = exchange_index
        return self._pedir("GET", "/portfolio/orders", params).get("orders", [])

    # ------------------------------------------------------------------ órdenes
    def crear_orden(self, ticker, lado_libro, precio, contratos, tif, exchange_index=None, reduce_only=False):
        """lado_libro: 'bid' (comprar YES) o 'ask' (vender YES). precio: del lado YES, en dólares."""
        cuerpo = {"ticker": ticker, "side": lado_libro, "count": f"{contratos:.2f}", "price": f"{precio:.4f}",
                  "time_in_force": tif, "self_trade_prevention_type": "taker_at_cross", "post_only": False,
                  "cancel_order_on_pause": False, "reduce_only": reduce_only,
                  "client_order_id": f"bot-{uuid.uuid4().hex[:24]}"}
        if exchange_index is not None:
            cuerpo["exchange_index"] = exchange_index
        d = self._pedir("POST", "/portfolio/events/orders", cuerpo=cuerpo, reintentos=1)   # nunca repetir a ciegas
        d = d.get("order", d)
        return dict(order_id=d.get("order_id"), llenos=num(d.get("fill_count")) or 0.0,
                    precio_yes=num(d.get("average_fill_price")), comision_media=num(d.get("average_fee_paid")) or 0.0)

    def orden(self, order_id, exchange_index=None):
        params = {"exchange_index": exchange_index} if exchange_index is not None else None
        o = self._pedir("GET", f"/portfolio/orders/{order_id}", params).get("order", {})
        return dict(estado=o.get("status"), llenos=num(o.get("fill_count_fp")) or 0.0,
                    restantes=num(o.get("remaining_count_fp")) or 0.0,
                    comision=(num(o.get("taker_fees_dollars")) or 0.0) + (num(o.get("maker_fees_dollars")) or 0.0))

    def cancelar(self, order_id, exchange_index=None):
        params = {"exchange_index": exchange_index} if exchange_index is not None else None
        try:
            self._pedir("DELETE", f"/portfolio/events/orders/{order_id}", params, reintentos=2)
        except ErrorKalshi as e:
            if e.status != 404:          # 404 = ya no estaba abierta (llena o cancelada)
                raise

    def llenados(self, order_id, lado):
        """(contratos, precio medio del lado, comisiones $) sumando /portfolio/fills de una orden."""
        n = nocional = fee = 0.0
        cursor = None
        for _ in range(20):
            params = {"order_id": order_id, "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            d = self._pedir("GET", "/portfolio/fills", params)
            for f in d.get("fills", []):
                if f.get("order_id") != order_id:
                    continue
                c = num(f.get("count_fp")) or num(f.get("count")) or 0.0
                p = num(f.get(f"{lado}_price_dollars"))
                if p is None:
                    p = (num(f.get(f"{lado}_price")) or 0) / 100
                n += c
                nocional += c * p
                fee += num(f.get("fee_cost")) or 0.0
            cursor = d.get("cursor")
            if not cursor:
                break
        return n, (nocional / n if n else None), fee


def precio_coinbase(sesion, activo):
    """Precio spot de Coinbase (igual que el grabador)."""
    r = sesion.get(f"https://api.exchange.coinbase.com/products/{activo}-USD/ticker", timeout=3)
    r.raise_for_status()
    return float(r.json()["price"])
