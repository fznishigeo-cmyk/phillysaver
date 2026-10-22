// Operator 2026-10-22: a typed product name never spends a lookup (no /api/search
// call). It opens Amazon's highly rated list for those words; a link or ASIN gets the grade.
var nameHint = 'For an A, B or C grade, paste the product’s Amazon link.';
function highlyRatedUrl(words) {
  // The only place the sort/filter lives, so Amazon's "4 Stars & Up" filter can replace it.
  return 'https://www.amazon.com/s?k=' + encodeURIComponent(words) + '&s=review-rank&tag=' + encodeURIComponent(toolSettings.tag);
}
// Fills the panel and returns its button, or null (no button) for adult or unusable words.
function namePanel(panel, raw) {
  var words = analyzeAdult(raw) ? '' : toolQuery(raw);
  panel.replaceChildren(); panel.hidden = !words;
  if (!words) return null;
  var button = document.createElement('a'), arrow = document.createElement('span'), hint = document.createElement('p');
  button.className = 'name-button'; button.href = highlyRatedUrl(words); button.target = '_blank'; button.rel = 'sponsored noopener';
  arrow.textContent = ' →'; arrow.setAttribute('aria-hidden', 'true');
  button.append('See highly rated ' + words + ' on Amazon', arrow);
  hint.id = panel.id + '-hint'; hint.className = 'name-hint'; hint.textContent = nameHint;
  button.setAttribute('aria-describedby', hint.id);
  panel.append(button, hint);
  return button;
}
// Long placeholders where they fit; the short one where the long one would be cut off.
function namePlaceholders() {
  var fields = document.querySelectorAll('[data-short-placeholder]'), canvas;
  fields.forEach(field => {
    field.dataset.longPlaceholder = field.dataset.longPlaceholder || field.placeholder;
    var style = getComputedStyle(field), room = field.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
    canvas = canvas || document.createElement('canvas').getContext('2d');
    canvas.font = style.font;
    field.placeholder = room > 0 && canvas.measureText(field.dataset.longPlaceholder).width <= room ?
      field.dataset.longPlaceholder : field.dataset.shortPlaceholder;
  });
}
