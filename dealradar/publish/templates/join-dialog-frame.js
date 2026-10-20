function dialogPost(data) {parent.postMessage(data, location.origin);}
document.getElementById('dialog-close').addEventListener('click', () => dialogPost({joinDialog:'close'}));
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {e.preventDefault();dialogPost({joinDialog:'close'});}
  if (e.key !== 'Tab') return;
  const items = [...document.querySelectorAll('a[href],button,input,iframe,[tabindex="0"]')]
    .filter(el => !el.disabled && el.getClientRects().length);
  const first = items[0], last = items.at(-1);
  if (e.shiftKey && (document.activeElement === first || !items.includes(document.activeElement))) {
    e.preventDefault();last.focus();
  } else if (!e.shiftKey && (document.activeElement === last || !items.includes(document.activeElement))) {
    e.preventDefault();first.focus();
  }
});
new ResizeObserver(() => dialogPost({joinDialog:'height',height:document.querySelector('.joincard').offsetHeight})).observe(document.querySelector('.joincard'));
window.wpTrack = (name, params) => {if (typeof parent.wpTrack === 'function') parent.wpTrack(name, params);};
document.getElementById('join-email-choice').focus();
