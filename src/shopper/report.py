"""Render a CompareResult as chat text (Telegram/CLI) or as an HTML comparison table."""
from __future__ import annotations

from html import escape

from .adapters import Adapter
from .config import Settings
from .models import Proposal, now_utc
from .learner import confidence_label
from .memory import offer_key
from .service import CompareResult


def _per(unit: str) -> str:
    return {"ml": "L", "g": "kg"}.get(unit, "pc")


def tip_line(settings: Settings, r) -> str | None:
    t = r.tip
    if not t:
        return None
    u = _per(t.offer.size.unit)
    return (f"{t.offer.name} at {label(settings, t.offer.platform)} is {t.unit_price:,.1f}/{u} "
            f"vs {t.baseline:,.1f}/{u} (−{t.saving_pct:.0f}%)")


def snapshot_ages(result: CompareResult, older_than_min: int = 30) -> dict[str, str]:
    """Platforms whose offers were fetched well before this comparison (catalogue snapshots)."""
    out = {}
    for r in result.items:
        for p, offers in r.candidates.items():
            if offers and (result.created_at - offers[0].fetched_at).total_seconds() > older_than_min * 60:
                out[p] = offers[0].fetched_at.astimezone().strftime("%d %b %H:%M")
    return out


def age_notes(result: CompareResult, settings: Settings) -> list[str]:
    """Where prices are older than this comparison: catalogue snapshots, and prices typed in from apps."""
    out = []
    for p, t in snapshot_ages(result).items():
        cfg = settings.platforms.get(p)
        if cfg and cfg.status == "app":
            out.append(f"{label(settings, p)} prices are the ones you typed in from the app (latest {t}).")
        else:
            out.append(f"{label(settings, p)} prices are from its catalogue snapshot of {t}.")
    return out


def _egp(x: float) -> str:
    return f"EGP {x:,.2f}"


def label(settings: Settings, name: str) -> str:
    p = settings.platforms.get(name)
    return p.label if p else name


def cart_links(result: CompareResult, prop: Proposal, adapters: dict[str, Adapter]) -> dict[str, str]:
    out = {}
    for platform in prop.platforms:
        lines = [(m.offer, m.request.qty) for m in prop.assignments.values() if m.offer.platform == platform]
        link = adapters[platform].cart_link(lines) if platform in adapters else None
        if link:
            out[platform] = link
    return out


def _no_plan(result: CompareResult) -> str:
    if any(m for r in result.items for m in r.matches.values()):
        return ("No plan meets the shops' minimum orders. The prices are below; add items or allow more "
                "orders to get a plan.")
    return "No shop had an acceptable match for this list."


def _plan_lines(result: CompareResult, prop: Proposal, settings: Settings) -> list[str]:
    lines = []
    for platform in prop.platforms:
        lines.append(f"{label(settings, platform)} — {_egp(prop.subtotals[platform])} + fees {_egp(prop.fees[platform])}")
        for i, m in sorted(prop.assignments.items()):
            if m.offer.platform != platform:
                continue
            tag = "" if m.exact else " [close match]"
            if result.items[i].needs_pick:
                tag += " [choose your product]"
            lines.append(f"  • {m.request.qty} × {m.offer.name} @ {m.offer.price:,.2f}{tag}\n    {m.offer.url}")
    return lines


def to_text(result: CompareResult, settings: Settings, adapters: dict[str, Adapter], table_url: str | None = None) -> str:
    """Plain text for the CLI. Telegram uses ``to_chat``."""
    n = len(result.items)
    out = [f"{n} item{'s' if n != 1 else ''} · {settings.area_name} · searched {', '.join(label(settings, p) for p in result.platforms)}"]
    best = result.best
    if not best:
        out.append(_no_plan(result))
    else:
        names = " + ".join(label(settings, p) for p in best.platforms)
        out.append(f"\nBEST PLAN: {names} — {_egp(best.total)}")
        pending = [r.request.query for r in result.items if r.needs_pick]
        if pending:
            out.append(f"Not final: several products fit {len(pending)} line(s). Choose yours in the Full table: "
                       + "; ".join(pending))
        out += _plan_lines(result, best, settings)
        for platform, link in cart_links(result, best, adapters).items():
            out.append(f"  Cart link ({label(settings, platform)}): {link}")
        single = result.single
        if single and single is not best:
            diff = single.total - best.total
            out.append(f"\nBest single platform: {label(settings, single.platforms[0])} — {_egp(single.total)} "
                       f"({'+' if diff >= 0 else ''}{diff:,.2f} vs best plan)")
        if best.missing:
            out.append("\nNot found: " + "; ".join(result.items[i].request.query for i in best.missing))
        tips = [(r, tip_line(settings, r)) for r in result.items if r.tip]
        if tips:
            out.append("\nBetter-value packs:")
            out += [f"  • {r.request.query}: {line}\n    {r.tip.offer.url}" for r, line in tips]
    if result.unverified_fees:
        out.append("\nFees not verified (counted as 0): " + ", ".join(label(settings, p) for p in result.unverified_fees))
    for p, err in result.errors.items():
        out.append(f"Problem with {label(settings, p)}: {err}")
    out.append(f"Prices fetched {result.created_at.astimezone().strftime('%H:%M')}. Check the total in the app before paying.")
    out += age_notes(result, settings)
    if table_url:
        out.append(f"\nFull table: {table_url}")
    return "\n".join(out)


