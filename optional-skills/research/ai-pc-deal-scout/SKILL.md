---
name: ai-pc-deal-scout
description: Nightly scout for used desktops to run local LLMs.
version: 1.0.0
author: Vlado Stipan (@DowlaInGreen), Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [Shopping, Hardware, Local LLM, Deals, Njuskalo, eBay, Amazon, Cron]
    category: research
    requires_toolsets: [terminal, web, browser]
---

# AI PC Deal Scout Skill

Searches Njuškalo, eBay and Amazon every evening for used or refurbished
desktops that give the best value for a home server running local LLMs, RAG,
scraping and agents. The agent collects listings; `scripts/score_listings.py`
does the deterministic part — EUR normalisation, shipping + import VAT, a
0–100 score, hard rules, dedup against earlier nights, and a Telegram/Slack
ready report. It does not buy, message sellers, or guess missing specs.

## When to Use

- A nightly cron job asks for the used-desktop deal report.
- The user asks "is there a good RTX 3060 / 3090 PC on Njuškalo/eBay today?"
- The user pastes listings and wants them scored on the same rubric.

## Prerequisites

- Toolsets: `terminal` (runs the scorer), `web` (`web_search`, `web_extract`),
  `browser` (`browser_navigate`, `browser_snapshot` — Njuškalo blocks plain fetches).
- Python 3.10+, stdlib only.
- Buyer profile is baked into the rubric: Zagreb, desktop only, GPU ≥12 GB
  VRAM, 32 GB RAM ideal, NVMe SSD, mid-range CPU, upgradeable case.

## How to Run

Manual run (one-off):

```
python3 ${HERMES_SKILL_DIR}/scripts/score_listings.py score --input /tmp/pc_listings.json --format text
```

Nightly at 21:00, delivered to Telegram:

```
hermes cron add --name "ai-pc-deal-scout" --skill ai-pc-deal-scout --deliver telegram \
  "0 21 * * *" "Run the ai-pc-deal-scout procedure and send the daily report."
```

## Quick Reference

| Command | Purpose |
|---|---|
| `score_listings.py schema` | Print the listing JSON fields |
| `score_listings.py score --input F --format text` | Telegram/Slack report |
| `score_listings.py score --input F --format json` | Structured report for a DB |
| `--no-state` | Don't read/write dedup history |
| `--state PATH` | Custom history file (default `$HERMES_HOME/ai-pc-deal-scout/seen.json`) |
| `--rates rates.json` | Override FX rates, e.g. `{"USD": 0.86, "GBP": 1.15}` |

Example input: `${HERMES_SKILL_DIR}/templates/listings.example.json`.
Rubric: `${HERMES_SKILL_DIR}/references/scoring-rubric.md`.
Queries and URLs: `${HERMES_SKILL_DIR}/references/search-playbook.md`.

## Procedure

1. `read_file` `references/search-playbook.md`. Run `score_listings.py schema`
   so you know the exact fields.
2. **Njuškalo** — `browser_navigate` the category URLs with each query, open
   the promising listings, extract specs. Local pickup: `origin_region: "zagreb"`.
3. **eBay** — `web_extract` (or browser) on `ebay.de` searches sorted by
   price + shipping. Record seller rating, returns, item location.
4. **Amazon** — `amazon.de` Renewed/refurbished searches. Read the spec
   table, not the title.
5. For each listing write one JSON object. Unknown → `null`. Never infer RAM,
   VRAM, SSD or shipping from a "gaming" title or from what is typical.
6. Save all platforms into one file with `write_file`, e.g.
   `/tmp/pc_listings.json` as `{"listings": [...]}`.
7. `terminal`: `python3 ${HERMES_SKILL_DIR}/scripts/score_listings.py score --input /tmp/pc_listings.json --format text`
8. Deliver the text report as-is. Add at most 3 lines of your own judgement
   (e.g. "the 3090 workstation is worth a message to the seller to ask about PSU").
   If the cron job asks for JSON, use `--format json` instead.

## Pitfalls

- **Njuškalo listings are under-specified.** "Gaming PC, top stanje" with no
  RAM/SSD is excluded by the scorer (needs 3 of GPU/RAM/SSD/CPU). Don't fill
  the gaps to make it pass.
- **UK/US eBay sellers look cheap until import.** Set `origin_region` correctly;
  the scorer adds 25% VAT + handling. Missing it inflates scores by ~25 points.
- **4060 Ti 8 GB vs 16 GB** — same name, very different value. Use the VRAM
  from the listing; if not stated, leave `vram_gb: null` and the scorer
  assumes the 8 GB default (conservative).
- **Default FX rates are approximate.** Pass `--rates` when USD/GBP moves.
- **GPU fair prices age.** Update `GPU_TABLE` in the script when the used
  market shifts; stale prices skew the price component.
- **Do not repeat listings already shown** unless the price dropped — the
  report marks `[NOVO]` and `[cijena -40 EUR]` for you.

## Verification

```
python3 ${HERMES_SKILL_DIR}/scripts/score_listings.py score \
  --input ${HERMES_SKILL_DIR}/templates/listings.example.json --no-state --format text
```

Expected: the Zagreb RTX 3060 / 32 GB build ranks first (≈79, razmotriti),
the RGB RTX 4060 8 GB Amazon PC is the "IZBJEGAVATI" example.
