"use strict";
// Only creates extension UI. No host-page content or attributes are read.
Wowpriced.card = function (doc, model, dismiss, hide) {
  const box = doc.createElement("section");
  box.setAttribute("aria-label", "wowpriced price check");
  const style = doc.createElement("style");
  style.textContent = `:host{all:initial;color-scheme:light dark}*{box-sizing:border-box}section{font:15px/1.5 system-ui,sans-serif;color:#172b32;background:#fff;border:1px solid #b9cec9;border-radius:18px;padding:18px;box-shadow:0 8px 36px #062e2926;overflow-wrap:anywhere}header{display:flex;align-items:center;gap:8px;font-weight:800;font-size:18px}svg{width:28px;height:28px;transform:rotate(-8deg)}p{margin:12px 0}.answer{font-size:19px;font-weight:750;line-height:1.35}.muted{color:#4e6268;font-size:13px}a{display:block;text-align:center;background:#0e7569;color:white;text-decoration:none;border-radius:10px;padding:12px;font-weight:700;min-height:44px}button{font:inherit;cursor:pointer;border:0;background:transparent;color:inherit;min-height:44px;padding:8px}.close{margin-left:auto;min-width:44px;font-size:22px}.hide{width:100%;font-size:13px;text-decoration:underline}a:focus-visible,button:focus-visible{outline:3px solid #b36b00;outline-offset:3px}@media(prefers-color-scheme:dark){section{background:#14232b;color:#eaf3f4;border-color:#405962}.muted{color:#b4c7cd}a{background:#60ddca;color:#073b35}}`;
  box.appendChild(style);
  const header = doc.createElement("header");
  const svg = doc.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 32 32"); svg.setAttribute("aria-hidden", "true");
  for (const [tag, attrs] of [["rect", {x:1,y:1,width:30,height:30,rx:8,fill:"#129486"}], ["polyline", {points:"6.5,11 11,22.5 16,14 21,22.5 25.5,12.5",fill:"none",stroke:"#fff","stroke-width":3.6,"stroke-linecap":"round","stroke-linejoin":"round"}], ["circle", {cx:25.5,cy:6.3,r:2.4,fill:"#ffc94d"}]]) {
    const child = doc.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attrs)) child.setAttribute(key, value);
    svg.appendChild(child);
  }
  header.appendChild(svg);
  const brand = doc.createElement("span"); brand.textContent = "wowpriced"; header.appendChild(brand);
  function button(text, label, action, cls) {
    const el = doc.createElement("button"); el.type = "button"; el.textContent = text;
    el.setAttribute("aria-label", label); el.className = cls; el.addEventListener("click", action); return el;
  }
  if (dismiss) header.appendChild(button("×", "Dismiss price check", dismiss, "close"));
  box.appendChild(header);
  function paragraph(text, cls) {
    if (!text) return;
    const el = doc.createElement("p"); el.textContent = text; el.className = cls; box.appendChild(el); return el;
  }
  paragraph(model.item, "item");
  if (model.verdict) paragraph("Is this a good deal?", "muted");
  paragraph(model.verdict, "answer");
  paragraph(model.detail, "price");
  if (model.at) {
    const checked = paragraph("checked " + Wowpriced.relativeTime(model.at), "muted");
    checked.title = new Date(model.at).toISOString();
  }
  const link = doc.createElement("a"); link.href = model.href; link.target = "_blank";
  link.rel = "noopener noreferrer"; link.textContent = "Price history on wowpriced"; box.appendChild(link);
  if (hide) box.appendChild(button("Hide on this site", "Hide on this site", hide, "hide"));
  return box;
};
