# SK-GADS-CAMPAIGN-FROM-SHOPIFY

Use this workflow before preparing, uploading, or activating Google Ads campaigns from a Shopify catalog.

## Required Workflow

1. Read the real Shopify collection/catalog through the live Admin API or the verified local export named in project-atlas. Do not invent products, URLs, SKUs, variants, prices, or collections.
2. Produce a complete collection inventory with product count, handle, title, vendor, type, tags, availability signal, canonical product URL, and variant count.
3. Classify every product into an explicit campaign/ad-group type. For Recambios BMW, separate at minimum OEM/genuine parts, catalog aftermarket, retrofit, accessory, maintenance/consumable, and incompatible/hold.
4. Generate campaign structure from the classified inventory: ad groups, exact/phrase keywords, responsive search ads, sitelinks/assets when applicable, and campaign/ad-group negatives.
5. Validate every Final URL with an HTTP request that returns 200. Redirects are acceptable only when the final landing is still the intended product/collection and returns 200.
6. Run Google Ads API `validate_only=true` for the full mutation set before applying anything.
7. If applying is authorized, create/upload the campaign and all mutable entities in `PAUSED` state first.
8. Query Google Ads with GAQL after mutation and record counts for campaign, ad groups, ads, keywords, negatives, assets, and paused status.
9. Review quality before activation: coverage, keyword/ad relevance, URL health, negative list, budgets, bidding, geo/language, tracking, and conversion readiness.
10. Activate only after explicit quality review and authorization. Never activate directly from a generated draft.

## Closure Evidence

Closing as prepared, applied, published, or done requires:

- Shopify source evidence: collection/export path, timestamp, product count, and classification totals.
- Final URL evidence: every URL checked and HTTP 200 summary.
- Google Ads evidence: `validate_only=true` result and post-mutation GAQL counts when anything was applied.
- Status evidence: uploaded entities are `PAUSED`.
- Quality evidence: the review decision and any holds/blocked products.
