import ipaddress
import json
import re
import socket
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, unquote, urlparse
from urllib.request import Request, urlopen

SINGLE_UNIT_CATEGORIES = {
    "electronics",
    "accessories",
    "gadgets",
    "phones",
    "appliances",
    "cosmetics",
    "fashion",
    "stationery",
    "hardware",
}

SINGLE_UNIT_KEYWORDS = (
    "charger",
    "cable",
    "earpod",
    "earbud",
    "headset",
    "power bank",
    "powerbank",
    "speaker",
    "watch",
    "iron",
    "kettle",
    "blender",
    "fan",
    "bulb",
    "extension",
    "adapter",
    "mouse",
    "keyboard",
)

# Fallback catalog used automatically when Open Food Facts returns 503/times out
# or when a non-food / keyword / URL lookup is entered.
CATEGORY_STARTER_CATALOG: Dict[str, List[Dict[str, Any]]] = {
    "beverages": [
        {
            "name": "Maltina Classic Can 330ml",
            "vendor_name": "Nigerian Breweries",
            "price_retail": 650.0,
            "description": "Chilled-ready malt drink can (330ml).",
            "image_url": "",
            "video_url": "",
        },
        {
            "name": "Coca-Cola Pet 500ml",
            "vendor_name": "Coca-Cola HBC",
            "price_retail": 450.0,
            "description": "Sparkling cola beverage in 500ml bottle.",
            "image_url": "",
            "video_url": "",
        },
        {
            "name": "Pepsi Pet 500ml",
            "vendor_name": "Seven-Up Bottling",
            "price_retail": 400.0,
            "description": "Refreshing cola drink in 500ml bottle.",
            "image_url": "",
            "video_url": "",
        },
        {
            "name": "Chi Exotic Mango Juice 1L",
            "vendor_name": "Chi Limited",
            "price_retail": 2200.0,
            "description": "Tropical mango juice blend in 1-litre pack.",
            "image_url": "",
            "video_url": "",
        },
        {
            "name": "Lacasa Milk Drink 200ml",
            "vendor_name": "Lacasa",
            "price_retail": 350.0,
            "description": "Creamy milk drink in 200ml carton.",
            "image_url": "",
            "video_url": "",
        },
        {
            "name": "Schweppes Chapman 330ml",
            "vendor_name": "Coca-Cola HBC",
            "price_retail": 500.0,
            "description": "Classic citrus-infused Chapman soft drink.",
            "image_url": "",
            "video_url": "",
        },
    ],
    "noodles": [
        {
            "name": "Indomie Chicken Pepper 70g",
            "vendor_name": "Dufil Prima Foods",
            "price_retail": 300.0,
            "description": "Quick-cook chicken-flavored instant noodles (70g).",
            "image_url": "",
            "video_url": "",
        },
        {
            "name": "Indomie Super Pack 120g",
            "vendor_name": "Dufil Prima Foods",
            "price_retail": 500.0,
            "description": "Larger serving instant noodles pack (120g).",
            "image_url": "",
            "video_url": "",
        },
        {
            "name": "Golden Penny Noodles Jollof 70g",
            "vendor_name": "FMN",
            "price_retail": 300.0,
            "description": "Seasoned Jollof-style instant noodles (70g).",
            "image_url": "",
            "video_url": "",
        },
        {
            "name": "Mimee Noodles Chicken 70g",
            "vendor_name": "May & Baker",
            "price_retail": 280.0,
            "description": "Tasty chicken instant noodles (70g).",
            "image_url": "",
            "video_url": "",
        },
    ],
    "dairy": [
        {
            "name": "Peak Evaporated Milk Tin 150g",
            "vendor_name": "FrieslandCampina WAMCO",
            "price_retail": 850.0,
            "description": "Rich creamy evaporated milk tin (150g).",
            "image_url": "",
            "video_url": "",
        },
        {
            "name": "Cowbell Milk Sachet 12g",
            "vendor_name": "Promasidor",
            "price_retail": 150.0,
            "description": "Fortified powdered milk sachet (12g).",
            "image_url": "",
            "video_url": "",
        },
        {
            "name": "Dano Full Cream Milk Sachet 350g",
            "vendor_name": "Arla Foods",
            "price_retail": 2400.0,
            "description": "Full cream instant milk powder refill (350g).",
            "image_url": "",
            "video_url": "",
        },
        {
            "name": "Hollandia Yoghurt Plain Sweet 1L",
            "vendor_name": "Chi Limited",
            "price_retail": 2500.0,
            "description": "Smooth sweetened plain yoghurt (1L).",
            "image_url": "",
            "video_url": "",
        },
    ],
    "soaps & detergents": [
        {
            "name": "Sunlight Detergent Powder 900g",
            "vendor_name": "Unilever",
            "price_retail": 2600.0,
            "description": "Washing powder for bright stain removal (900g).",
            "image_url": "",
            "video_url": "",
        },
        {
            "name": "Ariel Detergent 1kg",
            "vendor_name": "Procter & Gamble",
            "price_retail": 3200.0,
            "description": "Deep-clean laundry detergent powder (1kg).",
            "image_url": "",
            "video_url": "",
        },
        {
            "name": "Dettol Antiseptic Soap 110g",
            "vendor_name": "Reckitt",
            "price_retail": 950.0,
            "description": "Antibacterial bathing soap bar (110g).",
            "image_url": "",
            "video_url": "",
        },
        {
            "name": "Joy Beauty Soap 100g",
            "vendor_name": "PZ Cussons",
            "price_retail": 550.0,
            "description": "Everyday moisturizing bar soap (100g).",
            "image_url": "",
            "video_url": "",
        },
    ],
    "electronics & accessories": [
        {
            "name": "Oraimo Fast Wall Charger 20W",
            "vendor_name": "Oraimo",
            "price_retail": 7500.0,
            "description": "Fast-charging USB wall adapter with safe power protection.",
            "image_url": "",
            "video_url": "",
        },
        {
            "name": "Oraimo Type-C Braided Cable 1m",
            "vendor_name": "Oraimo",
            "price_retail": 3500.0,
            "description": "Durable braided USB-C charging and data cable.",
            "image_url": "",
            "video_url": "",
        },
        {
            "name": "Oraimo FreePods Wireless Earbuds",
            "vendor_name": "Oraimo",
            "price_retail": 18500.0,
            "description": "Wireless Bluetooth earbuds with charging case.",
            "image_url": "",
            "video_url": "",
        },
        {
            "name": "20000mAh Fast Power Bank",
            "vendor_name": "Oraimo",
            "price_retail": 24000.0,
            "description": "High-capacity portable power bank with dual output.",
            "image_url": "",
            "video_url": "",
        },
    ],
}


