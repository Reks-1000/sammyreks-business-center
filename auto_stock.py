import asyncio
from concurrent.futures import ThreadPoolExecutor
import re
from typing import Any, Coroutine, Dict, List, Optional, TypeVar
from urllib.parse import urlparse
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

T = TypeVar("T")

router = APIRouter(prefix="/api/auto-stock", tags=["auto-stock"])

OFF_SEARCH_URL = "https://world.openfoodfacts.org/cgi/search.pl"
OFF_PRODUCT_URL = "https://world.openfoodfacts.org/api/v2/product/{barcode}.json"

USER_AGENT = "SammyreksBusinessCenter/2.0 (Calabar-Nigeria-POS)"


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
    parsed = urlparse(clean)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return clean
    if clean.startswith("/static/"):
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
    },
    {
        "keywords": ["coca cola", "coke", "coca-cola 50cl"],
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
    },
    {
        "keywords": ["peak", "peak milk", "peak refill", "peak evaporated"],
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
    ("Cooking & Groceries", ["rice", "beans", "garri", "semovita", "semolina", "wheat", "oil", "power oil", "kings", "mamador", "devon", "maggi", "knorr", "royco", "seasoning", "curry", "thyme", "tomato", "gino", "sonia", "tasty tom", "salt", "flour"]),
]


def classify_product_category(product_name: str, fallback: str = "General") -> str:
    lower = (product_name or "").strip().lower()
    if not lower:
        return fallback
    for category_name, keywords in CATEGORY_RULES:
        for kw in keywords:
            if kw in lower:
                return category_name
    return fallback


def match_nigerian_catalog(query_name: str) -> Optional[Dict[str, Any]]:
    q = (query_name or "").strip().lower()
    if not q:
        return None
    best_match = None
    best_score = 0
    for entry in NIGERIAN_FMCG_CATALOG:
        for kw in entry["keywords"]:
            if kw in q or q in kw:
                score = len(kw)
                if score > best_score:
                    best_score = score
                    best_match = entry
    return best_match


def _extract_best_off_image(product_obj: Dict[str, Any]) -> str:
    for key in ("image_front_url", "image_url", "image_front_small_url", "image_small_url"):
        val = str(product_obj.get(key) or "").strip()
        if val.startswith("http"):
            return val
    return ""


async def fetch_off_product_by_name(client: httpx.AsyncClient, search_query: str) -> Dict[str, Any]:
    params = {
        "search_terms": search_query,
        "search_simple": 1,
        "action": "process",
        "json": 1,
        "page_size": 6,
        "fields": "code,product_name,brands,categories,image_front_url,image_url,quantity,generic_name",
    }
    try:
        resp = await client.get(OFF_SEARCH_URL, params=params, headers={"User-Agent": USER_AGENT})
        if resp.status_code != 200:
            return {}
        data = resp.json()
        products = data.get("products") or []
        for p in products:
            img = _extract_best_off_image(p)
            pname = str(p.get("product_name") or "").strip()
            if img and pname:
                return {
                    "name": pname,
                    "brand": str(p.get("brands") or "").split(",")[0].strip(),
                    "image_url": img,
                    "quantity": str(p.get("quantity") or "").strip(),
                    "generic_name": str(p.get("generic_name") or "").strip(),
                }
        if products:
            first = products[0]
            return {
                "name": str(first.get("product_name") or "").strip(),
                "brand": str(first.get("brands") or "").split(",")[0].strip(),
                "image_url": _extract_best_off_image(first),
                "quantity": str(first.get("quantity") or "").strip(),
                "generic_name": str(first.get("generic_name") or "").strip(),
            }
    except Exception:
        pass
    return {}


