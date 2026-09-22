#!/usr/bin/env node
/*
 * HUNDESUCHE JS-SCRAPER — Playwright-Browser für Seiten, die nur mit JavaScript rendern.
 * Schreibt js_results.json; scanner.py liest das ein, scored und merged in den Report.
 *
 *   node scraper.js            # Standard-Tiefe
 *   node scraper.js --pages 15 # tiefer = ältere Anzeigen
 */
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const extractCards = require('./extract.js');

const arg = (n, d) => { const i = process.argv.indexOf(n); return i > -1 ? process.argv[i + 1] : d; };
const PAGES = parseInt(arg('--pages', '5'), 10);
const OUT = path.join(__dirname, 'js_results.json');
const UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36';

const Q = ['schäferhund husky', 'husky mix', 'schäferhund mix', 'husky', 'schäferhund', 'mischling rüde'];
const enc = s => encodeURIComponent(s);
const dash = s => s.trim().replace(/\s+/g, '-');

// {page} wird durch die Seitenzahl ersetzt. Seite 1 = ohne Parameter-Suffix wo nötig.
const SOURCES = [
  // deine-tierwelt
  ...Q.map(q => ({ site: 'deine-tierwelt', url: `https://www.deine-tierwelt.de/kleinanzeigen/hunde-c4087/q-${enc(dash(q))}/?page={page}`, ad: 'deine-tierwelt\\.de/kleinanzeigen/[^/]+-a\\d+/?$' })),
  { site: 'deine-tierwelt', url: 'https://www.deine-tierwelt.de/kleinanzeigen/entlaufen-zugelaufen-gestohlen-c4126/?page={page}', ad: 'deine-tierwelt\\.de/kleinanzeigen/[^/]+-a\\d+/?$', lost: true, pages: 12 },
  // quoka
  ...Q.map(q => ({ site: 'quoka', url: `https://www.quoka.de/anzeigen/tiermarkt/hunde/?q=${enc(q)}&page={page}`, ad: 'quoka\\.de/.+/anzeige/.+\\.html' })),
  ...Q.slice(0, 3).map(q => ({ site: 'quoka', url: `https://www.quoka.de/anzeigen/tiermarkt/hunde/bayern/?q=${enc(q)}&page={page}`, ad: 'quoka\\.de/.+/anzeige/.+\\.html' })),
  { site: 'quoka', url: 'https://www.quoka.de/anzeigen/tiermarkt/hunde/bayern/?dogbreed=mischling&page={page}', ad: 'quoka\\.de/.+/anzeige/.+\\.html' },
  { site: 'quoka', url: 'https://www.quoka.de/anzeigen/tiermarkt/hunde/bayern/?dogbreed=deutscher+sch%c3%a4ferhund&page={page}', ad: 'quoka\\.de/.+/anzeige/.+\\.html' },
  // markt.de (Pagination über "weiter"-Link)
  ...Q.map(q => ({ site: 'markt.de', url: `https://www.markt.de/tiere/hunde/?keywords=${enc(q)}`, ad: 'markt\\.de/.+/a/[0-9a-f]{6,}/', nextLink: true })),
  { site: 'markt.de', url: 'https://www.markt.de/tiere/entlaufen-zugelaufen/', ad: 'markt\\.de/.+/a/[0-9a-f]{6,}/', nextLink: true, lost: true, pages: 12 },
  { site: 'markt.de', url: 'https://www.markt.de/tiere/tiervermittlung/?keywords=sch%C3%A4ferhund', ad: 'markt\\.de/.+/a/[0-9a-f]{6,}/', nextLink: true },
  // ---------- NACHBARLÄNDER (gestohlene Hunde werden oft ins Ausland verkauft) ----------
  // Österreich – willhaben (Haag → Grenze Salzburg/Braunau ca. 1 h)
  ...['husky', 'schäferhund husky', 'schäferhund mischling', 'husky mischling', 'rüde abzugeben', 'hund zugelaufen']
    .map(q => ({ site: 'willhaben.at', url: `https://www.willhaben.at/iad/kaufen-und-verkaufen/marktplatz?keyword=${enc(q)}&page={page}`, ad: 'willhaben\\.at/iad/kaufen-und-verkaufen/d/', intl: 'AT' })),
  // Tschechien – bazos.cz (ovčák = Schäfer, kříženec = Mischling)
  ...['husky', 'ovčák husky', 'kříženec ovčák', 'německý ovčák pes', 'nalezen pes']
    .map(q => ({ site: 'bazos.cz', url: `https://zvirata.bazos.cz/pes/{page}?hledat=${enc(q)}`, ad: 'bazos\\.cz/inzerat/\\d+', intl: 'CZ', bazos: true })),
  // Slowakei – bazos.sk
  ...['husky', 'ovčiak husky', 'kríženec ovčiak']
    .map(q => ({ site: 'bazos.sk', url: `https://zvierata.bazos.sk/pes/{page}?hledat=${enc(q)}`, ad: 'bazos\\.sk/inzerat/\\d+', intl: 'SK', bazos: true })),
  // Polen – olx.pl (owczarek = Schäfer, mieszaniec = Mischling)
  ...['owczarek husky', 'husky mieszaniec', 'owczarek mieszaniec', 'owczarek niemiecki pies']
    .map(q => ({ site: 'olx.pl', url: `https://www.olx.pl/zwierzeta/psy/q-${enc(dash(q))}/?page={page}`, ad: 'olx\\.pl/d/oferta/', intl: 'PL' })),
  // Niederlande – marktplaats (herder = Schäfer, kruising = Mischling)
  ...['husky herder', 'herder kruising', 'husky kruising']
    .map(q => ({ site: 'marktplaats.nl', url: `https://www.marktplaats.nl/q/${enc(q).replace(/%20/g, '+')}/p/{page}/`, ad: 'marktplaats\\.nl/v/dieren-en-toebehoren/honden', intl: 'NL' })),
  // Italien – subito (Südtirol/Brenner-Route)
  ...['husky pastore tedesco', 'incrocio husky pastore']
    .map(q => ({ site: 'subito.it', url: `https://www.subito.it/annunci-italia/vendita/animali/?q=${enc(q)}&o={page}`, ad: 'subito\\.it/animali/.+\\d{6,}\\.htm', intl: 'IT' })),
  // Ungarn – jofogas
  ...['husky', 'juhász husky', 'németjuhász keverék']
    .map(q => ({ site: 'jofogas.hu', url: `https://www.jofogas.hu/magyarorszag/kutya?q=${enc(q)}&o={page}`, ad: 'jofogas\\.hu/[a-z_]+/.+_\\d{6,}\\.htm', intl: 'HU' })),
];

