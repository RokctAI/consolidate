# Shoprite Product Scraper

This directory contains scripts to scrape product information from the Shoprite website (shoprite.co.za).

## Scripts

### 1. `shoprite_scraper.py`

The main scraper script that fetches product details from category pages.

**Usage:**
```bash
python3 .rokct/scripts/scrappers/shoprite/shoprite_scraper.py [args]
```

**Arguments:**
- `--category`: The category slug or full URL to scrape. Defaults to "All-Departments".
- `--limit`: Limit the number of products to scrape (useful for testing). Default is 0 (no limit).

**Functionality:**
- Extracts: Product Name, Price (Current/Was), Description, Nutrition Data, and Images.
- Uses Playwright (headless) for JavaScript rendering.
- Implements a 1-second polite delay between requests.
- Deduplicates by skipping products that already have a markdown card.
- Logs to `.rokct/agent/logs/shoprite_scraper.log` and `.rokct/agent/logs/shoprite_failures.log`.

### 2. `update_prices.py`

A script to re-scrape and update prices for products that have already been scraped.

**Usage:**
```bash
python3 .rokct/scripts/scrappers/shoprite/update_prices.py
```

**Functionality:**
- Iterates through all `.md` cards in the `products/` directory.
- Fetches the current price and was-price from the source URL.
- Updates the markdown card if the price has changed.
- Reuses the price extraction logic from `shoprite_scraper.py`.

## Output Structure

The scraped data is stored in the `products/` directory:

```
products/
└── {product-slug}/
    ├── images/
    │   ├── {product-slug}_0.jpg
    │   ├── {product-slug}_1.jpg
    │   └── ...
    └── {product-slug}_card.md
```

### Card Format (`_card.md`)

```markdown
# {Product Name}

## Price
- **Current Price**: R{price}
- **Was**: R{was_price}  ← omitted if not on promotion

## Description
{description}

## Nutrition Information
| Nutrient | Per 100g | Per Serving |
|----------|----------|-------------|
| ...      | ...      | ...         |

## Images
- images/{filename_1}
- images/{filename_2}

## Meta
- **Source**: {product_url}
- **Scraped**: {date}
- **Store**: Shoprite/Checkers
```
