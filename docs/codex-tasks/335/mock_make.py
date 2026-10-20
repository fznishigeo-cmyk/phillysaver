"""Mockups for the more exciting tool pages (not production code)."""
import math
import re
from pathlib import Path

T = Path("/tmp/claude-0/-home-user-phillysaver/9d433223-0f31-5451-98af-0cdb161c5449/scratchpad/wt333/dealradar/publish/templates")
OUT = Path(__file__).parent
css = (T / "tool-pages.css").read_text()
hub = (T / "hub.html").read_text()
brand = re.search(r'<p class="brand">.*?</p>', hub)[0]

MOCK_CSS = """
.hero{display:grid;grid-template-columns:1.1fr .9fr;gap:56px;align-items:center;max-width:1200px;margin:0 auto;padding:60px 40px 48px}
.eyebrow{margin:0;font-size:14px;font-weight:800;letter-spacing:2.4px;text-transform:uppercase;color:var(--good)}
[data-tool=finder] .eyebrow{color:var(--link)}
.htitle{margin:14px 0 18px;font-size:62px;line-height:1.03;font-weight:850;letter-spacing:-2.2px}
.htitle span{display:block;color:var(--good)}
[data-tool=finder] .htitle span{color:var(--link)}
.sub{margin:0 0 28px;font-size:21px;color:var(--muted);max-width:540px}
.trust{display:flex;flex-wrap:wrap;gap:8px 22px;margin:18px 0 0;padding:0;list-style:none;font-size:15px;font-weight:650;color:var(--muted)}
.trust li::before{content:"✓ ";color:var(--good);font-weight:900}
.meter-card{position:relative;background:var(--card);border:1px solid var(--line);border-radius:26px;box-shadow:0 24px 60px #0f172a1f;padding:24px 26px 26px}
.example{position:absolute;top:18px;right:20px;border-radius:999px;padding:3px 11px;background:var(--tile);color:var(--muted);font-size:12px;font-weight:800;letter-spacing:1px}
.prod{display:flex;align-items:center;gap:14px;padding-right:80px}
.prod img{width:64px;height:64px;border-radius:14px;object-fit:cover;background:#fff;border:1px solid var(--line)}
.prod b{font-size:17px;line-height:1.3}
.gauge{display:block;width:100%;max-width:380px;margin:10px auto 0}
.gauge text{font-family:inherit}
.price-row{display:flex;align-items:baseline;justify-content:center;gap:12px;margin-top:2px}
.price-row s{color:var(--muted);font-size:17px}
.price-row strong{font-size:38px;letter-spacing:-1px}
.save{margin:2px 0 14px;text-align:center;color:var(--good);font-weight:800;font-size:17px}
.center{text-align:center}
.amz{display:block;margin:14px auto 0;width:max-content;border-radius:999px;padding:12px 26px;background:#ffc94d;color:#111;font-weight:800;text-decoration:none}
.cloud{position:relative;height:460px}
.tile{position:absolute;border-radius:22px;background:#fff;border:1px solid var(--line);box-shadow:0 16px 40px #0f172a24}
.tile img{display:block;width:100%;height:100%;border-radius:22px;object-fit:contain;padding:10px}
.tile i{position:absolute;top:-12px;left:-10px;border-radius:999px;padding:4px 11px;background:#ffb21e;color:#1a1200;font-style:normal;font-weight:850;font-size:15px;box-shadow:0 4px 10px #0003}
.cloudnote{margin:8px 0 0;text-align:center;font-size:14px;color:var(--muted);font-weight:650}
.faqpeek{max-width:1120px;margin:0 auto;padding:8px 40px 60px;opacity:.55}
.faqpeek h2{font-size:28px;margin:0 0 6px}
.modal-back{position:fixed;inset:0;background:#0b0f14a6;display:grid;place-items:center;z-index:9}
.modal{position:relative;width:420px;max-width:calc(100vw - 32px);background:var(--card);border-radius:24px;padding:30px 30px 24px;box-shadow:0 30px 80px #0006;text-align:center}
.modal .brand{display:flex;justify-content:center}
.modal h2{margin:8px 0 6px;font-size:28px;letter-spacing:-.6px}
.modal p{margin:0 0 20px;color:var(--muted)}
.modal .x{position:absolute;top:12px;right:14px;width:44px;height:44px;border:0;background:none;font-size:28px;color:var(--muted)}
.mbtn{display:flex;align-items:center;justify-content:center;gap:10px;width:100%;min-height:52px;margin:0 0 12px;border-radius:999px;font:inherit;font-size:17px;font-weight:750;border:1.5px solid var(--line);background:var(--card);color:var(--fg)}
.mbtn.email{background:#111;color:#fff;border-color:#111}
.g{font-weight:900;font-size:20px;color:#4285f4}
.fine{font-size:13px;color:var(--muted);margin-top:4px}
@media(max-width:899px){
 .hero{grid-template-columns:1fr;gap:28px;padding:28px 16px 24px}
 .htitle{font-size:42px;letter-spacing:-1.4px;margin:10px 0 12px}
 .sub{font-size:17px;margin-bottom:20px}
 .hero>div:last-child{overflow:hidden;padding-top:14px}
 .cloud{height:460px;width:520px;transform:scale(.66);transform-origin:top left;margin-bottom:-150px}
 .meter-card{padding:20px 18px 22px}
 .price-row strong{font-size:32px}
 .faqpeek{padding:8px 16px 40px}
}
"""


