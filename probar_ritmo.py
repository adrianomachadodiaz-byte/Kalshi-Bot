"""Comprueba con reloj que el ciclo corre al ritmo configurado y cuenta las peticiones de verdad.

No lee el código: levanta un Kalshi falso que duerme lo que tarda el de verdad (89 ms el libro,
78 ms el índice; medido el 30-sep-2026), deja correr el bot y cronometra las vueltas.

    python probar_ritmo.py            # prueba 0.25 y 1.0 (lo que trae de fábrica)
    python probar_ritmo.py 0.5 2.0    # ritmo del libro y del índice a mano
"""
import json
import math
import os
import shutil
import statistics
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)

LAT_KALSHI = float(os.environ.get("LAT_KALSHI", "0.089"))
LAT_INDICE = float(os.environ.get("LAT_INDICE", "0.078"))
PUERTO = 8973
CIERRE = (int(time.time()) // 900 + 1) * 900
CUENTA, LOCK = {}, threading.Lock()


def contar(que):
    with LOCK:
        CUENTA[que] = CUENTA.get(que, 0) + 1


class Falso(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        ruta = self.path.split("?")[0]
        if ruta.endswith("_current.json"):
            contar("indice")
            time.sleep(LAT_INDICE)
            ahora = time.time()
            serie = [84450 + 40 * math.sin((ahora - 59 + i) / 90) for i in range(60)]
            return self._json({"maturity_ts_ms": int(ahora * 1000),
                               "candlesticks": {"1M": {"close": serie[-1]}},
                               "timeseries": {"second": serie}})
        if ruta.endswith("/markets"):
            contar("markets")
            time.sleep(LAT_KALSHI)
            iso = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            return self._json({"markets": [{"ticker": "KXBTC15M-PRUEBA", "close_time": iso(CIERRE),
                                            "open_time": iso(CIERRE - 900), "floor_strike": 84450.0,
                                            "exchange_index": 2,
                                            "price_ranges": [{"start": 0, "end": 1, "step": 0.01}]}]})
        if ruta.endswith("/orderbook"):
            contar("orderbook")
            time.sleep(LAT_KALSHI)
            return self._json({"orderbook": {"yes": [[74, 100]], "no": [[21, 100]]}})
        contar("otra:" + ruta)
        return self._json({}, 404)

    def _json(self, o, codigo=200):
        b = json.dumps(o).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


def medir(ritmo, ritmo_indice, segundos=20.0):
    CUENTA.clear()
    datos = "/tmp/probar_ritmo_datos"
    shutil.rmtree(datos, ignore_errors=True)
    os.makedirs(datos)
    os.environ.update(ACCESS_TOKEN="x", PORT="8974", DATA_DIR=datos, NOTICIAS="0",
                      KALSHI_REST_URL=f"http://127.0.0.1:{PUERTO}/trade-api/v2",
                      KALSHI_INDICE_URL=f"http://127.0.0.1:{PUERTO}/crypto/{{activo}}_current.json")
    import bot as B

    b = B.Bot()
    b.op.pausar(True)                       # solo lectura: la prueba nunca manda órdenes
    b.ciclo, b.ciclo_indice = ritmo, ritmo_indice

    marcas, original = [], b.paso

    def cronometrado(activo):
        marcas.append(time.monotonic())
        return original(activo)

    b.paso = cronometrado
    threading.Thread(target=b.correr, daemon=True).start()
    time.sleep(segundos)

    per = sorted(round((marcas[i + 1] - marcas[i]) * 1000) for i in range(len(marcas) - 1))
    kalshi = CUENTA.get("orderbook", 0) + CUENTA.get("markets", 0)
    return {
        "ritmo pedido (s)": ritmo,
        "ritmo índice pedido (s)": ritmo_indice,
        "vueltas": len(marcas),
        "periodo real ms": {"p50": per[len(per) // 2], "p90": per[int(len(per) * 0.9)],
                            "min": per[0], "max": per[-1], "media": round(statistics.mean(per))},
        "peticiones": dict(CUENTA),
        "peticiones Kalshi/s": round(kalshi / segundos, 2),
        "peticiones índice/s": round(CUENTA.get("indice", 0) / segundos, 2),
    }


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PUERTO), Falso)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)

    ritmo = float(sys.argv[1]) if len(sys.argv) > 1 else 0.25
    ritmo_indice = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
    segundos = float(sys.argv[3]) if len(sys.argv) > 3 else 20.0

    r = medir(ritmo, ritmo_indice, segundos)
    print(json.dumps(r, indent=1, ensure_ascii=False))

    fallos = []
    p = r["periodo real ms"]
    objetivo = ritmo * 1000
    # el reloj real manda: el periodo medido tiene que caer a menos de un 20% del pedido
    if abs(p["p50"] - objetivo) > objetivo * 0.2:
        fallos.append(f"el periodo mediano ({p['p50']} ms) no se parece al pedido ({objetivo:.0f} ms)")
    if p["p90"] > objetivo * 1.5:
        fallos.append(f"el p90 ({p['p90']} ms) se pasa un 50% del pedido")
    # el índice no se pide más rápido que su propio ritmo
    tope_indice = segundos / ritmo_indice * 1.15 + 1
    if CUENTA.get("indice", 0) > tope_indice:
        fallos.append(f"el índice se pidió {CUENTA.get('indice')} veces, más de las {tope_indice:.0f} que tocan")

    print("\nRESULTADO:", "BIEN" if not fallos else "MAL")
    for f in fallos:
        print(" -", f)
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
