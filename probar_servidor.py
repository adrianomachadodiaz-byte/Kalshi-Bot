"""Sirve el panel con el JSON que genera el Bot de verdad (sin red ni API key).

Así lo que se prueba es la estructura real de /api/estado, no una copia a mano que se
queda desfasada en cuanto cambia el backend.
"""
import json, math, os, sys, tempfile, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

BASE = "/home/claude/adrianomachadodiaz-byte/kalshi-bot"
sys.path.insert(0, BASE)
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="falso6_"))
os.environ.setdefault("NOTICIAS", "0")

import bot as B
from estrategia import BLOQUE, Foto

T0 = time.time()
ENCENDIDOS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "HYPE", "GOLD", "OIL", "NATGAS"]
REF = {"BTC": 82550.92, "ETH": 2487.04, "SOL": 112.43, "XRP": 1.3941, "DOGE": 0.0871,
       "HYPE": 85.83, "BNB": 740.2, "NEAR": 4.85, "ZEC": 1210.0,
       "GOLD": 4136.82, "SILVER": 48.91, "OIL": 61.44, "NATGAS": 3.21}
AMP = {a: (REF[a] * 0.004) for a in REF}

bot = B.Bot()
bot.estado.pausado = False
bot.activos = list(ENCENDIDOS)
bot.ajustes.valores["activos"] = ",".join(ENCENDIDOS)
bot.aplicar(bot.ajustes.valores)
bot.ej = type("EjFalso", (), {"modo": "sim", "info": {}, "contratos": {}, "desliz": 0.02,
                              "n_activos": len(ENCENDIDOS), "espera": 2.0})()


def refrescar():
    """Rellena bot.vista como lo haría paso(), menos en los activos con mercado cerrado."""
    ahora = time.time()
    cierre = (int(ahora) // 900) * 900 + 900
    t = 900 - (cierre - ahora)
    for a in B.ACTIVOS_VALIDOS:
        if a not in ENCENDIDOS:
            continue
        if B.motivo_horario(a, cierre):          # cerrado de verdad: ni mercado hay
            bot.vista.pop(a, None)
            continue
        p = REF[a] + AMP[a] * math.sin(t / 120.0 + hash(a) % 7)
        mudo = a in B.SIN_INDICE
        f = Foto(ts=ahora, ticker=f"KX{a}15M-PRUEBA", cierre=cierre, referencia=REF[a],
                 precio=None if mudo else round(p, 8), atraso=None if mudo else 2.4,
                 yes_ask=0.84, yes_bid=0.82, no_ask=0.17, no_bid=0.15)
        bot.vista[a] = f
        if not mudo:
            bot._anotar_grafico(a, f)


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        if urlparse(self.path).path == "/api/estado":
            refrescar()
            return self.j(bot._resumen(9.78))
        b = open(BASE + "/panel.html", "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        datos = json.loads(self.rfile.read(n) or b"{}")
        v, err = B.validar(datos)
        if err:
            return self.j({"ok": False, "errores": err, "msg": "Revisa los campos marcados."})
        bot.ajustes.valores = v
        bot.aplicar(v)
        ENCENDIDOS[:] = [x for x in v["activos"].split(",") if x]
        return self.j({"ok": True, "msg": "Ajustes guardados y aplicados.", "ajustes": v})

    def j(self, o):
        b = json.dumps(o, default=str).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


print("en http://127.0.0.1:8902/", flush=True)
ThreadingHTTPServer(("127.0.0.1", 8902), H).serve_forever()
