import ipaddress
import json
import re
import socket
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote, quote_plus, unquote, urlparse
from urllib.request import Request, urlopen

SINGLE_UNIT_CATEGORIES = {
    "electronics",
    "electronics & accessories",
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

POPULAR_MARKET_CATALOG: Dict[str, List[Dict[str, Any]]] = {
    "beverages": [
        {
            "name": "Maltina Classic Can 330ml",
            "vendor_name": "Nigerian Breweries",
            "price_retail": 650.0,
            "popularity": 98,
            "markets": ["calabar", "watt market", "marian market", "cross river", "nigeria"],
            "image_query": "Maltina malt drink can",
            "description": "Top-selling chilled malt drink across Calabar & Watt Market stalls.",
        },
        {
            "name": "Coca-Cola Pet 500ml",
            "vendor_name": "Coca-Cola HBC",
            "price_retail": 450.0,
            "popularity": 97,
            "markets": ["calabar", "watt market", "marian market", "cross river", "nigeria"],
            "image_query": "Coca-Cola 500ml bottle",
            "description": "High-turnover sparkling cola drink in 500ml PET bottle.",
        },
        {
            "name": "Lacasa Milk Drink 200ml",
            "vendor_name": "Lacasa",
            "price_retail": 350.0,
            "popularity": 94,
            "markets": ["calabar", "watt market", "cross river", "nigeria"],
            "image_query": "milk drink carton 200ml",
            "description": "Fast-moving daily refreshment milk drink pack.",
        },
        {
            "name": "Fearless Energy Drink 500ml",
            "vendor_name": "Rite Foods",
            "price_retail": 500.0,
            "popularity": 92,
            "markets": ["calabar", "watt market", "marian market", "cross river", "nigeria"],
            "image_query": "energy drink bottle 500ml",
            "description": "High-demand energy drink among market traders and commuters.",
        },
        {
            "name": "Chi Exotic Mango Nectar 1L",
            "vendor_name": "Chi Limited",
            "price_retail": 2200.0,
            "popularity": 89,
            "markets": ["calabar", "watt market", "marian market", "nigeria"],
            "image_query": "mango fruit juice carton 1L",
            "description": "Popular family-size tropical fruit juice pack.",
        },
        {
            "name": "Pepsi Pet 500ml",
            "vendor_name": "Seven-Up Bottling",
            "price_retail": 400.0,
            "popularity": 88,
            "markets": ["calabar", "watt market", "nigeria"],
            "image_query": "Pepsi bottle 500ml",
            "description": "Everyday carbonated cola drink in 500ml bottle.",
        },
    ],
    "provisions": [
        {
            "name": "Peak Evaporated Milk Tin 150g",
            "vendor_name": "FrieslandCampina WAMCO",
            "price_retail": 850.0,
            "popularity": 99,
            "markets": ["calabar", "watt market", "marian market", "cross river", "nigeria"],
            "image_query": "evaporated milk tin",
            "description": "Household staple evaporated milk tin in high daily demand.",
        },
        {
            "name": "Milo Refill Sachet 400g",
            "vendor_name": "Nestle Nigeria",
            "price_retail": 2600.0,
            "popularity": 96,
            "markets": ["calabar", "watt market", "marian market", "nigeria"],
            "image_query": "Nestle Milo chocolate powder pack",
            "description": "Fast-selling chocolate malt beverage refill pack.",
        },
        {
            "name": "Cowbell Milk Sachet 12g (Roll)",
            "vendor_name": "Promasidor",
            "price_retail": 150.0,
            "popularity": 95,
            "markets": ["calabar", "watt market", "cross river", "nigeria"],
            "image_query": "powdered milk sachet",
            "description": "High-volume single-serve milk sachet for daily retail.",
        },
        {
            "name": "Golden Morn Maize Cereal 450g",
            "vendor_name": "Nestle Nigeria",
            "price_retail": 2100.0,
            "popularity": 91,
            "markets": ["calabar", "watt market", "marian market", "nigeria"],
            "image_query": "breakfast cereal pack",
            "description": "Popular ready-to-eat maize & soya breakfast cereal.",
        },
        {
            "name": "Bournvita Refill Pack 450g",
            "vendor_name": "Cadbury Nigeria",
            "price_retail": 2400.0,
            "popularity": 89,
            "markets": ["calabar", "watt market", "nigeria"],
            "image_query": "Bournvita chocolate drink pack",
            "description": "Rich cocoa food drink refill pack.",
        },
    ],
    "noodles": [
        {
            "name": "Indomie Chicken Pepper 70g",
            "vendor_name": "Dufil Prima Foods",
            "price_retail": 300.0,
            "popularity": 99,
            "markets": ["calabar", "watt market", "marian market", "cross river", "nigeria"],
            "image_query": "Indomie instant noodles pack",
            "description": "#1 fastest-selling instant noodle pack in Calabar markets.",
        },
        {
            "name": "Indomie Super Pack 120g",
            "vendor_name": "Dufil Prima Foods",
            "price_retail": 500.0,
            "popularity": 95,
            "markets": ["calabar", "watt market", "marian market", "nigeria"],
            "image_query": "instant noodles 120g pack",
            "description": "Large-portion chicken instant noodles in strong demand.",
        },
        {
            "name": "Golden Penny Noodles Jollof 70g",
            "vendor_name": "FMN",
            "price_retail": 300.0,
            "popularity": 90,
            "markets": ["calabar", "watt market", "nigeria"],
            "image_query": "instant noodles packet",
            "description": "Seasoned Jollof-flavored instant noodles.",
        },
        {
            "name": "Mimee Noodles Chicken 70g",
            "vendor_name": "May & Baker",
            "price_retail": 280.0,
            "popularity": 86,
            "markets": ["calabar", "watt market", "cross river", "nigeria"],
            "image_query": "chicken instant noodles",
            "description": "Value-priced chicken instant noodles.",
        },
    ],
    "soaps & detergents": [
        {
            "name": "Sunlight Detergent Powder 900g",
            "vendor_name": "Unilever",
            "price_retail": 2600.0,
            "popularity": 96,
            "markets": ["calabar", "watt market", "marian market", "nigeria"],
            "image_query": "washing detergent powder bag",
            "description": "High-turnover laundry washing powder.",
        },
        {
            "name": "Dettol Antiseptic Bar Soap 110g",
            "vendor_name": "Reckitt",
            "price_retail": 950.0,
            "popularity": 94,
            "markets": ["calabar", "watt market", "marian market", "nigeria"],
            "image_query": "antibacterial bar soap",
            "description": "Trusted antibacterial bathing soap bar.",
        },
        {
            "name": "Viva Plus Detergent 850g",
            "vendor_name": "Aspira Nigeria",
            "price_retail": 2200.0,
            "popularity": 92,
            "markets": ["calabar", "watt market", "cross river", "nigeria"],
            "image_query": "laundry detergent packet",
            "description": "Fast-moving multi-purpose detergent powder.",
        },
        {
            "name": "Joy Beauty Soap 100g",
            "vendor_name": "PZ Cussons",
            "price_retail": 550.0,
            "popularity": 88,
            "markets": ["calabar", "watt market", "nigeria"],
            "image_query": "toilet beauty soap bar",
            "description": "Everyday moisturizing bar soap.",
        },
    ],
    "electronics & accessories": [
        {
            "name": "Oraimo Fast Wall Charger 20W",
            "vendor_name": "Oraimo",
            "price_retail": 7500.0,
            "popularity": 98,
            "markets": ["calabar", "watt market", "marian market", "cross river", "nigeria"],
            "image_query": "USB wall charger adapter",
            "description": "Top-requested fast phone charger around Watt Market phone stalls.",
        },
        {
            "name": "Oraimo Type-C Braided Cable 1m",
            "vendor_name": "Oraimo",
            "price_retail": 3500.0,
            "popularity": 96,
            "markets": ["calabar", "watt market", "marian market", "nigeria"],
            "image_query": "USB-C braided charging cable",
            "description": "Durable fast-charge USB-C cable in high daily demand.",
        },
        {
            "name": "Oraimo 20000mAh Power Bank",
            "vendor_name": "Oraimo",
            "price_retail": 24000.0,
            "popularity": 95,
            "markets": ["calabar", "watt market", "marian market", "cross river", "nigeria"],
            "image_query": "portable power bank 20000mah",
            "description": "High-capacity backup power bank sought after by students & traders.",
        },
        {
            "name": "Oraimo FreePods Wireless Earbuds",
            "vendor_name": "Oraimo",
            "price_retail": 18500.0,
            "popularity": 91,
            "markets": ["calabar", "watt market", "nigeria"],
            "image_query": "wireless bluetooth earbuds case",
            "description": "Popular true-wireless Bluetooth earbuds with charging case.",
        },
    ],
}

_IMAGE_CACHE: Dict[str, str] = {}


def is_safe_public_https_url(url: str) -> bool:
    clean = (url or "").strip()
    if not clean:
        return False
    if clean.startswith("data:image/svg+xml;utf8,"):
        return True
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


def _build_svg_badge_data_url(product_name: str, category: str) -> str:
    initials = "".join([w[0].upper() for w in re.findall(r"[A-Za-z0-9]+", product_name)[:2]]) or "PR"
    cat_short = re.sub(r"[^A-Za-z0-9 &]", "", category or "STORE")[:16].upper()
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160" viewBox="0 0 160 160">'
        f'<rect width="160" height="160" rx="18" fill="#166534"/>'
        f'<text x="80" y="86" font-family="Arial,sans-serif" font-size="44" font-weight="bold" '
        f'fill="#ffffff" text-anchor="middle">{initials}</text>'
        f'<text x="80" y="128" font-family="Arial,sans-serif" font-size="13" '
        f'fill="#bbf7d0" text-anchor="middle">{cat_short}</text>'
        f"</svg>"
    )
    return "data:image/svg+xml;utf8," + quote(svg)


