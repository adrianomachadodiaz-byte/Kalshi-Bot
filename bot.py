"""Bot Kalshi 15 min (config ganadora del backtest de BTC) para correr 24/7 en Railway. Opera en REAL.

Todo se maneja desde el panel web ($PORT, con la clave ACCESS_TOKEN):
encender / apagar, ajustes, API key de Kalshi, logs, operaciones y estadísticas.
Los ajustes y la API key que se guardan en el panel quedan en DATA_DIR (el Volume de Railway).
"""
import hashlib
import hmac
import json
import os
import threading
import time
from datetime import datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

import operador
from ejecutores import EjecutorReal, log_cada
from estrategia import BLOQUE, Config, Estrategia, Foto
from kalshi import INDICE_URL as INDICE_URL_DEF
from kalshi import ErrorKalshi, Kalshi, cargar_clave, precio_indice
from operador import CDT, Estado, Operador, Registro, log


def env(nombre, defecto=""):
    v = os.environ.get(nombre, "").strip()
    return v if v else defecto


ACCESS_TOKEN = env("ACCESS_TOKEN")
PORT = int(env("PORT", "8080"))
DATA_DIR = Path(env("DATA_DIR", "/data" if os.environ.get("RAILWAY_ENVIRONMENT") else "datos"))
REST_URL = env("KALSHI_REST_URL", "https://api.elections.kalshi.com/trade-api/v2")
INDICE_URL = env("KALSHI_INDICE_URL", INDICE_URL_DEF)    # feed público del índice ({activo} = btc/eth)
ACTIVOS_VALIDOS = ("BTC", "ETH")
CICLO = float(env("CICLO_S", "0.5"))     # segundos entre fotos: con qué rapidez reacciona el stop y la entrada
PASO_GRAFICO = 1                         # segundos entre puntos de la serie que dibuja el panel

# ---------------------------------------------------------------- ajustes (panel > variables de Railway > defecto)
# clave, tipo, defecto, etiqueta, ayuda, sección ("" = básico, "av" = avanzado)
CAMPOS = [
    ("activos", "activos", "BTC", "Cripto", "BTC o ETH", ""),
    ("delta", "num", 100.0, "Delta ($)", "mínimo para entrar", ""),
    ("delay", "ent", 7, "Delay (min)", "ventana de entrada", ""),
    ("entrada_min", "num", 0.81, "Entrada mínima", "EntryRange desde", ""),
    ("entrada_max", "num", 0.86, "Entrada máxima", "EntryRange hasta", ""),
    ("tp", "num", 0.13, "Take profit (+)", "sobre la entrada", ""),
    ("exit", "opc", 0.46, "Exit (stop)", "vacío = sin Exit", ""),
    ("contratos", "num", 1.0, "Contratos", "por operación", "av"),
    ("breakeven", "opc", None, "BreakEven (+)", "vacío = sin BE", "av"),
    ("max_perdida_dia", "num", 0.0, "Pérdida máx. ($)", "0 = sin límite", "av"),
    ("meta_ganancia_dia", "num", 0.0, "Meta ($)", "0 = sin meta", "av"),
    ("desliz_entrada", "num", 0.02, "Deslizamiento", "sobre el ask", "av"),
    ("espera_entrada", "num", 2.0, "Espera al entrar (s)", "0 = IOC; 2 = deja la orden puesta", "av"),
]


def convertir(tipo, v):
    """Texto del panel / variable -> valor. ValueError si no se entiende."""
    if tipo == "activos":
        lista = [x.strip().upper() for x in str(v).split(",") if x.strip()]
        return ",".join(lista)
    if v is None or (isinstance(v, str) and v.strip().lower() in ("", "no", "sin", "none")):
        if tipo == "opc":
            return None
        raise ValueError("falta el valor")
    n = float(str(v).replace(",", ".")) if isinstance(v, str) else float(v)
    return int(n) if tipo == "ent" else n


