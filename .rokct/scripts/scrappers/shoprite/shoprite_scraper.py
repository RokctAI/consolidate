import os
import sys
import asyncio
import logging
import argparse
import datetime
import re
from typing import Optional, Dict, Any, List
from urllib.parse import urljoin
import requests
from playwright.async_api import async_playwright

# Setup logging
os.makedirs(".rokct/agent/logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(".rokct/agent/logs/shoprite_scraper.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

failure_logger = logging.getLogger("failures")
failure_logger.setLevel(logging.ERROR)
failure_handler = logging.FileHandler(".rokct/agent/logs/shoprite_failures.log")
failure_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
failure_logger.addHandler(failure_handler)

BASE_URL = "https://www.shoprite.co.za"

# Shared JavaScript logic for price extraction
JS_PRICE_EXTRACTION = """() => {
    const getPriceText = (selector) => {
        const el = document.querySelector(selector);
        return el ? el.innerText.trim() : null;
    };
    return {
        price_now_raw: getPriceText('.special-now-price, .price-now, .pdp-main-details__price'),
        price_was_raw: getPriceText('.special-was-price, .price-was, .pdp-main-details__price-was'),
        insider_product: window.insider_object?.product
    };
}"""

def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_-]+', '-', text)
    text = text.strip('-')
    return text

