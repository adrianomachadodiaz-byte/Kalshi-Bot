"""Reglas de la estrategia: las mismas del backtest (resultados_analisis/scripts/motor.py).

Recibe una foto por segundo y solo decide cuándo entrar y cuándo salir.
No sabe nada de Kalshi ni de órdenes. La usan el bot en vivo y probar_con_datos.py.
"""
from dataclasses import asdict, dataclass, field
from typing import Optional

TOL = 0.0001    # mismo margen que el backtest (0.01 centavos)
BLOQUE = 900    # segundos de un bloque de 15 min


def comision(p):
    """Comisión taker de Kalshi por contrato, sin redondear (igual que el backtest)."""
    return 0.07 * p * (1 - p)


@dataclass
class Config:
    delta: float                       # entra solo si |precio - referencia| >= delta
    delay: int                         # entra solo en los últimos `delay` minutos
    entrada_min: float                 # EntryRange
    entrada_max: float
    tp: float                          # TP = entrada + tp (cuenta cuando el bid llega)
    exit: Optional[float]              # stop fijo: sale si el bid <= exit (None = sin Exit)
    breakeven: Optional[float] = None  # si el bid sube +X, el stop pasa al precio de entrada

    def texto(self):
        ex = f"{self.exit:.2f}" if self.exit is not None else "sin"
        be = f"+{self.breakeven:.2f}" if self.breakeven is not None else "sin"
        return (f"Delta {self.delta:g} · Delay {self.delay} · Entrada {self.entrada_min:.2f}-{self.entrada_max:.2f} · "
                f"TP +{self.tp:.2f} · Exit {ex} · BreakEven {be}")


@dataclass
class Foto:
    """Lo que se ve en un segundo: libro del bloque y precio del activo."""
    ts: float                    # epoch en segundos
    ticker: str
    cierre: float                # epoch del cierre del bloque
    referencia: Optional[float]  # precio a superar del bloque (floor_strike / underlyingOpen)
    precio: Optional[float]      # precio actual del activo
    yes_ask: Optional[float]
    no_ask: Optional[float]
    yes_bid: Optional[float]
    no_bid: Optional[float]
    atraso: Optional[float] = None   # segundos de antigüedad del precio (para vigilar el feed)

    def valida(self):
        """Igual que el backtest: los dos asks existen y suman entre 0.97 y 1.10."""
        a, b = self.yes_ask, self.no_ask
        if a is None or b is None:
            return False
        return 0.0005 <= a <= 1.0 and 0.0005 <= b <= 1.0 and 0.97 <= a + b <= 1.10

    def ask(self, lado):
        return self.yes_ask if lado == "yes" else self.no_ask

    def bid(self, lado):
        return self.yes_bid if lado == "yes" else self.no_bid

    def segundo(self):
        """Segundo del bloque (0 = apertura, 899 = último)."""
        return int(self.ts - (self.cierre - BLOQUE))

    def delta(self):
        if self.precio is None or self.referencia is None:
            return None
        return abs(self.precio - self.referencia)


@dataclass
class Operacion:
    """Una operación (una por bloque y activo). Los precios son del lado comprado (YES o NO)."""
    activo: str
    ticker: str
    lado: str                  # "yes" o "no"
    entrada: float             # precio pagado por contrato
    contratos: float
    cierre: float              # epoch del cierre del bloque
    objetivo: float            # precio del TP (1.0 = sin TP: se cobra al cerrar el bloque)
    ts_entrada: float
    delta: Optional[float]
    comision_entrada: float    # en dólares, total
    salidas: list = field(default_factory=list)   # {n, precio, comision, motivo, ts}
    be_armado: bool = False
    saliendo: bool = False     # ya tocó el stop: hay que vender lo que quede
    tp_orden: Optional[str] = None
    tp_llenos: float = 0.0     # contratos del TP ya registrados (modo real)
    tp_comision: float = 0.0
    shard: Optional[int] = None  # exchange_index del mercado (modo real)
    atraso: Optional[float] = None   # antigüedad del precio con el que se decidió entrar
    sin_tp: bool = False       # entrada + TP pasa de 0.99: no se pone orden, se cobra al cerrar el bloque

    def abiertos(self):
        return round(self.contratos - sum(s["n"] for s in self.salidas), 2)

    def salir(self, n, precio, comision_total, motivo, ts):
        if n > 0:
            self.salidas.append(dict(n=n, precio=precio, comision=comision_total, motivo=motivo, ts=ts))

    def pnl(self):
        """Ganancia en dólares con comisiones."""
        return sum(s["n"] * (s["precio"] - self.entrada) - s["comision"] for s in self.salidas) - self.comision_entrada

    def a_dict(self):
        return asdict(self)

    @staticmethod
    def de_dict(d):
        return Operacion(**d)


class Estrategia:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def entrada(self, f: Foto):
        """('yes' | 'no', ask) si en esta foto se cumple todo para entrar; si no, None."""
        c = self.cfg
        if not f.valida() or f.ts >= f.cierre:
            return None
        if f.segundo() < BLOQUE - 60 * c.delay:
            return None
        d = f.delta()
        if d is None or d < c.delta - 1e-9:
            return None
        lados = [(lado, f.ask(lado)) for lado in ("yes", "no")
                 if c.entrada_min - TOL <= f.ask(lado) <= c.entrada_max + TOL]
        if not lados:
            return None
        # si los dos lados están en rango se compra el de ask más alto (empate: YES, como el backtest)
        return max(lados, key=lambda x: x[1])

    def salida(self, op: Operacion, f: Foto):
        """'stop', 'tp' o None para una operación abierta. Si en la misma foto se tocan los dos, cuenta el stop."""
        c = self.cfg
        if not f.valida() or f.ts >= f.cierre:
            return None
        bid = f.bid(op.lado)
        if bid is None:
            return None
        if c.exit is not None and bid <= c.exit + TOL:
            return "stop"
        if op.be_armado and bid <= op.entrada + TOL:
            return "stop"
        if not op.sin_tp and bid >= op.objetivo - TOL:
            return "tp"                  # sin TP no hay salida por objetivo: se cobra al cerrar
        if c.breakeven is not None and bid >= op.entrada + c.breakeven - TOL:
            op.be_armado = True          # el stop en BE vale desde la foto siguiente
        return None
