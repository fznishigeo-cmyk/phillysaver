// Pure snapshot checks. No browser, network, credentials or persisted prices.
const QUERY = 'zzqx cupping test';
// The Amazon exit for typed words; since 342 the yellow "See Amazon discounts →".
const SEARCH_BUTTON = /^\W*(?:Search (?:all of )?Amazon(?:\s+for\b.*)?|Compare all\b.*\bprices on Amazon\b.*|See Amazon discounts\b.*)$/i;
const NAMES = ['top_rotation', 'price_mix', 'impulse', 'photos', 'errors', 'freshness', 'search', 'dark',
  'photos_deep', 'optical', 'working', 'text', 'links'];
const BLOCK = /googletagmanager|google-analytics|googleadservices|doubleclick|googlesyndication|facebook\.net|facebook\.com\/tr|analytics\.tiktok|pinimg\.com\/ct|bat\.bing|clarity\.ms/;

function rulesFromConfig(config) {
  const health = config.quality?.visitor_watch || {};
  return {rotationEnabled: Number(config.site?.drop_rotation_windows || 0) > 0,
    utilityGroups: config.publish?.front_page?.impulse?.utility_groups || {},
    amazonTag: String(config.affiliate?.amazon_tag || ''),
    imageHosts: Array.isArray(health.image_hosts) ? health.image_hosts : [],
    categoryPhotos: health.category_photos === true, dealPhotos: health.deal_photos === true,
    excludeUsedOnAll: health.exclude_used_on_all === true};
}

const DURATION = String.raw`90(?:[-\u2010-\u2015]|\s+)days?`;
const HISTORY = new RegExp(String.raw`\b(?:price history|lowest we|highest we|` +
  DURATION + String.raw`(?=\s+(?:history\b|price\s+(?:history|average|median|lowest|highest)\b|` +
  String.raw`(?:average|median|lowest|highest)\b))|` +
  String.raw`(?:(?:lowest|highest|average|median)\s+price|price\s+(?:average|median|history))` +
  String.raw`\s+(?:in|over|for)\s+(?:the\s+)?(?:(?:last|past)\s+)?` + DURATION + String.raw`)\b`, 'gi');
