import random
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
import time
from playwright.async_api import async_playwright


# Setup logging
os.makedirs(".rokct/agent/logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(".rokct/agent/logs/shoprite_scraper.log", mode="w"),
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
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0"
]


# Shared JavaScript logic for price extraction
JS_PRICE_EXTRACTION = """() => {
    const getPriceText = (selector) => {
        const el = document.querySelector(selector);
        return el ? el.innerText.trim() : null;
    };
    const cardPriceEl = Array.from(document.querySelectorAll('.pdp-main-details__price, .special-now-price')).find(el => {
        const next = el.nextElementSibling;
        return next && next.innerText.toUpperCase().includes('WITH CARD');
    });
    const promoDates = document.querySelector('.pdp-main-details__promotion-dates, .product-promotion__dates')?.innerText.trim();

    return {
        price_now_raw: getPriceText('.special-now-price, .price-now, .pdp-main-details__price'),
        price_was_raw: getPriceText('.special-was-price, .price-was, .pdp-main-details__price-was'),
        is_card_price: !!cardPriceEl || document.body.innerText.includes('WITH CARD'),
        promotion_dates: promoDates || Array.from(document.querySelectorAll('*')).find(el => el.innerText && el.innerText.includes('Valid until'))?.innerText.trim(),
        insider_product: window.insider_object?.product
    };
}"""


def get_source_url() -> Optional[str]:
    source_path = "products/sources/ShopriteZA.md"
    if not os.path.exists(source_path):
        return None
    try:
        with open(source_path, 'r') as f:
            for line in f:
                if '- **URL**:' in line:
                    return line.split(':', 1)[1].strip()
    except Exception as e:
        logger.warning(f"Failed to read source card {source_path}: {e}")
    return None

def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_-]+', '-', text)
    text = text.strip('-')
    return text

def extract_price_from_page(data: Dict[str, Any]) -> Dict[str, Optional[Any]]:
    """
    Standalone function to extract price data from the product data extracted by Playwright.
    """
    price_now = None
    price_was = None

    insider = data.get('insider_product')
    if insider:
        price_now = insider.get('unit_sale_price')
        # Check for NaN or None
        if price_now is not None and str(price_now).lower() != 'nan':
            try:
                price_now = f"{float(price_now):.2f}"
            except (ValueError, TypeError):
                price_now = None
        else:
            price_now = None

        unit_price = insider.get('unit_price')
        try:
            if unit_price is not None and price_now and float(unit_price) > float(price_now):
                price_was = f"{float(unit_price):.2f}"
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
        "was_price": price_was,
        "is_card_price": data.get('is_card_price', False),
        "promotion_dates": data.get('promotion_dates')
    }

async def get_hardened_context(browser, headless: bool = False):
    """Creates a browser context with hardened fingerprints to avoid bot detection."""
    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={"width": 1920, "height": 1080},
        locale="en-ZA",
        timezone_id="Africa/Johannesburg",
        extra_http_headers={
            "Accept-Language": "en-ZA,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Sec-Ch-Ua": '"Not-A.Brand";v="99", "Chromium";v="124", "Google Chrome";v="124"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1"
        }
    )
    return context

