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

- **Encender / Apagar.** Los dos piden confirmación en dos pasos (encender mueve dinero real; apagar deja de operar),
  y la confirmación se cae sola a los 6 segundos. Una **banda de color** en el borde de arriba repite el estado:
  verde encendido, gris apagado, ámbar si falta la API key. Además, mientras está apagado, un aviso amarillo lo dice
  con todas las letras. Arranca **apagado** la primera vez. Apagado no abre operaciones nuevas; si hay una abierta,
  la sigue cuidando hasta el TP, el stop o el cierre. Queda guardado aunque Railway reinicie.
- **Cabecera**: el Delta y el tiempo que queda del bloque siempre visibles arriba (la cabecera se queda fija al
  bajar), para verlos de un vistazo sin buscar.
- **Estadísticas**: profit total y de hoy, operaciones, ganadas, perdidas (con la media de cada una), % de acierto,
  tiempo encendido y saldo de Kalshi.
- **En vivo**: bloque, tiempo que queda, referencia, precio, antigüedad del índice, Delta, asks/bids y qué
  está haciendo.
- **Bloque en vivo**: el gráfico del índice durante el bloque (verde por encima de la referencia, rojo por debajo),
  con las líneas de ±Delta que se encienden al cumplirse, la ventana de entrada sombreada, el máximo y el mínimo
  del bloque y, si hay una operación abierta, una barra que muestra dónde está el bid entre el stop y el TP.
  Pasando el ratón sale la cruz con el precio y el Delta de ese segundo.
- **Próximas noticias**: el calendario económico de ForexFactory (feed público semanal, sin clave). Muestra los
  próximos eventos de impacto alto y medio del USD, con cuánto falta, la hora, el pronóstico y el dato anterior.
  Se pide cada 20 minutos en un hilo aparte: si falla, el panel lo dice y el bot sigue igual. Se apaga con
  `NOTICIAS=0`; con `NOTICIAS_MONEDAS` cambias las divisas (por defecto `USD`).
- **Operaciones abiertas** (arriba del todo): una tarjeta por operación con el activo, el lado (YES verde, NO
  rojo), contratos, entrada, bid, lo que daría venderla ahora, el tiempo que queda y la barra entre stop y TP.
  Pulsarla abre la tarjeta de ese activo.
- **Bloques en vivo**: plegadas, las tarjetas van de dos en dos (o tres, según el ancho); la que se abre ocupa la
  fila entera con su gráfico. Cada activo tiene un icono de color.
- **Saldo por shard**: el bot reserva 1 $ por contrato de cada activo encendido en el shard donde opera (las
  divisas en el 0, cripto y materias primas en el 2). Si a un shard le falta, lo pide al otro, que solo da lo que
  le sobra por encima de lo que necesitan sus propios activos (en las dos direcciones: 0 → 2 y 2 → 0). Si no
  llega para todo, pasa lo que pueda. Solo avisa cuando de verdad **no pudo entrar** en una operación por falta
  de dinero: una línea "NO ENTRÓ por falta de saldo" en el registro y un aviso arriba durante 15 minutos.
- **Las que más bajaron** (debajo del Registro): las operaciones ordenadas por lo más bajo que llegó su bid
  después de entrar, de la que más bajó a la que menos, con la hora a la que tocó ese mínimo (columna
  `hora_min` del CSV), cuánto bajó desde la entrada y cómo terminó. Las tablas largas enseñan las primeras
  filas y un botón "ver todas".
- **Plegar**: cada recuadro (Resultado, Operaciones abiertas, Operaciones, Registro, Las que más bajaron, cada
  grupo de bloques, los del lateral y los dos de Ajustes) tiene una flechita a la izquierda para plegarlo; plegado
  esconde también los botones de su cabecera. Registro y Ajustes arrancan plegados. Lo que se toca se recuerda en
  ese navegador.
- **Por activo** (lateral): dentro de cada grupo, arriba el que más operaciones lleva.
- **Pestañas**: los bloques en vivo van en tres pestañas (Cripto, Materias primas, Divisas). Cada una dice
  cuántas de su grupo operan y, con un punto verde, cuántas operaciones abiertas tiene. Se recuerda la elegida.
