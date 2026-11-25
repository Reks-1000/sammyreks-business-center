import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import re
from typing import Any, Coroutine, Dict, List, Optional, TypeVar, Union
from urllib.parse import urlparse
import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

T = TypeVar("T")

router = APIRouter(prefix="/api/auto-stock", tags=["auto-stock"])

OFF_SEARCH_URL = "https://world.openfoodfacts.org/cgi/search.pl"
OFF_PRODUCT_URL = "https://world.openfoodfacts.org/api/v2/product/{barcode}.json"
WIKIMEDIA_API_URL = "https://commons.wikimedia.org/w/api.php"
PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"

USER_AGENT = "SammyreksBusinessCenter/3.0 (Calabar-Nigeria-POS)"

BASE_DIR = Path(__file__).resolve().parent
AUTO_STOCK_IMG_DIR = BASE_DIR / "static" / "uploads" / "auto_stock"
AUTO_STOCK_IMG_DIR.mkdir(parents=True, exist_ok=True)


def _run_coro_sync(coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine safely whether or not an event loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    def _worker() -> T:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_worker).result()


def sanitize_url(url: str) -> str:
    clean = str(url or "").strip()
    if not clean:
        return ""
    if clean.startswith("/static/"):
        return clean
    parsed = urlparse(clean)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return clean
    return ""


