# priceGRID

priceGRID is a robust product data repository and scraping platform designed to maintain a comprehensive database of retail products, pricing, and metadata. It serves as a central data source for downstream applications, providing structured, validated product information via a static publication pipeline.

## Overview

The repository implements an end-to-end data lifecycle:
1.  **Scraping**: Automated scripts extract product details, images, and pricing from retail sources.
2.  **Maintenance**: Regular updates ensure pricing remains current and orphan images are removed.
3.  **Publication**: A transformation pipeline converts Markdown-based product cards into optimized JSON indices for high-performance consumption.

## Key Features

-   **Automated Data Extraction**: Multi-stage scraping process capturing ingredients, allergens, benefits, and technical specifications.
-   **Hierarchical Category Trees**: Automatically generated category structures with product counts.
-   **Optimized Indexing**: Provides specialized indices like `meta.json`, `platform_products.json`, and `categories.json` for efficient client-side filtering and navigation.
-   **Data Integrity**: Content hashing ensures efficient synchronization with downstream systems.
-   **Image Optimization**: Maintains a clean filesystem by identifying and removing unused assets.

## Project Structure

-   `.rokct/scripts/scrappers/{source}/`: Source-specific scraper implementations and maintenance tools.
-   `.rokct/scripts/publisher/`: The publication engine that builds the static JSON database.
-   `products/`: Hierarchical storage of product markdown cards and their corresponding JSON data and images.
-   `published/`: Public-facing directory containing generated indices and documentation for API consumers.

## Usage

### Prerequisites

Install dependencies and set up the browser environment:
```bash
pip install -r requirements.txt
playwright install chromium
```

### Running the Pipeline

1.  **Scraping & Maintenance**:
    Execute the scrapers to fetch new data or update existing records.
    ```bash
    python3 .rokct/scripts/scrappers/{source}/scraper.py --category <URL>
    python3 .rokct/scripts/scrappers/{source}/maintain.py
    ```

2.  **Publication**:
    Generate the static JSON indices.
    ```bash
    python3 .rokct/scripts/publisher/publish.py
    ```

## Integration

priceGRID is designed to be consumed by a Frappe-based backend. The backend synchronizes with this repository's `published/` directory to serve data to Next.js and Flutter clients via a REST API.

For detailed integration instructions, refer to the [Next.js Integration Guide](published/NEXTJS_GUIDE.md).

## Branding & Compliance

All product cards and marketing materials are branded under **priceGRID Holdings**. Generic store names and branding are preserved in source fields where necessary for data accuracy, but the platform identity remains priceGRID.