// Tierheime / Fundtier-Seiten rund um Haag: ganze Seite nach Schäfer/Husky-Textblöcken durchsuchen
const WATCH = [
  'https://www.tierschutzverein-erding.de/tiervermittlung/hunde/',
  'https://www.tierschutzverein-rosenheim.de/index.php/tiere/vermisste-tiere/hunde',
  'https://www.tierschutzverein-rosenheim.de/index.php/tiere/fundtiere',
  'https://www.tierschutzverein-muenchen.de/tiervermittlung/hunde',
  'https://www.tierschutzverein-muenchen.de/fundtiere',
  'https://www.tierheim-ebersberg.de/',
  'https://www.tierschutzverein-muehldorf.de/',
  'https://www.tasso.net/Tierregister/Suchmeldungen',
];

const pageUrl = (tpl, n, src = {}) => {
  if (src.bazos) return tpl.replace('{page}', n === 1 ? '' : `${(n - 1) * 20}/`); // bazos: Offset 20/40/…
  if (n === 1) return tpl.replace(/[?&](page|o)=\{page\}/, '').replace('p/{page}/', '');
  return tpl.replace('{page}', n);
};

function parseCard(c, src) {
  const lines = c.text.split('\n').map(s => s.trim()).filter(Boolean);
  const junk = l => /^(top|premium|merken|neu|neuer preis|noch \d+ tage?|bewaren.*|reserviert|gestern|heute.*|\d[\d.,:\s-]*(€|eur|kč|zł)?|[\d.]+\s*(km)?)$/i.test(l) || /^\d{1,2}\.\d{1,2}\.\d{2,4}/.test(l);
  const clean = l => l.replace(/^Bewaren in Mijn Favorieten/i, '').trim();
  const cands = [c.title, ...lines].map(x => clean(x || '').split('\n')[0]).filter(l => l.length >= 10 && !junk(l));
  const title = (cands[0] || lines[0] || '').slice(0, 160);
  const date = (c.text.match(/\b(heute|gestern)\b(,?\s*\d{1,2}:\d{2})?|\b\d{1,2}\.\d{1,2}\.\d{2,4}\b/i) || [''])[0];
  const locm = c.text.match(src.intl === 'AT' || src.intl === 'CH' ? /\b(\d{4})\s+([A-ZÄÖÜ][\wäöüß.\- ]{2,40})/ : /\b(\d{5})\s+([A-ZÄÖÜ][\wäöüß.\- ]{2,40})/);
  const price = (c.text.match(/\d[\d.]*,?-?\s*€|VB|zu verschenken/i) || [''])[0];
  return {
    id: src.site + ':' + c.url.split('?')[0], source: src.site, url: c.url, title,
    desc: lines.filter(l => l !== title).join(' ').slice(0, 1200),
    img: c.img, loc: locm ? `${locm[1]} ${locm[2].trim()}` : '', plz: locm ? locm[1] : null,
    date, price, lost: !!src.lost, country: src.intl || 'DE',
  };
}

async function acceptCookies(p) {
  for (const t of ['Alle akzeptieren', 'Akzeptieren', 'Zustimmen', 'Alle zulassen', 'Einverstanden', 'Accept all']) {
    const b = p.getByRole('button', { name: t }).first();
    if (await b.count().catch(() => 0)) { await b.click({ timeout: 3000 }).catch(() => {}); return; }
  }
}

