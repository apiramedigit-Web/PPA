# Requirements Summary — Product Performance Automation

Source of truth: `01_Requirements/Product_Performance_Automation.pdf` (copy; original in Downloads).

## Scope (from the PDF)
- Reports 1–3: Amazon, eBay, B&Q — Month, SKU (as sold), SKU Type, Quantity Sold, Sales Amount. Packs and combos stay as sold; no conversion; Sales Amount never divided.
- Report 4: Single SKU Units — Amazon / eBay / B&Q / Total / Avg Daily Units; packs × pack size, combos split into components; units only.
- Exceptions (Step 9), reconciliation (Reports 1–3 and Report 4), History, filters Month / Marketplace / SKU.
- Testing: 3 past months against totals.

## Approved changes / additions during delivery (owner decisions)
| Item | Decision |
|---|---|
| Schedule | PDF: 2nd of every month 06:00 → **operational: 3rd of every month 06:00** (requested by project owner) |
| PH dimension | 30 PHs from ledsone `staff.ph_category_products → ph_categories → users` (= sheet "Master Amazon UK 3rd Cycle"); current allocation applied to Jun–Aug 2026; 4th Cycle not used |
| Marketplace + ID lookup | `listing_data` (UK, wrong_sku = 0) used when the sold SKU is not in the product master |
| Product Category | Column + filter in Report 4 (display only); source and limitations in `11_Documentation/Project_Architecture.md` |
| D3 (merge listing SKUs into mapped SKU in Reports 1–3) | Option B approved — Status: Pending implementation |
| D2 (suffix variants) / D6 (OMS vs listing_data pairs) | Not approved for automatic handling; left as exceptions / review |
| D7 (ENC9164_ABM → ENC9164) | Approved; application method — Status: Pending decision |

## Open requirement items
See `15_Closure/Closure_Status.md`.
