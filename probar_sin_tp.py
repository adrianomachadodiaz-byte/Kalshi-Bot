"""Comprueba el modo "sin TP": si entrada + TP pasa de 0.99, no se pone orden y se cobra al cierre.

    python probar_sin_tp.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ACCESS_TOKEN", "x")
os.environ.setdefault("DATA_DIR", "/tmp/probar_sin_tp")
os.environ.setdefault("NOTICIAS", "0")

import bot as B
from ejecutores import TOPE_TP, EjecutorReal, EjecutorSim, nueva_operacion
from estrategia import Config, Estrategia, Foto

fallos = []


def check(ok, texto):
    print(("  OK  " if ok else "  MAL ") + texto)
    if not ok:
        fallos.append(texto)


def foto(bid, segundo=880):
    """Una foto del bloque con ese bid del lado YES."""
    cierre = 1_000_000.0
    return Foto(ts=cierre - 900 + segundo, ticker="T", cierre=cierre, referencia=84000.0,
                precio=84200.0, atraso=1.0, yes_bid=bid, no_bid=round(1 - bid - 0.01, 4),
                yes_ask=round(bid + 0.01, 4), no_ask=round(1 - bid, 4))


print("\n1) el panel acepta entrada 0.98 con TP 0.03")
crudo = {c: ("" if d is None else str(d)) for c, t, d, *_ in B.CAMPOS}
crudo.update(entrada_min="0.97", entrada_max="0.98", tp="0.03", exit="0.90", delta="100", delay="7")
v, err = B.validar(crudo)
check(not err, f"validar() no da error (dio: {err})")

print("\n2) la operación nace sin TP y con objetivo 1.00")
cfg = Config(delta=100, delay=7, entrada_min=0.97, entrada_max=0.98, tp=0.03, exit=0.90,
             breakeven=None)
f = foto(0.97)
op = nueva_operacion("BTC", f, "yes", 0.98, 1.0, cfg, 0.0)
check(op.sin_tp is True, f"sin_tp = {op.sin_tp}")
check(abs(op.objetivo - 1.0) < 1e-9, f"objetivo = {op.objetivo} (debe ser 1.0, no 0.99)")

print("\n3) con el bid en 0.99 NO sale: aguanta")
est = Estrategia(cfg)
check(est.salida(op, foto(0.99)) is None, "salida() con bid 0.99 devuelve None")
check(est.salida(op, foto(0.995)) is None, "salida() con bid 0.995 devuelve None")

print("\n4) el stop sigue funcionando")
check(est.salida(op, foto(0.90)) == "stop", "salida() con bid 0.90 devuelve 'stop'")
check(est.salida(op, foto(0.85)) == "stop", "salida() con bid 0.85 devuelve 'stop'")

print("\n5) el ejecutor no intenta poner ni ejecutar el TP")
sim = EjecutorSim()
check(sim.tp(op, foto(0.99)) is False, "EjecutorSim.tp() no vende")
check(op.abiertos() == 1.0, f"la posición sigue abierta ({op.abiertos():g} contratos)")

print("\n6) un TP normal (0.85 + 0.13) sigue igual que antes")
cfg2 = Config(delta=100, delay=7, entrada_min=0.81, entrada_max=0.86, tp=0.13, exit=0.46,
              breakeven=None)
op2 = nueva_operacion("BTC", foto(0.84), "yes", 0.85, 1.0, cfg2, 0.0)
est2 = Estrategia(cfg2)
check(op2.sin_tp is False, f"sin_tp = {op2.sin_tp}")
check(abs(op2.objetivo - 0.98) < 1e-9, f"objetivo = {op2.objetivo} (0.85 + 0.13)")
check(est2.salida(op2, foto(0.98)) == "tp", "salida() con bid 0.98 devuelve 'tp'")
check(est2.salida(op2, foto(0.46)) == "stop", "salida() con bid 0.46 devuelve 'stop'")

print("\n7) el BreakEven sigue armándose aunque no haya TP")
cfg3 = Config(delta=100, delay=7, entrada_min=0.97, entrada_max=0.98, tp=0.03, exit=0.90,
              breakeven=0.01)
op3 = nueva_operacion("BTC", foto(0.97), "yes", 0.98, 1.0, cfg3, 0.0)
est3 = Estrategia(cfg3)
check(op3.sin_tp is True, "la operación es sin TP")
est3.salida(op3, foto(0.99))                      # bid 0.99 >= 0.98 + 0.01 -> arma el BE
check(op3.be_armado is True, f"be_armado = {op3.be_armado} tras tocar entrada + BE")
check(est3.salida(op3, foto(0.975)) == "stop", "con el BE armado, bid 0.975 devuelve 'stop'")

print("\n8) el tope viejo de 0.99 ya no veta nada, pero el TP sigue acotado a (0,1)")
for tp, debe_fallar in [("0.03", False), ("0.5", False), ("0", True), ("1", True), ("1.5", True)]:
    c = dict(crudo, tp=tp)
    _, e = B.validar(c)
    check(("tp" in e) == debe_fallar, f"tp={tp} -> {'rechazado' if 'tp' in e else 'aceptado'}")

print("\n9) EL CRUCE DE SPREAD NO SE ACTIVA CUANDO NO HAY TP  (el bug de la v1)")
ej = EjecutorReal.__new__(EjecutorReal)      # sin tocar Kalshi: solo se prueba el cálculo del límite
ej.desliz = 0.02

# (a) sin TP (entrada_max + TP pasa de 0.99): NUNCA puede pagar más que entrada_max
sin_tp = Config(delta=140, delay=5, entrada_min=0.97, entrada_max=0.98, tp=0.03, exit=None, breakeven=None)
for ask in (0.97, 0.98):
    lim, cruzo = ej.limite_entrada(ask, sin_tp)
    check(not cruzo, f"sin TP, ask {ask}: no cruza")
    check(lim <= sin_tp.entrada_max + 1e-9, f"sin TP, ask {ask}: limite {lim:.4f} <= 0.98")
check(ej.limite_entrada(0.97, sin_tp)[0] == 0.98, "sin TP, ask 0.97 -> limite 0.98 (ask+2c topado)")

# (b) TP JUSTO en el tope (0.86 + 0.13 = 0.99): ahí sí se cruza, como estaba pensado
justo = Config(delta=100, delay=7, entrada_min=0.81, entrada_max=0.86, tp=0.13, exit=0.46, breakeven=None)
lim, cruzo = ej.limite_entrada(0.86, justo)
check(cruzo, "TP justo en 0.99, ask en el máximo: sí cruza")
check(abs(lim - 0.88) < 1e-9, f"TP justo en 0.99: limite {lim:.4f} = ask + 2c")
lim, cruzo = ej.limite_entrada(0.84, justo)
check(not cruzo, "TP justo en 0.99 pero ask por debajo del máximo: no cruza")
check(abs(lim - 0.86) < 1e-9, f"limite {lim:.4f} topado en entrada_max")

# (c) config normal (0.81-0.81 + 0.15 = 0.96): nunca cruza
normal = Config(delta=100, delay=7, entrada_min=0.78, entrada_max=0.81, tp=0.15, exit=0.40, breakeven=None)
for ask in (0.78, 0.80, 0.81):
    lim, cruzo = ej.limite_entrada(ask, normal)
    check(not cruzo, f"config normal, ask {ask}: no cruza")
    check(lim <= 0.81 + 1e-9, f"config normal, ask {ask}: limite {lim:.4f} <= 0.81")

# (d) jamás una orden de entrada a 1.00
alto = Config(delta=140, delay=5, entrada_min=0.99, entrada_max=0.99, tp=0.05, exit=None, breakeven=None)
lim, _ = ej.limite_entrada(0.99, alto)
check(lim <= TOPE_TP + 1e-9, f"entrada a 0.99: limite {lim:.4f} nunca pasa de {TOPE_TP}")


print("\nRESULTADO:", "BIEN" if not fallos else f"MAL ({len(fallos)} fallos)")
sys.exit(1 if fallos else 0)