def gauge(pct):
    cx, cy, r = 170, 150, 112

    def pt(p, rad=r):
        a = math.radians(180 - min(p, 80) / 80 * 180)
        return cx + rad * math.cos(a), cy - rad * math.sin(a)

    def arc(p0, p1, color):
        (x0, y0), (x1, y1) = pt(p0), pt(p1)
        return (f'<path d="M{x0:.1f} {y0:.1f} A{r} {r} 0 0 1 {x1:.1f} {y1:.1f}" fill="none" '
                f'stroke="{color}" stroke-width="26"/>')
    nx, ny = pt(pct, r - 30)
    gx, gy = pt(27, r + 24)
    labels = ''.join(f'<text x="{x:.0f}" y="{y:.0f}" text-anchor="{a}" font-size="14" font-weight="750" fill="currentColor">{t}</text>'
                     for t, x, y, a in (("Live price", cx - r - 13, cy + 24, "start"), ("Good", gx, gy, "middle"),
                                        ("Big discount", cx + r + 13, cy + 24, "end")))
    return (f'<svg class="gauge" viewBox="0 0 340 226" role="img" aria-label="Deal meter: {pct}% off list price, big discount">'
            + arc(0, 15, "#e5484d") + arc(15.6, 39.4, "#f5b301") + arc(40, 80, "#18a957") + labels
            + f'<line x1="{cx}" y1="{cy}" x2="{nx:.1f}" y2="{ny:.1f}" stroke="currentColor" stroke-width="7" stroke-linecap="round"/>'
            + f'<circle cx="{cx}" cy="{cy}" r="11" fill="currentColor"/>'
            + f'<text x="{cx}" y="{cy + 66}" text-anchor="middle" font-size="30" font-weight="850" fill="currentColor">{pct}% off</text></svg>')


def search(button):
    return ('<form class="search-box" onsubmit="return false"><svg class="search-icon" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/></svg>'
            f'<input id="q" type="text" placeholder="Paste an Amazon link or type a product"><button id="tool-submit" type="submit">{button}</button></form>')


TILES = [("holder2", "-10%", 2, 40, 150, -4), ("vac1", "-38%", 38, 0, 176, 3), ("cables", "-50%", 74, 60, 140, 5),
         ("holder3", "-43%", 0, 270, 146, 4), ("vac2", "-25%", 37, 222, 186, -3), ("holder4", "-23%", 75, 262, 150, -5)]


def page(mode, modal=False):
    nav = ('<a href="#"' + (' aria-current="page"' if mode == "checker" else '') + '>Deal Checker</a>'
           '<a href="#"' + (' aria-current="page"' if mode == "finder" else '') + '>Discount Finder</a>')
    if mode == "checker":
        left = ('<p class="eyebrow">Amazon Deal Checker</p><h1 class="htitle">Is it really a deal?<span>Know in 10 seconds.</span></h1>'
                '<p class="sub">Paste any Amazon link or type a product. We check today\'s price against Amazon\'s list price and our verified deals.</p>'
                + search("Analyze") + '<ul class="trust"><li>Free</li><li>No sign-up</li><li>No extension</li></ul>')
        right = ('<div class="meter-card"><span class="example">EXAMPLE</span><div class="prod"><img src="img/holder1.jpg" alt=""><b>Magnetic car phone mount</b></div>'
                 + gauge(50) + '<div class="price-row"><s>$29.99 list</s><strong>$14.99</strong></div>'
                 '<p class="save">You save $15.00</p><div class="center"><span class="badge big">Big discount · 50% off list price</span></div>'
                 '<a class="amz" href="#">View at Amazon →</a></div>')
    else:
        left = ('<p class="eyebrow">Amazon Discount Finder</p><h1 class="htitle">Every Amazon discount.<span>One click.</span></h1>'
                '<p class="sub">Type what you want or paste a link. We open Amazon\'s discounts for it and show our verified deals.</p>'
                + search("Find discounts") + '<ul class="trust"><li>Free</li><li>No sign-up</li><li>Updated every hour</li></ul>')
        right = ('<div><div class="cloud">' + ''.join(
            f'<div class="tile" style="left:{x}%;top:{y}px;width:{s}px;height:{s}px;transform:rotate({rot}deg)"><i>{pct}</i><img src="img/{n}.jpg" alt=""></div>'
            for n, pct, x, y, s, rot in TILES) + '</div><p class="cloudnote">Today\'s top verified discounts · updated every hour</p></div>')
    modal_html = ('<div class="modal-back"><div class="modal"><button class="x" aria-label="Close">×</button>' + brand
                  + '<h2>Join free</h2><p>The best verified deals in your inbox. No spam.</p>'
                  '<button class="mbtn"><span class="g">G</span> Continue with Google</button>'
                  '<button class="mbtn email">✉ Continue with email</button><p class="fine">Free · unsubscribe anytime</p></div></div>') if modal else ''
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>mock</title><style>{css}{MOCK_CSS}</style></head><body data-tool="{mode}">'
            f'<header class="tool-header">{brand}<nav aria-label="Tools">{nav}<a href="#">Today\'s Deals</a></nav><a class="join" href="#">Join free</a></header>'
            f'<section class="hero"><div>{left}</div>{right}</section>'
            '<section class="faqpeek"><h2>How it works</h2><p><b>Is it free?</b><br>Yes. Both tools are free to use.</p></section>'
            + modal_html + '</body></html>')


for mode in ("checker", "finder"):
    (OUT / f"{mode}.html").write_text(page(mode))
(OUT / "join.html").write_text(page("checker", modal=True))
print("written")
