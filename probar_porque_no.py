"""Comprueba el aviso de "por qué no entré en este bloque".

Tres bloques seguidos que no entran por motivos distintos: Delta corto, precio
fuera de rango, y uno que sí entra (no debe avisar de nada).
"""
import os, sys, tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pq_"))
os.environ.setdefault("NOTICIAS", "0")

import operador as O
from estrategia import BLOQUE, Config, Estrategia, Foto
from operador import Estado, Operador, Registro

fallos = []


def ok(c, q):
    print(("  BIEN  " if c else "  MAL   ") + q)
    if not c:
        fallos.append(q)


CIERRE = 1_000_000.0


def foto(ticker, seg, ref, precio, yes_ask, cierre=None):
    c = cierre or CIERRE
    return Foto(ticker=ticker, cierre=c, ts=c - BLOQUE + seg, referencia=ref, precio=precio,
                atraso=1.0, yes_ask=yes_ask, yes_bid=round(yes_ask - 0.01, 4),
                no_ask=round(1 - yes_ask + 0.01, 4), no_bid=round(1 - yes_ask, 4))


class EjNulo:
    def preparar(self, *a, **k):
        pass

    def entrar(self, *a, **k):
        return None


cfg = Config(delta=15, delay=60, entrada_min=0.98, entrada_max=0.99, tp=0.15, exit=0.01,
             breakeven=None, contratos=5)
d = Path(os.environ["DATA_DIR"])
estado = Estado(d)
estado.pausado = False            # con el bot apagado no hay nada que explicar
op = Operador({"BTC": Estrategia(cfg)}, EjNulo(), estado, Registro(d, "sim"), 1)

# bloque 1: Delta corto (el índice solo sube 3 $ y pide 15)
for seg in range(850, 899, 4):
    op.procesar("BTC", foto("BLOQUE-1", seg, 85000.0, 85003.0, 0.985))
# bloque 2: Delta de sobra pero el precio nunca llega al rango
for seg in range(850, 899, 4):
    op.procesar("BTC", foto("BLOQUE-2", seg, 85000.0, 85040.0, 0.72, CIERRE + 900))
# bloque 3: sólo para forzar el aviso del bloque 2
op.procesar("BTC", foto("BLOQUE-3", 860, 85000.0, 85040.0, 0.985, CIERRE + 1800))

avisos = [l for l in O.LOGS if "sin entrada" in l]
for a in avisos:
    print("   " + a.split("] ", 1)[-1])
ok(len(avisos) == 2, f"se avisó de los dos bloques sin entrada ({len(avisos)})")
ok(any("BLOQUE-1" in a and "Delta" in a for a in avisos), "el primero dice que faltó Delta")
ok(any("BLOQUE-1" in a and "+3.0" in a for a in avisos), "y dice hasta dónde llegó el Delta")
ok(any("BLOQUE-2" in a and "rango" in a for a in avisos), "el segundo dice que el precio no llegó al rango")
ok(any("BLOQUE-2" in a and "0.72" in a for a in avisos), "y dice el precio más alto que vio")
ok(not any("BLOQUE-3" in a for a in avisos), "del bloque en curso todavía no avisa")

print("\nRESULTADO:", "BIEN" if not fallos else f"MAL ({len(fallos)})")
for f in fallos:
    print("  -", f)
sys.exit(1 if fallos else 0)