# Design: a shop's shelf edge. Prices sit on shelf labels, the cheapest exact one on the
# yellow "best price" label that supermarkets use; everything else stays quiet basil and white.
FONTS = ("<link rel=preconnect href='https://fonts.googleapis.com'><link rel=preconnect href='https://fonts.gstatic.com' crossorigin>"
         "<link rel=stylesheet href='https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700"
         "&family=Poppins:wght@500;600;700&display=swap'>")

def to_chat(result: CompareResult, settings: Settings, adapters: dict[str, Adapter], table_url: str | None = None) -> str:
    """The result for Telegram, in its HTML subset: product names are links, no bare URLs.
    Each line is complete on its own, so splitting a long message at line breaks is safe."""
    e = escape
    best = result.best
    n = len(result.items)
    if not best:
        out = [e(_no_plan(result))]
    else:
        orders = "1 order" if len(best.platforms) == 1 else f"{len(best.platforms)} orders"
        out = [f"<b>Best plan: {_egp(best.total)}</b>, {orders}"]
        pending = [r.request.query for r in result.items if r.needs_pick]
        if pending:
            out.append(f"<b>Not final.</b> Choose the product for {e('; '.join(pending))} with the buttons below.")
        for platform in best.platforms:
            out.append(f"\n<b>{e(label(settings, platform))}</b>  {_egp(best.subtotals[platform] + best.fees[platform])}"
                       f" (fees {best.fees[platform]:,.2f})")
            for i, m in sorted(best.assignments.items()):
                if m.offer.platform != platform:
                    continue
                note = "" if m.exact else " <i>close match</i>"
                if result.items[i].needs_pick:
                    note += " <i>choose below</i>"
                out.append(f"• {m.request.qty} × <a href=\"{e(m.offer.url)}\">{e(m.offer.name)}</a> — {m.line_total:,.2f}{note}")
        single = result.single
        if single and single is not best:
            diff = single.total - best.total
            out.append(f"\nOne shop only: {e(label(settings, single.platforms[0]))} {_egp(single.total)} "
                       f"({'+' if diff >= 0 else ''}{diff:,.2f})")
        if best.missing:
            out.append("Not found anywhere: " + e("; ".join(result.items[i].request.query for i in best.missing)))
        for r in result.items:
            if r.tip:
                out.append(f"Better value: <a href=\"{e(r.tip.offer.url)}\">{e(tip_line(settings, r))}</a>")
    if result.unverified_fees:
        out.append("\nFees not counted yet: " + e(", ".join(label(settings, p) for p in result.unverified_fees)))
    for p, err in result.errors.items():
        out.append(f"Problem with {e(label(settings, p))}: {e(err)}")
    out += [e(x) for x in age_notes(result, settings) if "typed in" in x]
    when = result.created_at.astimezone().strftime("%H:%M")
    out.append(f"Prices from {when}, {len(result.platforms)} shops, {n} item{'s' if n != 1 else ''}. "
               "Check the total in the app before you pay.")
    if table_url:
        out.append(f'<a href="{e(table_url)}">Open the full table</a>')
    return "\n".join(out)


