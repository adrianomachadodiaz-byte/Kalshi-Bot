# Kalshi Bot (BTC 15 min)

Bot para los mercados de 15 minutos de Kalshi con la configuración ganadora del backtest del 25-sep-2026:

**Delta 100 $ · Delay 7 · Entrada 0.81–0.86 · TP +0.13 · Exit 0.46 · sin BreakEven · solo BTC · 1 contrato**

Probado contra los datos del 25-ago al 25-sep: el código del bot repite **las mismas 466 operaciones** del backtest
(399 ganadas / 67 perdidas, +1,965 $ con 100 contratos, 0 diferencias). Ver `probar_con_datos.py`.

**Opera en real** (manda órdenes con tu API key de Kalshi).

## Qué hace

Dos veces por segundo mira el bloque abierto de BTC (libro de Kalshi + índice de BTC de Kalshi) y:

1. **Entra** en los últimos 7 minutos si el ask de YES o de NO está entre 0.81 y 0.86 y
   |precio BTC − precio de referencia del bloque| ≥ 100 $. Si los dos lados están en rango, compra el de ask más alto.
   Una sola operación por bloque.
2. **TP**: deja una orden límite de venta en entrada + 0.13.
3. **Stop**: si el bid de su lado baja a 0.46 o menos, cancela el TP y vende todo al mejor bid.
4. Si no toca ni TP ni stop, se queda hasta el cierre (paga 1 o 0).

## Panel web

Todo se maneja desde el panel (el dominio de Railway, con tu clave `ACCESS_TOKEN`):

- **Encender / Apagar.** Arranca **apagado** la primera vez. Apagado no abre operaciones nuevas; si hay una abierta,
  la sigue cuidando hasta el TP, el stop o el cierre. Queda guardado aunque Railway reinicie.
- **Estadísticas**: profit total y de hoy, operaciones, ganadas, perdidas (con la media de cada una), % de acierto,
  tiempo encendido y saldo de Kalshi.
- **En vivo**: bloque, tiempo que queda, referencia, precio, antigüedad del índice, Delta, asks/bids y qué
  está haciendo.
- **Logs**: los últimos mensajes del bot (entradas, TP, stops, errores), se actualizan cada 2 segundos.
- **Operaciones**: las últimas 50 y un botón para bajarlas todas en CSV.
- **Ajustes**: contratos, Delta, Delay, rango de entrada, TP, Exit, BreakEven, límites del día y deslizamiento.
  Se aplican al instante al guardar (sin reiniciar) y revisa que tengan sentido (por ejemplo, entrada máxima + TP ≤ 0.99).
- **API de Kalshi**: pegas la API key ID y la private key; el bot las prueba pidiendo tu saldo y solo las guarda si Kalshi las acepta.
  La private key no se vuelve a mostrar.

## Subir a GitHub (sin instalar nada)

1. Entra a <https://github.com/new>. Nombre: `kalshi-bot`. Marca **Private**. Crea el repositorio.
2. En la página del repositorio vacío, haz clic en **uploading an existing file**.
3. Arrastra **todo el contenido** de esta carpeta (`bot.py`, `panel.html`, `estrategia.py`, `operador.py`, `ejecutores.py`,
   `kalshi.py`, `probar_con_datos.py`, `requirements.txt`, `railway.json`, `.python-version`, `.gitignore`, `README.md`).
   Si Windows no muestra los archivos que empiezan con punto, actívalo en el Explorador: Vista → Mostrar → Elementos ocultos.
4. **Commit changes**.

Nunca subas tu `.pem` ni tu API key a GitHub: van en el panel (o en las Variables de Railway).

## Encenderlo en Railway

1. En Railway: **New Project → Deploy from GitHub repo → kalshi-bot**.
2. En el servicio: **Settings → Volumes → New Volume**, montado en `/data`. **Es necesario**: ahí se guardan los ajustes,
   la API key, las operaciones y si está encendido. Sin Volume se pierde todo en cada reinicio.
3. **Variables**: solo `ACCESS_TOKEN` = una clave tuya para entrar al panel.
4. **Settings → Networking → Generate Domain** y abre ese enlace. Entra con tu clave.
5. En el panel: pega tu API key en **API de Kalshi** → **Guardar y probar**. Revisa los **Ajustes** → **Encender**.

## Variables de Railway (opcionales)

El panel manda: lo que guardes ahí gana sobre estas variables. Sirven solo como valores iniciales.