def is_safe_public_https_url(url: str) -> bool:
    """Allow only public https:// URLs and block local/private/link-local addresses."""
    clean = (url or "").strip()
    if not clean:
        return False
    try:
        parsed = urlparse(clean)
    except Exception:
        return False
    if parsed.scheme != "https" or not parsed.hostname:
        return False

    host = parsed.hostname.lower().strip(".")
    if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or host.endswith(".local"):
        return False

    try:
        ip_obj = ipaddress.ip_address(host)
        if (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_reserved
            or ip_obj.is_unspecified
        ):
            return False
        return True
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        for info in infos:
            addr_str = info[4][0]
            ip_obj = ipaddress.ip_address(addr_str)
            if (
                ip_obj.is_private
                or ip_obj.is_loopback
                or ip_obj.is_link_local
                or ip_obj.is_multicast
                or ip_obj.is_reserved
                or ip_obj.is_unspecified
            ):
                return False
    except OSError:
        return False

    return True


def sanitize_url(url: str) -> str:
    clean = (url or "").strip()
    return clean if is_safe_public_https_url(clean) else ""


def infer_packaging_profile(category: str, name: str, base_retail_price: float) -> Dict[str, Any]:
    """Automatically adapt packaging units & description for single-unit vs. carton/pack items."""
    cat_key = (category or "").strip().lower()
    name_key = (name or "").strip().lower()
    retail = round(max(50.0, float(base_retail_price or 500.0)), 2)

    is_single = cat_key in SINGLE_UNIT_CATEGORIES or any(k in name_key for k in SINGLE_UNIT_KEYWORDS)

    if is_single:
        return {
            "main_unit": "Unit",
            "sub_unit": "Unit",
            "retail_unit": "Piece",
            "packs_per_carton": 1,
            "units_per_pack": 1,
            "price_carton": retail,
            "price_pack": retail,
            "price_retail": retail,
            "cost_price_carton": round(retail * 0.78, 2),
            "packaging_note": "Sold individually per single piece/unit.",
        }

    packs = 12
    units = 1
    pack_price = round(retail * 0.92, 2)
    carton_price = round(pack_price * packs * 0.95, 2)
    return {
        "main_unit": "Carton",
        "sub_unit": "Pack",
        "retail_unit": "Piece",
        "packs_per_carton": packs,
        "units_per_pack": units,
        "price_carton": carton_price,
        "price_pack": pack_price,
        "price_retail": retail,
        "cost_price_carton": round(carton_price * 0.80, 2),
        "packaging_note": f"Available per Carton ({packs} Packs), Pack, or single Piece.",
    }


