"""Una operación abierta no se queda colgada cuando el mercado cierra.

Lo que pasó el 9-oct-2026: el bot tenía una operación abierta de oro del bloque de las
17:00 ET. A esa hora el oro cierra por fin de semana, así que Kalshi dejó de dar mercado,
`paso()` salía sin crear la Foto y `procesar()` no llegaba a ejecutarse nunca. La operación
se quedó en "abierta" horas, el panel la enseñaba como si siguiera viva y la tarjeta del
oro aparecía en blanco. Pasa igual al reiniciar el bot con una abierta guardada.
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="colgada_"))
os.environ.setdefault("NOTICIAS", "0")

import bot as B
from ejecutores import nueva_operacion
from estrategia import BLOQUE, Config, Foto

fallos = []


def ok(c, q):
    print(("  BIEN  " if c else "  MAL   ") + q)
    if not c:
        fallos.append(q)


cfg = Config(delta=0, delay=60, entrada_min=0.98, entrada_max=0.99, tp=0.15, exit=None,
             breakeven=None, contratos=1, usa_delta=False)


def nuevo_bot():
    """Cada caso con su carpeta: si no, el siguiente bot recupera el estado del anterior."""
    os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="colgada_")
    B.DATA_DIR = __import__("pathlib").Path(os.environ["DATA_DIR"])
    return B.Bot()


def abierta(bot, activo, cierre):
    """Mete en el estado una operación abierta de un bloque que cierra en `cierre`."""
    f = Foto(ticker=f"KX{activo}15M-PRUEBA", cierre=cierre, ts=cierre - 60, referencia=4136.82,
             precio=None, atraso=None, yes_ask=0.99, yes_bid=0.98, no_ask=0.02, no_bid=0.01)
    op = nueva_operacion(activo, f, "no", 0.99, 1, cfg, 0.0)
    bot.estado.abiertas[activo] = op
    return op


print("\n1) el bloque ya venció y el mercado está cerrado: se suelta")
bot = nuevo_bot()
bot.estado.pausado = False
bot.mercados.cache = {}                      # Kalshi no da mercado: fin de semana
op = abierta(bot, "GOLD", time.time() - 3600)   # cerró hace una hora
bot.paso("GOLD")
ok("GOLD" not in bot.estado.abiertas, "ya no figura como abierta")
ok(any(o is op for o in bot.estado.pendientes), "y está en la cola de cobro")

print("\n2) si el bloque todavía no ha vencido, no se toca")
bot2 = nuevo_bot()
bot2.estado.pausado = False
bot2.mercados.cache = {}
viva = abierta(bot2, "GOLD", time.time() + 300)   # cierra dentro de 5 min
bot2.paso("GOLD")
ok(bot2.estado.abiertas.get("GOLD") is viva, "la operación sigue abierta, como debe")
ok(not bot2.estado.pendientes, "y no se ha mandado a cobrar")

print("\n3) sin operación abierta no pasa nada raro")
bot3 = nuevo_bot()
bot3.mercados.cache = {}
bot3.paso("GOLD")
ok(not bot3.estado.abiertas and not bot3.estado.pendientes, "ni abiertas ni pendientes")

print("\n4) la tarjeta sin bloque ya no sale en blanco")
bot4 = nuevo_bot()
bot4.activos = ["GOLD", "BTC"]
j = bot4._resumen(10.0)
g = next(v for v in j["vivo"] if v["activo"] == "GOLD")
ok(bool(g.get("estado")), f'GOLD dice algo: "{g.get("estado")}"')
ok("cerrado" in g["estado"] or "sin bloque" in g["estado"],
   "y lo que dice explica por qué no hay bloque")

print("\n5) con una abierta esperando, la tarjeta lo avisa")
bot5 = nuevo_bot()
bot5.activos = ["GOLD"]
abierta(bot5, "GOLD", time.time() + 300)
g5 = next(v for v in bot5._resumen(10.0)["vivo"] if v["activo"] == "GOLD")
ok("operación abierta" in g5["estado"], f'lo dice: "{g5["estado"]}"')

print("\nRESULTADO:", "BIEN" if not fallos else f"MAL ({len(fallos)})")
for f in fallos:
    print("  -", f)
sys.exit(1 if fallos else 0)