async def build_enriched_product(
    client: httpx.AsyncClient,
    raw_name: str,
    default_stock: float,
    location: str,
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
        benchmark_note = f"Verified {location} wholesale/retail benchmark"
    else:
        resolved_title = off_data.get("name") or clean_query.title()
        qty_tag = off_data.get("quantity")
        if qty_tag and qty_tag.lower() not in resolved_title.lower():
            resolved_title = f"{resolved_title} ({qty_tag})"

        final_name = resolved_title
        category = classify_product_category(final_name)
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
        benchmark_note = f"Auto-categorized into {category} ({location} est. pricing — verify before publishing)"

    return {
        "selected": True,
        "name": final_name,
        "category": category,
        "vendor_name": vendor_name,
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
        "image_url": off_data.get("image_url", ""),
        "video_url": "",
        "description": description,
        "price_benchmark_note": benchmark_note,
    }


class NameBatchAutoStockRequest(BaseModel):
    product_names: List[str] = Field(..., min_length=1, max_length=25)
    default_stock_cartons: float = Field(default=5.0, ge=0)
    location: str = Field(default="Watt Market, Calabar")


@router.post("/search-names")
async def auto_stock_by_names(payload: NameBatchAutoStockRequest) -> Dict[str, Any]:
    cleaned_names: List[str] = []
    seen = set()
    for raw in payload.product_names:
        for part in re.split(r"[,;\r\n]+", raw):
            item = part.strip()
            if item and item.lower() not in seen:
                seen.add(item.lower())
                cleaned_names.append(item)

    if not cleaned_names:
        raise HTTPException(status_code=400, detail="Enter at least one product name.")

    cleaned_names = cleaned_names[:20]

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


@router.get("/barcode/{barcode}")
async def lookup_barcode(barcode: str) -> Dict[str, Any]:
    clean_code = re.sub(r"[^0-9A-Za-z]", "", barcode or "")
    if not clean_code:
        raise HTTPException(status_code=400, detail="Invalid barcode.")

    async with httpx.AsyncClient(timeout=12.0) as client:
        resp = await client.get(
            OFF_PRODUCT_URL.format(barcode=clean_code),
            headers={"User-Agent": USER_AGENT},
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=404, detail=f"Barcode {clean_code} not found.")
        data = resp.json()
        if data.get("status") != 1 or not data.get("product"):
            raise HTTPException(status_code=404, detail=f"No product record found for barcode {clean_code}.")

        p = data["product"]
        raw_name = str(p.get("product_name") or p.get("generic_name") or f"Item {clean_code}").strip()
        item = await build_enriched_product(
            client=client,
            raw_name=raw_name,
            default_stock=5.0,
            location="Watt Market, Calabar",
        )
        direct_img = _extract_best_off_image(p)
        if direct_img:
            item["image_url"] = direct_img
        if p.get("brands"):
            item["vendor_name"] = str(p["brands"]).split(",")[0].strip()

        return {"status": "ok", "item": item}


# ==============================================================================
# 3. ASYNC & EVENT-LOOP-SAFE COMPATIBILITY SHIMS
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
    location: str = "Watt Market, Calabar",
) -> Dict[str, Any]:
    clean = str(query or "").strip()
    if not clean:
        return {}
    if clean.isdigit() and len(clean) >= 8:
        res = await lookup_barcode(clean)
        return res.get("item", {})
    async with httpx.AsyncClient(timeout=12.0) as client:
        return await build_enriched_product(client, clean, 5.0, location)


async def fetch_category_preview_items_async(
    category: str = "Beverages",
    limit: int = 8,
    location: str = "Watt Market, Calabar",
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
            *[build_enriched_product(client, name, 5.0, location) for name in matches]
        )


def lookup_barcode_or_keyword(query: str, location: str = "Watt Market, Calabar") -> Dict[str, Any]:
    return _run_coro_sync(lookup_barcode_or_keyword_async(query, location))


def fetch_category_preview_items(
    category: str = "Beverages",
    limit: int = 8,
    location: str = "Watt Market, Calabar",
    **_: Any,
) -> List[Dict[str, Any]]:
    return _run_coro_sync(
        fetch_category_preview_items_async(category=category, limit=limit, location=location)
    )