def extract_price_from_page(data: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """
    Standalone function to extract price data from the product data extracted by Playwright.
    """
    price_now = None
    price_was = None

    insider = data.get('insider_product')
    if insider:
        price_now = insider.get('unit_sale_price')
        if price_now is not None:
            price_now = f"{price_now:.2f}"

        unit_price = insider.get('unit_price')
        # Check if unit_price is a number and higher than price_now
        try:
            if unit_price is not None and price_now and float(unit_price) > float(price_now):
                price_was = f"{unit_price:.2f}"
        except (ValueError, TypeError):
            pass

    if not price_now:
        raw_price = data.get('price_now_raw')
        if raw_price:
            match = re.search(r'R\s*(\d+(?:[\.,]\d+)?)', raw_price)
            if match:
                price_now = match.group(1).replace(',', '.')

    if not price_was:
        raw_was = data.get('price_was_raw')
        if raw_was:
            match = re.search(r'R\s*(\d+(?:[\.,]\d+)?)', raw_was)
            if match:
                price_was = match.group(1).replace(',', '.')

    return {
        "current_price": price_now,
        "was_price": price_was
    }

async def scrape_product(page, url: str) -> bool:
    logger.info(f"Scraping product: {url}")

    try:
        await page.goto(url, wait_until="networkidle", timeout=60000)
        # Scroll to ensure lazy images load
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        await page.wait_for_timeout(3000)

        data = await page.evaluate("""() => {
            const nutritionText = [];
            // Try to find nutrition info in common containers
            const nutSelectors = ['.nutrition-table', '.product-nutrition-table', '.pdp__product-information', 'table'];
            for (const sel of document.querySelectorAll(nutSelectors.join(','))) {
                if (sel.innerText.toLowerCase().includes('per 100')) {
                    nutritionText.push(sel.innerText);
                }
            }

            const imageSources = new Set();
            // Collect from insider object first
            if (window.insider_object && window.insider_object.product && window.insider_object.product.product_image_url) {
                imageSources.add(window.insider_object.product.product_image_url);
            }

            // Then from the page
            document.querySelectorAll('img, [data-zoom-image], [data-original-src]').forEach(el => {
                if (el.src && !el.src.includes('error.png') && el.src.includes('/medias/')) imageSources.add(el.src);
                const original = el.getAttribute('data-original-src');
                if (original && original.includes('/medias/')) imageSources.add(original);
                const zoom = el.getAttribute('data-zoom-image');
                if (zoom && zoom.includes('/medias/')) imageSources.add(zoom);
            });

            const getPriceText = (selector) => {
                const el = document.querySelector(selector);
                return el ? el.innerText.trim() : null;
            };

            return {
                name: document.querySelector('h1, .pdp-main-details__name')?.innerText.trim(),
                price_now_raw: getPriceText('.special-now-price, .price-now, .pdp-main-details__price'),
                price_was_raw: getPriceText('.special-was-price, .price-was, .pdp-main-details__price-was'),
                description: document.querySelector('.pdp__description, .pdp-details__description, .product-details-description, .pdp-main-details__description')?.innerText.trim(),
                images: Array.from(imageSources).filter(src => src && !src.includes('logo')),
                nutrition_raw: nutritionText.join('\\n'),
                insider_product: window.insider_object?.product
            };
        }""")

        name = None
        insider = data.get('insider_product')
        if insider and insider.get('name'):
            name = insider.get('name')
        else:
            name = data.get('name')

        if not name:
            logger.error(f"Could not find product name for {url}")
            failure_logger.error(f"Failed to extract name: {url}")
            return False

        product_slug = slugify(name)
        product_dir = f"products/{product_slug}"
        card_path = f"{product_dir}/{product_slug}_card.md"

        if os.path.exists(card_path):
            logger.info(f"Skipping {product_slug}, card already exists.")
            return True

        os.makedirs(f"{product_dir}/images", exist_ok=True)

        prices = extract_price_from_page(data)

        # Download images
        image_filenames = []
        # Use a dict to avoid duplicate filenames from same/similar URLs
        seen_urls = set()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        }

        logger.info(f"Found {len(data.get('images', []))} candidate images")
        for img_url in data.get('images', []):
            if not img_url or img_url in seen_urls: continue
            seen_urls.add(img_url)

            full_img_url = urljoin(BASE_URL, img_url)
            try:
                img_response = requests.get(full_img_url, headers=headers, timeout=10)
                if img_response.status_code == 200:
                    # Specific requirement: Save as {product_slug}_{index}.jpg
                    filename = f"{product_slug}_{len(image_filenames)}.jpg"
                    filepath = f"{product_dir}/images/{filename}"
                    with open(filepath, 'wb') as f:
                        f.write(img_response.content)
                    image_filenames.append(filename)
                    logger.info(f"Downloaded image: {filename}")
                else:
                    logger.warning(f"Failed to download image {full_img_url}, status: {img_response.status_code}")
            except Exception as e:
                logger.warning(f"Failed to download image {full_img_url}: {e}")

        # Improved Nutrition Table Parsing
        nutrition_md = ""
        raw_nut = data.get('nutrition_raw', '')
        if raw_nut:
            # Try to split by common headers
            per_100 = ""
            per_serving = ""
            if "per 100" in raw_nut.lower():
                parts = re.split(r'per serving', raw_nut, flags=re.IGNORECASE)
                per_100 = parts[0]
                if len(parts) > 1:
                    per_serving = parts[1]

                # Extract nutrients and values: "Energy: 1783kJ" or "Protein 13.8g"
                nutrient_pattern = r'([\w\s•-]+?):?\s+([\d\.]+\s*[kKjJgGmM]+)'
                nutrients_100 = re.findall(nutrient_pattern, per_100)
                nutrients_serving = re.findall(nutrient_pattern, per_serving)

                # Combine into a map for the table
                table_data = {} # { nutrient_name: [val_100, val_serving] }
                for n_name, val in nutrients_100:
                    n_name = n_name.strip().replace('• ', '')
                    if n_name.lower() in ['information', 'nutritional']: continue
                    table_data[n_name] = [val, ""]

                for n_name, val in nutrients_serving:
                    n_name = n_name.strip().replace('• ', '')
                    if n_name.lower() in ['information', 'nutritional']: continue
                    if n_name in table_data:
                        table_data[n_name][1] = val
                    else:
                        table_data[n_name] = ["", val]

                if table_data:
                    nutrition_md = "\n## Nutrition Information\n| Nutrient | Per 100g | Per Serving |\n|----------|----------|-------------|\n"
                    for n, vals in table_data.items():
                        nutrition_md += f"| {n} | {vals[0]} | {vals[1]} |\n"

            if not nutrition_md:
                # Fallback to simple list if regex fails or no table-like data found
                lines = [l.strip() for l in raw_nut.split('\n') if l.strip()]
                if lines:
                    nutrition_md = "\n## Nutrition Information\n" + "\n".join([f"- {l}" for l in lines])

        was_price_line = f"\n- **Was**: R{prices['was_price']}" if prices['was_price'] else ""
        images_list = "\n".join([f"- images/{fn}" for fn in image_filenames])

        card_content = f"""# {name}

## Price
- **Current Price**: R{prices['current_price'] or 'N/A'}{was_price_line}

## Description
{data.get('description') or 'No description available.'}
{nutrition_md}
## Images
{images_list}

## Meta
- **Source**: {url}
- **Scraped**: {datetime.date.today().isoformat()}
- **Store**: Shoprite/Checkers
"""
        with open(card_path, 'w') as f:
            f.write(card_content)

        logger.info(f"Successfully scraped {product_slug}")
        return True

    except Exception as e:
        logger.error(f"Error scraping {url}: {e}")
        failure_logger.error(f"Exception for {url}: {e}")
        return False

async def main():
    parser = argparse.ArgumentParser(description="Shoprite Product Scraper")
    parser.add_argument("--category", type=str, default="All-Departments", help="Category to scrape")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of products")
    args = parser.parse_args()

    cat_url = "https://www.shoprite.co.za/c-2256/All-Departments" if args.category == "All-Departments" else f"https://www.shoprite.co.za/c/{args.category}"
    if args.category.startswith("http"): cat_url = args.category

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36")
        page = await context.new_page()

        logger.info(f"Fetching category page: {cat_url}")
        try:
            await page.goto(cat_url, wait_until="networkidle", timeout=60000)
            await page.wait_for_timeout(5000)

            product_links = await page.evaluate("""() => {
                const links = new Set();
                document.querySelectorAll('a').forEach(a => { if (a.href.includes('/p/')) links.add(a.href); });
                return Array.from(links);
            }""")

            if args.limit > 0: product_links = product_links[:args.limit]
            logger.info(f"Found {len(product_links)} products to scrape")

            for link in product_links:
                await scrape_product(page, link)
                await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Failed to scrape category {cat_url}: {e}")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