CSS = """
:root{--page:#F8FAFC;--card:#FFFFFF;--ink:#0F172A;--muted:#64748B;--line:#E2E8F0;--primary:#16A34A;--primary-hover:#15803D;
--on-primary:#fff;--soft:#DCFCE7;--accent:#F59E0B;--on-accent:#1C1917;--accent-soft:#FEF3C7;--danger:#DC2626;--danger-bg:#FEE2E2;
--focus:#2563EB;--shadow:0 1px 2px rgba(15,23,42,.06),0 1px 3px rgba(15,23,42,.08);
--lift:0 8px 20px -6px rgba(23,23,23,.35);--lift-hover:0 14px 28px -8px rgba(23,23,23,.45);
--ui:'Inter',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;--head:'Poppins','Inter',system-ui,sans-serif;--num:'Poppins','Inter',system-ui,sans-serif}
@media (prefers-color-scheme:dark){:root{--page:#0B1220;--card:#111827;--ink:#E2E8F0;--muted:#94A3B8;--line:#1F2937;
--primary:#22C55E;--primary-hover:#16A34A;--on-primary:#052E16;--soft:#14301F;--accent:#FBBF24;--accent-soft:#3A2A06;
--danger:#F87171;--danger-bg:#3B1515;--shadow:0 1px 2px rgba(0,0,0,.4);--lift:0 8px 20px -6px rgba(0,0,0,.6);--lift-hover:0 14px 28px -8px rgba(0,0,0,.7)}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--page);color:var(--ink);font:16px/1.5 var(--ui)}
a{color:inherit;text-underline-offset:2px}:focus-visible{outline:3px solid var(--focus);outline-offset:2px}
.top{background:var(--card);border-bottom:1px solid var(--line)}.top .in{max-width:1152px;margin:0 auto;padding:28px 16px 40px}
.top h1{font-family:var(--head);font-size:clamp(1.875rem,3.6vw,2.75rem);line-height:1.1;font-weight:700;margin:0;letter-spacing:-.03em;max-width:22ch;text-wrap:balance}
.top p{margin:10px 0 0;color:var(--muted);font-size:16px;max-width:58ch}.top a{color:inherit}
main{max-width:1152px;margin:0 auto;padding:0 16px 64px}
h2{font-family:var(--head);font-size:24px;font-weight:700;margin:40px 0 14px;letter-spacing:-.02em;text-wrap:balance}h3{font-family:var(--head);font-size:17px;font-weight:600;margin:0}
@media (min-width:640px){h2{font-size:28px}}
.muted{color:var(--muted)}.small{font-size:13px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px;box-shadow:var(--shadow)}
.plan{margin-top:-20px;border-radius:24px;box-shadow:0 18px 40px -16px rgba(23,23,23,.25)}
.plan .head{display:flex;flex-wrap:wrap;align-items:center;gap:12px 16px}.plan .head h2{margin:0;flex:1 1 auto}
.tag{display:inline-flex;align-items:baseline;gap:4px;background:var(--accent-soft);color:var(--ink);font-family:var(--num);
font-weight:700;border-radius:10px;padding:4px 12px;line-height:1.1;font-variant-numeric:tabular-nums}
.tag small{font-size:.5em;font-weight:600}.tag.big{font-size:40px;padding:4px 14px}
.bag{border-top:1px solid var(--line);margin-top:14px;padding-top:12px}
.bag .row{display:flex;justify-content:space-between;gap:12px;align-items:baseline}
.bag ul{list-style:none;margin:8px 0 0;padding:0}.bag li{display:flex;justify-content:space-between;gap:12px;padding:5px 0;font-size:15px}
.bag li>span:last-child,.num{font-family:var(--num);font-size:18px;font-weight:600;white-space:nowrap;font-variant-numeric:tabular-nums}
.alt{margin-top:12px;font-size:14px}
.notice{border-left:4px solid var(--danger);background:var(--danger-bg);border-radius:4px 12px 12px 4px;padding:12px 14px;margin:18px 0 0}
.notice b{color:var(--danger)}
.shops{list-style:none;padding:0;margin:0;display:grid;gap:8px}
.shops li{display:grid;grid-template-columns:minmax(110px,1.2fr) 3fr auto;gap:12px;align-items:center;font-size:14px}
.bar{height:10px;border-radius:5px;background:var(--soft);overflow:hidden}.bar i{display:block;height:100%;background:var(--primary)}
.shops .best .bar i{background:var(--accent)}.shops .partial .bar i{background:repeating-linear-gradient(135deg,var(--muted) 0 4px,transparent 4px 8px);opacity:.5}
.line{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px;margin:14px 0;box-shadow:var(--shadow)}
.line>header{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 12px;margin-bottom:12px}
.qty{font-family:var(--num);font-weight:700;font-size:20px;color:var(--primary)}
.state{font-size:13px}.state.pick{color:var(--danger);font-weight:700}
.ask{font-size:13px;display:inline-flex;gap:6px;align-items:center;flex-wrap:wrap}
button.yes,button.no{padding:4px 14px;min-height:32px;font-size:13px;border-radius:12px;box-shadow:none}button.no{background:var(--danger-bg);color:var(--danger)}
button.yes:hover,button.no:hover{transform:none;box-shadow:none}button.no:hover{background:var(--danger);color:#fff}
.conf{font-size:12px;border-radius:999px;padding:1px 8px;background:var(--soft)}.conf.low{background:var(--danger-bg);color:var(--danger)}
.conf.high{background:var(--primary);color:var(--on-primary)}
.labels{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px}
.label{border:1px solid var(--line);border-radius:14px;padding:10px 12px 12px;background:var(--card)}
.label.best{background:var(--soft);border:2px solid var(--primary)}.label.best .amount{color:var(--primary)}
.label.close{border-style:dashed}
.label .shop{font-size:12px;font-weight:700}.label .amount{font-family:var(--num);font-size:30px;font-weight:700;line-height:1.05}
.label .product{display:block;font-size:13px;line-height:1.3;margin-top:4px}
.flag{font-size:12px;margin-top:4px}.flag.close{color:var(--danger)}
.missing{font-size:13px;margin:10px 0 0}
img.thumb,.thumb.nophoto{display:block;width:100%;height:84px;object-fit:contain;background:#fff;border-radius:10px;margin:0 0 8px}
.thumb.nophoto{background:repeating-linear-gradient(135deg,var(--soft) 0 6px,transparent 6px 12px);opacity:.6}
img.mini{width:36px;height:36px;object-fit:contain;background:#fff;border-radius:4px;flex:none}
.withimg{display:flex;gap:10px;align-items:center;min-width:0}.withimg>span{min-width:0;overflow-wrap:anywhere}
.bag li>span:first-child{min-width:0;overflow-wrap:anywhere}main a{overflow-wrap:anywhere}
.choices{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(230px,1fr);gap:10px;overflow-x:auto;padding-bottom:6px;scroll-snap-type:x mandatory}
.choice{scroll-snap-align:start;border:1px solid var(--line);border-radius:12px;padding:14px;background:var(--card);display:flex;flex-direction:column;gap:6px}
.choice ul{margin:0;padding-left:18px;font-size:13px}.choice .push{margin-top:auto}
details.choose{margin:10px 0 0}details.choose summary{cursor:pointer;font-size:14px;color:var(--muted)}
form.inline{display:inline;margin:0}
button{font:inherit;font-size:16px;font-weight:600;border:2px solid transparent;border-radius:12px;min-height:48px;padding:10px 26px;background:var(--primary);color:var(--on-primary);cursor:pointer;box-shadow:var(--lift);transition:transform .2s ease-out,box-shadow .2s ease-out,background-color .2s}
button:hover{background:var(--primary-hover);transform:translateY(-2px);box-shadow:var(--lift-hover)}button:active{transform:none}
@media (prefers-reduced-motion:reduce){button,button:hover{transform:none;transition:none}}
button.link{background:none;color:inherit;padding:2px 0;min-height:0;border:0;box-shadow:none;font-weight:500;font-size:13px;text-decoration:underline;opacity:.75}
button.link:hover{transform:none;box-shadow:none;background:none;opacity:1}
.saving{margin:8px 0 0;padding:8px 12px;border-radius:10px;background:var(--soft);font-size:15px}
.actionbar{display:none}
@media (max-width:720px){main{padding-bottom:84px}.actionbar{display:flex;gap:10px;position:fixed;left:0;right:0;bottom:0;
padding:10px 16px calc(10px + env(safe-area-inset-bottom));background:var(--card);border-top:1px solid var(--line);z-index:5}
.actionbar a{flex:1;text-align:center;padding:13px;border-radius:12px;border:2px solid var(--primary);color:var(--primary);font-weight:600;text-decoration:none}
.actionbar a.go{background:var(--primary);color:var(--on-primary);box-shadow:var(--lift)}}
.notes{margin-top:28px;font-size:13px}.notes p{margin:4px 0}.warn{color:var(--danger)}
textarea{width:100%;min-height:180px;font:inherit;padding:14px;border-radius:14px;border:2px dashed var(--line);background:var(--card);color:var(--ink)}
textarea:focus{border-color:var(--primary);outline:none}
.itemrow{display:grid;grid-template-columns:2fr 1.3fr 1.2fr 1.5fr .8fr .8fr;gap:8px;margin-bottom:10px;padding:10px;
border:1px solid var(--line);border-radius:12px;background:var(--page)}
.itemrow label{display:flex;flex-direction:column;gap:3px;font-size:12px;font-weight:600;color:var(--muted);min-width:0}
.itemrow label b{color:var(--danger)}
.itemrow input,.itemrow select,input[type=text]{font:inherit;font-size:15px;font-weight:400;min-width:0;width:100%;min-height:44px;padding:9px 12px;
border-radius:12px;border:1px solid var(--line);background:var(--card);color:var(--ink)}
.itemrow input:focus,.itemrow select:focus{border-color:var(--primary);outline:2px solid var(--soft)}
.sizebox{display:grid;grid-template-columns:1fr 70px;gap:4px}
.itemrow .preview{grid-column:1/-1;margin:0;font-size:13px;color:var(--muted)}.itemrow .preview b{color:var(--ink)}
@media (max-width:620px){.itemrow{grid-template-columns:1fr 1fr}.itemrow label:first-child{grid-column:1/-1}}
.fields{display:flex;flex-wrap:wrap;gap:6px;margin:-2px 0 10px;font-size:13px}
.fields span{background:var(--card);border:1px solid var(--line);border-radius:999px;padding:2px 10px}
.fields i{font-style:normal;color:var(--muted);margin-right:4px}.fields .open{opacity:.6}
.guide{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;margin:8px 0;font-size:14px}.guide b{white-space:nowrap}
select{font:inherit;max-width:100%;min-height:44px;padding:8px 12px;border-radius:12px;border:1px solid var(--line);background:var(--card);color:var(--ink)}
input[type=number]{font:inherit;width:4em;padding:4px 6px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink)}
code{font-size:.9em;background:var(--soft);padding:1px 5px;border-radius:4px}
.actions{display:flex;flex-wrap:wrap;gap:10px;margin-top:14px}button.quiet{background:transparent;color:var(--primary);border-color:var(--primary);box-shadow:none}
button.quiet:hover{background:var(--primary);color:var(--on-primary);box-shadow:none}
.chips{display:flex;flex-wrap:wrap;gap:8px}.chip{background:var(--card);color:var(--ink);border:1px solid var(--line);box-shadow:none;
border-radius:12px;min-height:36px;padding:6px 14px;font-weight:500;font-size:14px}.chip:hover{background:var(--soft);color:var(--ink);box-shadow:none}.chip:disabled{opacity:.45}
ul.list{list-style:none;margin:0;padding:0}ul.list li{display:flex;justify-content:space-between;align-items:baseline;gap:12px;
padding:8px 0;border-bottom:1px solid var(--line)}ul.list li:last-child{border-bottom:0}
@media (max-width:520px){.top h1{font-size:24px}.tag.big{font-size:34px}.shops li{grid-template-columns:1fr auto}.shops .bar{grid-column:1/-1;order:3}}
@media (prefers-reduced-motion:no-preference){.label.best{animation:pop .5s ease-out 1}@keyframes pop{from{transform:scale(.96)}to{transform:none}}}
/* quantity stepper: − n + */
.stepper{display:inline-flex;align-items:center;border:1px solid var(--line);border-radius:12px;background:var(--card);overflow:hidden}
.stepper button{min-height:40px;min-width:44px;padding:0;border:0;border-radius:0;box-shadow:none;background:transparent;color:var(--primary);font-size:22px;line-height:1}
.stepper button:hover{background:var(--soft);transform:none;box-shadow:none;color:var(--primary)}
.stepper input{width:3.2em;min-height:40px;border:0;border-inline:1px solid var(--line);text-align:center;font:600 16px var(--num);background:transparent;color:var(--ink)}
.stepper+.qty{display:none}
.itemrow .stepper{display:grid;grid-template-columns:44px 1fr 44px;width:100%}
.itemrow .stepper input{border:0;border-inline:1px solid var(--line);border-radius:0;min-height:42px;padding:0;outline:0}
.listline{display:flex;gap:14px;align-items:flex-start;min-width:0}.listline>span{min-width:0;overflow-wrap:anywhere;padding-top:8px}
.line>header .link{margin-left:auto}
/* order limit: 1 shop / up to 2 / up to 3 */
.segment{display:flex;flex-wrap:wrap;gap:8px;margin:-8px 0 28px;position:relative;z-index:1}
.segment form{flex:1 1 150px}.segment button{width:100%;display:flex;flex-direction:column;align-items:flex-start;gap:2px;
background:var(--card);color:var(--ink);border:2px solid var(--line);box-shadow:var(--shadow);padding:10px 16px;text-align:left}
.segment button small{font-weight:500;color:var(--muted);font-size:13px}.segment button:hover{background:var(--soft);color:var(--ink)}
.segment button.on{border-color:var(--primary);background:var(--soft)}.segment button.on small{color:var(--primary)}
/* step bar: where you are in add, review, compare, order */
.steps{display:flex;flex-wrap:wrap;gap:6px 18px;list-style:none;margin:18px 0 0;padding:0;font-size:14px;color:var(--muted)}
.steps li{display:flex;align-items:center;gap:8px}.steps b{display:inline-grid;place-items:center;width:26px;height:26px;border-radius:999px;
border:2px solid var(--line);font-size:13px;font-family:var(--head)}.steps .on{color:var(--ink);font-weight:600}
.steps .on b{background:var(--primary);border-color:var(--primary);color:var(--on-primary)}.steps .done b{border-color:var(--primary);color:var(--primary)}
.eyebrow{display:inline-block;font-size:13px;font-weight:600;color:var(--primary);background:var(--soft);border-radius:999px;padding:3px 12px;margin-bottom:12px}
"""


