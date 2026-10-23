// tools_merge (operator 2026-10-23): one ReviewMeta-style row with the price grade, from the
// same lookup as the review report (search.js price_check); otherwise a plain link to the check.
function mergePriceRow(report, data, asin) {
  var check = data && data.price_check, href = '/deal-spotter/?asin=' + encodeURIComponent(asin);
  var link = document.createElement('a');
  link.href = href;
  if (!check || !['A','B','C'].includes(check.grade) || typeof check.label !== 'string') {
    var line = document.createElement('p');
    line.className = 'price-check-link'; link.textContent = 'Check this price →'; line.append(link); report.append(line);
    return;
  }
  var pct = check.usual_pct, usual = Number.isSafeInteger(pct) && Math.abs(pct) <= 100 ?
    (pct < 0 ? -pct + '% below its usual price' : pct > 0 ? pct + '% above its usual price' : 'about its usual price') : check.label;
  var table = document.createElement('table'), body = document.createElement('tbody'), row = document.createElement('tr');
  var grade = document.createElement('td'), cell = document.createElement('td'), title = document.createElement('h3'), note = document.createElement('p');
  var caption = document.createElement('caption');
  table.className = 'checks-table price-check'; caption.className = 'sr-only'; caption.textContent = 'Price check';
  grade.className = 'price-grade'; grade.dataset.grade = check.grade; grade.textContent = check.grade;
  title.textContent = 'Price check: ' + check.grade + ' — ' + usual;
  note.textContent = 'From Fake Deal Spotter: today’s Amazon price, with shipping, against its 90-day average (its usual price) and our verified deals.';
  link.textContent = 'See the full price check →';
  cell.append(title, note, link); row.append(grade, cell); body.append(row); table.append(caption, body); report.append(table);
}