# ==============================================================================
# 1. NIGERIAN FMCG MASTER CATALOG (Exact Pack Ratios & Naira Benchmarks)
# ==============================================================================
NIGERIAN_FMCG_CATALOG: List[Dict[str, Any]] = [
    {
        "keywords": ["maltina", "maltina classic", "maltina can"],
        "name": "Maltina Classic Can 330ml (x24)",
        "category": "Beverages",
        "vendor_name": "Nigerian Breweries",
        "main_unit": "Carton",
        "sub_unit": "Pack",
        "retail_unit": "Can",
        "packs_per_carton": 4,
        "units_per_pack": 6,
        "cost_price_carton": 13200.0,
        "price_carton": 14400.0,
        "price_pack": 3700.0,
        "price_retail": 650.0,
        "description": "Rich non-alcoholic malt drink fortified with vitamins. Available per Carton (24 Cans), 6-Pack, or single Can.",
        "off_query": "Maltina malt drink",
        "image_query": "malt drink can studio photography",
    },
    {
        "keywords": ["amstel", "amstel malta"],
        "name": "Amstel Malta Can 330ml (x24)",
        "category": "Beverages",
        "vendor_name": "Nigerian Breweries",
        "main_unit": "Carton",
        "sub_unit": "Pack",
        "retail_unit": "Can",
        "packs_per_carton": 4,
        "units_per_pack": 6,
        "cost_price_carton": 13500.0,
        "price_carton": 14800.0,
        "price_pack": 3800.0,
        "price_retail": 650.0,
        "description": "Low-sugar premium malt beverage brewed with pure malted barley. Sold in Cartons, 6-Packs, or single Cans.",
        "off_query": "Amstel Malta",
        "image_query": "Amstel Malta can beverage",
    },
    {
        "keywords": ["sprite", "sprite 50cl"],
        "name": "Sprite PET Bottle 50cl (x12)",
        "category": "Beverages",
        "vendor_name": "Nigerian Bottling Company",
        "main_unit": "Pack",
        "sub_unit": "Half-Pack",
        "retail_unit": "Bottle",
        "packs_per_carton": 2,
        "units_per_pack": 6,
        "cost_price_carton": 4400.0,
        "price_carton": 5000.0,
        "price_pack": 2600.0,
        "price_retail": 450.0,
        "description": "Crisp lemon-lime sparkling soft drink (50cl PET). Fast-moving chilled refreshment for retail and events.",
        "off_query": "Sprite 50cl",
        "image_query": "Sprite bottle soft drink",
    },
    {
        "keywords": ["coca cola", "coke", "coca-cola 50cl", "coca-cola"],
        "name": "Coca-Cola PET Bottle 50cl (x12)",
        "category": "Beverages",
        "vendor_name": "Nigerian Bottling Company",
        "main_unit": "Pack",
        "sub_unit": "Half-Pack",
        "retail_unit": "Bottle",
        "packs_per_carton": 2,
        "units_per_pack": 6,
        "cost_price_carton": 4400.0,
        "price_carton": 5000.0,
        "price_pack": 2600.0,
        "price_retail": 450.0,
        "description": "Classic Coca-Cola 50cl PET bottle pack of 12. High-turnover beverage for stores, restaurants, and events.",
        "off_query": "Coca-Cola 50cl",
        "image_query": "Coca Cola bottle product",
    },
    {
        "keywords": ["fanta", "fanta orange"],
        "name": "Fanta Orange PET 50cl (x12)",
        "category": "Beverages",
        "vendor_name": "Nigerian Bottling Company",
        "main_unit": "Pack",
        "sub_unit": "Half-Pack",
        "retail_unit": "Bottle",
        "packs_per_carton": 2,
        "units_per_pack": 6,
        "cost_price_carton": 4400.0,
        "price_carton": 5000.0,
        "price_pack": 2600.0,
        "price_retail": 450.0,
        "description": "Fruity orange carbonated soft drink in 50cl PET bottles (12 bottles per pack).",
        "off_query": "Fanta Orange 50cl",
        "image_query": "Fanta orange bottle drink",
    },
    {
        "keywords": ["hollandia", "hollandia yoghurt", "hollandia evap"],
        "name": "Hollandia Yoghurt Plain Sweetened 1L (x10)",
        "category": "Dairy & Breakfast",
        "vendor_name": "CHI Limited",
        "main_unit": "Carton",
        "sub_unit": "Half-Carton",
        "retail_unit": "Pack",
        "packs_per_carton": 2,
        "units_per_pack": 5,
        "cost_price_carton": 13500.0,
        "price_carton": 14800.0,
        "price_pack": 7500.0,
        "price_retail": 1550.0,
        "description": "Creamy, nutritious 1-litre Hollandia drinking yoghurt. Ideal for family breakfast and chilled retail.",
        "off_query": "Hollandia yoghurt",
        "image_query": "Hollandia yoghurt carton",
    },
    {
        "keywords": ["indomie", "indomie chicken", "indomie super", "indomie onion"],
        "name": "Indomie Instant Noodles Chicken 70g (x40)",
        "category": "Noodles & Pasta",
        "vendor_name": "Dufil Prima Foods",
        "main_unit": "Carton",
        "sub_unit": "Half-Carton",
        "retail_unit": "Pack",
        "packs_per_carton": 2,
        "units_per_pack": 20,
        "cost_price_carton": 8800.0,
        "price_carton": 9600.0,
        "price_pack": 4900.0,
        "price_retail": 260.0,
        "description": "Nigeria's #1 instant noodles (70g standard pack, 40 packs per carton). Ready in 3 minutes.",
        "off_query": "Indomie instant noodles",
        "image_query": "Indomie instant noodles pack",
    },
    {
        "keywords": ["golden penny spaghetti", "spaghetti", "golden penny pasta"],
        "name": "Golden Penny Spaghetti 500g (x20)",
        "category": "Noodles & Pasta",
        "vendor_name": "Flour Mills of Nigeria",
        "main_unit": "Carton",
        "sub_unit": "Half-Carton",
        "retail_unit": "Pack",
        "packs_per_carton": 2,
        "units_per_pack": 10,
        "cost_price_carton": 14600.0,
        "price_carton": 15800.0,
        "price_pack": 8000.0,
        "price_retail": 850.0,
        "description": "Non-sticky durum wheat spaghetti fortified with vitamins (500g x 20 packs per carton).",
        "off_query": "Golden Penny spaghetti",
        "image_query": "spaghetti pasta package",
    },
    {
        "keywords": ["peak milk", "peak refill", "peak evaporated"],
        "name": "Peak Evaporated Full Cream Milk 160g (x24)",
        "category": "Dairy & Breakfast",
        "vendor_name": "FrieslandCampina WAMCO",
        "main_unit": "Carton",
        "sub_unit": "Tray",
        "retail_unit": "Tin",
        "packs_per_carton": 4,
        "units_per_pack": 6,
        "cost_price_carton": 15800.0,
        "price_carton": 17200.0,
        "price_pack": 4400.0,
        "price_retail": 750.0,
        "description": "Rich, creamy Peak evaporated milk fortified with 28 vitamins & minerals (24 tins per carton).",
        "off_query": "Peak evaporated milk",
        "image_query": "Peak evaporated milk tin",
    },
    {
        "keywords": ["milo", "nestle milo", "milo refill"],
        "name": "Nestle Milo Activ-Go Refill 400g (x12)",
        "category": "Dairy & Breakfast",
        "vendor_name": "Nestle Nigeria",
        "main_unit": "Carton",
        "sub_unit": "Half-Carton",
        "retail_unit": "Pack",
        "packs_per_carton": 2,
        "units_per_pack": 6,
        "cost_price_carton": 25200.0,
        "price_carton": 27000.0,
        "price_pack": 13600.0,
        "price_retail": 2350.0,
        "description": "Nutritious chocolate malt energy food drink for active mornings (400g refill pouch).",
        "off_query": "Nestle Milo",
        "image_query": "Nestle Milo chocolate drink",
    },
    {
        "keywords": ["golden morn"],
        "name": "Nestle Golden Morn Maize & Soya 500g (x12)",
        "category": "Dairy & Breakfast",
        "vendor_name": "Nestle Nigeria",
        "main_unit": "Carton",
        "sub_unit": "Half-Carton",
        "retail_unit": "Pack",
        "packs_per_carton": 2,
        "units_per_pack": 6,
        "cost_price_carton": 17400.0,
        "price_carton": 18800.0,
        "price_pack": 9500.0,
        "price_retail": 1650.0,
        "description": "Wholesome maize and soya cereal enriched with iron and vitamins. Just add water or milk.",
        "off_query": "Golden Morn cereal",
        "image_query": "Golden Morn cereal pack",
    },
    {
        "keywords": ["hypo", "hypo bleach", "hypo sachet"],
        "name": "Hypo Bleach Super Sachet 50ml (x60)",
        "category": "Soaps & Detergents",
        "vendor_name": "Hypo Homecare Products",
        "main_unit": "Carton",
        "sub_unit": "Roll",
        "retail_unit": "Sachet",
        "packs_per_carton": 6,
        "units_per_pack": 10,
        "cost_price_carton": 4200.0,
        "price_carton": 4800.0,
        "price_pack": 850.0,
        "price_retail": 100.0,
        "description": "Fast-acting germ-killing and stain-removing bleach sachets for laundry and home sanitation.",
        "off_query": "Hypo bleach",
        "image_query": "laundry bleach bottle cleaning",
    },
    {
        "keywords": ["sunlight", "sunlight detergent", "viva", "ariel", "omo"],
        "name": "Sunlight 2-in-1 Washing Powder 850g (x10)",
        "category": "Soaps & Detergents",
        "vendor_name": "Unilever Nigeria",
        "main_unit": "Carton",
        "sub_unit": "Half-Carton",
        "retail_unit": "Pack",
        "packs_per_carton": 2,
        "units_per_pack": 5,
        "cost_price_carton": 16500.0,
        "price_carton": 18000.0,
        "price_pack": 9100.0,
        "price_retail": 1900.0,
        "description": "Deep-cleansing laundry detergent with long-lasting sensational freshness.",
        "off_query": "Sunlight washing powder",
        "image_query": "washing powder detergent pack",
    },
    {
        "keywords": ["dettol", "dettol soap", "premier", "lux"],
        "name": "Dettol Antibacterial Bathing Soap 110g (x48)",
        "category": "Soaps & Detergents",
        "vendor_name": "Reckitt Nigeria",
        "main_unit": "Carton",
        "sub_unit": "Dozen",
        "retail_unit": "Tablet",
        "packs_per_carton": 4,
        "units_per_pack": 12,
        "cost_price_carton": 24000.0,
        "price_carton": 26400.0,
        "price_pack": 6700.0,
        "price_retail": 600.0,
        "description": "Trusted daily antibacterial protection bar soap for healthy family skin hygiene.",
        "off_query": "Dettol soap",
        "image_query": "Dettol antibacterial bar soap",
    },
]