def _http_get_json(url: str, timeout_sec: float = 3.5, retries: int = 1) -> Optional[Dict[str, Any]]:
    if not is_safe_public_https_url(url):
        return None
    req = Request(
        url,
        headers={
            "User-Agent": "SammyreksBusinessCenter/24.6 (https://example.com; market-sync)",
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
                time.sleep(0.4 * (attempt + 1))
    return None


def resolve_product_image_url(query: str, product_name: str, category: str) -> str:
    cache_key = f"{query}|{product_name}".lower().strip()
    if cache_key in _IMAGE_CACHE:
        return _IMAGE_CACHE[cache_key]

    search_q = (query or product_name or "").strip()
    if search_q:
        wiki_url = (
            "https://commons.wikimedia.org/w/api.php?action=query&generator=search"
            f"&gsrsearch={quote_plus(search_q)}&gsrnamespace=6&gsrlimit=3"
            "&prop=imageinfo&iiprop=url&iiurlwidth=320&format=json"
        )
        wdata = _http_get_json(wiki_url, timeout_sec=3.0, retries=0)
        pages = ((wdata or {}).get("query") or {}).get("pages") if isinstance(wdata, dict) else None
        if isinstance(pages, dict):
            for page in pages.values():
                ii = (page or {}).get("imageinfo")
                if isinstance(ii, list) and ii:
                    thumb = sanitize_url(ii[0].get("thumburl") or ii[0].get("url") or "")
                    if thumb and not thumb.lower().endswith(".pdf"):
                        _IMAGE_CACHE[cache_key] = thumb
                        return thumb

        off_url = (
            f"https://world.openfoodfacts.org/cgi/search.pl?search_terms={quote_plus(search_q)}"
            "&search_simple=1&action=process&json=1&page_size=3"
        )
        odata = _http_get_json(off_url, timeout_sec=3.0, retries=0)
        prods = (odata or {}).get("products") if isinstance(odata, dict) else None
        if isinstance(prods, list):
            for p in prods:
                if not isinstance(p, dict):
                    continue
                img = sanitize_url(p.get("image_front_small_url") or p.get("image_front_url") or "")
                if img:
                    _IMAGE_CACHE[cache_key] = img
                    return img

    fallback_svg = _build_svg_badge_data_url(product_name, category)
    _IMAGE_CACHE[cache_key] = fallback_svg
    return fallback_svg


def infer_packaging_profile(category: str, name: str, base_retail_price: float) -> Dict[str, Any]:
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


def _location_match_score(location: str, item_markets: List[str], base_popularity: int) -> int:
    loc = (location or "").strip().lower()
    score = int(base_popularity or 80)
    if not loc:
        return score

    tokens = [t for t in re.split(r"[,/\-\s]+", loc) if len(t) > 2]
    joined_markets = " ".join(m.lower() for m in item_markets)

    if loc in joined_markets:
        score += 25
    elif any(tok in joined_markets for tok in tokens):
        score += 15
    elif "nigeria" in joined_markets:
        score += 5
    return score


def build_stock_item(
    *,
    name: str,
    category: str,
    location: str = "",
    popularity_score: int = 85,
    vendor_name: str = "",
    base_retail_price: float = 500.0,
    stock_cartons: float = 5.0,
    description: str = "",
    image_url: str = "",
    image_query: str = "",
    video_url: str = "",
    source_label: str = "Market Demand Index",
) -> Dict[str, Any]:
    clean_name = re.sub(r"\s+", " ", (name or "").strip())[:120] or "Store Item"
    clean_cat = re.sub(r"\s+", " ", (category or "General").strip())[:60] or "General"
    clean_loc = re.sub(r"\s+", " ", (location or "Calabar").strip())[:80] or "Calabar"
    clean_vendor = re.sub(r"\s+", " ", (vendor_name or "").strip())[:80]

    safe_img = sanitize_url(image_url) or resolve_product_image_url(image_query, clean_name, clean_cat)
    safe_vid = sanitize_url(video_url)

    pkg = infer_packaging_profile(clean_cat, clean_name, base_retail_price)
    base_desc = re.sub(r"\s+", " ", (description or "").strip())[:240]
    if base_desc:
        final_desc = f"{base_desc} {pkg['packaging_note']}".strip()
    else:
        final_desc = f"Popular in {clean_loc}. {pkg['packaging_note']}"

    return {
        "name": clean_name,
        "category": clean_cat,
        "location": clean_loc,
        "popularity_score": popularity_score,
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
            f"Top Seller ({clean_loc}) • {source_label} • Unit: {pkg['main_unit']} "
            f"({pkg['packs_per_carton']}x{pkg['units_per_pack']})"
        ),
        "selected": True,
    }


def fetch_category_preview_items(
    category: str,
    count: int = 6,
    default_stock_cartons: float = 5.0,
    fetch_video: bool = False,
    location: str = "Watt Market, Calabar",
) -> Dict[str, Any]:
    safe_count = max(1, min(15, int(count or 6)))
    safe_stock = max(0.0, float(default_stock_cartons if default_stock_cartons is not None else 5.0))
    clean_cat = (category or "Beverages").strip() or "Beverages"
    clean_loc = (location or "Watt Market, Calabar").strip() or "Watt Market, Calabar"

    cat_key = clean_cat.lower()
    pool = POPULAR_MARKET_CATALOG.get(cat_key)

    if not pool:
        for k, candidates in POPULAR_MARKET_CATALOG.items():
            if cat_key in k or k in cat_key:
                pool = candidates
                break

    ranked_candidates: List[Dict[str, Any]] = []
    if pool:
        scored = []
        for tpl in pool:
            rank = _location_match_score(clean_loc, tpl.get("markets", []), tpl.get("popularity", 85))
            scored.append((rank, tpl))
        scored.sort(key=lambda pair: pair[0], reverse=True)

        for rank, tpl in scored[:safe_count]:
            ranked_candidates.append(
                build_stock_item(
                    name=tpl["name"],
                    category=clean_cat,
                    location=clean_loc,
                    popularity_score=rank,
                    vendor_name=tpl.get("vendor_name", ""),
                    base_retail_price=float(tpl.get("price_retail", 600.0)),
                    stock_cartons=safe_stock,
                    description=tpl.get("description", ""),
                    image_url=tpl.get("image_url", ""),
                    image_query=tpl.get("image_query", tpl["name"]),
                    source_label=f"Popular in {clean_loc}",
                )
            )

    if len(ranked_candidates) < safe_count:
        needed = safe_count - len(ranked_candidates)
        off_url = (
            f"https://world.openfoodfacts.org/cgi/search.pl?search_terms={quote_plus(clean_cat)}"
            "&tagtype_0=countries&tag_contains_0=contains&tag_0=nigeria"
            f"&sort_by=unique_scans_n&search_simple=1&action=process&json=1&page_size={needed + 3}"
        )
        data = _http_get_json(off_url, timeout_sec=3.5, retries=1)
        products = (data or {}).get("products") if isinstance(data, dict) else None
        existing = {it["name"].lower() for it in ranked_candidates}

        if isinstance(products, list):
            for idx, p in enumerate(products):
                if len(ranked_candidates) >= safe_count:
                    break
                if not isinstance(p, dict):
                    continue
                pname = (p.get("product_name") or p.get("product_name_en") or "").strip()
                if not pname or pname.lower() in existing:
                    continue
                brand = (p.get("brands") or "").split(",")[0].strip()
                img = sanitize_url(p.get("image_front_small_url") or p.get("image_front_url") or "")
                ranked_candidates.append(
                    build_stock_item(
                        name=pname,
                        category=clean_cat,
                        location=clean_loc,
                        popularity_score=max(70, 85 - idx),
                        vendor_name=brand,
                        base_retail_price=round(500.0 + (idx * 150.0), 2),
                        stock_cartons=safe_stock,
                        description=f"Frequently scanned {clean_cat} item ({clean_loc}).",
                        image_url=img,
                        image_query=f"{brand} {pname}".strip(),
                        source_label=f"Regional Popularity ({clean_loc})",
                    )
                )
                existing.add(pname.lower())

    return {
        "items": ranked_candidates[:safe_count],
        "location": clean_loc,
        "source": f"Location Popularity Engine ({clean_loc})",
        "video_downloaded": False,
    }


def lookup_barcode_or_keyword(
    query_value: str,
    default_stock_cartons: float = 5.0,
    location: str = "Watt Market, Calabar",
) -> Dict[str, Any]:
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
            location=location,
            vendor_name=brand.capitalize(),
            base_retail_price=5500.0,
            stock_cartons=default_stock_cartons,
            description=f"Imported from link reference ({host}).",
            image_query=title_guess or brand,
            source_label="URL Keyword Extractor",
        )
        return {"item": item, "matched_via": "url_keyword"}

    digits_only = re.sub(r"\D", "", raw)
    if len(digits_only) >= 8:
        off_url = f"https://world.openfoodfacts.org/api/v2/product/{quote_plus(digits_only)}.json"
        data = _http_get_json(off_url, timeout_sec=3.5, retries=1)
        if isinstance(data, dict) and data.get("status") == 1 and isinstance(data.get("product"), dict):
            p = data["product"]
            pname = (p.get("product_name") or p.get("product_name_en") or f"Item {digits_only}").strip()
            brand = (p.get("brands") or "").split(",")[0].strip()
            img = sanitize_url(p.get("image_front_small_url") or p.get("image_front_url") or "")
            item = build_stock_item(
                name=pname,
                category="General",
                location=location,
                vendor_name=brand,
                base_retail_price=750.0,
                stock_cartons=default_stock_cartons,
                description=(p.get("quantity") or "").strip(),
                image_url=img,
                image_query=f"{brand} {pname}".strip(),
                source_label=f"Barcode {digits_only}",
            )
            return {"item": item, "matched_via": "open_food_facts_barcode"}

    needle = raw.lower()
    for cat_name, pool in POPULAR_MARKET_CATALOG.items():
        for tpl in pool:
            if needle in tpl["name"].lower() or needle in (tpl.get("vendor_name") or "").lower() or needle in cat_name:
                item = build_stock_item(
                    name=tpl["name"],
                    category=cat_name.title(),
                    location=location,
                    popularity_score=tpl.get("popularity", 90),
                    vendor_name=tpl.get("vendor_name", ""),
                    base_retail_price=float(tpl.get("price_retail", 1000.0)),
                    stock_cartons=default_stock_cartons,
                    description=tpl.get("description", ""),
                    image_query=tpl.get("image_query", tpl["name"]),
                    source_label=f"Popular in {location}",
                )
                return {"item": item, "matched_via": "popular_catalog_match"}

    item = build_stock_item(
        name=raw,
        category="General",
        location=location,
        base_retail_price=1000.0,
        stock_cartons=default_stock_cartons,
        image_query=raw,
        source_label="Manual / Keyword Draft",
    )
    return {"item": item, "matched_via": "query_draft"}