STEPS = ("Add items", "Review", "Compare", "Order")


def steps_html(current: int) -> str:
    """The step bar: 1 Add items, 2 Review, 3 Compare, 4 Order; ``current`` counts from 1."""
    return "<ol class=steps aria-label=Steps>" + "".join(
        f"<li class='{'on' if k == current else 'done' if k < current else ''}'"
        f"{' aria-current=step' if k == current else ''}><b>{k}</b>{name}</li>"
        for k, name in enumerate(STEPS, 1)) + "</ol>"


def page(title: str, body: str, header: str = "") -> str:
    """``header``: inner HTML of the basil top band (title and summary)."""
    top = f"<div class=top><div class=in>{header}</div></div>" if header else ""
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{escape(title)}</title>{FONTS}<style>{CSS}</style></head><body>{top}<main>{body}</main></body></html>")


def _button(action: str | None, q: str, text: str, fields: dict, cls: str = "link") -> str:
    if not action:
        return ""
    hidden = "".join(f"<input type=hidden name={k} value='{escape(str(v))}'>" for k, v in fields.items())
    return f"<form method=post action='{escape(action)}{q}' class=inline>{hidden}<button class={cls}>{escape(text)}</button></form>"


def _unit(o) -> str:
    return f"{o.unit_price:,.1f}/{_per(o.size.unit)}" if o.unit_price else ""