async def get_stealthy_page(context):
    """Creates a new page with playwright-stealth and manual overrides."""
    page = await context.new_page()

    try:
        import playwright_stealth
        if hasattr(playwright_stealth, 'Stealth'):
            await playwright_stealth.Stealth().apply_stealth_async(page)
        elif hasattr(playwright_stealth, 'stealth_async'):
            await playwright_stealth.stealth_async(page)
        elif hasattr(playwright_stealth, 'stealth'):
            # In some versions stealth is a function, in others it might be a module
            if callable(playwright_stealth.stealth):
                res = playwright_stealth.stealth(page)
                if asyncio.iscoroutine(res):
                    await res
    except Exception as e:
        logger.warning(f"Could not apply playwright-stealth: {e}")

    # Additional manual overrides to further mask automation
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-ZA', 'en']});
    """)
    return page

async def scrape_product(page, url: str) -> bool:
    logger.info(f"Scraping product: {url}")

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        await page.wait_for_timeout(3000)

        data = await page.evaluate("""() => {
            const nutritionText = [];
            const nutSelectors = ['.nutrition-table', '.product-nutrition-table', '.pdp__product-information', 'table'];
            for (const sel of document.querySelectorAll(nutSelectors.join(','))) {
                if (sel.innerText.toLowerCase().includes('per 100')) {
                    nutritionText.push(sel.innerText);
                }
            }

            const imageSources = new Set();
            if (window.insider_object && window.insider_object.product && window.insider_object.product.product_image_url) {
                imageSources.add(window.insider_object.product.product_image_url);
            }

            // Targeted selectors for product images and thumbnails
            const imageSelectors = [
                '.pdp__image img',
                '.pdp__thumbnails img',
                '.pdp__image [data-zoom-image]',
                '.pdp__thumbnails [data-zoom-image]',
                '.product-images img',
                '.product-gallery img'
            ];
            const blacklist = ['facebook', 'twitter', 'tiktok', 'instagram', 'youtube', 'linkedin', 'whatsapp', 'logo', 'icon', 'banner', 'promotion', 'liquor', 'header', 'footer'];

            const processElement = (el) => {
                const src = el.getAttribute('src') || '';
                const original = el.getAttribute('data-original-src') || '';
                const zoom = el.getAttribute('data-zoom-image') || '';
                const dataSrc = el.getAttribute('data-src') || '';

                [src, original, zoom, dataSrc].forEach(url => {
                    if (url && (url.includes('/medias/') || url.includes('/products/')) && !url.includes('error.png')) {
                        const lowUrl = url.toLowerCase();
                        // Filter out common UI elements/social icons
                        const isBlacklisted = blacklist.some(term => lowUrl.includes(term));
                        if (!isBlacklisted) imageSources.add(url);
                    }
                });
            };

            const targetElements = document.querySelectorAll(imageSelectors.join(','));
            if (targetElements.length > 0) {
                targetElements.forEach(processElement);
            } else {
                // Fallback if no specific containers found
                document.querySelectorAll('img, [data-zoom-image], [data-original-src]').forEach(processElement);
            }

            const getPriceText = (selector) => {
                const el = document.querySelector(selector);
                return el ? el.innerText.trim() : null;
            };

            const cardPriceEl = Array.from(document.querySelectorAll('.pdp-main-details__price, .special-now-price')).find(el => {
                const next = el.nextElementSibling;
                return next && next.innerText.toUpperCase().includes('WITH CARD');
            });
            const promoDates = document.querySelector('.pdp-main-details__promotion-dates, .product-promotion__dates')?.innerText.trim();

            return {
                name: document.querySelector('h1, .pdp-main-details__name')?.innerText.trim(),
                price_now_raw: getPriceText('.special-now-price, .price-now, .pdp-main-details__price'),
                price_was_raw: getPriceText('.special-was-price, .price-was, .pdp-main-details__price-was'),
                is_card_price: !!cardPriceEl || document.body.innerText.includes('WITH CARD'),
                promotion_dates: promoDates || Array.from(document.querySelectorAll('*')).find(el => el.innerText && el.innerText.includes('Valid until'))?.innerText.trim(),
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
            return "skipped"

        os.makedirs(f"{product_dir}/images", exist_ok=True)

        prices = extract_price_from_page(data)

        image_filenames = []
        seen_urls = set()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }

        logger.info(f"Found {len(data.get('images', []))} candidate images")
        for img_url in data.get('images', []):
            if not img_url or img_url in seen_urls: continue
            seen_urls.add(img_url)

            full_img_url = urljoin(BASE_URL, img_url)
            try:
                img_response = requests.get(full_img_url, headers=headers, timeout=10)
                if img_response.status_code == 200:
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

        nutrition_md = ""
        raw_nut = data.get('nutrition_raw', '')
        if raw_nut:
            per_100 = ""
            per_serving = ""
            if "per 100" in raw_nut.lower():
                parts = re.split(r'per serving', raw_nut, flags=re.IGNORECASE)
                per_100 = parts[0]
                if len(parts) > 1:
                    per_serving = parts[1]

                nutrient_pattern = r'([\w\s•-]+?):?\s+([\d\.]+\s*[kKjJgGmM]+)'
                nutrients_100 = re.findall(nutrient_pattern, per_100)
                nutrients_serving = re.findall(nutrient_pattern, per_serving)

                table_data = {}
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
                lines = [l.strip() for l in raw_nut.split('\n') if l.strip()]
                if lines:
                    nutrition_md = "\n## Nutrition Information\n" + "\n".join([f"- {l}" for l in lines])

        was_price_line = f"\n- **Was**: R{prices['was_price']}" if prices['was_price'] else ""
        card_price_extra = " (WITH CARD)" if prices.get('is_card_price') else ""
        valid_line = f"\n- **Validity**: {prices.get('promotion_dates')}" if prices.get('promotion_dates') else ""

        images_list = "\n".join([f"- images/{fn}" for fn in image_filenames])

        card_content = f"""# {name}

## Price
- **Current Price**: R{prices['current_price'] or 'N/A'}{card_price_extra}{was_price_line}{valid_line}

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
        return "scraped"

    except Exception as e:
        logger.error(f"Error scraping {url}: {e}")
        failure_logger.error(f"Exception for {url}: {e}")
        return False

async def main():
    parser = argparse.ArgumentParser(description="Shoprite Product Scraper")
    parser.add_argument("--category", type=str, default="All-Departments", help="Category to scrape")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of products")
    parser.add_argument("--headless", action="store_true", default=False,
                        help="Run browser in headless mode (default: False for local, set True for CI)")
    args = parser.parse_args()

    source_url = get_source_url()
    default_url = source_url if source_url else "https://www.shoprite.co.za/c-2256/All-Departments"

    if args.category == "All-Departments":
        cat_url = default_url
    elif args.category.startswith("http"):
        cat_url = args.category
    else:
        cat_url = f"https://www.shoprite.co.za/c/{args.category}"
    if args.category.startswith("http"): cat_url = args.category

    logger.info(f"Running in {'headless' if args.headless else 'headed'} mode")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=args.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
        )
        context = await get_hardened_context(browser, headless=args.headless)
        page = await get_stealthy_page(context)

        await asyncio.sleep(random.uniform(1, 3))
        logger.info(f"Establishing cookies via {BASE_URL}")
        try:
            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(2, 4))
        except Exception as e:
            logger.warning(f"Failed to load home page: {e}")

        logger.info(f"Fetching category page: {cat_url}")
        try:
            response = None
            for attempt in range(3):
                try:
                    response = await page.goto(cat_url, wait_until="domcontentloaded", timeout=60000)
                    if response and response.status == 200:
                        break
                    logger.warning(f"Attempt {attempt + 1} failed with status: {response.status if response else 'No Response'}")
                    await asyncio.sleep(random.uniform(2, 5))
                except Exception as e:
                    logger.warning(f"Attempt {attempt + 1} exception: {e}")
                    await asyncio.sleep(random.uniform(2, 5))

            logger.info(f"Final response status: {response.status if response else 'No Response'}")

            await page.wait_for_timeout(5000)

            page_info = await page.evaluate("""() => {
                const text = document.body.innerText.toLowerCase();
                return {
                    title: document.title,
                    isBlocked: text.includes('oh no!') || text.includes('access denied') || text.includes('blocked') || text.includes('captcha'),
                    isMaintenance: text.includes('maintenance') || text.includes('scheduled update'),
                    linkCount: document.querySelectorAll('a').length,
                    htmlSnippet: document.body.innerHTML.substring(0, 500)
                };
            }""")

            logger.info(f"Page title: {page_info['title']}")
            if page_info['isBlocked']:
                logger.error("Detected bot blocking or access denial page.")
            if page_info['isMaintenance']:
                logger.warning("Site appears to be in maintenance mode.")

            if "/p/" in cat_url:
                product_links = [cat_url]
            else:
                product_links = await page.evaluate("""() => {
                    const links = new Set();
                    document.querySelectorAll('a[href*="/p/"]').forEach(a => links.add(a.href));

                    if (links.size === 0) {
                       document.querySelectorAll('.product-item a, .item-product a').forEach(a => {
                           if (a.href && !a.href.includes('#')) links.add(a.href);
                       });
                    }
                    return Array.from(links);
                }""")

            if len(product_links) == 0:
                logger.warning(f"No product links found. Total <a> tags on page: {page_info['linkCount']}")
                logger.debug(f"Page HTML Snippet: {page_info['htmlSnippet']}")
                screenshot_path = f".rokct/agent/logs/category_debug_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                await page.screenshot(path=screenshot_path)
                logger.info(f"Saved debug screenshot to {screenshot_path}")

            logger.info(f"Found {len(product_links)} potential products on page")
            scraped_count = 0
            for link in product_links:
                if args.limit > 0 and scraped_count >= args.limit:
                    break

                status = await scrape_product(page, link)
                if status == "scraped":
                    scraped_count += 1
                    await asyncio.sleep(1)
                elif status == "skipped":
                    # Don't increment scraped_count, don't sleep (or sleep less)
                    continue
                else:
                    # failure
                    await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Failed to scrape category {cat_url}: {e}")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