def validar(crudo):
    """(valores, errores). crudo: dict clave -> texto o número."""
    v, err = {}, {}
    for clave, tipo, _, _, _, _ in CAMPOS:
        try:
            v[clave] = convertir(tipo, crudo.get(clave))
        except (TypeError, ValueError):
            err[clave] = "número no válido"
    if err:
        return v, err
    activos = v["activos"].split(",") if v["activos"] else []
    if not activos or any(a not in ACTIVOS_VALIDOS for a in activos):
        err["activos"] = "usa BTC o ETH"
    if not 0 < v["contratos"] <= 10000:
        err["contratos"] = "entre 0.01 y 10000"
    if v["delta"] < 0:
        err["delta"] = "no puede ser negativo"
    if not 1 <= v["delay"] <= 15:
        err["delay"] = "entre 1 y 15 minutos"
    if not 0 < v["entrada_min"] < 1:
        err["entrada_min"] = "entre 0 y 1"
    if not 0 < v["entrada_max"] < 1 or v["entrada_max"] < v["entrada_min"]:
        err["entrada_max"] = "entre la mínima y 1"
    if not 0 < v["tp"] < 1:
        err["tp"] = "entre 0 y 1"
    elif v["entrada_max"] + v["tp"] > 0.99 + 1e-9:
        err["tp"] = f"entrada máxima + TP pasa de 0.99 ({v['entrada_max'] + v['tp']:.2f})"
    if v["exit"] is not None and not 0 < v["exit"] < v["entrada_min"]:
        err["exit"] = "tiene que ser menor que la entrada mínima"
    if v["breakeven"] is not None and not 0 < v["breakeven"] < v["tp"]:
        err["breakeven"] = "entre 0 y el TP"
    for d in ("max_perdida_dia", "meta_ganancia_dia"):
        if v[d] < 0:
            err[d] = "0 o más"
    if not 0 <= v["desliz_entrada"] <= 0.10:
        err["desliz_entrada"] = "entre 0 y 0.10"
    if not 0 <= v["espera_entrada"] <= 10:
        err["espera_entrada"] = "entre 0 y 10 segundos"
    return v, err


class Ajustes:
    def __init__(self, carpeta: Path):
        self.ruta = carpeta / "ajustes.json"
        crudo = {}
        for clave, tipo, defecto, _, _, _ in CAMPOS:
            crudo[clave] = env(clave.upper()) or defecto
        if self.ruta.exists():
            try:
                guardado = json.loads(self.ruta.read_text(encoding="utf-8"))
                if "delta" not in guardado:          # ajustes de antes: había un Delta por activo
                    a = (guardado.get("activos") or "BTC").split(",")[0].lower()
                    if guardado.get(f"delta_{a}") is not None:
                        guardado["delta"] = guardado[f"delta_{a}"]
                crudo.update({k: x for k, x in guardado.items() if k in {c[0] for c in CAMPOS}})
            except Exception as e:  # noqa: BLE001
                log(f"no pude leer {self.ruta.name}: {e}")
        v, err = validar(crudo)
        if err:
            log(f"ajustes con errores ({err}); uso los valores por defecto")
            v, _ = validar({c[0]: c[2] for c in CAMPOS})
        self.valores = v

    def guardar(self, v):
        self.ruta.write_text(json.dumps(v, indent=1), encoding="utf-8")
        self.valores = v


class Claves:
    """API key de Kalshi: la guardada en el panel (DATA_DIR/claves.json) o, si no hay, las variables de Railway."""

    def __init__(self, carpeta: Path):
        self.ruta = carpeta / "claves.json"

    def leer(self):
        if self.ruta.exists():
            try:
                d = json.loads(self.ruta.read_text(encoding="utf-8"))
                return d.get("key_id", ""), d.get("private_key", "")
            except Exception as e:  # noqa: BLE001
                log(f"no pude leer {self.ruta.name}: {e}")
        pem = env("KALSHI_PRIVATE_KEY")
        if not pem and env("KALSHI_PRIVATE_KEY_PATH"):
            pem = Path(env("KALSHI_PRIVATE_KEY_PATH")).read_text(encoding="utf-8")
        return env("KALSHI_API_KEY_ID"), pem

    def guardar(self, key_id, pem):
        self.ruta.write_text(json.dumps({"key_id": key_id, "private_key": pem}), encoding="utf-8")
        try:
            os.chmod(self.ruta, 0o600)
        except OSError:
            pass