# ==============================================================================
# 2. AUTOMATIC CATEGORY CLASSIFIER (Keyword -> Store Category)
# ==============================================================================
CATEGORY_RULES: List[tuple[str, List[str]]] = [
    ("Beverages", ["malt", "maltina", "amstel", "coke", "coca", "fanta", "sprite", "pepsi", "mirinda", "7up", "chivita", "chi exotic", "fearless", "predator", "monster", "lucozade", "ribena", "water", "eva", "aquafina", "drink", "juice", "soda", "wine"]),
    ("Noodles & Pasta", ["indomie", "noodle", "spaghetti", "macaroni", "pasta", "golden penny", "minimie", "dangote", "chikki", "tummy"]),
    ("Dairy & Breakfast", ["milk", "peak", "three crowns", "cowbell", "dano", "loya", "hollandia", "yoghurt", "milo", "bournvita", "ovaltine", "golden morn", "cornflakes", "kellogg", "quaker", "oats", "tea", "lipton", "top tea", "coffee", "nescafe", "sugar"]),
    ("Soaps & Detergents", ["soap", "detergent", "hypo", "bleach", "omo", "ariel", "sunlight", "viva", "klin", "good mama", "dettol", "premier", "lux", "joy", "morning fresh", "mama lemon", "harpic", "izall"]),
    ("Biscuits & Snacks", ["biscuit", "cabin", "digestive", "coaster", "shortbread", "gala", "sausage", "chin chin", "plantain chips", "pringles", "cheese balls", "minimie chinchin", "popcorn", "wafer", "sweet", "tom tom", "buttermint"]),
    ("Cooking & Groceries", ["rice", "beans", "garri", "semovita", "semolina", "wheat", "oil", "power oil", "kings", "mamador", "devon", "maggi", "knorr", "royco", "seasoning", "curry", "thyme", "tomato", "gino", "sonia", "tasty tom", "salt", "flour", "bread"]),
]


