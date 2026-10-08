const { chromium } = require('/opt/node-tools/node_modules/playwright');
const fallos = [];
const ok = (c,q)=>{console.log((c?'  BIEN  ':'  MAL   ')+q); if(!c) fallos.push(q);};
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport:{width:1600,height:1000}, deviceScaleFactor:1 });
  const errs=[]; p.on('pageerror', e=>errs.push(e.message));
  await p.goto('http://127.0.0.1:8901/', { waitUntil:'networkidle' });
  await p.waitForTimeout(2500);

  console.log('\n1) las nueve tarjetas existen');
  const ids = await p.$$eval('#s-bloque > section > details', es => es.map(e => e.id));
  ok(ids.length === 9, 'hay 9 tarjetas: ' + ids.length);
  for (const a of ['GOLD','SILVER','OIL']) ok(ids.includes('card_' + a), 'está ' + a);

  console.log('\n2) el oro no dibuja gráfico, explica por qué');
  const txt = await p.$eval('#grafico_GOLD', e => e.textContent.trim());
  ok(/Pyth/.test(txt) && /no hay gr/i.test(txt), 'lo dice: "' + txt.slice(0,80) + '…"');
  ok(await p.$('#svgG_GOLD') === null, 'y no hay SVG de índice');
  ok(await p.$('#svgG_BTC') !== null, 'pero BTC sí tiene su gráfico');

  console.log('\n3) su campo Delta está apagado y explicado');
  ok(await p.$eval('#f_GOLD-delta', e => e.disabled), 'el campo Delta de GOLD está desactivado');
  ok(await p.$eval('#f_BTC-delta', e => !e.disabled), 'el de BTC sigue activo');
  const ay = await p.$eval('#f_GOLD-delta', e => e.closest('div').querySelector('.ayuda').textContent);
  ok(/no se usa/.test(ay), 'y lo explica: "' + ay + '"');

  console.log('\n4) la tabla de lectura no finge un Delta');
  const fila = await p.$eval('#vivo_GOLD tr', e => [...e.children].map(t => t.textContent.trim()));
  ok(/no usa/.test(fila[4]), 'la columna Delta dice "' + fila[4] + '"');
  ok(fila[1] === '4136.82', 'la referencia va al céntimo: ' + fila[1]);

  console.log('\n5) el motivo de no entrar es el precio, no el Delta');
  const r = await p.$eval('#gPie_GOLD', e => e.textContent.trim());
  ok(!/Delta/.test(r), 'no menciona el Delta: "' + r.slice(0,70) + '"');

  console.log('\n6) se puede encender y apagar como las demás');
  ok(await p.$eval('#sw_GOLD', e => e.getAttribute('aria-checked')) === 'true', 'GOLD sale operando');
  ok(await p.$eval('#sw_SILVER', e => e.getAttribute('aria-checked')) === 'false', 'SILVER sale parada');

  console.log('\nERRORES DE JS:', errs.length ? errs : 'ninguno');
  if (errs.length) fallos.push('js');
  console.log('\nRESULTADO:', fallos.length ? `MAL (${fallos.length})` : 'BIEN');
  await b.close(); process.exit(fallos.length?1:0);
})();