def _tag(amount: float, big: bool = False) -> str:
    return f"<span class='tag{' big' if big else ''}'><small>EGP</small>{amount:,.2f}</span>"


def _img(o, cls: str = "thumb") -> str:
    """The product photo, loaded from the shop; nothing when the shop gave none."""
    if not getattr(o, "image", ""):
        return ""
    return (f"<img class={cls} src='{escape(o.image)}' alt='' loading=lazy decoding=async referrerpolicy=no-referrer "
            "onerror=\"this.remove()\">")


def _link(o, cls: str = "") -> str:
    return f"<a class='{cls}' href='{escape(o.url)}' target='_blank' rel='noopener'>{escape(o.name)}</a>"


def _plan_html(result: CompareResult, prop: Proposal, settings: Settings, adapters: dict[str, Adapter]) -> str:
    shops = " and ".join(label(settings, p) for p in prop.platforms)
    split = "one order" if len(prop.platforms) == 1 else f"{len(prop.platforms)} orders"
    bags = []
    for platform in prop.platforms:
        items = "".join(
            f"<li><span class=withimg>{_img(m.offer, 'mini')}<span>{m.request.qty} × {_link(m.offer)}"
            f"{'' if m.exact else ' <span class=warn>close match</span>'}</span></span>"
            f"<span>{m.line_total:,.2f}</span></li>"
            for _, m in sorted(prop.assignments.items()) if m.offer.platform == platform)
        fee = prop.fees[platform]
        bags.append(f"<div class=bag><div class=row><h3>{escape(label(settings, platform))}</h3>"
                    f"<span class=num>{prop.subtotals[platform] + fee:,.2f}</span></div>"
                    f"<div class='muted small'>items {prop.subtotals[platform]:,.2f}, fees {fee:,.2f}</div><ul>{items}</ul></div>")
    links = "".join(f"<p class=small><a href='{escape(u)}' target='_blank' rel='noopener'>Open the {escape(label(settings, p))} cart</a></p>"
                    for p, u in cart_links(result, prop, adapters).items())
    missing = ("<p class='warn small'>Not found anywhere: " + escape("; ".join(result.items[i].request.query for i in prop.missing))
               + "</p>") if prop.missing else ""
    single = result.single
    saving = ""
    if single and single is not prop and single.total > prop.total and not single.missing:
        saving = (f"<p class=saving>You save <b>{_egp(single.total - prop.total)}</b> against buying everything at "
                  f"{escape(label(settings, single.platforms[0]))} ({_egp(single.total)}).</p>")
    return (f"<div class=head><h2 id=plan>Best plan</h2>{_tag(prop.total, big=True)}</div>"
            f"<p class='muted small'>{escape(shops)}, {split}</p>{saving}{''.join(bags)}{links}{missing}<!--cart-push-->")