def config_de(activo, v):
    return Config(delta=v["delta"], delay=v["delay"], entrada_min=v["entrada_min"],
                  entrada_max=v["entrada_max"], tp=v["tp"], exit=v["exit"], breakeven=v["breakeven"])


# ---------------------------------------------------------------- datos en vivo
class Mercados:
    """Bloque abierto de cada activo; se vuelve a buscar al cerrar (o cada 60 s, o cada 3 s si falta la referencia)."""

    def __init__(self, k: Kalshi):
        self.k = k
        self.m, self.cuando, self.intento = {}, {}, {}

    def get(self, activo):
        ahora = time.time()
        m = self.m.get(activo)
        hace = ahora - self.cuando.get(activo, 0)
        vencido = m is None or ahora >= m["cierre"] or hace > 60 or (m["referencia"] is None and hace > 3)
        if vencido and ahora - self.intento.get(activo, 0) >= 1:
            self.intento[activo] = ahora
            try:
                nuevo = self.k.mercado_abierto(activo)
                if nuevo:
                    self.m[activo], self.cuando[activo] = nuevo, ahora
                    m = nuevo
            except Exception as e:  # noqa: BLE001
                log_cada(f"mercado{activo}", f"{activo} no pude buscar el mercado: {e}")
        return m if m and ahora < m["cierre"] else None


class Precios:
    """Precio del activo con el que se calcula el Delta: el índice de CF Benchmarks que usa Kalshi.

    Sale del feed público de Kalshi (kalshi-public-docs, `{activo}_current.json`), el mismo número que la app
    pone como "NOW". No necesita API key. El archivo se actualiza cada segundo y viene ~3-4 s por detrás.

    Si el feed no responde o se queda congelado (más de MAX_ATRASO s), el bot se queda SIN precio y no abre
    operaciones: no usa ninguna otra fuente.
    """

    MAX_ATRASO = 6         # segundos: más viejo que esto, el precio no sirve para operar.
                           # El feed viene ~3-4 s por detrás de fábrica, así que por debajo de 5 s el bot
                           # no operaría nunca. Con 15 s pasaban precios de ~28 s: medido el 27-28 sep,
                           # el Delta salía inflado (+$21 de mediana) y el bot entraba en movimientos que
                           # ya se habían dado la vuelta. Coste en el backtest de 30 días:
                           #   0 s +4.22 c/op | 4 s +2.67 | 6 s +2.23 | 15 s +2.23 | 28 s +1.91
    MAX_CONGELADO = 6      # segundos: si el archivo no avanza más que esto, está congelado

    def __init__(self, bot):
        self.bot = bot
        self.s = requests.Session()
        self.cache = {}     # activo -> (ts local, precio, atraso del feed)
        self.series = {}    # activo -> [(epoch, precio)] del último feed leído
        self.ult_ms = {}    # activo -> (marca del archivo, hora local en que la vimos avanzar)
        self.nota = ""      # por qué no hay precio (se ve arriba del panel)

    def reset(self):
        self.nota = ""

    def atraso(self, activo):
        return self.cache.get(activo, (0, None, None))[2]

    def get(self, activo):
        """Precio del índice, o None si no hay uno fresco (entonces el bot no opera)."""
        ahora = time.time()
        ts, p, atraso = self.cache.get(activo, (0.0, None, None))
        if ahora - ts < 0.5:                       # el feed cambia una vez por segundo
            return p
        try:
            precio, edad, serie = precio_indice(self.s, activo, INDICE_URL)
        except Exception as e:  # noqa: BLE001
            log_cada(f"indice{activo}", f"{activo} el índice de Kalshi no responde: {e}")
            if p is not None and ahora - ts < 5:   # fallo suelto: vale el último, hasta 5 s
                return p
            self.nota = "el índice de Kalshi no responde; sin precio no se abren operaciones"
            return None
        if precio is None or precio <= 0:
            self.nota = "el índice de Kalshi llegó vacío; sin precio no se abren operaciones"
            return None
        # feed congelado: el archivo responde pero su marca de tiempo no avanza
        marca = ahora - (edad or 0)
        vieja, visto = self.ult_ms.get(activo, (None, ahora))
        if vieja is None or marca > vieja + 0.5:
            self.ult_ms[activo] = (marca, ahora)
        elif ahora - visto > self.MAX_CONGELADO:
            self.nota = f"el índice de Kalshi lleva {ahora - visto:.0f} s sin avanzar; no opero con un precio congelado"
            log_cada(f"congelado{activo}", f"{activo} el índice lleva {ahora - visto:.0f} s congelado: no opero")
            self.cache[activo] = (ahora, None, ahora - visto)
            return None
        if edad is not None and edad > self.MAX_ATRASO:
            self.nota = f"el índice de Kalshi está atrasado {edad:.0f} s; no opero con un precio viejo"
            log_cada(f"atraso{activo}", f"{activo} el índice viene atrasado {edad:.0f} s: no opero")
            self.cache[activo] = (ahora, None, edad)
            return None
        self.nota = ""
        self.cache[activo] = (ahora, precio, edad)
        self.series[activo] = serie
        return precio

