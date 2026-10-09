"""Prueba del filtro de noticias: horas fijas, evento rojo en el bloque y día cargado."""
import os, sys, tempfile, time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BASE = "/home/claude/adrianomachadodiaz-byte/kalshi-bot"
sys.path.insert(0, BASE)
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="filtro_")
os.environ["NOTICIAS"] = "0"            # sin hilo de red: las noticias se ponen a mano

import bot as B

ET = ZoneInfo("America/New_York")
fallos = []


def ok(c, q):
    print(("  BIEN  " if c else "  MAL   ") + q)
    if not c:
        fallos.append(q)


def ts(h, m, dia=0):
    # siempre un día hábil: el horario solo aplica de lunes a viernes y el finde bloquea cripto
    hoy = datetime.now(ET)
    hoy -= timedelta(days=max(0, hoy.weekday() - 4))
    base = hoy.replace(hour=h, minute=m, second=0, microsecond=0) + timedelta(days=dia)
    return base.timestamp()


def nuevo(eventos=(), **aj):
    n = B.Noticias()
    n.eventos = list(eventos)
    f = B.FiltroNoticias(n)
    v = dict(filtro_noticias="si", filtro_horario="si", filtro_finde="si", noticia_margen=2.0,
             noticia_dia_alto=3, noticia_dia_modo="mitad")
    v.update(aj)
    f.aplicar(v)
    return f


def ev(h, m, titulo="Core CPI m/m", imp="High", dia=0):
    return dict(titulo=titulo, moneda="USD", impacto=imp, ts=ts(h, m, dia), previsto="", anterior="")


# ---------------------------------------------------------------- 1. horas fijas
print("\n1) horario (regla v2: cierres 08:00-09:00 y 10:00-10:30 ET; las 14:00 ya no)")
f = nuevo()
for h, m in [(8, 0), (8, 30), (8, 45), (10, 0), (10, 15)]:
    r = f.motivo(ts(h, m), "", "BTC")
    ok(bool(r), f"BTC, bloque que cierra a las {h:02d}:{m:02d} ET bloqueado → {r}")
for h, m in [(9, 0), (10, 30), (14, 0), (11, 15)]:
    ok(f.motivo(ts(h, m), "", "BTC") is None, f"BTC, bloque de las {h:02d}:{m:02d} ET: entra normal")
f = nuevo(filtro_horario="no")
ok(f.motivo(ts(8, 15), "", "BTC") is None, "con el filtro de horario apagado, las 08:15 ET entran")

# ---------------------------------------------------------------- 2. evento rojo
print("\n2) evento de alto impacto")
f = nuevo([ev(9, 45)])
ok(bool(f.motivo(ts(9, 45))), "dato justo al cierre del bloque: bloqueado")
ok(bool(f.motivo(ts(9, 50))), "dato dentro del bloque que cierra a las 9:50: bloqueado")
ok(bool(f.motivo(ts(9, 44))), "bloque que cierra 1 min antes del dato: bloqueado (margen de 2 min)")
ok(f.motivo(ts(9, 30)) is None, "bloque que cierra 15 min antes del dato: entra")
ok(f.motivo(ts(10, 15)) is None, "bloque siguiente, ya pasado el dato: entra")
f2 = nuevo([ev(9, 45, "Retail Sales", "Medium")])
ok(f2.motivo(ts(9, 45)) is None, "un dato naranja (impacto medio) no bloquea")

# ---------------------------------------------------------------- 3. día cargado
print("\n3) día con 3 o más datos rojos")
tres = [ev(9, 15, "A"), ev(11, 45, "B"), ev(13, 15, "C")]
f = nuevo(tres)                                     # modo mitad
libres = [h for h in range(9, 16) for m in (5, 20, 35, 50) if f.motivo(ts(h, m)) is None]
ok(0 < len(libres) < 28, f"modo mitad: opera {len(libres)} de 28 bloques")
f = nuevo(tres, noticia_dia_modo="nada")
ok(all(f.motivo(ts(h, 5)) for h in range(9, 16)), "modo nada: no opera ningún bloque del día")
f = nuevo(tres[:2])
ok(f.motivo(ts(11, 5)) is None, "con solo 2 rojos el día no se considera cargado")

# ---------------------------------------------------------------- 4. apagado
print("\n4) el interruptor de la página")
f = nuevo([ev(10, 0)], filtro_noticias="no")
ok(f.motivo(ts(10, 0)) is None, "filtro apagado: no bloquea nada")

# ---------------------------------------------------------------- 5. informe
print("\n5) informe de bloques saltados")
f = nuevo([ev(9, 45)])
for h, m in [(8, 30), (10, 0), (9, 45), (9, 50)]:
    for _ in range(40):                 # el bot lo pregunta 4 veces por segundo: cada bloque cuenta UNA vez
        f.motivo(ts(h, m), f"KXBTC15M-{h:02d}{m:02d}", "BTC")
inf = f.informe()
print("  ", inf)
ok(inf["semana"]["hora"] == 2 and inf["semana"]["evento"] == 2,
   f"2 por horario y 2 por evento ({inf['semana']})")
ok(inf["semana_total"] == 4, "los totales cuadran")
ok(inf["rojos_hoy"] == 1, "cuenta 1 rojo hoy")

# ---------------------------------------------------------------- 6. si el calendario falla, no bloquea
print("\n6) sin calendario")
f = nuevo([])
ok(f.motivo(ts(11, 15)) is None, "sin noticias el bot sigue operando (no se queda parado)")

print("\nRESULTADO:", "BIEN" if not fallos else f"MAL ({len(fallos)})")
for x in fallos:
    print("  -", x)
sys.exit(1 if fallos else 0)