def _one_shop_html(result: CompareResult, settings: Settings) -> str:
    """What the whole basket costs at each shop on its own: the honest comparison."""
    singles = sorted((p for p in result.proposals if len(p.platforms) == 1), key=lambda p: (len(p.missing), p.total))
    if len(singles) < 2:
        return ""
    top = max(p.total for p in singles) or 1
    n = len(result.items)
    rows = []
    for k, p in enumerate(singles):
        have = n - len(p.missing)
        note = "all items" if not p.missing else f"{have} of {n} items"
        rows.append(f"<li class='{'best' if k == 0 else 'partial' if p.missing else ''}'><span><b>{escape(label(settings, p.platforms[0]))}</b>"
                    f"<br><span class='muted small'>{note}</span></span><span class=bar><i style='width:{p.total / top * 100:.0f}%'></i></span>"
                    f"<span class=num>{p.total:,.2f}</span></li>")
    return f"<h2>If you buy everything in one shop</h2><div class=panel><ul class=shops>{''.join(rows)}</ul></div>"


def _cards_html(result: CompareResult, settings: Settings, i: int, base: str | None, q: str) -> str:
    r = result.items[i]
    cards = []
    for j, c in enumerate(r.cards):
        best = c.cheapest()
        listings = "".join(f"<li>{escape(label(settings, p))}: {_link(o)}, {o.price:,.2f}</li>" for p, o in c.per_platform().items())
        tag = "" if c.exact else "<span class='warn small'>other size or wording</span>"
        pic = next((o for o in c.offers if getattr(o, "image", "")), None)
        cards.append(f"<div class=choice>{_img(pic) if pic else ''}<h3>{escape(c.label)}</h3>{tag}<div>{_tag(best.price)} <span class='muted small'>{_unit(best)}</span></div>"
                     f"<ul>{listings}</ul><div class=push>{_button(base and base + '/pick', q, 'This one', {'item': i, 'card': c.key}, 'pick')}</div></div>")
    return f"<div class=choices>{''.join(cards)}</div>"


def fields_html(query: str) -> str:
    """What shopper understood from the line, field by field; an open field reads "any"."""
    from .entry import read_entry
    e = read_entry(query)
    parts = [("Item", e.item), ("Brand", e.brand or "any"), ("Type", e.type or "any"), ("Size", e.size or "any")]
    if e.pieces > 1:
        parts.append(("Pack", f"{e.pieces} pieces"))
    return "<p class=fields>" + "".join(
        f"<span class='{'open' if v == 'any' else ''}'><i>{k}</i> {escape(v)}</span>" for k, v in parts) + "</p>"