- **Campanita** (arriba a la derecha): avisa cuando el bot no pudo operar o algo le salió mal: falta de saldo,
  entrada parcial (Kalshi no tenía todos los contratos a ese precio), orden rechazada, un activo más de 30 s sin
  precio, una venta que no se llenó, el TP que no se pudo poner, la API key que no funciona o un error en el
  ciclo. El número rojo cuenta las nuevas; lo repetido en 10 min se agrupa (×N). Se guardan en
  `DATA_DIR/avisos.json`, así que sobreviven a un reinicio.
- **Calendario**: el resultado de cada día del mes (verde o rojo, más intenso cuanto más), con las operaciones
  y el acierto, y puntitos en los días con noticias de impacto alto (rojo) o medio (amarillo) de ForexFactory
  (solo trae la semana en curso). Pulsando un día sale su detalle. Se cambia de mes con las flechas.
- **Velocidad**: en cada vuelta los libros de todos los activos se piden a Kalshi en una sola petición
  (`/markets/orderbooks`) y los índices de las criptos se piden a la vez. Con 21 activos la vuelta pasó de ~2 s a
  ~0.2 s, así que el Ritmo (0.25 s) se cumple de verdad. Si esa petición fallara, el bot pide los libros uno por uno.
- **Activos**: tres grupos, cada uno con su sección. **Cripto** (BTC, ETH, SOL, XRP, DOGE, HYPE, BNB, NEAR, ZEC),
  con Delta. **Materias primas** (oro, plata, petróleo, gas natural, cobre, platino, paladio) y **divisas**
  (EUR/USD, GBP/USD, USD/JPY, AUD/USD, USD/CAD), sin Delta: Kalshi las liquida con Pyth, y la API de Pyth solo
  da los precios con clave (responde 401; comprobado el 11-oct-2026). Esas entran solo por la ventana y por el
  precio del contrato. Cobre, platino y paladio llevan la misma regla de horario que el oro (pausa de CME). Las
  divisas viven en el shard 0 de Kalshi y el resto en el 2 (ver "Saldo por shard"). Todos
  arrancan apagados; ninguno de los nuevos tiene backtest.
- **Horario de Kalshi** (materias primas y divisas): si el mercado está abierto o cerrado, con una **cuenta
  regresiva** que baja cada segundo hasta que vuelve a abrir (o hasta que cierra, si está abierto), cuándo cerró y
  una línea de tiempo de hace 6 h a dentro de 30 h con los tramos abiertos y las pausas diarias. No supone horarios:
  sale de los bloques que Kalshi ya tiene creados por delante (unas 30 h), pedidos cada 2 minutos en un hilo aparte
  y sin clave. Con el mercado cerrado, la tarjeta de ese activo enseña la cuenta atrás en grande, el primer bloque,
  cuánto dura la sesión siguiente y, si el filtro de horario hace esperar más, desde cuándo entra el bot. La
  cabecera dice de un vistazo cuántas materias primas están abiertas. Se apaga con `HORARIO_KALSHI=0`.
- **Registro**: los últimos mensajes del bot (entradas, TP, stops, errores), se actualizan cada segundo.
- **Operaciones**: las últimas 15 (con "ver todas", hasta 120) y un botón para bajarlas todas en CSV. La columna
  **Más bajo** dice hasta dónde bajó el bid de su lado mientras estuvo abierta (se mira con las mismas fotos que
  vigilan el stop). Una ganada que llegó a quedar a 0.05 o menos del stop (sin stop: de 0.50) lleva la etiqueta
  **casi pierde**. El margen se cambia en Ajustes → Avanzado. Las operaciones de antes de esta versión no traen el
  dato. En el CSV van como columnas nuevas `bid_min`, `stop` y `hora_min`; un CSV viejo se pone al día solo al
  arrancar, sin perder filas.
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
| `ACTIVOS`, `DELTA`, `DELAY`, `ENTRADA_MIN`, `ENTRADA_MAX`, `TP`, `EXIT`, `CONTRATOS`, `BREAKEVEN`, `MAX_PERDIDA_DIA`, `META_GANANCIA_DIA`, `DESLIZ_ENTRADA` | Ajustes iniciales (los mismos del panel) |
| `NOTICIAS` | `0` apaga el calendario de ForexFactory |
| `HORARIO_KALSHI` | `0` apaga la lectura del horario de las materias primas (y su cuenta regresiva) |
| `NOTICIAS_MONEDAS` | Divisas del calendario, separadas por coma (por defecto `USD`) |
| `NOTICIAS_URL` | Feed del calendario (por defecto el público semanal de ForexFactory) |
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
