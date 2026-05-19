import random
import os
import sys
import asyncio
import logging
import re
import argparse
from typing import Optional, Dict
from playwright.async_api import async_playwright

sys.path.append(os.path.dirname(__file__))
from scraper import (
    extract_price_from_page,
    JS_PRICE_EXTRACTION,
    get_hardened_context,
    get_stealthy_page,
)

# Setup logging
os.makedirs(".rokct/agent/logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(".rokct/agent/logs/shoprite_scraper.log", mode="w"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


def maintain_images():
    products_root = "products"
    if not os.path.exists(products_root):
        logger.info("No products folder found for image maintenance.")
        return

    logger.info("Starting image maintenance...")
    for root, dirs, files in os.walk(products_root):
        for file in files:
            if file.endswith("_card.md"):
                card_path = os.path.join(root, file)
                product_dir = root

                with open(card_path, "r") as f:
                    content = f.read()

                # Extract filenames listed under ## Images
                image_section = re.search(r"## Images\n((?:- images/.*\n?)*)", content)
                listed_images = []
                if image_section:
                    listed_images = [
                        line.strip().replace("- ", "")  # gives "images/filename.jpg"
                        for line in image_section.group(1).strip().split("\n")
                        if line.strip().startswith("- images/")
                    ]

                images_dir = os.path.join(product_dir, "images")
                if not os.path.exists(images_dir):
                    if listed_images:
                        logger.warning(
                            f"Images directory missing but images listed in {card_path}"
                        )
                    continue

                # Deletion logic
                for actual_file in os.listdir(images_dir):
                    relative = f"images/{actual_file}"
                    if relative not in listed_images:
                        os.remove(os.path.join(images_dir, actual_file))
                        logger.info(f"Deleted unlisted image: {relative}")

                # Warn if a listed image is missing from disk
                for listed_image in listed_images:
                    if not os.path.exists(os.path.join(product_dir, listed_image)):
                        logger.warning(
                            f"Listed image not found on disk: {listed_image} (in {card_path})"
                        )

    # Second pass: Ensure Is Platform field exists in all cards
    logger.info("Ensuring 'Is Platform' field exists in all cards...")
    for root, dirs, files in os.walk(products_root):
        for file in files:
            if file.endswith("_card.md"):
                card_path = os.path.join(root, file)
                with open(card_path, "r") as f:
                    content = f.read()

                if "- **Is Platform**:" not in content:
                    # Ensure it's added to the ## Meta section
                    if "## Meta" in content:
                        new_content = content.replace(
                            "## Meta", "## Meta\n- **Is Platform**: false"
                        )
                    else:
                        new_content = (
                            content.rstrip() + "\n\n## Meta\n- **Is Platform**: false\n"
                        )

                    with open(card_path, "w", encoding="utf-8") as f:
                        f.write(new_content)
                    logger.info(f"Added 'Is Platform: false' to {card_path}")

    logger.info("Image maintenance complete.")


async def update_price(context, card_path: str):
    with open(card_path, "r") as f:
        content = f.read()

    match = re.search(r"- \*\*Source\*\*: (https://www\.shoprite\.co\.za/.*)", content)
    if not match:
        logger.warning(f"Could not find source URL in {card_path}")
        return

    url = match.group(1).strip()
    logger.info(f"Updating price for: {url}")

    page = None
    try:
        page = await get_stealthy_page(context)
        try:
            response = await page.goto(
                url, wait_until="domcontentloaded", timeout=60000
            )
            status = response.status if response else "No Response"

            if not response or status != 200:
                logger.error(
                    f"Failed to load {url} (Status: {status}). Skipping product."
                )
                return
        except Exception as e:
            logger.error(f"Exception loading {url}: {e}")
            return

        await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        await page.wait_for_timeout(3000)

        data = await page.evaluate(JS_PRICE_EXTRACTION)

        prices = extract_price_from_page(data)
        current_price = prices["current_price"]
        was_price = prices["was_price"]

        if not current_price:
            logger.warning(f"Could not extract current price for {url}")
            return

        price_section = f"## Price\n- **Current Price**: R{current_price}"
        if prices.get("is_card_price"):
            price_section += " (WITH CARD)"
        if was_price:
            price_section += f"\n- **Was**: R{was_price}"
        if prices.get("promotion_dates"):
            price_section += f"\n- **Validity**: {prices.get('promotion_dates')}"

        # Improved regex to catch all possible lines in the Price section
        new_content = re.sub(
            r"## Price\n(?:- .*\n?)*\n(?=## Description)",
            price_section + "\n\n",
            content,
        )

        if new_content != content:
            with open(card_path, "w") as f:
                f.write(new_content)
            logger.info(f"Successfully updated price in {card_path}")
        else:
            logger.info(f"Price unchanged for {card_path}")

    except Exception as e:
        logger.error(f"Error updating price for {url}: {e}")
    finally:
        if page:
            await page.close()


async def main():
    parser = argparse.ArgumentParser(description="Maintain PriceGrid product data.")
    parser.add_argument(
        "--images-only",
        action="store_true",
        help="Only perform image maintenance, skip price updates.",
    )
    args = parser.parse_args()

    if not args.images_only:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,  # price updates are safe to run headless once initial scrape works
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            context = await get_hardened_context(browser)

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

            logger.info("Establishing cookies via home page...")
            temp_page = await get_stealthy_page(context)
            try:
                await temp_page.goto(
                    "https://www.shoprite.co.za",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                await asyncio.sleep(random.uniform(2, 4))
            except Exception as e:
                logger.warning(f"Failed to load home page: {e}")
            finally:
                await temp_page.close()

            logger.info(f"Found {len(cards)} product cards to update.")
            for card_path in cards:
                await update_price(context, card_path)
                await asyncio.sleep(random.uniform(3, 7))

            await browser.close()

    # maintain_images() runs after prices, or alone if --images-only is set
    maintain_images()


if __name__ == "__main__":
    asyncio.run(main())
