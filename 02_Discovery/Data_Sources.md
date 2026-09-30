# Data Sources (verified during discovery)

| Purpose | Source | Notes |
|---|---|---|
| Sales lines (Amazon, eBay, B&Q) | `order_management_copy.public.order_transaction` (`DATABASE_URL`) | Completed, `market_place = 'UK'`; ID = `asin` / `item_id` / `sku` (B&Q has no barcode on order lines); Sales = `order_total` (line total, verified) |
| Product master / combo components | `public.inv_products` + `public.inv_product_combo` | `pack_count` is a code → decoded via ledsone `inventory.product_pk` (20–28 = 1–9 units; A–S = 10…1000/25) |
| Pack codes | ledsone `inventory.product_pk` (`WLP_SOURCE_DB_URL`) | 28 rows |
| Marketplace + ID → SKU | `public.listing_data` (UK, `wrong_sku = 0`, which_channel 1 = Amazon, 2 = eBay) | B&Q `ref_id` is the EAN, not on order lines |
| PH allocation | ledsone `staff.ph_category_products` (source_id 1) → `ph_categories` → `users` | 11,399 ASINs, 30 PHs, assign_date 2026-07-01, no ASIN under >1 PH |
| Product Category (display) | `order_transaction.category_name` (PH-portfolio names) via the SKU's own lines, else its packs/combos; `Special-*` ignored; most lines wins | The only true product classification, `inv_products.sub_category`, has no name table in either database |
| Publishing | `order_management_copy.tech_team_outputs.ph_task` (project_code PPA, team ph_priors, ids 1974–2003) | Rendered at https://sales.vintageinterior.co.uk/ph-dashboard/ph-priors |

Discovery review pack: `review_pack/Product_Performance_Decision_Pack_2026-08.html` (left in place).