def _line_html(result: CompareResult, settings: Settings, i: int, base: str | None, q: str) -> str:
    r = result.items[i]
    found = [(p, m) for p, m in r.matches.items() if m]
    exact = [m for _, m in found if m.exact]
    cheapest = min(exact or [m for _, m in found], key=lambda m: m.offer.price, default=None)
    found.sort(key=lambda pm: (not pm[1].exact, pm[1].offer.price))
    labels = []
    for p, m in found:
        o = m.offer
        cls = "best" if m is cheapest else "" if m.exact else "close"
        flag = ("your pick" if m.pinned else "" if m.exact else "close match")
        was = f"was {o.regular_price:,.2f}" if o.on_deal else ""
        flags = ", ".join(x for x in (flag, was) if x)
        no = "" if m.pinned else _button(base and base + "/reject", q, "not this",
                                         {"item": i, "platform": p, "offer": offer_key(o)})
        pic = _img(o) or "<div class='thumb nophoto'></div>"
        labels.append(f"<li class='label {cls}'>{pic}<div class=shop>{escape(label(settings, p))}</div>"
                      f"<div class=amount>{o.price:,.2f}</div><div class='muted small'>{_unit(o)}</div>{_link(o, 'product')}"
                      f"<div class='flag {'close' if not m.exact else ''}'>{escape(flags)}</div>{no}</li>")
    missing = [label(settings, p) for p in result.platforms if not r.matches.get(p)]
    state = ""
    plan = result.best.assignments.get(i) if result.best else None
    if r.pinned:
        state = "<span class='state muted'>your product</span>" + _button(base and base + "/forget", q, "change", {"item": i})
    elif r.needs_pick:
        state = f"<a class='state pick' href='#pick-{i}'>choose your product</a>"
    elif plan and base:
        f = {"item": i, "platform": plan.offer.platform, "offer": offer_key(plan.offer)}
        conf = f" <span class='conf {confidence_label(r.confidence)}'>match {confidence_label(r.confidence)}</span>" if r.confidence is not None else ""
        state = (f"<span class=ask>{conf} Right product? {_button(base + '/confirm', q, 'Yes', f, 'yes')}"
                 f"{_button(base + '/deny', q, 'No', f, 'no')}</span>")
    body = f"<ul class=labels>{''.join(labels)}</ul>" if labels else "<p class=muted>No shop had this item.</p>"
    miss = f"<p class='missing muted'>Not found at {escape(', '.join(missing))}</p>" if labels and missing else ""
    choose = ""
    if r.cards and not r.pinned:
        cards = _cards_html(result, settings, i, base, q)
        choose = (f"<div id=pick-{i}><h3 class=small style='margin:16px 0 8px'>Which one do you mean?</h3>{cards}</div>"
                  if r.needs_pick else f"<details class=choose id=pick-{i}><summary>Wrong product? Choose</summary>{cards}</details>")
    key = {"item": i, "line": r.request.query}
    qty = stepper(base and base + "/qty", q, r.request.qty, key, r.request.query)
    drop = _button(base and base + "/remove", q, "Remove line", key) if len(result.items) > 1 else ""
    return (f"<section class=line id=line-{i}><header>{qty}<h3>{escape(r.request.query)}</h3>{state}{drop}</header>"
            f"{fields_html(r.request.query)}{body}{miss}{choose}</section>")


def branch_note(a) -> str:
    """Which branch's prices a shop with branches gave, and why."""
    area, branch = getattr(a, "area", None), getattr(a, "branch", None)
    if area and branch:
        return f"{a.label} prices are for its {branch} branch, which serves {area} (your area, set on the home page)."
    if area:
        return (f"{a.label} lists {area} without a branch, so it may not deliver there. "
                f"Prices are from its default store. Choose a nearby area on the home page to compare a branch.")
    if branch:
        return (f"{a.label} prices are for its {branch} branch, found from AREA_LAT/AREA_LON. "
                f"If that is not yours, choose your area on the home page.")
    return ""


def stepper(action: str | None, q: str, qty: int, fields: dict, label_text: str) -> str:
    """− qty + as one small form. Without ``action`` (a page from history) only the number shows."""
    if not action:
        return f"<span class=qty>{qty}×</span>"
    hidden = "".join(f"<input type=hidden name={k} value='{escape(str(v))}'>" for k, v in fields.items())
    return (f"<form method=post action='{escape(action)}{q}' class=stepper>{hidden}"
            f"<button name=delta value=-1 aria-label='One less {escape(label_text)}'>−</button>"
            f"<input name=qty value={qty} inputmode=numeric aria-label='How many {escape(label_text)}' "
            f"onchange='this.form.submit()'><button name=delta value=1 aria-label='One more {escape(label_text)}'>+</button>"
            f"</form><span class=qty>{qty}×</span>")


def _orders_html(result: CompareResult, settings: Settings, options: dict | None, base: str | None, q: str) -> str:
    """1 shop · up to 2 · up to 3, each with its best total; the current limit is pressed."""
    if not options or not base:
        return ""
    now = result.max_orders or settings.max_orders
    parts = []
    for k, prop in options.items():
        name = "1 shop" if k == 1 else f"Up to {k} shops"
        total = _egp(prop.total) if prop else "no plan"
        miss = f", {len(prop.missing)} missing" if prop and prop.missing else ""
        on = " aria-pressed=true class=on" if k == now else " aria-pressed=false"
        parts.append(f"<form method=post action='{escape(base)}/orders{q}' class=inline><input type=hidden name=limit value={k}>"
                     f"<button{on}><span>{name}</span><small>{escape(total + miss)}</small></button></form>")
    return f"<div class=segment role=group aria-label='How many orders'>{''.join(parts)}</div>"