def build_stock_item(
    *,
    name: str,
    category: str,
    vendor_name: str = "",
    base_retail_price: float = 500.0,
    stock_cartons: float = 5.0,
    description: str = "",
    image_url: str = "",
    video_url: str = "",
    source_label: str = "Starter Catalog",
) -> Dict[str, Any]:
    """Build a product draft compatible with both the preview table and manual form editor."""
    clean_name = re.sub(r"\s+", " ", (name or "").strip())[:120] or "Store Item"
    clean_cat = re.sub(r"\s+", " ", (category or "General").strip())[:60] or "General"
    clean_vendor = re.sub(r"\s+", " ", (vendor_name or "").strip())[:80]
    safe_img = sanitize_url(image_url)
    safe_vid = sanitize_url(video_url)

    pkg = infer_packaging_profile(clean_cat, clean_name, base_retail_price)
    base_desc = re.sub(r"\s+", " ", (description or "").strip())[:240]
    if base_desc:
        final_desc = f"{base_desc} {pkg['packaging_note']}".strip()
    else:
        final_desc = f"{clean_name} — {pkg['packaging_note']}"

    return {
        "name": clean_name,
        "category": clean_cat,
        "vendor_name": clean_vendor,
        "main_unit": pkg["main_unit"],
        "sub_unit": pkg["sub_unit"],
        "retail_unit": pkg["retail_unit"],
        "price_carton": pkg["price_carton"],
        "price_pack": pkg["price_pack"],
        "price_retail": pkg["price_retail"],
        "cost_price_carton": pkg["cost_price_carton"],
        "packs_per_carton": pkg["packs_per_carton"],
        "units_per_pack": pkg["units_per_pack"],
        "stock_cartons": max(0.0, float(stock_cartons)),
        "image_url": safe_img,
        "video_url": safe_vid,
        "description": final_desc,
        "price_benchmark_note": (
            f"Source: {source_label} | Unit: {pkg['main_unit']} "
            f"({pkg['packs_per_carton']}x{pkg['units_per_pack']}) — editable before publish"
        ),
        "selected": True,
    }


def _http_get_json(url: str, timeout_sec: float = 4.0, retries: int = 1) -> Optional[Dict[str, Any]]:
    if not is_safe_public_https_url(url):
        return None
    req = Request(
        url,
        headers={
            "User-Agent": "SammyreksBusinessCenter/24.5 (https://example.com; inventory-sync)",
            "Accept": "application/json",
        },
    )
    for attempt in range(retries + 1):
        try:
            with urlopen(req, timeout=timeout_sec) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw)
        except Exception:
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
    return None


def _starter_items_for_category(category: str, count: int, default_stock: float) -> List[Dict[str, Any]]:
    key = (category or "").strip().lower()
    pool = CATEGORY_STARTER_CATALOG.get(key)

    if not pool:
        for k, items in CATEGORY_STARTER_CATALOG.items():
            if key and (key in k or k in key):
                pool = items
                break

    if not pool:
        clean_c = (category or "General").strip()
        pool = [
            {
                "name": f"{clean_c} Standard Item {i + 1}",
                "vendor_name": "",
                "price_retail": 800.0 + (i * 250.0),
                "description": f"{clean_c} stock item #{i + 1}.",
                "image_url": "",
                "video_url": "",
            }
            for i in range(max(count, 5))
        ]

    output: List[Dict[str, Any]] = []
    for idx in range(count):
        tpl = pool[idx % len(pool)]
        suffix = "" if idx < len(pool) else f" (Batch {idx // len(pool) + 1})"
        output.append(
            build_stock_item(
                name=f"{tpl['name']}{suffix}",
                category=category or "General",
                vendor_name=tpl.get("vendor_name", ""),
                base_retail_price=float(tpl.get("price_retail", 600.0)),
                stock_cartons=default_stock,
                description=tpl.get("description", ""),
                image_url=tpl.get("image_url", ""),
                video_url=tpl.get("video_url", ""),
                source_label="Local Starter Catalog",
            )
        )
    return output


