import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import re
from typing import Any, Coroutine, Dict, List, TypeVar, Union
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

USER_AGENT = "SammyreksBusinessCenter/4.0 (Calabar-Nigeria-POS)"

BASE_DIR = Path(__file__).resolve().parent
AUTO_STOCK_IMG_DIR = BASE_DIR / "static" / "uploads" / "auto_stock"
AUTO_STOCK_IMG_DIR.mkdir(parents=True, exist_ok=True)


def _run_coro_sync(coro: Coroutine[Any, Any, T]) -> T:
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


def _upgrade_off_image_url(url: str) -> str:
    clean = sanitize_url(url)
    if not clean:
        return ""
    if "openfoodfacts.org/images/products/" in clean:
        clean = re.sub(r"\.(100|200|400)\.(jpg|jpeg|png|webp)$", r".full.\2", clean, flags=re.I)
    return clean


def _score_off_product(product_obj: Dict[str, Any], query_words: set[str]) -> tuple[int, str]:
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
        "fields": "code,product_name,brands,image_front_url,image_url,selected_images,quantity,generic_name",
    }
    query_words = {w for w in re.findall(r"[a-z0-9]{3,}", search_query.lower())}
    try:
        resp = await client.get(OFF_SEARCH_URL, params=params, headers={"User-Agent": USER_AGENT})
        if resp.status_code != 200:
            return {}
        products = resp.json().get("products") or []
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
            if str(info.get("mime") or "") not in {"image/jpeg", "image/png", "image/webp"}:
                continue
            width = int(info.get("width") or 0)
            height = int(info.get("height") or 0)
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
    clean_remote = sanitize_url(remote_url)
    if not clean_remote or clean_remote.startswith("/static/"):
        return clean_remote

    safe_slug = re.sub(r"[^a-z0-9]+", "_", slug_hint.lower()).strip("_")[:40] or "item"
    url_hash = hashlib.sha1(clean_remote.encode("utf-8")).hexdigest()[:12]

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
        if resp.status_code != 200 or len(resp.content) < 5120:
            return ""

        ctype = str(resp.headers.get("content-type") or "").lower()
        if "png" in ctype or clean_remote.lower().endswith(".png"):
            ext = "png"
        elif "webp" in ctype or clean_remote.lower().endswith(".webp"):
            ext = "webp"
        else:
            ext = "jpg"

        filename = f"{safe_slug}_{url_hash}.{ext}"
        (AUTO_STOCK_IMG_DIR / filename).write_bytes(resp.content)
        return f"/static/uploads/auto_stock/{filename}"
    except Exception:
        return clean_remote


async def resolve_nice_product_image(
    client: httpx.AsyncClient,
    product_title: str,
    off_image_url: str,
) -> str:
    candidates: List[str] = []

    pexels_url = await fetch_pexels_clean_image(client, f"{product_title} product")
    if pexels_url:
        candidates.append(pexels_url)

    if off_image_url:
        candidates.append(_upgrade_off_image_url(off_image_url))

    wiki_url = await fetch_wikimedia_clean_image(client, product_title)
    if wiki_url:
        candidates.append(wiki_url)

    for candidate in candidates:
        imported = await import_image_to_store(client, candidate, product_title)
        if imported:
            return imported
    return ""


async def build_direct_name_product(
    client: httpx.AsyncClient,
    raw_name: str,
    user_category: str,
    default_stock: float,
    location: str,
) -> Dict[str, Any]:
    """Direct search strictly by the user's typed name, preserving the user's chosen category."""
    clean_query = raw_name.strip()
    chosen_category = (user_category or "").strip() or "General"
    off_data = await fetch_off_product_by_name(client, clean_query)

    off_title = (off_data.get("name") or "").strip()
    query_words = {w for w in re.findall(r"[a-z0-9]{3,}", clean_query.lower())}
    off_words = {w for w in re.findall(r"[a-z0-9]{3,}", off_title.lower())}

    if off_title and (query_words & off_words):
        final_name = off_title
        qty_tag = off_data.get("quantity")
        if qty_tag and qty_tag.lower() not in final_name.lower():
            final_name = f"{final_name} ({qty_tag})"
    else:
        final_name = clean_query.title()

    vendor_name = off_data.get("brand") or ""
    desc_base = off_data.get("generic_name") or f"Quality {final_name} available in Carton, Pack, or single Retail Unit."

    imported_img = await resolve_nice_product_image(
        client=client,
        product_title=clean_query,
        off_image_url=off_data.get("image_url", ""),
    )

    return {
        "selected": True,
        "name": final_name,
        "category": chosen_category,
        "vendor_name": vendor_name,
        "location": location,
        "main_unit": "Carton",
        "sub_unit": "Pack",
        "retail_unit": "Retail Unit",
        "packs_per_carton": 1,
        "units_per_pack": 1,
        "cost_price_carton": 0.0,
        "price_carton": 0.0,
        "price_pack": 0.0,
        "price_retail": 0.0,
        "stock_cartons": default_stock,
        "image_url": imported_img,
        "video_url": "",
        "description": desc_base,
    }


class NameBatchAutoStockRequest(BaseModel):
    product_names: Union[List[str], str] = Field(...)
    category: str = Field(default="")
    default_stock_cartons: float = Field(default=5.0, ge=0)
    location: str = Field(default="Calabar")


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
            build_direct_name_product(
                client=client,
                raw_name=name,
                user_category=payload.category,
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
async def lookup_barcode(
    barcode: str,
    category: str = Query(default=""),
    location: str = Query(default="Calabar"),
    default_stock_cartons: float = Query(default=5.0, ge=0),
) -> Dict[str, Any]:
    clean_query = (barcode or "").strip()
    if not clean_query:
        raise HTTPException(status_code=400, detail="Enter a barcode or product name.")

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
                        item = await build_direct_name_product(
                            client=client,
                            raw_name=raw_name,
                            user_category=category,
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
        item = await build_direct_name_product(
            client=client,
            raw_name=clean_query,
            user_category=category,
            default_stock=default_stock_cartons,
            location=location,
        )
        return {"status": "ok", "item": item}


# Compatibility shims if main.py imports these helpers
async def lookup_barcode_or_keyword_async(query: str, location: str = "Calabar", category: str = "") -> Dict[str, Any]:
    clean = str(query or "").strip()
    if not clean:
        return {}
    res = await lookup_barcode(clean, category=category, location=location)
    return res.get("item", {})


def lookup_barcode_or_keyword(query: str, location: str = "Calabar", category: str = "") -> Dict[str, Any]:
    return _run_coro_sync(lookup_barcode_or_keyword_async(query, location, category))
