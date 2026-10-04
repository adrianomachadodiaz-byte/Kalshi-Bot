"""Arranque completo con API key y los dos activos: lo que falló en Railway.

Comprueba que Bot() arranca entero (ajustes viejos -> migración -> conectar -> ejecutor)
y que el hilo de noticias aguanta un 429 de ForexFactory sin tumbarse.
"""
import json, os, shutil, sys, tempfile, threading, time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
PUERTO = 8971
CIERRE = int(time.time()) + 300
REF = {"BTC": 84000.0, "ETH": 3000.0}
PETICIONES = {"noticias": 0}
fallos = []


def ok(c, q):
    print(("  BIEN  " if c else "  MAL   ") + q)
    if not c:
        fallos.append(q)


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        ruta = urlparse(self.path).path
        if ruta.endswith("noticias.json"):                       # ForexFactory: siempre 429
            PETICIONES["noticias"] += 1
            return self.j({"error": "too many requests"}, 429)
        if ruta.endswith("_current.json"):
            a = "BTC" if "BTC" in ruta.upper() else "ETH"
            return self.j({"maturity_ts_ms": int(time.time() * 1000),
                           "timeseries": {"second": [REF[a] + 50] * 60}})
        if ruta.endswith("/markets"):
            a = "BTC" if "KXBTC" in urlparse(self.path).query else "ETH"
            cierre = datetime.fromtimestamp(CIERRE, timezone.utc)
            return self.j({"markets": [{"ticker": f"KX{a}15M-X", "exchange_index": 2,
                                        "close_time": cierre.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                        "open_time": (cierre - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                        "floor_strike": REF[a],
                                        "price_ranges": [{"start": 0, "end": 1, "step": 0.01}]}]})
        if ruta.endswith("/orderbook"):
            return self.j({"orderbook": {"yes": [[82, 500]], "no": [[15, 500]]}})
        if "/portfolio/balance" in ruta:
            return self.j({"balance_dollars": "500.00"})
        if "/portfolio/positions" in ruta:
            return self.j({"market_positions": []})
        return self.j({}, 404)

    def j(self, o, c=200):
        b = json.dumps(o).encode()
        self.send_response(c)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", PUERTO), H).serve_forever, daemon=True).start()
time.sleep(0.3)

DATOS = tempfile.mkdtemp(prefix="arranque_")
# ajustes como los que hay hoy en Railway: planos y con el Delay en minutos
open(os.path.join(DATOS, "ajustes.json"), "w").write(json.dumps(
    {"activos": "BTC,ETH", "delta": 100.0, "delay": 7, "entrada_min": 0.81, "entrada_max": 0.86,
     "tp": 0.13, "exit": 0.46, "contratos": 2.0, "breakeven": None, "max_perdida_dia": 0.0,
     "meta_ganancia_dia": 0.0, "desliz_entrada": 0.02, "espera_entrada": 2.0,
     "ritmo": 0.25, "ritmo_indice": 1.0}))
# una API key de mentira, pero con forma buena: el bot intentará conectar
CLAVE = """-----BEGIN RSA PRIVATE KEY-----\n-----END RSA PRIVATE KEY-----"""
os.environ.update(DATA_DIR=DATOS, PORT="8972", ACCESS_TOKEN="x",
                  KALSHI_REST_URL=f"http://127.0.0.1:{PUERTO}/trade-api/v2",
                  NOTICIAS_URL=f"http://127.0.0.1:{PUERTO}/noticias.json")

import bot as B

for attr in dir(B):
    v = getattr(B, attr)
    if isinstance(v, str) and "kalshi-public-docs" in v:
        setattr(B, attr, f"http://127.0.0.1:{PUERTO}/{{activo}}_current.json")

print("\n1) el bot arranca con ajustes viejos y los dos activos")
bot = B.Bot()
ok(sorted(bot.activos) == ["BTC", "ETH"], f"activos: {bot.activos}")
ok(bot.cfgs["BTC"].delay == 420, f"el Delay de 7 min quedó en {bot.cfgs['BTC'].delay} s")
ok(bot.cfgs["BTC"].contratos == 2.0 and bot.cfgs["ETH"].contratos == 2.0,
   "los 2 contratos viejos pasan a los dos activos")

print("\n2) conectar() con API key no revienta (era el KeyError de Railway)")
try:
    ok2, msg = bot.conectar("una-key", CLAVE)
    ok(True, f"conectar() devuelve en vez de romper: {msg[:60]}")
except KeyError as e:
    ok(False, f"conectar() lanzó KeyError({e}) — es el fallo de producción")

print("\n3) con un ejecutor puesto a mano, los contratos van por activo")


class EjFalso:
    contratos, desliz, n_activos, espera = None, 0, 0, 0


bot.ej = EjFalso()
bot.aplicar(bot.ajustes.valores)
ok(bot.ej.contratos == {"BTC": 2.0, "ETH": 2.0}, f"ej.contratos = {bot.ej.contratos}")

print("\n4) ForexFactory devolviendo 429 no tumba el hilo de noticias")
time.sleep(2.5)
vivos = [t.name for t in threading.enumerate() if t.is_alive()]
ok(PETICIONES["noticias"] >= 1, f"se pidió el calendario {PETICIONES['noticias']} vez/veces")
ok(bot.noticias.error != "", f"el error quedó anotado: {bot.noticias.error[:60]}")
ok(any("Thread" in n for n in vivos), "el hilo sigue vivo (antes moría con TypeError)")
ok(bot.filtro.motivo(time.time() + 300) is None,
   "sin calendario el filtro no bloquea: el bot sigue operando")

print("\n5) el panel se arma entero")
j = bot._resumen(500.0)
for clave in ("por_activo", "campos_activo", "activos_validos", "filtro"):
    ok(clave in j, f"el panel recibe {clave}")

shutil.rmtree(DATOS, ignore_errors=True)
print("\nRESULTADO:", "BIEN" if not fallos else f"MAL ({len(fallos)})")
for f in fallos:
    print("  -", f)
sys.exit(1 if fallos else 0)