def classify_product_category(product_name: str, fallback: str = "General") -> str:
    lower = (product_name or "").strip().lower()
    if not lower:
        return fallback
    for category_name, keywords in CATEGORY_RULES:
        for kw in keywords:
            if re.search(rf"\b{re.escape(kw.lower())}\b", lower):
                return category_name
    return fallback


def match_nigerian_catalog(query_name: str) -> Optional[Dict[str, Any]]:
    q = re.sub(r"\s+", " ", (query_name or "").strip().lower())
    if len(q) < 3:
        return None
    best_match = None
    best_score = 0
    for entry in NIGERIAN_FMCG_CATALOG:
        for kw in entry["keywords"]:
            kw_clean = kw.lower()
            if re.search(rf"\b{re.escape(kw_clean)}\b", q) or (
                len(q) >= 4 and kw_clean.startswith(q)
            ):
                score = len(kw_clean)
                if score > best_score:
                    best_score = score
                    best_match = entry
    return best_match


# ==============================================================================
# 3. HIGH-RESOLUTION IMAGE SEARCH & LOCAL/CLOUD IMPORT PIPELINE
# ==============================================================================
def _upgrade_off_image_url(url: str) -> str:
    """Upgrade OpenFoodFacts thumbnail suffixes (.200.jpg, .400.jpg) to .full.jpg."""
    clean = sanitize_url(url)
    if not clean:
        return ""
    if "openfoodfacts.org/images/products/" in clean:
        clean = re.sub(r"\.(100|200|400)\.(jpg|jpeg|png|webp)$", r".full.\2", clean, flags=re.I)
    return clean


