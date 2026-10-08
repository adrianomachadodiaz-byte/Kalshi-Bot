const { chromium } = require('/opt/node-tools/node_modules/playwright');
const fallos = [];
const ok = (c, q) => { console.log((c ? '  BIEN  ' : '  MAL   ') + q); if (!c) fallos.push(q); };

(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
  const errs = [];
  p.on('pageerror', e => errs.push(e.message));
  await p.goto('http://127.0.0.1:8901/', { waitUntil: 'networkidle' });
  await p.waitForTimeout(2200);

  const T = id => p.$eval('#' + id, e => e.textContent.trim());
  const d = await p.evaluate(async () => (await (await fetch('/api/estado')).json()));

  console.log('\n1) las tarjetas enseñan el total');
  ok(await T('sProfit') === '+$' + d.total.usd.toFixed(2), 'Resultado = ' + await T('sProfit'));
  ok(await T('sWin') === String(d.total.ganadas) && await T('sLoss') === String(d.total.perdidas),
     'Ganadas/perdidas = ' + await T('sWin') + '/' + await T('sLoss'));
  ok(await T('sWr') === d.total.wr + '%', 'Acierto = ' + await T('sWr'));
  ok((await T('sGanado')).includes(String(Math.abs(d.total.ganado))), 'Lo ganado = ' + await T('sGanado'));
  ok((await T('sPerdido')).includes(String(Math.abs(d.total.perdido))), 'Lo perdido = ' + await T('sPerdido'));
  ok((await T('sEquilibrio')).includes(String(d.total.equilibrio)),
     'dice el acierto de equilibrio: "' + await T('sEquilibrio') + '"');
  ok((await T('sPeorNota')).includes(String(Math.abs(d.total.bajon))),
     'dice el mayor bajón: "' + await T('sPeorNota') + '"');

  console.log('\n2) el periodo cambia los números');
  await p.click('#segPeriodo button[data-v="semana"]');
  await p.waitForTimeout(500);
  ok(await T('sProfit') === '+$' + d.semana.usd.toFixed(2), '7 días: ' + await T('sProfit'));
  ok((await T('sTitulo')).includes('7 días'), 'el título lo dice: "' + await T('sTitulo') + '"');
  await p.click('#segPeriodo button[data-v="hoy"]');
  await p.waitForTimeout(500);
  ok(await T('sProfit') === '+$' + d.hoy.usd.toFixed(2), 'hoy: ' + await T('sProfit'));

  console.log('\n3) el periodo y la cripto se cruzan');
  await p.click('#segStats button[data-v="SOL"]');
  await p.waitForTimeout(500);
  ok(await T('sProfit') === '+$' + d.por_activo.SOL.hoy.usd.toFixed(2),
     'SOL hoy: ' + await T('sProfit'));
  ok((await T('sTitulo')).includes('SOL') && (await T('sTitulo')).includes('hoy'),
     'el título dice las dos cosas: "' + await T('sTitulo') + '"');
  await p.click('#segPeriodo button[data-v="total"]');
  await p.waitForTimeout(500);
  ok(await T('sProfit') === '+$' + d.por_activo.SOL.total.usd.toFixed(2),
     'SOL todo: ' + await T('sProfit'));

  console.log('\n4) la curva se dibuja y responde al ratón');
  ok(await p.$('#sCurva svg path') !== null, 'hay curva dibujada');
  const caja = await p.$('#sCurva svg');
  const bb = await caja.boundingBox();
  await p.mouse.move(bb.x + bb.width * 0.4, bb.y + bb.height / 2);
  await p.waitForTimeout(300);
  const globo = await T('cGlobo');
  ok(globo.length > 3, 'el globo dice qué llevabas ese día: "' + globo + '"');
  ok(await p.$eval('#cCruz', e => e.innerHTML.length > 0), 'y se marca el punto en la línea');

  console.log('\n5) sin operaciones no revienta');
  const vacio = await p.evaluate(() => {
    try { curvaGanancia([]); curvaGanancia(null); curvaGanancia([['2026-10-01', 1]]); return 'ok'; }
    catch (e) { return 'ERROR ' + e.message; }
  });
  ok(vacio === 'ok', 'con 0 y 1 puntos: ' + vacio);

  console.log('\nERRORES DE JS:', errs.length ? errs : 'ninguno');
  if (errs.length) fallos.push('errores de javascript');
  console.log('\nRESULTADO:', fallos.length ? `MAL (${fallos.length})` : 'BIEN');
  await b.close();
  process.exit(fallos.length ? 1 : 0);
})();