function textFindings(text) {
  return {invalid: [...new Set(String(text).match(/\$NaN|\bundefined\b|\bnull\b|\[object/gi) || [])],
    history: [...new Set(String(text).match(HISTORY) || [])]};
}

function dealLinkProblem(card, rules) {
  if (card.ended === true && !card.href) return '';
  try {
    const url = new URL(card.href);
    if (!/^https:\/\/[^/\s]/i.test(card.href) || url.protocol !== 'https:' || url.username || url.password || url.port)
      return 'invalid HTTPS URL';
    const host = url.hostname.replace(/\.$/, '');
    const own = new URL(rules.site || 'https://wowpriced.com/').hostname.replace(/\.$/, '');
    if (host === own || host.endsWith('.' + own) || host === 'wowpriced.com' || host.endsWith('.wowpriced.com'))
      return 'Get deal points to our own domain';
    // Store labels also cover credited posts and affiliate wrappers. Only a
    // destination on Amazon itself must carry our configured tag.
    if (/(^|\.)amazon\.[a-z]{2,3}(\.[a-z]{2})?$/.test(host))
      return !rules.amazonTag || url.searchParams.getAll('tag').length !== 1 || url.searchParams.get('tag') !== rules.amazonTag ?
        'Amazon tag incorrect' : '';
    return '';
  } catch {return 'invalid HTTPS URL';}
}

function opticalProblems(optical) {
  if (!optical || optical.error) return [optical?.error || 'layout capture unavailable'];
  const problems = [], boxes = optical.boxes || [];
  if (optical.overflow > 1) problems.push('horizontal overflow');
  if (!boxes.length && !optical.emptyAllowed) problems.push('no visible card boxes');
  const heights = boxes.map(box => box.height).filter(height => height > 0).sort((a, b) => a - b);
  const median = heights.length % 2 ? heights[(heights.length - 1) / 2] :
    (heights[heights.length / 2 - 1] + heights[heights.length / 2]) / 2;
  for (let i = 0; i < boxes.length; i++) {
    const a = boxes[i];
    if (a.height > median * 2.5) problems.push('stretched card ' + a.key);
    for (const b of boxes.slice(i + 1)) if (Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 &&
      Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1) problems.push('overlapping cards ' + a.key + '/' + b.key);
  }
  let end = -1, start = -1;
  for (const band of [...(optical.bands || [])].sort((a, b) => a.top - b.top)) {
    if (band.top > end) start = band.top;
    end = Math.max(end, band.bottom);
    if (end - start >= 400) {problems.push('blank content band >=400px'); break;}
  }
  return problems;
}

const normalize = text => String(text).normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
function utilityMatch(title, groups) {
  const text = ' ' + normalize(title) + ' ';
  const matches = phrase => text.includes(' ' + normalize(phrase) + ' ');
  return Object.values(groups).some(group => {
    const phrases = Array.isArray(group) ? group : group.phrases || group.include || group.patterns || [];
    const excluded = Array.isArray(group) ? [] : group.exclude || group.exclusions || [];
    return phrases.some(matches) && !excluded.some(matches);
  });
}

function searchProblems(search, tag) {
  const problems = [];
  if (!/search/i.test(search?.placeholder || '')) problems.push('search placeholder missing');
  if (!search?.visible || !SEARCH_BUTTON.test(search.label || '')) problems.push('Search Amazon button missing');
  if (search?.target !== '_blank') problems.push('target is not _blank');
  try {
    const url = new URL(search.clickedHref);
    if (url.protocol !== 'https:' || !['amazon.com', 'www.amazon.com'].includes(url.hostname) ||
        url.pathname !== '/s' || url.searchParams.get('k') !== QUERY || !tag ||
        url.searchParams.getAll('tag').length !== 1 || url.searchParams.get('tag') !== tag)
      problems.push('clicked Amazon search URL/query/tag incorrect');
  } catch { problems.push('no search URL on click'); }
  if (search?.error) problems.push(search.error);
  return problems;
}

function sameTopSix(scene, previous) {
  return previous?.cards?.length >= 6 && scene.cards?.length >= 6 &&
    scene.cards.slice(0, 6).every((card, i) => (card.key || card.title + '|' + card.store) ===
      (previous.cards[i].key || previous.cards[i].title + '|' + previous.cards[i].store));
}

function checksForScene(scene, previous, rules) {
  const cards = (scene.cards || []).slice(0, 24), checks = [];
  const add = (name, flag, detail = '') => checks.push({name, scene: scene.name, flag: Boolean(flag), detail});
  const sameTop = sameTopSix(scene, previous);
  const deadline = previous?.pendingDropAt || previous?.nextDropAt;
  const crossed = Number.isFinite(Date.parse(deadline)) &&
    Date.parse(scene.takenAt) >= Date.parse(deadline) &&
    Date.parse(scene.takenAt) > Date.parse(previous.takenAt);
  add('top_rotation', sameTop && crossed && rules.rotationEnabled && previous.rotationEnabled,
    !previous ? 'report only: previous snapshot unavailable' : sameTop && crossed ? (rules.rotationEnabled && previous.rotationEnabled ? 'first 6 unchanged after drop mark' :
      'report only: first 6 unchanged; task 269 not enabled for both snapshots') : 'no unchanged top across a drop mark');
  const valid = cards.filter(card => typeof card.price === 'number' && Number.isFinite(card.price) && card.price >= 0);
  const cheap = valid.filter(card => card.price <= 50).length, expensive = valid.filter(card => card.price > 100).length;
  add('price_mix', cards.length < 24 || valid.length !== cards.length || cheap < 19 || expensive > 1,
    `${cheap}/24 at most $50 (goal >=19); ${expensive}/24 over $100 (goal <=1); ${valid.length} known prices`);
  const utility = cards.filter(card => utilityMatch(card.title, rules.utilityGroups)).map(card => card.title);
  add('impulse', utility.length, utility.length ? utility.join('; ') :
    Object.keys(rules.utilityGroups).length ? 'no utility titles' : 'report only: task 267 utility_groups not present');
  const missing = cards.filter(card => !card.hasPhoto).map(card => card.title);
  add('photos', cards.length < 24 || missing.length, missing.length ? missing.join('; ') : `${cards.length}/24 loaded visible photos`);
  const errors = [...(scene.errors || [])];
  if (!/^\d+:[0-5]\d$/.test(scene.countdown || '')) errors.push('missing/invalid drop countdown');
  add('errors', errors.length, errors.join('; '));
  const age = (Date.parse(scene.takenAt) - Date.parse(scene.publishedAt)) / 60000;
  add('freshness', !Number.isFinite(age) || age < -5 || age > 40,
    Number.isFinite(age) ? `publish age ${age.toFixed(1)} minutes (limit 40)` : 'publish time unavailable');
  const search = searchProblems(scene.search, rules.amazonTag);
  add('search', search.length, search.join('; ') || `placeholder "${scene.search?.placeholder}", button, _blank and clicked tagged search URL`);
  add('dark', scene.scheme === 'dark' && (!Number.isFinite(scene.glare) || scene.glare > .01),
    scene.scheme === 'dark' ? `near-white pixel share ${Number.isFinite(scene.glare) ? (100 * scene.glare).toFixed(2) + '%' : 'unavailable'} (limit 1%)` : 'light mode');
  const deep = scene.photosDeep, photoProblems = [];
  if (!deep?.home?.length) photoProblems.push('home photo capture unavailable');
  const placeholders = (deep?.home || []).filter(card => card.photo === 'placeholder').length;
  const broken = (deep?.home || []).filter(card => card.photo === 'broken').length;
  if (placeholders || broken) photoProblems.push(`home: ${placeholders} store-tile placeholders; ${broken} broken/hidden photos`);
  for (const [page, enabled] of [['category', rules.categoryPhotos], ['deal', rules.dealPhotos]]) {
    if (enabled && (!deep?.[page]?.length || deep[page].some(card => card.photo !== 'loaded')))
      photoProblems.push(page + ': missing/broken photo');
  }
  add('photos_deep', photoProblems.length, photoProblems.join('; ') ||
    `${deep?.home?.length || 0} home photos loaded; category/deal gates ${Boolean(rules.categoryPhotos)}/${Boolean(rules.dealPhotos)}`);
  const layout = opticalProblems(scene.optical);
  for (const sample of scene.pageOptical || []) layout.push(...opticalProblems(sample).map(problem => sample.page + ': ' + problem));
  add('optical', layout.length, layout.join('; ') || 'no overflow, overlaps, stretched cards or blank bands');
  const working = scene.working, workProblems = [];
  const department = working?.department;
  const expected = new Set(department?.expected || []);
  if (department?.active !== true || !expected.size || department.after?.length !== expected.size ||
      new Set(department.after).size !== expected.size || department.after.some(key => !expected.has(key)) ||
      !department.allowed?.length || department.categories?.length !== department.after.length ||
      department.categories.some(category => !department.allowed.includes(category))) workProblems.push('department does not show its eligible published cards');
  if (!working?.inventorySearch?.query || !(working?.inventorySearch?.matches > 0))
    workProblems.push('inventory search has no matching results');
  if (working?.deal?.status !== 200 || !working.deal.price || !working.deal.getDeal) workProblems.push('deal page needs HTTP 200, price and Get deal');
  if (working?.deal?.linkProblem) workProblems.push('deal page: ' + working.deal.linkProblem);
  for (const card of cards) {
    const problem = dealLinkProblem(card, rules);
    if (problem) workProblems.push(card.title + ': ' + problem);
  }
  if (working?.error) workProblems.push(working.error);
  add('working', workProblems.length, workProblems.join('; ') || 'department, inventory search, deal page and Get deal URLs work');
  const text = scene.text, textProblems = [];
  if (!text) textProblems.push('visible text capture unavailable');
  if (text?.invalid?.length) textProblems.push('invalid card text: ' + text.invalid.join(', '));
  if (text?.history?.length) textProblems.push('history wording: ' + text.history.join(', '));
  if (rules.excludeUsedOnAll && text?.used?.length) textProblems.push('used/open_box on All: ' + text.used.join(', '));
  add('text', textProblems.length, textProblems.join('; ') || 'visible text clean' + (rules.excludeUsedOnAll ? '; All excludes used/open_box' : '; used-on-All gate off'));
  const links = scene.links;
  const badLinks = (links || []).filter(link => !(link.status >= 200 && link.status < 400));
  add('links', !Array.isArray(links) || badLinks.length, !Array.isArray(links) ? 'internal link capture unavailable' :
    badLinks.map(link => `${link.path}: ${link.status || link.error || 'unavailable'}`).join('; ') || `${links.length} internal links answer 2xx/3xx (run cap 20)`);
  return checks;
}

function assess(snapshot, previous, rules) {
  const prior = previous?.schema === 1 && Array.isArray(previous.scenes) && Array.isArray(previous.checks) &&
    Date.parse(previous.takenAt) < Date.parse(snapshot.takenAt) ? previous : null;
  snapshot.checks = snapshot.scenes.flatMap(scene => {
    const old = prior?.scenes?.find(old => old.name === scene.name);
    // New publishes must not keep moving the pending mark for unchanged cards.
    // A changed top six or a disabled rotation starts a fresh deadline.
    scene.pendingDropAt = sameTopSix(scene, old) && rules.rotationEnabled && old.rotationEnabled ?
      old.pendingDropAt || old.nextDropAt : scene.nextDropAt;
    return checksForScene(scene, old, rules);
  });
  const priorFlags = new Set((prior?.checks || []).filter(check => check.flag).map(check => check.scene + ':' + check.name));
  snapshot.repeated = snapshot.checks.filter(check => check.flag && priorFlags.has(check.scene + ':' + check.name))
    .map(check => check.scene + ':' + check.name);
  return snapshot.repeated.length ? 1 : 0;
}

function health(snapshot) {
  const flags = [...new Set(snapshot.checks.filter(check => check.flag).map(check => check.name))];
  return {schema: 1, takenAt: snapshot.takenAt, status: flags.length ? 'flags' : 'ok', count: flags.length,
    flags, repeated: snapshot.repeated};
}

function summary(snapshot) {
  const clean = value => String(value).replace(/[\r\n]+/g, ' ').replace(/[<>`]/g, '');
  const rows = NAMES.map(name => {
    const checks = snapshot.checks.filter(check => check.name === name), flags = checks.filter(check => check.flag);
    return {flag: flags.length > 0, line: `${name}: ${flags.length ? 'FLAG ' : 'ok '}` +
      (flags.length ? flags : checks).map(check => `${check.scene}: ${clean(check.detail)}`).join(' | ')};
  });
  rows.sort((a, b) => Number(b.flag) - Number(a.flag));
  const status = health(snapshot);
  return (status.count ? `health: ${status.count} flags (${status.flags.join(', ')})` : 'health: ok') +
    '\n\n### Visitor watch\n\n' + rows.map(row => row.line.trimEnd()).join('\n\n') +
    '\n\n' + (snapshot.repeated.length ? 'Repeated flags: ' + snapshot.repeated.join(', ') :
      'No repeated flags; one-off flags are report only.') + '\n';
}

module.exports = {QUERY, SEARCH_BUTTON, NAMES, BLOCK, rulesFromConfig, utilityMatch, searchProblems, checksForScene, assess, summary,
  textFindings, dealLinkProblem, opticalProblems, health};