def _score_off_product(product_obj: Dict[str, Any], query_words: set[str]) -> tuple[int, str]:
    """Score OpenFoodFacts hits so clean front-of-pack photos win over blurry thumbnails."""
    pname = str(product_obj.get("product_name") or "").strip()
    if not pname:
        return (-1, "")
    pname_words = {w for w in re.findall(r"[a-z0-9]{3,}", pname.lower())}
    overlap = len(query_words & pname_words)
    if query_words and overlap == 0:
        return (-1, "")

    img_front = _upgrade_off_image_url(str(product_obj.get("image_front_url") or ""))
    img_main = _upgrade_off_image_url(str(product_obj.get("image_url") or ""))
    chosen = img_front or img_main
    if not chosen:
        return (-1, "")

    score = overlap * 20
    if img_front:
        score += 15
    if ".full." in chosen:
        score += 10
    selected_imgs = product_obj.get("selected_images", {}).get("front", {}).get("display", {})
    if isinstance(selected_imgs, dict) and selected_imgs:
        first_display = next(iter(selected_imgs.values()), "")
        if sanitize_url(first_display):
            chosen = _upgrade_off_image_url(first_display)
            score += 12
    return (score, chosen)


async def fetch_off_product_by_name(client: httpx.AsyncClient, search_query: str) -> Dict[str, Any]:
    params = {
        "search_terms": search_query,
        "search_simple": 1,
        "action": "process",
        "json": 1,
        "page_size": 10,
        "fields": "code,product_name,brands,categories,image_front_url,image_url,selected_images,quantity,generic_name",
    }
    query_words = {w for w in re.findall(r"[a-z0-9]{3,}", search_query.lower())}
    try:
        resp = await client.get(OFF_SEARCH_URL, params=params, headers={"User-Agent": USER_AGENT})
        if resp.status_code != 200:
            return {}
        data = resp.json()
        products = data.get("products") or []
        best_prod = None
        best_img = ""
        best_score = -1

        for p in products:
            sc, img = _score_off_product(p, query_words)
            if sc > best_score and img:
                best_score = sc
                best_img = img
                best_prod = p

        if best_prod and best_img:
            return {
                "name": str(best_prod.get("product_name") or "").strip(),
                "brand": str(best_prod.get("brands") or "").split(",")[0].strip(),
                "image_url": best_img,
                "quantity": str(best_prod.get("quantity") or "").strip(),
                "generic_name": str(best_prod.get("generic_name") or "").strip(),
            }
    except Exception:
        pass
    return {}


async def fetch_pexels_clean_image(client: httpx.AsyncClient, query: str) -> str:
    """Optional high-res commercial photo search if PEXELS_API_KEY is set in .env."""
    api_key = os.getenv("PEXELS_API_KEY", "").strip()
    if not api_key:
        return ""
    try:
        resp = await client.get(
            PEXELS_SEARCH_URL,
            params={"query": query, "per_page": 3, "orientation": "square"},
            headers={"Authorization": api_key, "User-Agent": USER_AGENT},
        )
        if resp.status_code == 200:
            photos = resp.json().get("photos") or []
            if photos:
                src = photos[0].get("src") or {}
                return sanitize_url(src.get("large") or src.get("medium") or "")
    except Exception:
        pass
    return ""


async def fetch_wikimedia_clean_image(client: httpx.AsyncClient, query: str) -> str:
    """Free high-resolution fallback via Wikimedia Commons API (no key required)."""
    try:
        params = {
            "action": "query",
            "generator": "search",
            "gsrsearch": f"filetype:bitmap {query}",
            "gsrnamespace": 6,
            "gsrlimit": 5,
            "prop": "imageinfo",
            "iiprop": "url|size|mime",
            "iiurlwidth": 800,
            "format": "json",
        }
        resp = await client.get(WIKIMEDIA_API_URL, params=params, headers={"User-Agent": USER_AGENT})
        if resp.status_code != 200:
            return ""
        pages = (resp.json().get("query") or {}).get("pages") or {}
        best_url = ""
        best_width = 0
        for page in pages.values():
            infos = page.get("imageinfo") or []
            if not infos:
                continue
            info = infos[0]
            mime = str(info.get("mime") or "")
            if mime not in {"image/jpeg", "image/png", "image/webp"}:
                continue
            width = int(info.get("width") or 0)
            height = int(info.get("height") or 0)
            # Reject tiny icons or ultra-wide banners
            if width < 350 or height < 250 or (width / max(height, 1)) > 2.4:
                continue
            candidate = sanitize_url(info.get("thumburl") or info.get("url") or "")
            if candidate and width > best_width:
                best_width = width
                best_url = candidate
        return best_url
    except Exception:
        return ""


