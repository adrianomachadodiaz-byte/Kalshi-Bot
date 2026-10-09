"""Casos de prueba de la especificación regla_horarios_v2 (filtro de horario y fin de semana)."""
import os
import sys
import tempfile
from datetime import datetime, timezone

os.environ.setdefault("ACCESS_TOKEN", "x")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
import bot as B  # noqa: E402

fallos = []


def ok(c, q):
    print(("  BIEN  " if c else "  MAL   ") + q)
    if not c:
        fallos.append(q)


class SinNoticias:
    def altos(self):
        return []


f = B.FiltroNoticias(SinNoticias())
f.activo = False          # solo horario + fin de semana


def caso(n, activo, utc, bloquea, finde=True):
    f.finde = finde
    ts = datetime.strptime(utc, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp()
    m = f.motivo(ts, f"T{n}{activo}{utc}", activo)
    ok(bool(m) == bloquea, f"{n:>2}. {activo} cierre {utc} UTC -> {'BLOQUEADO' if m else 'PERMITIDO'} {('(' + m + ')') if m else ''}")


print("\nCasos de la especificación (finde de cripto apagado para aislar el horario)")
caso(1, "ETH", "2026-10-09 14:15", True, False)
caso(2, "SILVER", "2026-10-09 11:00", True, False)
caso(3, "SILVER", "2026-10-09 11:15", True, False)
caso(4, "BTC", "2026-10-09 12:15", True, False)
caso(5, "BTC", "2026-10-09 13:15", False, False)
caso(6, "BTC", "2026-10-09 14:45", False, False)
caso(7, "BTC", "2026-10-09 18:15", False, False)
caso(8, "GOLD", "2026-10-09 11:00", False, False)
caso(9, "OIL", "2026-10-07 14:45", True, False)
caso(10, "OIL", "2026-10-08 14:45", False, False)
caso(11, "BTC", "2026-11-04 14:15", False, False)
caso(12, "SILVER", "2026-10-10 11:00", True, False)

print("\nExtra: límites, CME y fin de semana de cripto")
caso(13, "BTC", "2026-10-09 12:00", True, False)       # 08:00 ET justo: entra en la ventana
caso(14, "BTC", "2026-10-09 13:00", False, False)      # 09:00 ET: ya fuera
caso(15, "BTC", "2026-10-09 14:30", False, False)      # 10:30 ET: fuera
caso(16, "GOLD", "2026-10-09 09:45", True, False)      # 10:45 Londres: LBMA AM
caso(17, "GOLD", "2026-10-09 14:15", True, False)      # 15:15 Londres: LBMA PM (y 10:15 ET)
caso(18, "GOLD", "2026-10-08 21:15", True, False)      # 17:15 ET jueves: pausa CME
caso(19, "GOLD", "2026-10-08 22:45", True, False)      # 18:45 ET: primera hora tras reabrir
caso(20, "GOLD", "2026-10-08 23:15", False, False)     # 19:15 ET: ya abierto
caso(21, "NATGAS", "2026-10-11 21:00", True, False)    # domingo 17:00 ET: cerrado
caso(22, "BTC", "2026-10-10 15:00", True, True)        # sábado: finde de cripto
caso(23, "BTC", "2026-10-11 23:45", True, True)        # domingo
caso(24, "BTC", "2026-10-12 00:15", False, True)       # lunes 00:15 UTC
caso(25, "BTC", "2026-10-10 15:00", False, False)      # sábado con el finde apagado
caso(26, "BTC", "2026-10-10 12:15", False, False)      # sábado 08:15 ET: ventana solo lun-vie
caso(27, "GOLD", "2026-10-09 20:45", False, False)     # viernes 16:45 ET: todavía abierto

print("\nRESULTADO: " + ("BIEN" if not fallos else f"MAL ({len(fallos)})"))
sys.exit(1 if fallos else 0)