def fetch_category_preview_items(
    category: str,
    count: int = 8,
    default_stock_cartons: float = 5.0,
    fetch_video: bool = False,
) -> Dict[str, Any]:
    """Fetch category items via Open Food Facts with retry + automatic starter-catalog fallback."""
    safe_count = max(1, min(15, int(count or 8)))
    safe_stock = max(0.0, float(default_stock_cartons if default_stock_cartons is not None else 5.0))
    clean_cat = (category or "General").strip() or "General"

    items: List[Dict[str, Any]] = []
    source_used = "Local Starter Catalog"

    off_url = (
        f"https://world.openfoodfacts.org/cgi/search.pl?search_terms={quote_plus(clean_cat)}"
        f"&search_simple=1&action=process&json=1&page_size={safe_count}"
    )
    data = _http_get_json(off_url, timeout_sec=4.0, retries=1)
    products = (data or {}).get("products") if isinstance(data, dict) else None

    if isinstance(products, list) and products:
        for idx, p in enumerate(products):
            if len(items) >= safe_count:
                break
            if not isinstance(p, dict):
                continue
            pname = (p.get("product_name") or p.get("product_name_en") or "").strip()
            if not pname:
                continue
            brand = (p.get("brands") or "").split(",")[0].strip()
            img = sanitize_url(p.get("image_front_small_url") or p.get("image_front_url") or "")
            qty_txt = (p.get("quantity") or "").strip()
            desc = f"{brand} {qty_txt}".strip() if brand or qty_txt else f"{pname} ({clean_cat})"
            benchmark_retail = round(450.0 + (idx * 150.0), 2)

            items.append(
                build_stock_item(
                    name=pname,
                    category=clean_cat,
                    vendor_name=brand,
                    base_retail_price=benchmark_retail,
                    stock_cartons=safe_stock,
                    description=desc,
                    image_url=img,
                    video_url="",
                    source_label="Open Food Facts",
                )
            )
        if items:
            source_used = "Open Food Facts"

    if len(items) < safe_count:
        fallback_items = _starter_items_for_category(clean_cat, safe_count - len(items), safe_stock)
        existing_names = {it["name"].lower() for it in items}
        for fb in fallback_items:
            if fb["name"].lower() not in existing_names:
                items.append(fb)
                existing_names.add(fb["name"].lower())
        source_used = (
            "Open Food Facts + Starter Catalog" if source_used == "Open Food Facts" else "Local Starter Catalog (Auto-Fallback)"
        )

    return {
        "items": items[:safe_count],
        "source": source_used,
        "video_downloaded": False,
    }


def lookup_barcode_or_keyword(query_value: str, default_stock_cartons: float = 5.0) -> Dict[str, Any]:
    """Look up a numeric barcode, extract a URL/brand name, or prepare a manual/starter item."""
    raw = (query_value or "").strip()
    if not raw:
        raise ValueError("Barcode or product query is empty.")

    if raw.lower().startswith(("http://", "https://")):
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower().replace("www.", "")
        brand = host.split(".")[0] if host else "Store"
        slug_words = [w for w in re.split(r"[-_/]+", unquote(parsed.path)) if len(w) > 2 and not w.isdigit()]
        title_guess = " ".join([brand.capitalize()] + [w.capitalize() for w in slug_words[-3:]])
        item = build_stock_item(
            name=title_guess or f"{brand.capitalize()} Product",
            category="Electronics & Accessories" if brand in {"oraimo", "itel", "tecno", "infinix", "jbl", "anker"} else "General",
            vendor_name=brand.capitalize(),
            base_retail_price=5500.0,
            stock_cartons=default_stock_cartons,
            description=f"Imported from link reference ({host}).",
            source_label="URL Keyword Extractor",
        )
        return {"item": item, "matched_via": "url_keyword"}

    digits_only = re.sub(r"\D", "", raw)
    if len(digits_only) >= 8:
        off_url = f"https://world.openfoodfacts.org/api/v2/product/{quote_plus(digits_only)}.json"
        data = _http_get_json(off_url, timeout_sec=4.0, retries=1)
        if isinstance(data, dict) and data.get("status") == 1 and isinstance(data.get("product"), dict):
            p = data["product"]
            pname = (p.get("product_name") or p.get("product_name_en") or f"Item {digits_only}").strip()
            brand = (p.get("brands") or "").split(",")[0].strip()
            img = sanitize_url(p.get("image_front_small_url") or p.get("image_front_url") or "")
            item = build_stock_item(
                name=pname,
                category="General",
                vendor_name=brand,
                base_retail_price=750.0,
                stock_cartons=default_stock_cartons,
                description=(p.get("quantity") or "").strip(),
                image_url=img,
                source_label=f"Barcode {digits_only}",
            )
            return {"item": item, "matched_via": "open_food_facts_barcode"}

    needle = raw.lower()
    for cat_name, pool in CATEGORY_STARTER_CATALOG.items():
        for tpl in pool:
            if needle in tpl["name"].lower() or needle in (tpl.get("vendor_name") or "").lower() or needle in cat_name:
                item = build_stock_item(
                    name=tpl["name"],
                    category=cat_name.title(),
                    vendor_name=tpl.get("vendor_name", ""),
                    base_retail_price=float(tpl.get("price_retail", 1000.0)),
                    stock_cartons=default_stock_cartons,
                    description=tpl.get("description", ""),
                    image_url=tpl.get("image_url", ""),
                    source_label="Starter Catalog Match",
                )
                return {"item": item, "matched_via": "starter_catalog_match"}

    item = build_stock_item(
        name=raw,
        category="General",
        base_retail_price=1000.0,
        stock_cartons=default_stock_cartons,
        source_label="Manual / Keyword Draft",
    )
    return {"item": item, "matched_via": "query_draft"}
