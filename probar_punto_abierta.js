const { chromium } = require('/opt/node-tools/node_modules/playwright');
const fallos = [];
const ok = (c,q)=>{console.log((c?'  BIEN  ':'  MAL   ')+q); if(!c) fallos.push(q);};
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport:{width:1400,height:900}, deviceScaleFactor:3 });
  const errs=[]; p.on('pageerror', e=>errs.push(e.message));
  await p.goto('http://127.0.0.1:8901/', { waitUntil:'networkidle' });
  await p.waitForTimeout(2200);
  const vis = a => p.$eval('#pt_'+a, e => getComputedStyle(e).display !== 'none');
  const ab = await p.evaluate(async () => (await (await fetch('/api/estado')).json()).abiertas.map(o=>o.activo));
  console.log('\nabiertas segun el servidor:', ab);
  for (const a of ['BTC','ETH','XRP','DOGE','HYPE']) {
    const v = await vis(a);
    ok(v === ab.includes(a), `${a}: punto ${v ? 'SI' : 'no'} y abierta ${ab.includes(a) ? 'SI' : 'no'}`);
  }
  await (await p.$('#card_XRP summary')).screenshot({ path:'i_punto.png' });
  console.log('\nERRORES DE JS:', errs.length ? errs : 'ninguno');
  if (errs.length) fallos.push('js');
  console.log('\nRESULTADO:', fallos.length ? 'MAL' : 'BIEN');
  await b.close(); process.exit(fallos.length?1:0);
})();
