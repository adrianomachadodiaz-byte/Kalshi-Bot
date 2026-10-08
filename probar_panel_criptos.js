const { chromium } = require('/opt/node-tools/node_modules/playwright');
const fallos = [];
const ok = (c, q) => { console.log((c ? '  BIEN  ' : '  MAL   ') + q); if (!c) fallos.push(q); };

(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
  const errs = [];
  p.on('pageerror', e => errs.push('PAGEERROR: ' + e.message));
  await p.goto('http://127.0.0.1:8901/', { waitUntil: 'networkidle' });
  await p.waitForTimeout(2000);

  const txt = a => p.$eval('#sw_' + a, e => e.textContent.trim());
  const act = () => p.$eval('#f_activos', e => e.value);

  console.log('\n1) estado de arranque');
  ok(await txt('BTC') === 'Operando', 'BTC sale como "Operando" (' + await txt('BTC') + ')');
  ok(await txt('HYPE') === 'Parada', 'HYPE sale como "Parada" (' + await txt('HYPE') + ')');
  ok((await act()) === 'BTC,ETH,XRP,DOGE', 'el campo Cripto arranca con las cuatro: ' + await act());

  console.log('\n2) encender HYPE pide confirmación');
  await p.click('#sw_HYPE');
  await p.waitForTimeout(250);
  ok(await txt('HYPE') === 'Confirmar', 'un clic solo pide confirmar (' + await txt('HYPE') + ')');
  ok(!(await act()).includes('HYPE'), 'con un solo clic NO se ha encendido nada');
  ok(await p.$eval('#card_HYPE', e => !e.open), 'la tarjeta no se ha plegado/desplegado sola');

  console.log('\n3) el segundo clic la enciende');
  await p.click('#sw_HYPE');
  await p.waitForTimeout(1200);
  ok((await act()).includes('HYPE'), 'HYPE entra en el campo Cripto: ' + await act());
  ok(await txt('HYPE') === 'Operando', 'el botón pasa a "Operando" (' + await txt('HYPE') + ')');

  console.log('\n4) apagar es inmediato, sin confirmar');
  await p.click('#sw_BTC');
  await p.waitForTimeout(1200);
  ok(!(await act()).includes('BTC'), 'BTC sale del campo Cripto: ' + await act());
  ok(await txt('BTC') === 'Parada', 'el botón pasa a "Parada" (' + await txt('BTC') + ')');

  console.log('\n5) no deja quedarse sin ninguna');
  for (const a of ['ETH', 'XRP', 'DOGE']) { await p.click('#sw_' + a); await p.waitForTimeout(900); }
  await p.click('#sw_HYPE');
  await p.waitForTimeout(700);
  ok((await act()) === 'HYPE', 'queda solo HYPE: ' + await act());
  const aviso = await p.$eval('#msgSw_HYPE', e => e.textContent.trim());
  ok(/al menos una/.test(aviso), 'avisa en vez de dejarlo vacío: "' + aviso + '"');
  ok(await txt('HYPE') === 'Operando', 'y el botón sigue diciendo la verdad (' + await txt('HYPE') + ')');

  console.log('\n6) guardar los ajustes de una cripto');
  await p.$eval('#card_XRP', e => e.open = true);
  await p.$eval('#aj_XRP', e => e.open = true);
  await p.fill('#f_XRP-tp', '0.07');
  await p.click('[data-guardar="XRP"]');
  await p.waitForTimeout(1200);
  const m = await p.$eval('#msgAct_XRP', e => e.textContent.trim());
  ok(/guardad/i.test(m), 'el botón de XRP guarda: "' + m + '"');
  const tp = await p.evaluate(async () => (await (await fetch('/api/estado')).json()).ajustes.por_activo.XRP.tp);
  ok(tp === 0.07, 'el servidor recibió el TP nuevo de XRP: ' + tp);

  console.log('\n7) un valor imposible se marca en la tarjeta de su cripto');
  // HYPE es la unica encendida ahora: los errores de una cripto parada no molestan
  await p.$eval('#card_HYPE', e => e.open = true);
  await p.$eval('#aj_HYPE', e => e.open = true);
  await p.fill('#f_HYPE-tp', '9');
  await p.click('[data-guardar="HYPE"]');
  await p.waitForTimeout(1000);
  const err = await p.$eval('#e_HYPE-tp', e => e.textContent.trim());
  ok(err.length > 0, 'el error sale junto al campo de HYPE: "' + err + '"');
  ok(await p.$eval('#f_HYPE-tp', e => e.classList.contains('malo')), 'y el campo queda marcado');

  console.log('\n8) una cripto parada con un ajuste raro no bloquea el guardado');
  await p.fill('#f_HYPE-tp', '0.15');
  await p.$eval('#card_XRP', e => e.open = true);
  await p.$eval('#aj_XRP', e => e.open = true);
  await p.fill('#f_XRP-tp', '9');            // XRP esta parada
  await p.click('[data-guardar="HYPE"]');
  await p.waitForTimeout(1000);
  const m8 = await p.$eval('#msgAct_HYPE', e => e.textContent.trim());
  ok(/guardad/i.test(m8), 'se guarda igual: "' + m8 + '"');

  console.log('\nERRORES DE JS:', errs.length ? errs : 'ninguno');
  if (errs.length) fallos.push('errores de javascript');
  console.log('\nRESULTADO:', fallos.length ? `MAL (${fallos.length})` : 'BIEN');
  await b.close();
  process.exit(fallos.length ? 1 : 0);
})();
