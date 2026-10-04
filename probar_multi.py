"""Prueba del modo multi-activo: configuración, estadísticas e historial separados por activo.

1. Ajustes viejos (planos) se migran: BTC y ETH heredan lo que había, nadie pierde nada.
2. Cada activo guarda SU configuración y el bot usa la suya (Delta, TP, contratos...).
3. Las estadísticas y el historial salen filtrados por activo.
4. Un activo apagado conserva su configuración y no bloquea el guardado.
"""
import json, os, shutil, sys, tempfile
from pathlib import Path

BASE = "/home/claude/adrianomachadodiaz-byte/kalshi-bot"
sys.path.insert(0, BASE)
DATOS = tempfile.mkdtemp(prefix="multi_")
os.environ["DATA_DIR"] = DATOS

import bot as B
from operador import Registro

fallos = []


def ok(cond, que):
    print(("  BIEN  " if cond else "  MAL   ") + que)
    if not cond:
        fallos.append(que)


# ---------------------------------------------------------------- 1. migración
print("\n1) ajustes viejos (un solo juego para los dos) -> uno por activo")
viejo = {"activos": "BTC", "delta": 250.0, "delay": 5, "entrada_min": 0.70,
         "entrada_max": 0.80, "tp": 0.10, "exit": 0.40, "contratos": 3.0,
         "breakeven": None, "ritmo": 0.25, "ritmo_indice": 1.0,
         "max_perdida_dia": 0.0, "meta_ganancia_dia": 0.0,
         "desliz_entrada": 0.02, "espera_entrada": 2.0}
Path(DATOS, "ajustes.json").write_text(json.dumps(viejo), encoding="utf-8")
aj = B.Ajustes(Path(DATOS))
pa = aj.valores["por_activo"]
ok(set(pa) == {"BTC", "ETH"}, f"aparecen los dos activos: {sorted(pa)}")
for a in ("BTC", "ETH"):
    ok(pa[a]["delta"] == 250.0 and pa[a]["tp"] == 0.10 and pa[a]["contratos"] == 3.0,
       f"{a} hereda los valores viejos (delta {pa[a]['delta']:g}, tp {pa[a]['tp']:.2f}, "
       f"{pa[a]['contratos']:g} contratos)")
ok("delta" not in aj.valores, "el Delta ya no vive en la raíz, solo dentro de cada activo")
ok(pa["BTC"]["delay"] == 300 and pa["ETH"]["delay"] == 300,
   f"el Delay viejo de 5 min pasa a 300 s ({pa['BTC']['delay']})")

# ---------------------------------------------------------------- 2. config distinta por activo
print("\n2) cada activo con su propia configuración")
crudo = {c[0]: c[2] for c in B.CAMPOS_GLOBAL}     # los globales, con sus valores por defecto
crudo["activos"] = "BTC,ETH"
crudo["por_activo"] = {
    "BTC": dict(delta=300.0, delay=7, entrada_min=0.81, entrada_max=0.86, tp=0.13,
                exit=0.46, contratos=2.0, breakeven=None),
    "ETH": dict(delta=25.0, delay=4, entrada_min=0.60, entrada_max=0.70, tp=0.25,
                exit=0.30, contratos=5.0, breakeven=0.10),
}
v, err = B.validar(crudo)
ok(not err, f"los ajustes de los dos activos son válidos ({err})")
cb, ce = B.config_de("BTC", v), B.config_de("ETH", v)
ok(cb.delta == 300.0 and ce.delta == 25.0, f"Delta separado: BTC {cb.delta:g} / ETH {ce.delta:g}")
ok(cb.tp == 0.13 and ce.tp == 0.25, f"TP separado: BTC +{cb.tp:.2f} / ETH +{ce.tp:.2f}")
ok(cb.contratos == 2.0 and ce.contratos == 5.0,
   f"contratos separados: BTC {cb.contratos:g} / ETH {ce.contratos:g}")
ok(ce.breakeven == 0.10 and cb.breakeven is None, "BreakEven solo en ETH, como se pidió")

# ---------------------------------------------------------------- 3. errores marcados por activo
print("\n3) un error en un activo se marca con su nombre")
malo = json.loads(json.dumps(crudo))
malo["por_activo"]["ETH"]["entrada_max"] = 0.50          # menor que la mínima
_, err = B.validar(malo)
ok("ETH.entrada_max" in err, f"el error llega como ETH.entrada_max ({err})")
ok(not any(k.startswith("BTC.") for k in err), "BTC no se marca por culpa de ETH")

print("\n4) un activo apagado no bloquea el guardado y conserva lo suyo")
apagado = json.loads(json.dumps(malo))
apagado["activos"] = "BTC"                                # ETH apagado, con su error dentro
v2, err2 = B.validar(apagado)
ok(not err2, f"se puede guardar con ETH apagado ({err2})")
ok(v2["por_activo"]["ETH"]["delta"] == 25.0, "ETH apagado conserva su Delta")

# ---------------------------------------------------------------- 5. estadísticas e historial por activo
print("\n5) estadísticas e historial separados")
r = Registro(Path(DATOS), "sim")
r.modo = "sim"
for activo, pnl in [("BTC", 1.5), ("BTC", -0.5), ("ETH", 4.0), ("ETH", 2.0), ("ETH", -1.0)]:
    r.filas.append({"fecha": "2026-10-03", "modo": "sim", "activo": activo,
                    "pnl_usd": pnl, "resultado": "G" if pnl > 0 else "P"})
tb, te, tt = r.resumen(activo="BTC"), r.resumen(activo="ETH"), r.resumen()
print("   BTC:", tb, "\n   ETH:", te, "\n   todo:", tt)
ok(tb["ops"] == 2 and te["ops"] == 3 and tt["ops"] == 5,
   f"operaciones: BTC {tb['ops']}, ETH {te['ops']}, total {tt['ops']}")
ok(abs(tb["usd"] - 1.0) < 1e-9 and abs(te["usd"] - 5.0) < 1e-9,
   f"ganancia separada: BTC {tb['usd']:+.2f} / ETH {te['usd']:+.2f}")
ok(abs(tt["usd"] - 6.0) < 1e-9, f"el total sigue siendo la suma: {tt['usd']:+.2f}")

shutil.rmtree(DATOS, ignore_errors=True)
print("\nRESULTADO:", "BIEN" if not fallos else f"MAL ({len(fallos)})")
for f in fallos:
    print("  -", f)
sys.exit(1 if fallos else 0)