# ---------------------------------------------------------------- bot
class Bot:
    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.inicio = time.time()
        self.estado = Estado(DATA_DIR)
        self.registro = Registro(DATA_DIR, "real")
        self.ajustes = Ajustes(DATA_DIR)
        self.claves = Claves(DATA_DIR)
        self.k = Kalshi(REST_URL)            # sin clave: solo datos públicos (precios)
        self.ej = None                       # EjecutorReal cuando hay API key
        self.key_id = ""
        self.mercados = Mercados(self.k)
        self.precios = Precios(self)
        self.op = Operador({}, None, self.estado, self.registro, 1)
        self.activos, self.cfgs = [], {}
        self.aplicar(self.ajustes.valores)
        self.preparado, self.visto, self.vista, self.historia = {}, {}, {}, {}
        self.ultimo_liquidar = 0.0
        self.saldo_cache = (0.0, None)
        key_id, pem = self.claves.leer()
        if key_id and pem:
            ok, msg = self.conectar(key_id, pem)
            log(f"API key de Kalshi: {msg}")
        else:
            log("Falta la API key de Kalshi: ponla en el panel (sección API de Kalshi)")

    # ------------------------------------------------------------------ ajustes y claves
    def aplicar(self, v):
        activos = v["activos"].split(",")
        with self.op.lock:
            self.activos = activos
            self.cfgs = {a: config_de(a, v) for a in activos}
            est = {a: Estrategia(c) for a, c in self.cfgs.items()}
            for a in self.estado.abiertas:                    # una abierta de un activo quitado se sigue cuidando
                est.setdefault(a, Estrategia(config_de(a, v)))
            self.op.estrategias = est
            self.op.contratos = v["contratos"]
            self.op.max_perdida_dia = v["max_perdida_dia"]
            self.op.meta_dia = v["meta_ganancia_dia"]
            if self.ej:
                self.ej.contratos, self.ej.desliz, self.ej.n_activos = v["contratos"], v["desliz_entrada"], len(activos)
                self.ej.espera = v["espera_entrada"]
        for a, c in self.cfgs.items():
            log(f"ajustes {a}: {c.texto()} · {v['contratos']:g} contratos")

    def conectar(self, key_id, pem):
        """Prueba la API key pidiendo el saldo. (ok, mensaje)."""
        key_id, pem = key_id.strip(), pem.strip()
        if not key_id or not pem:
            return False, "faltan la API key ID o la private key"
        try:
            clave = cargar_clave(pem)
        except Exception as e:  # noqa: BLE001
            return False, f"la private key no es válida ({e.__class__.__name__}). Pega el .pem completo, con BEGIN y END."
        k = Kalshi(REST_URL, key_id, clave)
        try:
            saldo = k.saldo()
        except ErrorKalshi as e:
            return False, f"Kalshi rechazó la clave (HTTP {e.status}). Revisa que la API key ID y la private key sean pareja."
        except Exception as e:  # noqa: BLE001
            return False, f"no pude conectar con Kalshi: {e}"
        v = self.ajustes.valores
        with self.op.lock:
            ej = EjecutorReal(k, v["contratos"], v["desliz_entrada"], len(self.activos), v["espera_entrada"])
            if self.ej:
                ej.info = self.ej.info
            self.k, self.ej, self.op.ej, self.mercados.k = k, ej, ej, k
            self.key_id = key_id
            self.preparado = {}                  # vuelve a revisar posición y saldo del bloque actual
            self.precios.reset()                 # y vuelve a probar el índice con la clave nueva
        self.saldo_cache = (time.time(), saldo)
        return True, f"conectada, saldo {saldo:.2f} $"

    def saldo(self):
        ts, s = self.saldo_cache
        if self.ej and time.time() - ts > 20:
            try:
                s = self.k.saldo()
            except Exception:  # noqa: BLE001
                pass
            self.saldo_cache = (time.time(), s)
        return s if self.ej else None

    # ------------------------------------------------------------------ ciclo
    def paso(self, activo):
        m = self.mercados.get(activo)
        if not m:
            return
        t = m["ticker"]
        if self.visto.get(activo) != t:
            self.visto[activo] = t
            log(f"{activo} nuevo bloque {t} | referencia {m['referencia']} | shard {m['exchange_index']}")
        libro = self.k.libro(t)
        f = Foto(ts=time.time(), ticker=t, cierre=m["cierre"], referencia=m["referencia"],
                 precio=self.precios.get(activo), atraso=self.precios.atraso(activo), **libro)
        self.vista[activo] = f
        self._anotar_grafico(activo, f)
        if not self.ej:
            return
        self.ej.info[t] = m
        encendido = not self.estado.pausado
        if encendido and self.preparado.get(activo) != t and activo in self.activos:
            self.preparado[activo] = t
            if self.ej.preparar_bloque(activo, m, self.estado) == "ocupado":
                self.estado.operado[activo] = t
                self.estado.guardar()
        if activo not in self.activos and activo not in self.estado.abiertas:
            return
        self.op.procesar(activo, f)

    def _anotar_grafico(self, activo, f: Foto):
        """Serie del bloque para el gráfico del panel, un punto por segundo.

        El feed del índice trae los últimos 60 segundos en cada lectura, así que se vuelcan todos:
        la línea queda con un punto por segundo aunque el bot pregunte más despacio o se pierda una lectura.
        Del contrato se guarda el bid del lado comprado, solo mientras hay una operación abierta.
        """
        h = self.historia.get(activo)
        if not h or h["ticker"] != f.ticker:
            h = self.historia[activo] = dict(ticker=f.ticker, activo={}, contrato={})
        ini = f.cierre - BLOQUE
        for ts, val in (self.precios.series.get(activo) or []):
            seg = int(round(ts - ini))
            if 0 <= seg <= BLOQUE and seg not in h["activo"]:
                h["activo"][seg] = round(val, 2)
        seg = f.segundo()
        if 0 <= seg <= BLOQUE:
            if f.precio is not None and seg not in h["activo"]:
                h["activo"][seg] = round(f.precio, 2)
            op = self.estado.abiertas.get(activo)
            if op and op.ticker == f.ticker:
                bid = f.bid(op.lado)
                if bid is not None and seg not in h["contrato"]:
                    h["contrato"][seg] = round(bid, 3)

    def liquidar(self):
        if not self.ej or time.time() - self.ultimo_liquidar < 3:
            return
        self.ultimo_liquidar = time.time()
        for op in list(self.estado.pendientes):
            try:
                res = self.k.mercado(op.ticker)["resultado"]
            except Exception as e:  # noqa: BLE001
                log_cada(f"liq{op.ticker}", f"no pude leer el resultado de {op.ticker}: {e}")
                continue
            if res in ("yes", "no"):
                self.op.liquidar(op, res)
            elif time.time() - op.cierre > 1800:
                log_cada(f"liq{op.ticker}", f"{op.ticker} lleva 30 min cerrado sin resultado oficial; sigo esperando", 600)

    def correr(self):
        log(f"BOT INICIADO | {'APAGADO' if self.estado.pausado else 'ENCENDIDO'} | datos en {DATA_DIR} | "
            f"una foto cada {CICLO:g} s")
        while True:
            t0 = time.time()
            for a in list(dict.fromkeys(self.activos + list(self.estado.abiertas))):
                try:
                    self.paso(a)
                except Exception as e:  # noqa: BLE001
                    log_cada(f"paso{a}", f"{a} error en el ciclo: {e}")
            try:
                self.liquidar()
            except Exception as e:  # noqa: BLE001
                log_cada("liquidar", f"error al liquidar: {e}")
            time.sleep(max(0.05, CICLO - (time.time() - t0)))

    # ------------------------------------------------------------------ para el panel
    def resumen_json(self):
        saldo = self.saldo()                      # fuera del lock: es un pedido a Kalshi
        with self.op.lock:
            return self._resumen(saldo)

    def _resumen(self, saldo):
        hoy = datetime.now(CDT).strftime("%Y-%m-%d")
        ahora = time.time()
        vivo = []
        for a in list(dict.fromkeys(self.activos + list(self.estado.abiertas))):
            f, cfg = self.vista.get(a), self.cfgs.get(a)
            op = self.estado.abiertas.get(a)
            fila = dict(activo=a)
            if f:
                quedan = max(0, f.cierre - ahora)
                if op:
                    est = f"abierta {op.lado.upper()} {op.abiertos():g} a {op.entrada:.3f} → TP {op.objetivo:.3f}"
                elif not self.ej:
                    est = "sin API key"
                elif self.estado.pausado:
                    est = "apagado"
                elif f.precio is None:
                    est = "sin precio del índice: no opera"
                elif self.estado.operado.get(a) == f.ticker:
                    est = "ya operó este bloque"
                elif cfg and quedan > cfg.delay * 60:
                    est = f"espera los últimos {cfg.delay} min"
                else:
                    est = "buscando entrada"
                d = f.delta()
                h = self.historia.get(a) or {}
                mio = h.get("ticker") == f.ticker
                orden = lambda k: sorted([s_, v_] for s_, v_ in (h.get(k) or {}).items()) if mio else []
                fila["grafico"] = dict(
                    activo=orden("activo"), contrato=orden("contrato"),
                    referencia=f.referencia, ventana=(cfg.delay * 60) if cfg else None,
                    delta_min=cfg.delta if cfg else None,
                    entrada=op.entrada if op else None, tp=op.objetivo if op else None,
                    exit=cfg.exit if (op and cfg) else None, lado=op.lado if op else None,
                    bid=(f.bid(op.lado) if op else None))
                fila.update(mercado=f.ticker, quedan=quedan, referencia=f.referencia, precio=f.precio,
                            indice_s=self.precios.atraso(a), delta=d,
                            delta_ok=bool(cfg and d is not None and d >= cfg.delta), yes_ask=f.yes_ask,
                            yes_bid=f.yes_bid, no_ask=f.no_ask, no_bid=f.no_bid, estado=est)
            vivo.append(fila)
        filas = [r for r in self.registro.filas if r.get("modo") == "real"]
        return dict(
            encendido=not self.estado.pausado, conectado=bool(self.ej),
            key_id=(self.key_id[:4] + "…" + self.key_id[-4:]) if len(self.key_id) > 8 else ("configurada" if self.key_id else ""),
            saldo=saldo, limite_diario=self.op.limite_diario(), aviso_precio=self.precios.nota,
            servidor_s=ahora - self.inicio,
            encendido_s=(ahora - self.estado.encendido_desde) if self.estado.encendido_desde and not self.estado.pausado else None,
            total=self.registro.resumen(), hoy=self.registro.resumen(hoy), vivo=vivo,
            abiertas=[dict(activo=o.activo, lado=o.lado.upper(), contratos=o.abiertos(), entrada=o.entrada,
                           objetivo=o.objetivo, ticker=o.ticker) for o in self.estado.abiertas.values()],
            pendientes=len(self.estado.pendientes),
            operaciones=filas[-50:][::-1], logs=list(operador.LOGS)[-250:],
            ajustes=self.ajustes.valores,
            campos=[dict(clave=c, tipo=t, etiqueta=e, ayuda=h, seccion=sec) for c, t, _, e, h, sec in CAMPOS])