async function nextHref(p) {
  return p.evaluate(() => {
    const cand = [...document.querySelectorAll('a[rel=next], link[rel=next], a[href]')];
    const a = cand.find(a => a.rel === 'next') ||
      cand.find(a => /^(weiter|nächste( seite)?|›|»|>)$/i.test((a.innerText || a.getAttribute('aria-label') || '').trim())) ||
      cand.find(a => /nächste|next/i.test(a.getAttribute('aria-label') || ''));
    return a ? a.href : null;
  });
}

async function scrapeSource(ctx, src, log) {
  const p = await ctx.newPage();
  const out = [];
  const maxPages = Math.max(PAGES, src.pages || 0);
  let url = pageUrl(src.url, 1, src);
  const seenOnSource = new Set();
  for (let n = 1; n <= maxPages && url; n++) {
    try {
      await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });
      await p.waitForTimeout(2500 + Math.random() * 2000);
      if (n === 1) await acceptCookies(p);
      await p.mouse.wheel(0, 6000); await p.waitForTimeout(800);
      const cards = await p.evaluate(`(${extractCards.toString()})(${JSON.stringify(src.ad)})`);
      const fresh = cards.filter(c => !seenOnSource.has(c.url.split('?')[0]));
      fresh.forEach(c => seenOnSource.add(c.url.split('?')[0]));
      out.push(...fresh.map(c => parseCard(c, src)));
      log(`  ${src.site.padEnd(15)} S.${String(n).padStart(2)}: ${cards.length} Karten, ${fresh.length} neu  ${url.slice(0, 90)}`);
      if (fresh.length === 0) break; // Ende erreicht (nur noch TOP-Wiederholungen)
      url = src.nextLink ? await nextHref(p) : pageUrl(src.url, n + 1, src);
    } catch (e) { log(`  ! ${src.site} S.${n}: ${e.message.slice(0, 100)}`); break; }
  }
  await p.close();
  return out;
}

async function scrapeWatch(ctx, url, log) {
  const p = await ctx.newPage();
  try {
    await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });
    await p.waitForTimeout(4000); await acceptCookies(p);
    // Textblöcke mit Schäfer/Husky, samt nächstem Bild und Link
    const hits = await p.evaluate(() => {
      const re = /schäfer|schaefer|husky|wolfs?hund/i;
      const res = []; const used = new Set();
      for (const el of document.querySelectorAll('article, li, .card, .item, div, section, tr')) {
        const t = (el.innerText || '').trim();
        if (!re.test(t) || t.length > 1500 || t.length < 20 || used.has(t)) continue;
        if ([...el.children].some(ch => re.test(ch.innerText || '') && (ch.innerText || '').length > 20 && ch.innerText.length < 1500)) continue; // kleinstes Element nehmen
        used.add(t);
        const img = el.querySelector('img'); const a = el.querySelector('a[href]') || el.closest('a[href]');
        res.push({ text: t, img: img ? (img.currentSrc || img.src) : '', url: a ? a.href : location.href });
      }
      return res.slice(0, 40);
    });
    log(`  watch ${hits.length} Treffer  ${url}`);
    return hits.map(h => ({ ...parseCard({ ...h, url: h.url }, { site: 'tierheim/' + new URL(url).hostname }), id: 'watch:' + url + ':' + h.text.slice(0, 80), lost: true }));
  } catch (e) { log(`  ! watch ${url}: ${e.message.slice(0, 80)}`); return []; }
  finally { await p.close(); }
}

async function pool(items, n, fn) {
  const res = []; let i = 0;
  await Promise.all(Array.from({ length: n }, async () => { while (i < items.length) { const k = i++; res[k] = await fn(items[k]); } }));
  return res;
}

(async () => {
  const t0 = Date.now();
  const log = s => console.log(s);
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ locale: 'de-DE', userAgent: UA, viewport: { width: 1300, height: 2000 } });
  await ctx.route(/\.(woff2?|ttf|mp4|webm)(\?|$)|doubleclick|googlesyndication|adservice|criteo|taboola/, r => r.abort());
  const only = arg('--only', '');
  const lists = await pool(SOURCES.filter(s => !only || only.split(',').some(o => s.site.includes(o))), 4, s => scrapeSource(ctx, s, log));
  const watch = only ? [] : await pool(WATCH, 4, u => scrapeWatch(ctx, u, log));
  await browser.close();
  const all = {};
  if (only && fs.existsSync(OUT)) for (const a of JSON.parse(fs.readFileSync(OUT)).ads) all[a.id] = a;
  for (const a of [...lists.flat(), ...watch.flat()]) all[a.id] = all[a.id] && all[a.id].desc.length > a.desc.length ? all[a.id] : a;
  const arr = Object.values(all);
  fs.writeFileSync(OUT, JSON.stringify({ at: new Date().toISOString(), ads: arr }, null, 1));
  log(`→ JS-Scraper: ${arr.length} Anzeigen in ${((Date.now() - t0) / 1000).toFixed(0)}s → ${OUT}`);
})();