| Variable | Para qué |
|---|---|
| `ACCESS_TOKEN` | **Obligatoria.** Clave del panel. Sin ella el panel no abre. |
| `KALSHI_API_KEY_ID`, `KALSHI_PRIVATE_KEY` | API key si prefieres ponerla aquí en vez de en el panel |
| `CICLO_S` | Segundos entre fotos (por defecto `0.5`): con qué rapidez reaccionan la entrada y el stop |
| `KALSHI_INDICE_URL` | URL del feed del índice (por defecto el público de Kalshi; `{activo}` se cambia por btc/eth) |
| `CONTRATOS`, `ACTIVOS`, `DELTA_BTC`, `DELTA_ETH`, `DELAY`, `ENTRADA_MIN`, `ENTRADA_MAX`, `TP`, `EXIT`, `BREAKEVEN`, `MAX_PERDIDA_DIA`, `META_GANANCIA_DIA`, `DESLIZ_ENTRADA` | Ajustes iniciales (los mismos del panel) |
| `DATA_DIR` | Carpeta del Volume (por defecto `/data`) |

## Cosas a saber

- **Un solo bot por cuenta y mercado.** No lo corras al mismo tiempo que NightShark en BTC: se pisarían.
  Si al empezar un bloque ya hay una posición que el bot no abrió, no opera ese bloque.
- **Saldo en el shard.** Los mercados 15m están en el shard 2 de Kalshi. Al empezar cada bloque (si está encendido)
  el bot deja `CONTRATOS × 1 $` en ese shard, pasándolo del shard 0 si falta (igual que NightShark).
- **Entrada.** Acepta pagar hasta `DESLIZ_ENTRADA` (0.02) más que el ask, pero nunca más que la entrada máxima (0.86).
- **Velocidad.** El bot toma una foto cada 0.5 s (variable `CICLO_S`), así que la entrada y el stop reaccionan el
  doble de rápido que antes. El precio del índice se pide una vez por segundo, que es cada cuánto se actualiza.
- **Reinicios.** Si Railway reinicia con una operación abierta, el TP sigue puesto en Kalshi, pero el stop no se vigila
  durante los segundos en que el bot está caído. Los ajustes del panel no reinician nada.
- **La private key queda guardada en el Volume** (`/data/claves.json`). Cualquiera con tu `ACCESS_TOKEN` puede usar el
  panel: pon una clave larga y no la compartas.
- **Precio de BTC: el índice de Kalshi.** El Delta se calcula con el índice de CF Benchmarks (BRTI), el mismo
  número que la app de Kalshi muestra como **NOW**. Sale del feed público de Kalshi
  (`kalshi-public-docs.s3.amazonaws.com/external/crypto/btc_current.json`): **no necesita API key**, trae los
  últimos 60 valores del índice (uno por segundo) y viene unos 3-4 segundos por detrás del tiempo real.
  - El bot avisa y **no abre operaciones** si el feed no responde o si se queda congelado más de 15 segundos.
    Nunca cambia a otra fuente de precio.
  - La columna **Índice** de la tabla "En vivo" dice hace cuántos segundos es el precio.
  - Ojo: el backtest de las 466 operaciones se hizo con el precio de Coinbase, que se lleva unos dólares con el
    índice. Cerca del corte de Delta = 100 algunas entradas pueden salir distintas a las del backtest.
- **La referencia NO es el precio al abrir el bloque.** El `floor_strike` de Kalshi es el promedio de 60 segundos del
  índice durante el último minuto del bloque anterior (el mismo valor con el que Kalshi liquidó ese bloque). Por eso
  el Delta **nunca arranca en 0**: al abrir el bloque ya vale unos 9 $ de media (en 73 bloques medidos del 26-27 de
  septiembre: mediana 8.8 $, p90 21 $, máximo 42 $). No es un error del bot ni de la fuente del precio.
- **Liquidez.** El backtest no pudo comprobar el tamaño del libro. Con muchos contratos, la entrada y el stop pueden
  llenarse a peor precio que el ask/bid de arriba.
- **Comisiones.** Se anotan las que devuelve Kalshi en cada orden.

## Probar con datos guardados

```
pip install -r requirements.txt
python probar_con_datos.py "CARPETA_CON_price-data" --comparar operaciones_btc_466.csv
```

Pasa archivos `price-data` (del grabador o de Kalshi BackTest) por las mismas reglas del bot, sin mandar órdenes,
para comprobar que hace lo mismo que el backtest.