def to_html(result: CompareResult, settings: Settings, adapters: dict[str, Adapter],
            base: str | None = None, token: str | None = None, learned: dict[str, int] | None = None,
            options: dict | None = None, undo: bool = False) -> str:
    """``base`` (like "/r/<id>") turns on the pick / not this / change buttons, quantities and order limits."""
    q = f"?t={escape(token)}" if token else ""
    n = len(result.items)
    when = result.created_at.astimezone().strftime("%d %b, %H:%M")
    header = (f"<span class=eyebrow>{len(result.platforms)} shops searched</span><h1>Your plan</h1>"
              f"<p>{n} item{'s' if n != 1 else ''} for {escape(settings.area_name)}, prices from {when}. "
              f"Check the total in the app before you pay.</p>{steps_html(3)}")
    pending = [r for r in result.items if r.needs_pick]
    plan = (_plan_html(result, result.best, settings, adapters) if result.best
            else f"<h2>No plan yet</h2><p class=muted>{escape(_no_plan(result))}</p>")
    notice = (f"<p class=notice><b>Not final.</b> Several products fit {len(pending)} line{'s' if len(pending) != 1 else ''}. "
              "Choose yours below; shopper remembers it for next time.</p>") if pending else ""
    tips = [(r, tip_line(settings, r)) for r in result.items if r.tip]
    tips_html = ("<h2>Better-value packs</h2><div class=panel><ul>" + "".join(
        f"<li>{escape(r.request.query)}: <a href='{escape(r.tip.offer.url)}' target='_blank' rel='noopener'>{escape(line)}</a></li>"
        for r, line in tips) + "</ul></div>") if tips else ""
    lines = "".join(_line_html(result, settings, i, base, q) for i in range(n))
    notes = []
    if result.unverified_fees:
        notes.append("Fees not verified, counted as 0: " + ", ".join(label(settings, p) for p in result.unverified_fees))
    notes += [f"{label(settings, p)}: {e}" for p, e in result.errors.items()]
    notes += age_notes(result, settings)
    notes += [branch_note(a) for p, a in adapters.items() if p in result.platforms and branch_note(a)]
    if learned:
        notes.append(f"Learned so far: {learned.get('answers', 0)} answer{'s' if learned.get('answers', 0) != 1 else ''}, "
                     f"{learned['lines']} chosen product{'s' if learned['lines'] != 1 else ''}, "
                     f"{learned['rejects']} rejected listing{'s' if learned['rejects'] != 1 else ''}, "
                     f"{learned['avoid']} avoided word{'s' if learned['avoid'] != 1 else ''}.")
    notes_html = "<div class='notes muted'>" + "".join(f"<p>{escape(x)}</p>" for x in notes) + "</div>" if notes else ""
    first_pick = next((i for i, r in enumerate(result.items) if r.needs_pick), None)
    bar = ("<nav class=actionbar>"
           + (f"<a class=go href='#pick-{first_pick}'>Choose products</a>" if first_pick is not None else
              "<a class=go href='#plan'>Best plan</a>")
           + f"<a href='/{q}'>New list</a></nav>") if base else ""
    undo_html = (f"<p class=notice><b>Line removed.</b> {_button(base + '/undo', q, 'Undo', {}, 'link')}</p>"
                 if undo and base else "")
    old = (now_utc() - result.created_at).total_seconds() > 30 * 60
    refresh = (f"<p class='notice'><b>Prices are over 30 minutes old.</b> "
               f"{_button(base + '/refresh', q, 'Refresh prices', {}, 'link')}</p>") if old and base else ""
    body = (f"{_orders_html(result, settings, options, base, q)}<div class='panel plan'>{plan}</div>{undo_html}{refresh}{notice}"
            f"{_one_shop_html(result, settings)}{tips_html}"
            f"<h2>{'Choose your products and compare' if pending else 'Every price, item by item'}</h2>{lines}{notes_html}{bar}")
    return page("Basket comparison", body, header)


def debug_text(result: CompareResult, settings: Settings, top: int = 3) -> str:
    """Per platform: how many offers came back and how the best few scored."""
    from .matching import ACCEPT_SCORE, score

    out = []
    for r in result.items:
        out.append(f"\n## {r.request.qty} × {r.request.query}")
        for p in result.platforms:
            offers = r.candidates.get(p)
            if offers is None:
                out.append(f"  {label(settings, p)}: no response ({result.errors.get(p, 'not searched')})")
                continue
            ranked = sorted((score(r.request, o) for o in offers), key=lambda m: -m.score)[:top]
            out.append(f"  {label(settings, p)}: {len(offers)} offers")
            out += [f"     {m.score:5.1f} {'EXACT' if m.exact else 'close' if m.score >= ACCEPT_SCORE else 'below cut-off'}"
                    f" {m.offer.price:>9,.2f}  {m.offer.name}" for m in ranked]
    return "\n".join(out)
