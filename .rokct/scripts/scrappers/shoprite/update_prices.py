import os
import sys
import asyncio
import logging
import re
from typing import Optional, Dict
from playwright.async_api import async_playwright

sys.path.append(os.path.dirname(__file__))
from shoprite_scraper import extract_price_from_page, JS_PRICE_EXTRACTION, get_hardened_context, get_stealthy_page

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

async def update_price(page, card_path: str):
    with open(card_path, 'r') as f:
        content = f.read()

    match = re.search(r'- \*\*Source\*\*: (https://www\.shoprite\.co\.za/.*)', content)
    if not match:
        logger.warning(f"Could not find source URL in {card_path}")
        return

    url = match.group(1).strip()
    logger.info(f"Updating price for: {url}")

    try:
        await page.goto(url, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(2000)

        data = await page.evaluate(JS_PRICE_EXTRACTION)

        prices = extract_price_from_page(data)
        current_price = prices['current_price']
        was_price = prices['was_price']

        if not current_price:
            logger.warning(f"Could not extract current price for {url}")
            return

        price_section = f"## Price\n- **Current Price**: R{current_price}"
        if was_price:
            price_section += f"\n- **Was**: R{was_price}"

        new_content = re.sub(r'## Price\n(?:- \*\*Current Price\*\*: R.*\n?(?:- \*\*Was\*\*: R.*\n?)?)', price_section + "\n", content)

        if new_content != content:
            with open(card_path, 'w') as f:
                f.write(new_content)
            logger.info(f"Successfully updated price in {card_path}")
        else:
            logger.info(f"Price unchanged for {card_path}")

    except Exception as e:
        logger.error(f"Error updating price for {url}: {e}")

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,  # price updates are safe to run headless once initial scrape works
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
        )
        context = await get_hardened_context(browser)
        page = await get_stealthy_page(context)

        products_root = "products"
        if not os.path.exists(products_root):
            logger.info("No products folder found.")
            await browser.close()
            return

        cards = []
        for root, dirs, files in os.walk(products_root):
            for file in files:
                if file.endswith("_card.md"):
                    cards.append(os.path.join(root, file))

        logger.info(f"Found {len(cards)} product cards to update.")
        for card_path in cards:
            await update_price(page, card_path)
            await asyncio.sleep(1)

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
