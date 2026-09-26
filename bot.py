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
from estrategia import Config, Estrategia, Foto
from kalshi import ErrorKalshi, Kalshi, cargar_clave, precio_coinbase
from operador import CDT, Estado, Operador, Registro, log


def env(nombre, defecto=""):
    v = os.environ.get(nombre, "").strip()
    return v if v else defecto


ACCESS_TOKEN = env("ACCESS_TOKEN")
PORT = int(env("PORT", "8080"))
DATA_DIR = Path(env("DATA_DIR", "/data" if os.environ.get("RAILWAY_ENVIRONMENT") else "datos"))
REST_URL = env("KALSHI_REST_URL", "https://api.elections.kalshi.com/trade-api/v2")
ACTIVOS_VALIDOS = ("BTC", "ETH")

# ---------------------------------------------------------------- ajustes (panel > variables de Railway > defecto)
# clave, tipo, defecto, etiqueta, ayuda
CAMPOS = [
    ("activos", "activos", "BTC", "Activos", "BTC, o BTC,ETH"),
    ("contratos", "num", 1.0, "Contratos", "por operación"),
    ("delta_btc", "num", 100.0, "Delta BTC ($)", "|precio − referencia| mínimo"),
    ("delta_eth", "num", 6.0, "Delta ETH ($)", "solo si operas ETH"),
    ("delay", "ent", 7, "Delay (min)", "entra en los últimos X min"),
    ("entrada_min", "num", 0.81, "Entrada mínima", "EntryRange desde"),
    ("entrada_max", "num", 0.86, "Entrada máxima", "EntryRange hasta"),
    ("tp", "num", 0.13, "Take profit (+)", "puntos sobre la entrada"),
    ("exit", "opc", 0.46, "Exit (stop)", "vacío = sin Exit"),
    ("breakeven", "opc", None, "BreakEven (+)", "vacío = sin BreakEven"),
    ("max_perdida_dia", "num", 0.0, "Pérdida máxima del día ($)", "0 = sin límite"),
    ("meta_ganancia_dia", "num", 0.0, "Meta de ganancia del día ($)", "0 = sin meta"),
    ("desliz_entrada", "num", 0.02, "Deslizamiento de entrada", "máximo sobre el ask"),
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
    for clave, tipo, _, _, _ in CAMPOS:
        try:
            v[clave] = convertir(tipo, crudo.get(clave))
        except (TypeError, ValueError):
            err[clave] = "número no válido"
    if err:
        return v, err
    activos = v["activos"].split(",") if v["activos"] else []
    if not activos or any(a not in ACTIVOS_VALIDOS for a in activos):
        err["activos"] = "usa BTC, ETH o BTC,ETH"
    if not 0 < v["contratos"] <= 10000:
        err["contratos"] = "entre 0.01 y 10000"
    for d in ("delta_btc", "delta_eth"):
        if v[d] < 0:
            err[d] = "no puede ser negativo"
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
    return v, err


class Ajustes:
    def __init__(self, carpeta: Path):
        self.ruta = carpeta / "ajustes.json"
        crudo = {}
        for clave, tipo, defecto, _, _ in CAMPOS:
            crudo[clave] = env(clave.upper()) or defecto
        if self.ruta.exists():
            try:
                crudo.update(json.loads(self.ruta.read_text(encoding="utf-8")))
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
    return Config(delta=v[f"delta_{activo.lower()}"], delay=v["delay"], entrada_min=v["entrada_min"],
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
    def __init__(self):
        self.s = requests.Session()
        self.cache = {}

    def get(self, activo):
        ts, p = self.cache.get(activo, (0, None))
        if time.time() - ts < 0.8:
            return p
        try:
            p = precio_coinbase(self.s, activo)
            self.cache[activo] = (time.time(), p)
            return p
        except Exception as e:  # noqa: BLE001
            log_cada(f"coinbase{activo}", f"{activo} Coinbase no responde: {e}")
            return p if time.time() - ts < 5 else None     # precio viejo de más de 5 s: no se usa


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
        self.precios = Precios()
        self.op = Operador({}, None, self.estado, self.registro, 1)
        self.activos, self.cfgs = [], {}
        self.aplicar(self.ajustes.valores)
        self.preparado, self.visto, self.vista = {}, {}, {}
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
            ej = EjecutorReal(k, v["contratos"], v["desliz_entrada"], len(self.activos))
            if self.ej:
                ej.info = self.ej.info
            self.k, self.ej, self.op.ej, self.mercados.k = k, ej, ej, k
            self.key_id = key_id
            self.preparado = {}                  # vuelve a revisar posición y saldo del bloque actual
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
                 precio=self.precios.get(activo), **libro)
        self.vista[activo] = f
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
        log(f"BOT INICIADO | {'APAGADO' if self.estado.pausado else 'ENCENDIDO'} | datos en {DATA_DIR}")
        while True:
            for a in list(dict.fromkeys(self.activos + list(self.estado.abiertas))):
                try:
                    self.paso(a)
                except Exception as e:  # noqa: BLE001
                    log_cada(f"paso{a}", f"{a} error en el ciclo: {e}")
            try:
                self.liquidar()
            except Exception as e:  # noqa: BLE001
                log_cada("liquidar", f"error al liquidar: {e}")
            time.sleep(max(0.05, int(time.time()) + 1.2 - time.time()))     # una foto por segundo

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
                elif self.estado.operado.get(a) == f.ticker:
                    est = "ya operó este bloque"
                elif cfg and quedan > cfg.delay * 60:
                    est = f"espera los últimos {cfg.delay} min"
                else:
                    est = "buscando entrada"
                d = f.delta()
                fila.update(mercado=f.ticker, quedan=quedan, referencia=f.referencia, precio=f.precio, delta=d,
                            delta_ok=bool(cfg and d is not None and d >= cfg.delta), yes_ask=f.yes_ask,
                            yes_bid=f.yes_bid, no_ask=f.no_ask, no_bid=f.no_bid, estado=est)
            vivo.append(fila)
        filas = [r for r in self.registro.filas if r.get("modo") == "real"]
        return dict(
            encendido=not self.estado.pausado, conectado=bool(self.ej),
            key_id=(self.key_id[:4] + "…" + self.key_id[-4:]) if len(self.key_id) > 8 else ("configurada" if self.key_id else ""),
            saldo=saldo, limite_diario=self.op.limite_diario(),
            servidor_s=ahora - self.inicio,
            encendido_s=(ahora - self.estado.encendido_desde) if self.estado.encendido_desde and not self.estado.pausado else None,
            total=self.registro.resumen(), hoy=self.registro.resumen(hoy), vivo=vivo,
            abiertas=[dict(activo=o.activo, lado=o.lado.upper(), contratos=o.abiertos(), entrada=o.entrada,
                           objetivo=o.objetivo, ticker=o.ticker) for o in self.estado.abiertas.values()],
            pendientes=len(self.estado.pendientes),
            operaciones=filas[-50:][::-1], logs=list(operador.LOGS)[-250:],
            ajustes=self.ajustes.valores,
            campos=[dict(clave=c, tipo=t, etiqueta=e, ayuda=h) for c, t, _, e, h in CAMPOS])


# ---------------------------------------------------------------- panel web
AQUI = Path(__file__).parent
SESION = hashlib.sha256(("kalshi-bot:" + ACCESS_TOKEN).encode()).hexdigest() if ACCESS_TOKEN else ""

LOGIN = """<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kalshi Bot</title><style>body{font-family:system-ui,sans-serif;background:#0f1115;color:#e8e8ea;display:flex;align-items:center;
justify-content:center;min-height:100vh;margin:0}form{background:#181b22;border:1px solid #2a2f3a;border-radius:12px;padding:28px;width:280px}
h1{font-size:20px;font-weight:600;margin:0 0 16px}input{width:100%;box-sizing:border-box;padding:10px;border-radius:8px;border:1px solid #2a2f3a;
background:#0f1115;color:#e8e8ea;font-size:16px}button{margin-top:12px;width:100%;padding:10px;border:0;border-radius:8px;background:#3b82f6;
color:#fff;font-size:16px;cursor:pointer}p{color:#f87171;font-size:14px}</style></head><body><form method="post" action="/login">
<h1>Kalshi Bot</h1><input type="password" name="clave" placeholder="Clave (ACCESS_TOKEN)" autofocus>{error}<button>Entrar</button></form></body></html>"""

SIN_TOKEN = """<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kalshi Bot</title></head><body style="font-family:system-ui;background:#0f1115;color:#e8e8ea;padding:24px;max-width:560px">
<h2>Falta la clave del panel</h2><p>Para proteger el bot, el panel solo abre con una clave. En Railway ve a
<b>Variables</b>, agrega <code>ACCESS_TOKEN</code> con una clave tuya y vuelve a entrar.</p></body></html>"""


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
                return self._send(200, ruta.read_bytes() if ruta.exists() else b"", "text/csv; charset=utf-8",
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
