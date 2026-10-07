#!/usr/bin/env python3
"""Score used/refurbished desktop listings for a local-AI home server.

The agent collects listings (Njuškalo, eBay, Amazon, ...) into a JSON file,
then this script does the deterministic part: currency normalisation,
shipping + import costs, a 0-100 value-for-money score, hard rules
(laptops, no SSD, unclear condition, ...), dedup against previous runs,
and a daily report ready for Telegram/Slack or a database.

Usage:
    python score_listings.py score --input listings.json [--format text|json|both]
                                   [--state PATH | --no-state] [--rates rates.json]
    python score_listings.py schema       # print the expected listing fields

Stdlib only. Unknown values must be null -- the scorer never guesses.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from datetime import date
from pathlib import Path
from typing import Any

# ── Market assumptions (EUR, used EU market) ────────────────────────────────
# Rough used prices; override per run with --rates {"gpu_fair": {...}}.
# Ordered most-specific first: the first regex that matches wins.
GPU_TABLE: list[tuple[str, str, int, int]] = [
    # (regex, label, default_vram_gb, fair_eur)
    (r"rtx\s*a4000", "RTX A4000", 16, 450),
    (r"rtx\s*a2000", "RTX A2000", 12, 300),
    (r"tesla\s*p40", "Tesla P40", 24, 250),
    (r"3090", "RTX 3090", 24, 650),
    (r"4090", "RTX 4090", 24, 1500),
    (r"4080", "RTX 4080", 16, 800),
    (r"4070\s*ti\s*super", "RTX 4070 Ti Super", 16, 650),
    (r"4070\s*super", "RTX 4070 Super", 12, 490),
    (r"4070\s*ti", "RTX 4070 Ti", 12, 560),
    (r"4070", "RTX 4070", 12, 430),
    (r"5060\s*ti", "RTX 5060 Ti", 16, 420),
    (r"4060\s*ti", "RTX 4060 Ti", 8, 280),
    (r"4060", "RTX 4060", 8, 230),
    (r"3080\s*ti", "RTX 3080 Ti", 12, 420),
    (r"3080", "RTX 3080", 10, 330),
    (r"3070", "RTX 3070", 8, 240),
    (r"3060\s*ti", "RTX 3060 Ti", 8, 200),
    (r"3060", "RTX 3060", 12, 210),
    (r"2080\s*ti", "RTX 2080 Ti", 11, 260),
    (r"2060", "RTX 2060", 6, 120),
    (r"7900\s*xtx", "RX 7900 XTX", 24, 750),
    (r"7800\s*xt", "RX 7800 XT", 16, 400),
    (r"7600\s*xt", "RX 7600 XT", 16, 260),
    (r"6800", "RX 6800", 16, 300),
    (r"6700\s*xt", "RX 6700 XT", 12, 230),
    (r"a770", "Arc A770", 16, 200),
]
# VRAM-specific overrides for cards sold in two memory sizes.
GPU_VRAM_FAIR = {("RTX 4060 Ti", 16): 370, ("RTX 3080", 12): 360, ("RTX 2060", 12): 160}

DEFAULT_RATES = {"EUR": 1.0, "USD": 0.87, "GBP": 1.16, "CHF": 1.06, "PLN": 0.235, "HUF": 0.0025}
CROATIA_VAT = 0.25
IMPORT_HANDLING_EUR = 15.0  # courier customs clearance fee, typical
NON_EU = {"uk", "non_eu", "us", "cn"}

NO_GPU_RE = re.compile(r"^(none|nema|no\s*gpu|integrated|igpu|intel\s*(uhd|hd|iris)|onboard)", re.I)
LAPTOP_RE = re.compile(r"\b(laptop|notebook|prijenosn)", re.I)
RGB_RE = re.compile(r"\b(rgb|gaming|gamer|led)\b", re.I)

VERDICTS = [(80, "kupiti"), (65, "razmotriti"), (50, "samo ako nema boljeg"), (0, "preskočiti")]


# ── Helpers ─────────────────────────────────────────────────────────────────

def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _default_state_path() -> Path:
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    return Path(home) / "ai-pc-deal-scout" / "seen.json"


def identify_gpu(gpu: str | None, vram: float | None) -> dict:
    """Return {kind, label, vram_gb, vendor, fair_eur}. kind: dgpu|none|unknown."""
    if not gpu or not str(gpu).strip():
        return {"kind": "unknown", "label": None, "vram_gb": vram, "vendor": None, "fair_eur": 0}
    text = str(gpu).lower()
    if NO_GPU_RE.search(text.strip()):
        return {"kind": "none", "label": None, "vram_gb": 0, "vendor": None, "fair_eur": 0}
    vendor = "amd" if re.search(r"\brx\b|radeon", text) else "intel" if "arc" in text else "nvidia"
    if vram is None:
        m = re.search(r"(\d{1,2})\s*gb", text)
        vram = float(m.group(1)) if m else None
    for pattern, label, default_vram, fair in GPU_TABLE:
        if re.search(pattern, text):
            vram_eff = vram if vram is not None else default_vram
            fair = GPU_VRAM_FAIR.get((label, int(vram_eff)), fair)
            return {"kind": "dgpu", "label": label, "vram_gb": vram_eff, "vendor": vendor, "fair_eur": fair}
    fair = int(vram * 20) if vram else 0
    return {"kind": "dgpu", "label": str(gpu), "vram_gb": vram, "vendor": vendor, "fair_eur": fair}


def cpu_tier(cpu: str | None) -> int | None:
    """0-10 practical usefulness for a home AI server. None = unknown."""
    if not cpu:
        return None
    t = str(cpu).lower()
    m = re.search(r"i([3579])[\s-]*(\d{4,5})", t)
    if m:
        cls, model = int(m.group(1)), m.group(2)
        gen = int(model[:2]) if len(model) == 5 else int(model[0])
        base = 9 if gen >= 12 else 7 if gen >= 10 else 5 if gen >= 8 else 3 if gen >= 6 else 1
        if cls == 3:
            base -= 2
        elif cls >= 7 and gen >= 8:
            base += 1
        return max(0, min(10, base))
    if re.search(r"core\s*ultra", t):
        return 9
    m = re.search(r"ryzen\s*([3579])\s*(?:pro\s*)?(\d)(\d{3})", t)
    if m:
        cls, series = int(m.group(1)), int(m.group(2))
        base = 9 if series >= 5 else 7 if series == 3 else 4 if series == 2 else 3
        if cls == 3:
            base -= 2
        elif cls >= 7 and series >= 3:
            base += 1
        return max(0, min(10, base))
    m = re.search(r"xeon\s*w-?(\d)", t)
    if m:
        return 6
    if "xeon" in t:
        return 3  # old E5/E3: lots of cores, high idle power, slow single-thread
    return None


def _ssd_points(ssd_type: str | None, ssd_gb: float | None) -> int:
    if ssd_type in (None, "unknown") or ssd_type == "none":
        return 0
    pts = 4 if ssd_type == "nvme" else 2
    if ssd_gb is None:
        return pts
    pts += 6 if ssd_gb >= 2000 else 5 if ssd_gb >= 960 else 3 if ssd_gb >= 480 else 1
    return min(10, pts)


def _price_points(ratio: float | None) -> int:
    if ratio is None:
        return 0
    for limit, pts in ((0.75, 25), (0.85, 22), (0.95, 19), (1.05, 15), (1.15, 11), (1.3, 6)):
        if ratio <= limit:
            return pts
    return 2


def _gpu_points(g: dict) -> int:
    if g["kind"] != "dgpu" or g["vram_gb"] is None:
        return 0
    v = g["vram_gb"]
    pts = 25 if v >= 24 else 23 if v >= 16 else 20 if v >= 12 else 12 if v >= 10 else 6 if v >= 8 else 2
    if g["vendor"] == "amd":
        pts -= 3  # ROCm works, but CUDA tooling is still the path of least resistance
    elif g["vendor"] == "intel":
        pts -= 4
    return max(0, pts)


def _verdict(score: int) -> str:
    return next(v for limit, v in VERDICTS if score >= limit)


def _recommendation(verdict: str) -> str:
    return {"kupiti": "kupiti", "razmotriti": "razmotriti"}.get(verdict, "preskočiti")


# ── Scoring ─────────────────────────────────────────────────────────────────

def score_listing(raw: dict, rates: dict) -> dict:
    item = dict(raw)
    flags: list[str] = []
    reasons: list[str] = []
    unknown: list[str] = []
    caps: list[tuple[int, str]] = []

    # Currency + totals
    cur = (item.get("currency") or "EUR").upper()
    rate = rates.get(cur)
    price = _num(item.get("price"))
    shipping = _num(item.get("shipping"))
    if rate is None:
        return {**item, "excluded": True, "exclude_reason": f"nepoznata valuta {cur}"}
    if price is None:
        return {**item, "excluded": True, "exclude_reason": "cijena nepoznata"}
    price_eur = round(price * rate, 2)
    region = (item.get("origin_region") or "unknown").lower()
    if shipping is None:
        if region == "zagreb" or item.get("pickup"):
            shipping_eur = 0.0
        else:
            shipping_eur = 0.0
            unknown.append("dostava")
            flags.append("dostava nepoznata - ukupna cijena je donja granica")
    else:
        shipping_eur = round(shipping * rate, 2)
    import_eur = 0.0
    if region in NON_EU:
        import_eur = round((price_eur + shipping_eur) * CROATIA_VAT + IMPORT_HANDLING_EUR, 2)
        flags.append(f"uvoz izvan EU: +{import_eur:.0f} EUR PDV i carinjenje")
    elif region == "unknown":
        unknown.append("lokacija")
    total = round(price_eur + shipping_eur + import_eur, 2)

    # Hardware
    g = identify_gpu(item.get("gpu"), _num(item.get("vram_gb")))
    ram = _num(item.get("ram_gb"))
    ssd_type = (item.get("ssd_type") or "unknown").lower()
    ssd_gb = _num(item.get("ssd_gb"))
    tier = cpu_tier(item.get("cpu"))
    form = (item.get("form_factor") or "unknown").lower()

    known = sum([g["kind"] != "unknown", ram is not None, ssd_type != "unknown", tier is not None])
    for name, ok in (("GPU", g["kind"] != "unknown"), ("RAM", ram is not None),
                     ("SSD", ssd_type != "unknown"), ("CPU", tier is not None)):
        if not ok:
            unknown.append(name)
    if known < 3:
        return {**item, "excluded": True, "total_eur": total,
                "exclude_reason": "nedovoljno podataka: " + ", ".join(unknown)}

    # Fair value -> value ratio
    fair = 80 + (tier or 0) * 12 + (ram or 0) * 2.5 + g["fair_eur"]
    if ssd_type in ("nvme", "sata") and ssd_gb:
        fair += ssd_gb * (0.06 if ssd_type == "nvme" else 0.05)
    if form in ("tower", "workstation"):
        fair += 20
    ratio = round(total / fair, 3) if fair > 0 else None

    # Points
    ram_pts = 0 if ram is None else 10 if ram >= 64 else 9 if ram >= 32 else 6 if ram >= 16 else 2
    form_pts = {"tower": 5, "workstation": 5, "sff": 2, "mini": 1}.get(form, 0)
    if form == "unknown":
        unknown.append("kućište")

    cond = (item.get("condition") or "unknown").lower()
    described = bool(item.get("condition_described"))
    warranty = _num(item.get("warranty_months"))
    returns = item.get("returns")
    rating = _num(item.get("seller_rating"))
    trust = 0
    if cond in ("new", "refurbished") or (cond == "used" and described):
        trust += 3
    trust += 3 if (warranty or 0) >= 12 else 2 if (warranty or 0) >= 1 else 0
    trust += 2 if returns is True else 0
    trust += 2 if (rating or 0) >= 98 else 1 if (rating or 0) >= 95 else 0
    trust = min(10, trust)

    loc_pts = {"zagreb": 5, "hr": 4, "eu": 3, "uk": 1}.get(region, 0)

    breakdown = {
        "cijena_dostava": _price_points(ratio),
        "gpu_vram": _gpu_points(g),
        "ram_nadogradivost": min(15, ram_pts + form_pts),
        "ssd": _ssd_points(ssd_type, ssd_gb),
        "cpu": tier or 0,
        "stanje_povjerenje": trust,
        "lokacija": loc_pts,
    }
    score = sum(breakdown.values())

    # Hard rules
    title = f"{item.get('title') or ''} {item.get('description') or ''}"
    if form == "laptop" or LAPTOP_RE.search(item.get("title") or ""):
        caps.append((0, "laptop - izvan opsega"))
    if ssd_type == "none":
        caps.append((45, "nema SSD"))
    if cond == "unknown" or (cond == "used" and not described):
        caps.append((49, "stanje nije jasno opisano"))
    local_pickup = region == "zagreb" or bool(item.get("pickup"))
    if returns is False and not local_pickup:
        caps.append((49, "dostava bez mogućnosti povrata"))
    if rating is not None and rating < 90:
        caps.append((49, f"loša ocjena prodavatelja ({rating:.0f}%)"))
    if g["kind"] == "none":
        cheap_base = total <= 250 and form in ("tower", "workstation")
        caps.append((64, "bez GPU-a, jeftina baza za nadogradnju") if cheap_base
                    else (49, "bez GPU-a i nije ekstremno jeftin"))
    if g["kind"] == "dgpu" and g["vram_gb"] is not None and g["vram_gb"] <= 8 and (ratio or 9) > 0.8:
        caps.append((59, f"{g['vram_gb']:.0f} GB VRAM bez iznimne cijene"))
    if RGB_RE.search(title) and (ratio or 0) > 1.05:
        flags.append("gaming/RGB oglas s premijom na cijenu")
    for cap, why in caps:
        if score > cap:
            score = cap
        flags.append(why)

    # Human-readable reasons
    if ratio is not None:
        reasons.append(f"cijena {ratio:.2f}x procijenjene vrijednosti ({fair:.0f} EUR)")
    if g["kind"] == "dgpu":
        reasons.append(f"{g['label']} {_fmt_gb(g['vram_gb'])} GB VRAM")
    if ram:
        reasons.append(f"{ram:.0f} GB RAM")
    if ssd_type in ("nvme", "sata"):
        reasons.append(f"{ssd_type.upper()} {ssd_gb:.0f} GB" if ssd_gb else ssd_type.upper())

    verdict = _verdict(score)
    return {
        **item,
        "excluded": False,
        "price_eur": price_eur,
        "shipping_eur": shipping_eur,
        "import_eur": import_eur,
        "total_eur": total,
        "fair_value_eur": round(fair, 0),
        "value_ratio": ratio,
        "gpu_label": g["label"],
        "vram_gb": g["vram_gb"],
        "breakdown": breakdown,
        "score": int(score),
        "verdict": verdict,
        "recommendation": _recommendation(verdict),
        "reasons": reasons,
        "flags": flags,
        "unknown": unknown,
    }


# ── State (dedup / price drops) ─────────────────────────────────────────────

def apply_state(results: list[dict], state_path: Path | None, today: str) -> None:
    state: dict = {}
    if state_path and state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
    for r in results:
        key = r.get("url") or f"{r.get('platform')}|{r.get('title')}"
        prev = state.get(key)
        r["is_new"] = prev is None
        if prev and r.get("total_eur") is not None and prev.get("last_total") is not None:
            r["price_change_eur"] = round(r["total_eur"] - prev["last_total"], 2)
        state[key] = {
            "first_seen": prev["first_seen"] if prev else today,
            "last_seen": today,
            "last_total": r.get("total_eur"),
            "last_score": r.get("score"),
        }
    if state_path:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


# ── Report ──────────────────────────────────────────────────────────────────

def build_report(results: list[dict], today: str) -> dict:
    scored = sorted((r for r in results if not r.get("excluded")),
                    key=lambda r: (-r["score"], r["total_eur"]))
    excluded = [r for r in results if r.get("excluded")]
    top5 = scored[:5]
    best = scored[0] if scored and scored[0]["score"] >= 50 else None
    cheap_pool = [r for r in scored if r["score"] >= 65] or [r for r in scored if r["score"] >= 50]
    # Prefer a different listing than best_overall so the report shows two options.
    alt_pool = [r for r in cheap_pool if r is not best and r["total_eur"] < (best or {}).get("total_eur", 1e9)]
    cheapest = min(alt_pool or cheap_pool, key=lambda r: r["total_eur"]) if cheap_pool else None
    flagged = [r for r in scored if r["score"] < 50 and r["flags"]]
    avoid = max(flagged, key=lambda r: r["total_eur"]) if flagged else (scored[-1] if scored and scored[-1]["score"] < 50 else None)

    ratios = [r["value_ratio"] for r in scored if r.get("value_ratio")]
    median_ratio = round(statistics.median(ratios), 2) if ratios else None
    if not scored:
        note = "Nema oglasa s dovoljno podataka za procjenu."
    elif not any(r["score"] >= 65 for r in scored):
        note = "Tržište danas slabo: nijedan oglas nije prešao 65 bodova. Čekaj."
    elif median_ratio and median_ratio > 1.15:
        note = f"Tržište preskupo: medijan {median_ratio}x procijenjene vrijednosti."
    else:
        note = None

    return {
        "date": today,
        "counts": {"scored": len(scored), "excluded": len(excluded),
                   "buy": sum(r["score"] >= 80 for r in scored),
                   "consider": sum(65 <= r["score"] < 80 for r in scored)},
        "median_value_ratio": median_ratio,
        "top5": top5,
        "best_overall": best,
        "best_cheap": cheapest,
        "avoid_example": avoid,
        "market_note": note,
        "excluded": [{"title": r.get("title"), "url": r.get("url"),
                      "reason": r.get("exclude_reason")} for r in excluded],
    }


def _fmt_gb(v: float | None) -> str:
    return "?" if v is None else f"{v:g}"


def _fmt_eur(v: float | None) -> str:
    return "?" if v is None else f"{v:,.0f} EUR".replace(",", ".")


def _fmt_listing(r: dict, idx: int | None = None) -> str:
    head = f"{idx}. " if idx else ""
    new = " [NOVO]" if r.get("is_new") else ""
    drop = r.get("price_change_eur")
    drop_s = f" [cijena {drop:+.0f} EUR]" if drop else ""
    ship = "nepoznata" if "dostava" in r.get("unknown", []) else _fmt_eur(r["shipping_eur"])
    lines = [
        f"{head}[{r['score']}] {r.get('title')}{new}{drop_s}",
        f"   {r.get('platform')} | {_fmt_eur(r['price_eur'])} + dostava {ship}"
        + (f" + uvoz {_fmt_eur(r['import_eur'])}" if r["import_eur"] else "")
        + f" = {_fmt_eur(r['total_eur'])}",
        f"   CPU {r.get('cpu') or '?'} | RAM {_fmt_gb(_num(r.get('ram_gb')))} GB | GPU "
        + (f"{r.get('gpu_label') or r.get('gpu')} {_fmt_gb(r.get('vram_gb'))} GB" if r.get("gpu_label")
           else "nema" if r.get("vram_gb") == 0 else "?")
        + f" | SSD {_fmt_gb(_num(r.get('ssd_gb')))} GB {r.get('ssd_type') or ''}".rstrip(),
        f"   {r.get('location') or '?'} | stanje {r.get('condition') or '?'} | "
        f"jamstvo {r.get('warranty_months') if r.get('warranty_months') is not None else '?'} mj | "
        f"povrat {({True: 'da', False: 'ne'}).get(r.get('returns'), '?')}",
        f"   {r['recommendation'].upper()}: " + "; ".join(r["reasons"] + r["flags"]),
    ]
    if r.get("url"):
        lines.append(f"   {r['url']}")
    return "\n".join(lines)


def render_text(report: dict) -> str:
    c = report["counts"]
    out = [f"AI PC DEAL SCOUT - {report['date']}",
           f"Ocijenjeno {c['scored']} | kupiti {c['buy']} | razmotriti {c['consider']} | "
           f"isključeno {c['excluded']}", ""]
    out.append("TOP 5")
    out += [_fmt_listing(r, i) for i, r in enumerate(report["top5"], 1)] or ["(nema)"]
    for title, key in (("NAJBOLJA UKUPNA KUPNJA", "best_overall"),
                       ("NAJBOLJA JEFTINA OPCIJA", "best_cheap"),
                       ("IZBJEGAVATI", "avoid_example")):
        out += ["", title]
        r = report[key]
        out.append(_fmt_listing(r) if r else "(nema)")
    if report["market_note"]:
        out += ["", "NAPOMENA: " + report["market_note"]]
    return "\n".join(out)


# ── CLI ─────────────────────────────────────────────────────────────────────

SCHEMA = {
    "platform": "njuskalo | ebay | amazon | ...",
    "title": "str", "url": "str",
    "price": "number (listing currency)", "currency": "EUR | USD | GBP ... (default EUR)",
    "shipping": "number | null (null = unknown; 0 = free)", "pickup": "bool - local pickup possible",
    "cpu": "str | null", "ram_gb": "number | null",
    "gpu": "str | null ('none' for no dedicated GPU)", "vram_gb": "number | null",
    "ssd_type": "nvme | sata | none | null", "ssd_gb": "number | null",
    "form_factor": "tower | workstation | sff | mini | laptop | aio | null",
    "location": "str", "origin_region": "zagreb | hr | eu | uk | non_eu | null",
    "condition": "new | refurbished | used | null", "condition_described": "bool",
    "warranty_months": "number | null", "returns": "bool | null",
    "seller_rating": "percent 0-100 | null", "description": "str (optional, short)",
}


def load_listings(path: str) -> list[dict]:
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("listings", [])
    if not isinstance(data, list):
        raise ValueError("input must be a list of listings or {\"listings\": [...]}")
    return [d for d in data if isinstance(d, dict)]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("score", help="score listings and print the daily report")
    s.add_argument("--input", required=True, help="listings JSON file, or - for stdin")
    s.add_argument("--format", choices=("text", "json", "both"), default="both")
    s.add_argument("--state", help="dedup state file (default: $HERMES_HOME/ai-pc-deal-scout/seen.json)")
    s.add_argument("--no-state", action="store_true", help="do not read/write dedup state")
    s.add_argument("--rates", help='JSON file {"EUR":1,"USD":0.87,...} overriding default FX rates')
    s.add_argument("--date", help="report date (default: today)")
    sub.add_parser("schema", help="print expected listing fields")
    args = p.parse_args(argv)

    if args.cmd == "schema":
        print(json.dumps(SCHEMA, ensure_ascii=False, indent=2))
        return 0

    rates = dict(DEFAULT_RATES)
    if args.rates:
        rates.update({k.upper(): float(v) for k, v in
                      json.loads(Path(args.rates).read_text(encoding="utf-8")).items()})
    today = args.date or date.today().isoformat()
    results = [score_listing(l, rates) for l in load_listings(args.input)]
    state_path = None if args.no_state else Path(args.state) if args.state else _default_state_path()
    apply_state(results, state_path, today)
    report = build_report(results, today)

    if args.format in ("json", "both"):
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.format == "both":
        print("\n---\n")
    if args.format in ("text", "both"):
        print(render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