# ---------------------------------------------------------------- panel web
AQUI = Path(__file__).parent
SESION = hashlib.sha256(("kalshi-bot:" + ACCESS_TOKEN).encode()).hexdigest() if ACCESS_TOKEN else ""

FUENTE = ('<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
          '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&display=swap">')
ICONO = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
         "%3Crect width='32' height='32' rx='8' fill='%230d0f14'/%3E%3Cpath d='M5 21 L12 13 L17 18 L27 7' "
         "fill='none' stroke='%232ed673' stroke-width='3' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E")

BASE = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="dark">
<title>Kalshi Bot</title><link rel="icon" href="{icono}">{fuente}<style>
*{{box-sizing:border-box}}
body{{margin:0;min-height:100dvh;display:flex;align-items:center;justify-content:center;padding:24px;
background:#0d0f14;color:#e9ebf0;font:15px/1.55 Geist,system-ui,-apple-system,"Segoe UI",sans-serif;-webkit-font-smoothing:antialiased}}
body::before{{content:"";position:fixed;inset:0;pointer-events:none;opacity:.035;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='3'/%3E%3C/filter%3E%3Crect width='160' height='160' filter='url(%23n)'/%3E%3C/svg%3E")}}
.caja{{position:relative;background:linear-gradient(158deg,#181c24,#14171e 62%);border:1px solid #232834;border-radius:14px;
padding:28px;width:100%;max-width:340px;box-shadow:0 2px 4px rgba(5,7,12,.6),0 18px 44px -20px rgba(5,7,12,1)}}
.marca{{display:flex;align-items:center;gap:9px;margin-bottom:18px}}
h1{{font-size:17px;font-weight:600;margin:0;letter-spacing:-.015em}}
p{{color:#8d94a3;font-size:13.5px;margin:0 0 16px}}
label{{display:block;font-size:12.5px;color:#8d94a3;margin-bottom:5px}}
input{{width:100%;padding:10px 12px;border-radius:9px;border:1px solid #2d3341;background:#0a0c10;color:#e9ebf0;
font:inherit;transition:border-color 180ms}}
input:hover{{border-color:#646b7a}} input:focus{{outline:none;border-color:#4c8dff}}
button{{margin-top:14px;width:100%;padding:10px;border:0;border-radius:9px;background:#4c8dff;color:#fff;
font:inherit;font-weight:550;cursor:pointer;transition:background 180ms,transform 180ms,box-shadow 180ms}}
button:hover{{background:#649dff;box-shadow:0 6px 18px -8px rgba(76,141,255,.75)}}
button:active{{transform:translateY(1px) scale(.99)}}
:focus-visible{{outline:2px solid #4c8dff;outline-offset:2px}}
.err{{color:#ff6b6b;font-size:13px;margin:10px 0 0}}
code{{background:#0a0c10;border:1px solid #232834;border-radius:5px;padding:1px 6px;font-family:"Geist Mono",ui-monospace,monospace}}
</style></head><body><div class="caja"><div class="marca">
<svg width="22" height="22" viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="8" fill="#14171e"></rect>
<path d="M5 21 L12 13 L17 18 L27 7" fill="none" stroke="#2ed673" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"></path></svg>
<h1>Kalshi Bot</h1></div>{cuerpo}</div></body></html>"""

LOGIN = BASE.format(icono=ICONO, fuente=FUENTE, cuerpo="""<form method="post" action="/login">
<p>Entra con la clave del panel para ver el bot.</p>
<label for="clave">Clave de acceso</label>
<input id="clave" type="password" name="clave" autofocus autocomplete="current-password">
{error}<button>Entrar</button></form>""")

SIN_TOKEN = BASE.format(icono=ICONO, fuente=FUENTE, cuerpo="""<p style="color:#e9ebf0;font-size:15px;margin-bottom:10px">
Falta la clave del panel</p><p>El panel solo abre con una clave, para que nadie más pueda encender el bot.
En Railway ve a <b style="color:#e9ebf0">Variables</b>, agrega <code>ACCESS_TOKEN</code> con una clave tuya y vuelve a entrar.</p>""")


def servidor(bot: Bot):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _logueado(self):
            if not SESION:
                return False
            c = SimpleCookie(self.headers.get("Cookie", ""))
            return "kbot" in c and hmac.compare_digest(c["kbot"].value, SESION)

        def _cookie(self):
            seguro = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
            return f"kbot={SESION}; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000{seguro}"

        def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _json(self, obj, code=200):
            return self._send(code, json.dumps(obj, default=str), "application/json")

        def _cuerpo(self):
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n).decode("utf-8") if n else ""

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/health":
                return self._send(200, "ok", "text/plain")
            if not SESION:
                return self._send(200, SIN_TOKEN)
            token = parse_qs(url.query).get("token", [""])[0]
            if token and hmac.compare_digest(token, ACCESS_TOKEN):      # enlace viejo con ?token=
                return self._send(303, "", extra={"Location": "/", "Set-Cookie": self._cookie()})
            if not self._logueado():
                if url.path.startswith("/api/"):
                    return self._json({"error": "sesión vencida"}, 401)
                return self._send(200, LOGIN.replace("{error}", ""))
            if url.path == "/":
                return self._send(200, (AQUI / "panel.html").read_bytes())
            if url.path == "/api/estado":
                return self._json(bot.resumen_json())
            if url.path == "/operaciones.csv":
                ruta = bot.registro.ruta
                vacio = (",".join(operador.COLUMNAS) + "\n").encode()
                return self._send(200, ruta.read_bytes() if ruta.exists() else vacio, "text/csv; charset=utf-8",
                                  {"Content-Disposition": 'attachment; filename="operaciones.csv"'})
            return self._send(404, "No encontrado", "text/plain; charset=utf-8")

        def do_POST(self):
            url = urlparse(self.path)
            if not SESION:
                return self._send(403, "Falta ACCESS_TOKEN", "text/plain; charset=utf-8")
            if url.path == "/login":
                clave = parse_qs(self._cuerpo()).get("clave", [""])[0]
                if hmac.compare_digest(clave, ACCESS_TOKEN):
                    return self._send(303, "", extra={"Location": "/", "Set-Cookie": self._cookie()})
                time.sleep(1)
                return self._send(401, LOGIN.replace("{error}", "<p>Clave incorrecta</p>"))
            if not self._logueado():
                return self._json({"error": "sesión vencida"}, 401)
            if url.path == "/logout":
                return self._send(303, "", extra={"Location": "/", "Set-Cookie": "kbot=; Path=/; Max-Age=0"})
            try:
                datos = json.loads(self._cuerpo() or "{}")
            except ValueError:
                return self._json({"ok": False, "msg": "datos no válidos"}, 400)
            if url.path == "/api/encender":
                if not bot.ej:
                    return self._json({"ok": False, "msg": "Primero conecta tu API key de Kalshi."})
                bot.op.pausar(False)
                return self._json({"ok": True, "msg": "Bot encendido."})
            if url.path == "/api/apagar":
                bot.op.pausar(True)
                abiertas = len(bot.estado.abiertas)
                extra = f" Hay {abiertas} operación abierta: se sigue cuidando hasta el TP, el stop o el cierre." if abiertas else ""
                return self._json({"ok": True, "msg": "Bot apagado." + extra})
            if url.path == "/api/reiniciar":
                n, copia = bot.registro.archivar()
                extra = f" Historial guardado en {copia}." if copia else ""
                if bot.estado.abiertas:
                    extra += " La operación abierta contará en la cuenta nueva."
                log(f"estadísticas reiniciadas desde el panel: {n} operaciones archivadas")
                return self._json({"ok": True, "msg": f"Reiniciado: {n} operaciones archivadas.{extra}"})
            if url.path == "/api/ajustes":
                v, err = validar(datos)
                if err:
                    return self._json({"ok": False, "errores": err, "msg": "Revisa los campos marcados."})
                bot.ajustes.guardar(v)
                bot.aplicar(v)
                return self._json({"ok": True, "msg": "Ajustes guardados y aplicados.", "ajustes": v})
            if url.path == "/api/claves":
                ok, msg = bot.conectar(datos.get("key_id", ""), datos.get("private_key", ""))
                if ok:
                    bot.claves.guardar(datos["key_id"].strip(), datos["private_key"].strip())
                    log(f"API key de Kalshi actualizada desde el panel: {msg}")
                return self._json({"ok": ok, "msg": msg[0].upper() + msg[1:].rstrip(".") + "."})
            return self._json({"ok": False, "msg": "no existe"}, 404)

    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    log(f"panel web en el puerto {PORT}" + ("" if ACCESS_TOKEN else " (falta ACCESS_TOKEN: el panel no abre)"))
    srv.serve_forever()


def main():
    bot = Bot()
    threading.Thread(target=servidor, args=(bot,), daemon=True).start()
    bot.correr()


if __name__ == "__main__":
    main()
