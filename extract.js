// Generic card extractor, runs in page context. For each ad link, climbs to the largest
// ancestor that still contains only that one ad -> card text + image.
module.exports = function extractCards(adPattern) {
  const re = new RegExp(adPattern);
  const norm = h => h.split('#')[0].split('?')[0];
  const links = [...document.querySelectorAll('a[href]')].filter(a => re.test(a.href));
  const seen = new Map();
  for (const a of links) {
    const key = norm(a.href);
    let el = a;
    while (el.parentElement && el.parentElement !== document.body) {
      const inside = new Set([...el.parentElement.querySelectorAll('a[href]')]
        .filter(x => re.test(x.href)).map(x => norm(x.href)));
      if (inside.size > 1) break;
      el = el.parentElement;
    }
    const text = (el.innerText || '').replace(/\s+\n/g, '\n').trim();
    const img = [...el.querySelectorAll('img')].map(i => i.currentSrc || i.src || i.dataset.src || '')
      .find(s => s && !s.startsWith('data:') && !/logo|icon|sprite|placeholder/i.test(s)) || '';
    const title = [...el.querySelectorAll('a[href]')].filter(x => re.test(x.href))
      .map(x => (x.innerText || x.title || '').trim()).sort((x, y) => y.length - x.length)[0] || '';
    const prev = seen.get(key);
    if (!prev || text.length > prev.text.length) seen.set(key, { url: a.href, text: text.slice(0, 1500), img, title: title.split('\n')[0] });
  }
  return [...seen.values()];
};
