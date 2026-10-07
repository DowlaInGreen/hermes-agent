# Scoring Rubric

Implemented in `scripts/score_listings.py`. 100 points total.

| Component | Max | How |
|---|---|---|
| Price + shipping | 25 | `value_ratio = total / fair_value`. ≤0.75 → 25, ≤0.85 → 22, ≤0.95 → 19, ≤1.05 → 15, ≤1.15 → 11, ≤1.3 → 6, else 2 |
| GPU / VRAM | 25 | ≥24 GB → 25, ≥16 → 23, ≥12 → 20, ≥10 → 12, 8 → 6, less → 2, none → 0. AMD −3, Intel −4 (CUDA tooling) |
| RAM + upgradability | 15 | RAM ≥64 → 10, 32 → 9, 16 → 6, 8 → 2. Tower/workstation +5, SFF +2, mini +1 |
| SSD / storage | 10 | NVMe 4 / SATA 2, plus ≥2 TB +6, ≥1 TB +5, ≥512 GB +3, smaller +1. No SSD → 0 |
| CPU | 10 | Practical tier: Intel 12th gen+ / Ryzen 5000+ ≈ 9, 10–11th / Ryzen 3000 ≈ 7, 8–9th ≈ 5, old Xeon E5 3. i9/R9 earn no premium |
| Condition / trust | 10 | Clear condition +3, warranty ≥12 mo +3 (≥1 mo +2), returns +2, seller ≥98% +2 (≥95% +1) |
| Location | 5 | Zagreb 5, HR 4, EU 3, UK 1, non-EU 0 |

## Total price

`total = price + shipping + import`. Import applies to UK/non-EU origin:
25% Croatian VAT on (price + shipping) + 15 EUR courier customs handling.
Computers and GPUs carry 0% EU customs duty, so VAT is the real cost.

## Fair value

`80 + cpu_tier×12 + RAM_GB×2.5 + GPU used price + SSD (NVMe 0.06 €/GB,
SATA 0.05 €/GB) + 20 for a tower/workstation case + PSU`.

GPU used prices live in `GPU_TABLE` at the top of the script. Update them
when the market moves — they drive the price score.

## Hard caps

| Rule | Cap |
|---|---|
| Laptop | 0 |
| No SSD | 45 |
| Condition unclear / undescribed | 49 |
| Shipped with no returns (local pickup exempt) | 49 |
| Seller rating < 90% | 49 |
| No GPU, total > 250 EUR or not a tower | 49 |
| No GPU, ≤250 EUR tower/workstation (upgrade base) | 64 |
| ≤8 GB VRAM without value_ratio ≤ 0.8 | 59 |

Listings with fewer than 3 of {GPU, RAM, SSD, CPU} known are excluded, not scored.

## Thresholds

- 80+ → kupiti (strong buy)
- 65–79 → razmotriti
- 50–64 → samo ako nema boljeg
- < 50 → preskočiti
