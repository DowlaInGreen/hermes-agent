# Search Playbook

Where and how to search each platform. Run every platform separately and keep
its raw results apart until scoring.

## Njuškalo (HR, local pickup, no returns)

Category pages (add `?keywords=<query>` and sort by newest):

- `https://www.njuskalo.hr/stolna-racunala`
- `https://www.njuskalo.hr/informatika` (components, workstations, mini PC)

Queries, highest yield first:

1. `rtx 3060 12gb`
2. `rtx 4060 ti 16gb`
3. `rtx 3090`
4. `workstation 32gb`
5. `ryzen 5 rtx`
6. `i7 32gb ssd`
7. `mini pc 32gb`

Notes:

- The site uses aggressive bot protection. Prefer `browser_navigate` +
  `browser_snapshot`; fall back to `web_extract` only if it returns listing data.
- Open each promising listing — the search card hides RAM/SSD/GPU details.
- Private sellers rarely state warranty or returns: set `returns: null`,
  `warranty_months: null`, `pickup: true`, `origin_region: "zagreb"` (or `"hr"`
  if outside Zagreb, with BoxNow / Overseas shipping as the cost).
- A listing that only says "odlično stanje" with no photos or specifics is
  `condition_described: false`.

## eBay (EU sellers first)

Use `ebay.de` (largest EU refurbished market, ships to HR). Append
`&_sop=15` (price + shipping, lowest first). Condition filter
`&LH_ItemCondition=2500|3000` = seller refurbished + used.

- `https://www.ebay.de/sch/i.html?_nkw=<query>&_sop=15&LH_ItemCondition=2500|3000`

Queries:

1. `desktop pc rtx 3060 12gb`
2. `workstation rtx 3090`
3. `rtx 4060 ti 16gb pc`
4. `refurbished tower 32gb ssd`
5. `precision 5820 rtx` / `hp z4 g4 rtx` / `thinkstation p520 rtx`
6. `mini pc refurbished 32gb`

Notes:

- Record `seller_rating` (positive feedback %) and the return policy.
- Item location UK, US, CN → `origin_region` `uk` / `non_eu`; the scorer adds
  25% HR VAT + customs handling automatically.
- Ignore listings where "ships to Croatia" is not offered.

## Amazon (Renewed / Refurbished)

Use `amazon.de` (ships most Renewed items to HR); check `amazon.it` only if
`.de` is empty.

- `https://www.amazon.de/s?k=<query>`

Queries:

1. `renewed desktop pc rtx 3060`
2. `refurbished tower pc 32gb ssd`
3. `refurbished desktop windows 11 i7`
4. `renewed workstation`

Notes:

- Amazon Renewed carries a 12-month guarantee and returns: set
  `warranty_months: 12`, `returns: true`, `condition: "refurbished"`.
- Third-party "Gaming PC" sellers on Amazon are the main source of RGB markup;
  read the spec table, not the title.
- Shipping to HR is shown only at checkout or on the product page delivery box.
  If you cannot see it, leave `shipping: null`.

## Extraction rules

- Unknown means `null`. Never fill a field from the title alone if the spec
  table contradicts it, and never guess VRAM from the GPU name if the listing
  says otherwise.
- `gpu: "none"` only when the listing explicitly says integrated graphics or no
  GPU. Missing GPU info is `null`.
- Prices in listing currency; the scorer converts.
- Collect at most ~15 serious candidates per platform. Quality beats volume.
