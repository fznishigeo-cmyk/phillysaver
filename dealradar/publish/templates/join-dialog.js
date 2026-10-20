let active;
export function openJoin(button) {
  if (active) return;
  // Close any timed prompt before giving the header dialog focus.
  document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}));
  try {localStorage.setItem('wp.join.promptUntil', String(Date.now() + 7 * 864e5));} catch (_) { /* manual joins work without storage */ }
  const overlay = document.createElement('div');
  overlay.id = 'header-join-dialog';
  overlay.tabIndex = -1;
  overlay.setAttribute('role', 'dialog');
  overlay.setAttribute('aria-modal', 'true');
  overlay.setAttribute('aria-labelledby', 'header-join-title');
  overlay.style.cssText = 'position:fixed;inset:0;z-index:10000;display:grid;place-items:center;padding:16px;background:#0b0f14a6;overscroll-behavior:contain';
  overlay.innerHTML = '<h2 id="header-join-title" style="position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)">Join free</h2><iframe title="Join free sign-up"  style="display:block;border:0;border-radius:24px;width:420px;max-width:100%;height:376px;max-height:calc(100dvh - 32px);box-shadow:0 30px 80px #0006;color-scheme:light dark"></iframe>';
  const frame = overlay.querySelector('iframe');
  const originalOverflow = document.documentElement.style.overflow;
  const siblings = [...document.body.children].map(node => [node, node.inert]);
  siblings.forEach(([node]) => {node.inert = true;});
  document.documentElement.style.overflow = 'hidden';
  document.body.appendChild(overlay);
  active = overlay;
  function close() {
    if (active !== overlay) return;
    active = null;
    overlay.remove();
    document.documentElement.style.overflow = originalOverflow;
    siblings.forEach(([node, inert]) => {node.inert = inert;});
    window.removeEventListener('message', message);
    document.removeEventListener('keydown', key);
    document.removeEventListener('focusin', focus);
    button.focus({preventScroll:true});
  }
  function message(e) {
    if (e.source !== frame.contentWindow || e.origin !== location.origin) return;
    if (e.data.joinDialog === 'close') close();
    if (e.data.joinDialog === 'height') frame.style.height = e.data.height + 'px';
  }
  function key(e) {
    if (e.key === 'Escape') {e.preventDefault();close();}
    if (e.key === 'Tab') {e.preventDefault();overlay.focus();}
  }
  function focus(e) {if (!overlay.contains(e.target)) frame.focus();}
  window.addEventListener('message', message);
  document.addEventListener('keydown', key);
  document.addEventListener('focusin', focus);
  overlay.addEventListener('click', e => {if (e.target === overlay) close();});
  overlay.focus();
  fetch('/join-dialog.json' + new URL(import.meta.url).search).then(r => {
    if (!r.ok) throw Error('Join card unavailable');
    return r.json();
  }).then(data => {if (active === overlay) frame.srcdoc = data.html;})
    .catch(() => {if (active === overlay) {close();location.href = button.href;}});
}
