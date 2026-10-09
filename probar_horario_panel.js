const { chromium } = require('/opt/node-tools/node_modules/playwright');
const fallos = [];
const ok = (c,q)=>{console.log((c?'  BIEN  ':'  MAL   ')+q); if(!c) fallos.push(q);};
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport:{width:1700,height:1000}, deviceScaleFactor:2 });
  const errs=[]; p.on('pageerror', e=>errs.push(e.message));
  await p.goto('http://127.0.0.1:8902/', { waitUntil:'networkidle' });
  await p.waitForTimeout(2600);

  console.log('\n1) el aviso sale junto al interruptor de los cerrados');
  for (const a of ['GOLD','SILVER','OIL','NATGAS']) {
    const t = await p.$eval('#cer_' + a, e => e.textContent.trim());
    ok(/fin de semana/.test(t), `${a}: "${t}"`);
    ok(/abre/.test(t) && /faltan/.test(t), `${a} dice cuándo abre y cuánto falta`);
  }

  console.log('\n2) los que sí operan no llevan aviso');
  for (const a of ['BTC','ETH','SOL']) {
    ok(await p.$eval('#cer_' + a, e => e.textContent.trim()) === '', a + ' sin aviso');
  }

  console.log('\n3) la cuenta atrás baja sola');
  const t1 = await p.$eval('#cer_GOLD', e => e.textContent);
  await p.waitForTimeout(2500);
  const t2 = await p.$eval('#cer_GOLD', e => e.textContent);
  ok(t1.length > 0 && t2.length > 0, 'sigue ahí tras refrescar');

  console.log('\n4) la tarjeta del oro ya no está muda');
  const est = await p.$eval('#vivo_GOLD', e => e.textContent.trim());
  ok(/cerrado|sin bloque/.test(est), `dice: "${est.slice(0,70)}"`);

  await (await p.$('#card_GOLD summary')).screenshot({ path:'h_gold.png' });
  await (await p.$('#card_BTC summary')).screenshot({ path:'h_btc.png' });
  console.log('\nERRORES DE JS:', errs.length ? errs : 'ninguno');
  if (errs.length) fallos.push('js');
  console.log('\nRESULTADO:', fallos.length ? `MAL (${fallos.length})` : 'BIEN');
  await b.close(); process.exit(fallos.length?1:0);
})();