async def import_image_to_store(client: httpx.AsyncClient, remote_url: str, slug_hint: str) -> str:
    """
    Download a verified clean image into /static/uploads/auto_stock/ so it loads fast,
    never breaks from external hotlink blocks, and rejects tiny/corrupt images.
    """
    clean_remote = sanitize_url(remote_url)
    if not clean_remote or clean_remote.startswith("/static/"):
        return clean_remote

    safe_slug = re.sub(r"[^a-z0-9]+", "_", slug_hint.lower()).strip("_")[:40] or "item"
    url_hash = hashlib.sha1(clean_remote.encode("utf-8")).hexdigest()[:12]

    # Reuse already-imported file if cached on disk
    for ext in ("jpg", "png", "webp"):
        existing = AUTO_STOCK_IMG_DIR / f"{safe_slug}_{url_hash}.{ext}"
        if existing.exists() and existing.stat().st_size > 4096:
            return f"/static/uploads/auto_stock/{existing.name}"

    try:
        resp = await client.get(
            clean_remote,
            headers={"User-Agent": USER_AGENT, "Accept": "image/webp,image/jpeg,image/png,*/*"},
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return ""
        content = resp.content
        # Reject tiny broken placeholders (< 5 KB)
        if len(content) < 5120:
            return ""

        ctype = str(resp.headers.get("content-type") or "").lower()
        if "png" in ctype or clean_remote.lower().endswith(".png"):
            ext = "png"
        elif "webp" in ctype or clean_remote.lower().endswith(".webp"):
            ext = "webp"
        else:
            ext = "jpg"

        filename = f"{safe_slug}_{url_hash}.{ext}"
        target_path = AUTO_STOCK_IMG_DIR / filename
        target_path.write_bytes(content)
        return f"/static/uploads/auto_stock/{filename}"
    except Exception:
        return clean_remote


async def resolve_nice_product_image(
    client: httpx.AsyncClient,
    product_title: str,
    image_query: str,
    off_image_url: str,
) -> str:
    """Pick the cleanest, clearest image from Pexels -> OpenFoodFacts Full -> Wikimedia, then import locally."""
    candidates: List[str] = []

    pexels_url = await fetch_pexels_clean_image(client, image_query)
    if pexels_url:
        candidates.append(pexels_url)

    if off_image_url:
        candidates.append(_upgrade_off_image_url(off_image_url))

    wiki_url = await fetch_wikimedia_clean_image(client, image_query)
    if wiki_url:
        candidates.append(wiki_url)

    for candidate in candidates:
        imported = await import_image_to_store(client, candidate, product_title)
        if imported:
            return imported
    return ""


async def build_enriched_product(
    client: httpx.AsyncClient,
    raw_name: str,
    default_stock: float,
    location: str,
    fetch_video: bool = False,
) -> Dict[str, Any]:
    clean_query = raw_name.strip()
    catalog_hit = match_nigerian_catalog(clean_query)
    off_query = catalog_hit["off_query"] if catalog_hit else clean_query
    off_data = await fetch_off_product_by_name(client, off_query)

    if catalog_hit:
        final_name = catalog_hit["name"]
        category = catalog_hit["category"]
        vendor_name = catalog_hit["vendor_name"]
        main_unit = catalog_hit["main_unit"]
        sub_unit = catalog_hit["sub_unit"]
        retail_unit = catalog_hit["retail_unit"]
        packs_per_carton = catalog_hit["packs_per_carton"]
        units_per_pack = catalog_hit["units_per_pack"]
        cost_carton = catalog_hit["cost_price_carton"]
        price_carton = catalog_hit["price_carton"]
        price_pack = catalog_hit["price_pack"]
        price_retail = catalog_hit["price_retail"]
        description = catalog_hit["description"]
        image_query = catalog_hit.get("image_query") or final_name
        benchmark_note = f"Verified {location} wholesale/retail benchmark"
    else:
        off_title = (off_data.get("name") or "").strip()
        query_words = {w for w in re.findall(r"[a-z0-9]{3,}", clean_query.lower())}
        off_words = {w for w in re.findall(r"[a-z0-9]{3,}", off_title.lower())}

        if off_title and (query_words & off_words):
            resolved_title = off_title
            qty_tag = off_data.get("quantity")
            if qty_tag and qty_tag.lower() not in resolved_title.lower():
                resolved_title = f"{resolved_title} ({qty_tag})"
        else:
            resolved_title = clean_query.title()

        final_name = resolved_title
        category = classify_product_category(f"{clean_query} {final_name}")
        vendor_name = off_data.get("brand") or "Authorized Distributor"
        main_unit = "Carton"
        sub_unit = "Pack"
        retail_unit = "Piece"
        packs_per_carton = 2
        units_per_pack = 12
        cost_carton = 10800.0
        price_carton = 12000.0
        price_pack = 6200.0
        price_retail = 550.0
        desc_base = off_data.get("generic_name") or f"Quality {final_name} stocked for wholesale and retail customers."
        description = f"{desc_base} Available in {main_unit}, {sub_unit}, or single {retail_unit}."
        image_query = f"{clean_query} product package"
        benchmark_note = f"Auto-categorized into {category} ({location} est. pricing — verify before publishing)"

    imported_img = await resolve_nice_product_image(
        client=client,
        product_title=final_name,
        image_query=image_query,
        off_image_url=off_data.get("image_url", ""),
    )

    return {
        "selected": True,
        "name": final_name,
        "category": category,
        "vendor_name": vendor_name,
        "location": location,
        "main_unit": main_unit,
        "sub_unit": sub_unit,
        "retail_unit": retail_unit,
        "packs_per_carton": packs_per_carton,
        "units_per_pack": units_per_pack,
        "cost_price_carton": cost_carton,
        "price_carton": price_carton,
        "price_pack": price_pack,
        "price_retail": price_retail,
        "stock_cartons": default_stock,
        "image_url": imported_img,
        "video_url": "",
        "fetch_video": bool(fetch_video),
        "description": description,
        "price_benchmark_note": benchmark_note,
    }


class NameBatchAutoStockRequest(BaseModel):
    product_names: Union[List[str], str] = Field(...)
    default_stock_cartons: float = Field(default=5.0, ge=0)
    location: str = Field(default="Calabar")


class CategoryBatchAutoStockRequest(BaseModel):
    category: str = Field(default="Beverages")
    count: int = Field(default=5, ge=1, le=15)
    default_stock_cartons: float = Field(default=5.0, ge=0)
    location: str = Field(default="Calabar")
    fetch_video: bool = Field(default=False)


def _normalize_name_list(raw_names: Union[List[str], str]) -> List[str]:
    source_list = [raw_names] if isinstance(raw_names, str) else list(raw_names or [])
    cleaned_names: List[str] = []
    seen = set()
    for raw in source_list:
        for part in re.split(r"[,;\r\n]+", str(raw or "")):
            item = part.strip()
            if item and item.lower() not in seen:
                seen.add(item.lower())
                cleaned_names.append(item)
    return cleaned_names[:20]


@router.post("/search-names")
async def auto_stock_by_names(payload: NameBatchAutoStockRequest) -> Dict[str, Any]:
    cleaned_names = _normalize_name_list(payload.product_names)
    if not cleaned_names:
        raise HTTPException(status_code=400, detail="Enter at least one product name.")

    async with httpx.AsyncClient(timeout=15.0) as client:
        tasks = [
            build_enriched_product(
                client=client,
                raw_name=name,
                default_stock=payload.default_stock_cartons,
                location=payload.location,
            )
            for name in cleaned_names
        ]
        items = await asyncio.gather(*tasks)

    return {
        "status": "ok",
        "items": items,
        "count": len(items),
    }


@router.post("/category")
async def auto_stock_by_category(payload: CategoryBatchAutoStockRequest) -> Dict[str, Any]:
    items = await fetch_category_preview_items_async(
        category=payload.category,
        limit=payload.count,
        location=payload.location,
        default_stock=payload.default_stock_cartons,
        fetch_video=payload.fetch_video,
    )
    return {
        "status": "ok",
        "items": items,
        "count": len(items),
    }


@router.get("/barcode/{barcode}")
async def lookup_barcode(
    barcode: str,
    location: str = Query(default="Calabar"),
    default_stock_cartons: float = Query(default=5.0, ge=0),
) -> Dict[str, Any]:
    clean_query = (barcode or "").strip()
    if not clean_query:
        raise HTTPException(status_code=400, detail="Enter a barcode or product keyword.")

    digits_only = re.sub(r"\D+", "", clean_query)
    if len(digits_only) >= 8 and clean_query.replace("-", "").isdigit():
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                resp = await client.get(
                    OFF_PRODUCT_URL.format(barcode=digits_only),
                    headers={"User-Agent": USER_AGENT},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("status") == 1 and data.get("product"):
                        p = data["product"]
                        raw_name = str(
                            p.get("product_name") or p.get("generic_name") or f"Item {digits_only}"
                        ).strip()
                        item = await build_enriched_product(
                            client=client,
                            raw_name=raw_name,
                            default_stock=default_stock_cartons,
                            location=location,
                        )
                        direct_img = _upgrade_off_image_url(
                            str(p.get("image_front_url") or p.get("image_url") or "")
                        )
                        if direct_img:
                            imported = await import_image_to_store(client, direct_img, raw_name)
                            if imported:
                                item["image_url"] = imported
                        if p.get("brands"):
                            item["vendor_name"] = str(p["brands"]).split(",")[0].strip()
                        return {"status": "ok", "item": item}
            except Exception:
                pass
        raise HTTPException(status_code=404, detail=f"No product record found for barcode {digits_only}.")

    async with httpx.AsyncClient(timeout=15.0) as client:
        item = await build_enriched_product(
            client=client,
            raw_name=clean_query,
            default_stock=default_stock_cartons,
            location=location,
        )
        return {"status": "ok", "item": item}


# ==============================================================================
# 4. ASYNC & EVENT-LOOP-SAFE COMPATIBILITY SHIMS
# ==============================================================================
CATEGORY_ALIASES: Dict[str, set[str]] = {
    "food & groceries": {
        "noodles & pasta",
        "dairy & breakfast",
        "cooking & groceries",
        "biscuits & snacks",
    },
    "groceries": {
        "noodles & pasta",
        "dairy & breakfast",
        "cooking & groceries",
        "biscuits & snacks",
    },
    "household & cleaning": {"soaps & detergents"},
}


async def lookup_barcode_or_keyword_async(
    query: str,
    location: str = "Calabar",
) -> Dict[str, Any]:
    clean = str(query or "").strip()
    if not clean:
        return {}
    if clean.isdigit() and len(clean) >= 8:
        res = await lookup_barcode(clean, location=location)
        return res.get("item", {})
    async with httpx.AsyncClient(timeout=15.0) as client:
        return await build_enriched_product(client, clean, 5.0, location)


async def fetch_category_preview_items_async(
    category: str = "Beverages",
    limit: int = 8,
    location: str = "Calabar",
    default_stock: float = 5.0,
    fetch_video: bool = False,
    **_: Any,
) -> List[Dict[str, Any]]:
    cat_lower = (category or "").strip().lower()
    allowed_cats = CATEGORY_ALIASES.get(cat_lower, {cat_lower})
    matches = [
        entry["name"]
        for entry in NIGERIAN_FMCG_CATALOG
        if cat_lower in {"all", "general"} or entry["category"].lower() in allowed_cats
    ][:limit]
    if not matches:
        matches = [entry["name"] for entry in NIGERIAN_FMCG_CATALOG[:limit]]

    async with httpx.AsyncClient(timeout=15.0) as client:
        return await asyncio.gather(
            *[
                build_enriched_product(
                    client,
                    name,
                    default_stock=default_stock,
                    location=location,
                    fetch_video=fetch_video,
                )
                for name in matches
            ]
        )


def lookup_barcode_or_keyword(query: str, location: str = "Calabar") -> Dict[str, Any]:
    return _run_coro_sync(lookup_barcode_or_keyword_async(query, location))


def fetch_category_preview_items(
    category: str = "Beverages",
    limit: int = 8,
    location: str = "Calabar",
    **_: Any,
) -> List[Dict[str, Any]]:
    return _run_coro_sync(
        fetch_category_preview_items_async(category=category, limit=limit, location=location)
    )
