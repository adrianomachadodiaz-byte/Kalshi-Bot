"""Pasa archivos price-data (del grabador o de Kalshi BackTest) por el MISMO código del bot en modo simulación.

Sirve para comprobar que el bot hace lo mismo que el backtest:
    python probar_con_datos.py CARPETA_O_ARCHIVOS [--activo BTC] [--comparar operaciones_btc_466.csv]

El ganador de cada bloque se decide igual que el backtest (último precio antes del cierre o línea de cierre);
los bloques sin datos en los últimos 10 s o sin ganador claro no se cuentan.
"""
import argparse
import csv
import glob
import gzip
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

import ejecutores
import operador
from ejecutores import EjecutorSim
from estrategia import Config, Estrategia, Foto
from operador import Estado, Operador, Registro

CFG_BTC = Config(delta=100, delay=420, entrada_min=0.81, entrada_max=0.86, tp=0.13, exit=0.46, breakeven=None)


def archivos(rutas):
    out = []
    for p in rutas:
        out += sorted(glob.glob(os.path.join(p, "**", "price-data-*.jsonl*"), recursive=True)) if os.path.isdir(p) else [p]
    return out


def ts(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("datos", nargs="+")
    ap.add_argument("--activo", default="BTC")
    ap.add_argument("--comparar", help="CSV de operaciones del backtest (columnas mercado, lado, entrada, salida, pnl_c)")
    ap.add_argument("--contratos", type=float, default=100)
    a = ap.parse_args()

    operador.log = ejecutores.log = lambda msg: None          # sin mensajes por cada operación
    tmp = Path(tempfile.mkdtemp())
    reg = Registro(tmp, "simulacion")
    estado = Estado(tmp)
    estado.pausado = False
    op = Operador({a.activo: Estrategia(CFG_BTC)}, EjecutorSim(), estado, reg, a.contratos)

    visto = set()
    info = {}           # ticker -> dict(cierre, ult_pre=(ts, up, down), post=[(up, down)])
    malas = 0
    for f in archivos(a.datos):
        abrir = gzip.open if f.endswith(".gz") else open
        with abrir(f, "rb") as fh:
            for raw in fh:
                try:
                    r = json.loads(raw.decode("utf-8"))
                except ValueError:
                    malas += 1
                    continue
                t = ts(r["timestampUtc"])
                for x in r.get("assets") or []:
                    if x.get("assetName") != a.activo or not x.get("marketName") or not x.get("marketCloseUtc"):
                        continue
                    tk, cierre = x["marketName"], ts(x["marketCloseUtc"])
                    if (tk, int(t)) in visto:
                        continue
                    visto.add((tk, int(t)))
                    foto = Foto(ts=t, ticker=tk, cierre=cierre, referencia=num(x.get("underlyingOpen")),
                                precio=num(x.get("underlyingCurrent")), yes_ask=num(x.get("upPrice")),
                                no_ask=num(x.get("downPrice")), yes_bid=num(x.get("upBid")), no_bid=num(x.get("downBid")))
                    d = info.setdefault(tk, dict(cierre=cierre, ult_pre=None, post=[]))
                    if t < cierre:
                        if foto.valida():
                            d["ult_pre"] = (t, foto.yes_ask, foto.no_ask)
                        op.procesar(a.activo, foto)
                    elif len(d["post"]) < 5:
                        d["post"].append((foto.yes_ask, foto.no_ask))

    # ganador de cada bloque, igual que armar_bloques del backtest
    ganador = {}
    for tk, d in info.items():
        if not d["ult_pre"] or d["cierre"] - d["ult_pre"][0] > 10:
            continue
        _, u, dn = d["ult_pre"]
        g = "yes" if (u >= 0.90 and dn <= 0.15) else "no" if (dn >= 0.90 and u <= 0.15) else None
        for pu, pd in d["post"]:
            if g:
                break
            if pu is not None and pd is not None:
                g = "yes" if (pu >= 0.99 and pd <= 0.02) else "no" if (pd >= 0.99 and pu <= 0.02) else None
        if g:
            ganador[tk] = g
    abierta = op.estado.abiertas.pop(a.activo, None)
    if abierta:
        op.estado.pendientes.append(abierta)
    for o in list(op.estado.pendientes):
        if o.ticker in ganador:
            op.liquidar(o, ganador[o.ticker])

    filas = [r for r in reg.filas if r["mercado"] in ganador]
    pnl = [float(r["pnl_c"]) for r in filas]
    gan = sum(p > 0 for p in pnl)
    print(f"{len(info)} bloques leídos, {len(ganador)} válidos, {malas} líneas corruptas saltadas")
    if pnl:
        print(f"BOT: {len(pnl)} ops | {gan} ganadas / {len(pnl) - gan} perdidas | WR {100 * gan / len(pnl):.1f}% | "
              f"{sum(pnl) / len(pnl):+.2f} c/op | total {sum(pnl) * a.contratos / 100:+,.2f} $ con {a.contratos:g} contratos")

    if a.comparar:
        with open(a.comparar, encoding="utf-8") as fh:
            base = {r["mercado"]: r for r in csv.DictReader(fh)}
        bot = {r["mercado"]: r for r in filas}
        iguales, distintas = 0, []
        for tk in sorted(set(base) & set(bot)):
            b, o = base[tk], bot[tk]
            same = (b["lado"] == o["lado"] and abs(float(b["entrada"]) - float(o["entrada"])) < 0.0006
                    and b["salida"] == o["salida"] and abs(float(b["pnl_c"]) - float(o["pnl_c"])) < 0.02)
            if same:
                iguales += 1
            else:
                distintas.append((tk, b, o))
        solo_base = sorted(set(base) - set(bot))
        solo_bot = sorted(set(bot) - set(base))
        print(f"\nCOMPARACIÓN con {os.path.basename(a.comparar)}: {len(base)} en el backtest, {len(bot)} del bot")
        print(f"  iguales: {iguales} | distintas: {len(distintas)} | solo en el backtest: {len(solo_base)} | solo del bot: {len(solo_bot)}")
        for tk, b, o in distintas[:15]:
            print(f"  {tk}: backtest {b['lado']} {b['entrada']} {b['salida']} {b['pnl_c']} c | "
                  f"bot {o['lado']} {o['entrada']} {o['salida']} {o['pnl_c']} c")
        for tk in solo_base[:10]:
            print(f"  solo backtest: {tk} {base[tk]['lado']} {base[tk]['entrada']} {base[tk]['salida']}")
        for tk in solo_bot[:10]:
            print(f"  solo bot: {tk} {bot[tk]['lado']} {bot[tk]['entrada']} {bot[tk]['salida']}")


if __name__ == "__main__":
    main()
