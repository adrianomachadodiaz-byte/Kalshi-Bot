"""Oro, plata y petróleo operan sin Delta; las criptos lo siguen exigiendo.

Kalshi liquida esos tres con Pyth y ese precio no es público, así que el bot nunca
tiene el precio del subyacente: su Foto llega con precio=None. La prueba comprueba
que aun así entran, y que una cripto en la misma situación NO entra.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="sindelta_"))
os.environ.setdefault("NOTICIAS", "0")

import bot as B
from estrategia import BLOQUE, Estrategia, Foto

fallos = []


def ok(c, q):
    print(("  BIEN  " if c else "  MAL   ") + q)
    if not c:
        fallos.append(q)


CIERRE = 1_000_000.0


def foto(seg, precio, yes_ask=0.985):
    """precio=None imita a un activo sin índice público."""
    return Foto(ticker="T", cierre=CIERRE, ts=CIERRE - BLOQUE + seg, referencia=4136.82,
                precio=precio, atraso=1.0, yes_ask=yes_ask, yes_bid=round(yes_ask - 0.01, 4),
                no_ask=round(1 - yes_ask + 0.01, 4), no_bid=round(1 - yes_ask, 4))


v = {c[0]: c[2] for c in B.CAMPOS_GLOBAL}
v["por_activo"] = {a: B.defectos_de(a) for a in B.ACTIVOS_VALIDOS}
for a in B.ACTIVOS_VALIDOS:                       # un rango donde 0.985 entra
    v["por_activo"][a].update(entrada_min=0.98, entrada_max=0.99, delay=120, exit=0.01)

print("\n1) los tres salen marcados como sin Delta")
ok(B.SIN_INDICE == ("GOLD", "SILVER", "OIL", "NATGAS"), f"SIN_INDICE = {B.SIN_INDICE}")
for a in ("GOLD", "SILVER", "OIL", "NATGAS"):
    ok(not B.config_de(a, v).usa_delta, f"{a} no usa Delta")
for a in ("BTC", "ETH", "SOL", "XRP", "DOGE", "HYPE"):
    ok(B.config_de(a, v).usa_delta, f"{a} sí usa Delta")

print("\n2) el oro entra sin precio del subyacente")
oro = Estrategia(B.config_de("GOLD", v))
s = oro.entrada(foto(850, None))
ok(s is not None and s[0] == "yes", f"entra con precio=None: {s}")
ok(oro.entrada(foto(850, None, yes_ask=0.72)) is None,
   "pero NO entra si el precio del contrato está fuera del rango")
ok(oro.entrada(foto(100, None)) is None, "ni fuera de la ventana de entrada")

print("\n3) una cripto en esa misma situación no entra")
btc = Estrategia(B.config_de("BTC", v))
ok(btc.entrada(foto(850, None)) is None, "BTC sin precio del índice se queda fuera")
ok(btc.entrada(foto(850, 4136.82 + 500)) is not None, "y con Delta de sobra sí entra")

print("\n4) el bot no le pide a Pyth un feed que no existe")
bot = B.Bot()
for a in ("GOLD", "SILVER", "OIL", "NATGAS"):
    ok(bot.precios.get(a) is None, f"{a}: Precios.get devuelve None sin salir a la red")
ok(bot.precios.nota == "", f"y sin dejar un aviso de error: {bot.precios.nota!r}")

print("\n5) los tres están en las series de Kalshi")
import kalshi
for a, serie in [("GOLD", "KXGOLD15M"), ("SILVER", "KXSILVER15M"), ("OIL", "KXWTI15M"), ("NATGAS", "KXNATGAS15M")]:
    ok(kalshi.SERIES.get(a) == serie, f"{a} -> {kalshi.SERIES.get(a)}")

print("\n6) el panel recibe la lista")
j = bot._resumen(100.0)
ok(j.get("sin_indice") == ["GOLD", "SILVER", "OIL", "NATGAS"], f"sin_indice = {j.get('sin_indice')}")
ok(len(j["activos_validos"]) == 13, f"trece activos: {j['activos_validos']}")

print("\n7) el texto de la configuración lo dice")
ok("sin Delta" in B.config_de("OIL", v).texto(), B.config_de("OIL", v).texto())
ok("Delta 100" in B.config_de("BTC", v).texto(), B.config_de("BTC", v).texto())

print("\nRESULTADO:", "BIEN" if not fallos else f"MAL ({len(fallos)})")
for f in fallos:
    print("  -", f)
sys.exit(1 if fallos else 0)
