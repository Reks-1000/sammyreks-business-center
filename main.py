import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import tempfile
import time
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request as UrlRequest, urlopen

import cloudinary
import cloudinary.uploader
import firebase_admin
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from firebase_admin import credentials, firestore

from auto_stock import (
    fetch_category_preview_items,
    lookup_barcode_or_keyword,
    router as auto_stock_router,
    sanitize_url,
)

app = FastAPI(title="Sammyreks Business Center")
app.include_router(auto_stock_router)
templates = Jinja2Templates(directory="templates")

LOCAL_ADS_DIR = os.path.join("static", "uploads", "ads")
os.makedirs(LOCAL_ADS_DIR, exist_ok=True)
if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_VIDEO_BYTES = 50 * 1024 * 1024
MAX_BATCH_ITEMS = 15
CACHE_TTL_SECONDS = 8.0
TOKEN_TTL_SECONDS = 60 * 60 * 24  # 24 hours
MAIN_SHOP_ID = "main"

PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "").strip()
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "").strip()
AUTO_STOCK_VIDEO_TARGET = (
    os.getenv("AUTO_STOCK_VIDEO_TARGET", "local").strip().lower()
)  # "local" or "cloudinary"


def init_firestore() -> firestore.Client:
    if firebase_admin._apps:
        return firestore.client()

    raw_json = (
        os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
        or os.getenv("FIREBASE_CREDENTIALS_JSON")
        or os.getenv("FIREBASE_SERVICE_ACCOUNT")
        or ""
    ).strip()

    key_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()

    if raw_json:
        if (raw_json.startswith("'") and raw_json.endswith("'")) or (
            raw_json.startswith('"') and raw_json.endswith('"')
        ):
            try:
                decoded_once = json.loads(raw_json)
                if isinstance(decoded_once, dict):
                    service_info = decoded_once
                elif isinstance(decoded_once, str):
                    service_info = json.loads(decoded_once)
                else:
                    raise ValueError("Unexpected JSON structure")
            except Exception:
                service_info = json.loads(raw_json[1:-1])
        else:
            try:
                service_info = json.loads(raw_json)
            except json.JSONDecodeError:
                decoded_b64 = base64.b64decode(raw_json).decode("utf-8")
                service_info = json.loads(decoded_b64)

        if not isinstance(service_info, dict):
            raise ValueError("Firebase service account JSON must decode to an object")

        if isinstance(service_info.get("private_key"), str):
            service_info["private_key"] = service_info["private_key"].replace("\\n", "\n")

        cred = credentials.Certificate(service_info)
        firebase_admin.initialize_app(cred)
    elif key_path and os.path.exists(key_path):
        cred = credentials.Certificate(key_path)
        firebase_admin.initialize_app(cred)
    else:
        raise RuntimeError(
            "Missing Firebase credentials. Set FIREBASE_SERVICE_ACCOUNT_JSON in Railway Variables."
        )

    return firestore.client()


def init_cloudinary() -> bool:
    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME", "").strip()
    api_key = os.getenv("CLOUDINARY_API_KEY", "").strip()
    api_secret = os.getenv("CLOUDINARY_API_SECRET", "").strip()

    if cloud_name and api_key and api_secret:
        cloudinary.config(
            cloud_name=cloud_name,
            api_key=api_key,
            api_secret=api_secret,
            secure=True,
        )
        return True
    return False


db = init_firestore()
cloudinary_ready = init_cloudinary()

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
SESSION_SECRET = (
    os.getenv("SESSION_SECRET")
    or hashlib.sha256(f"{ADMIN_USERNAME}:{ADMIN_PASSWORD}:sammyreks-v2".encode("utf-8")).hexdigest()
)

_attempt_buckets: Dict[str, List[float]] = defaultdict(list)

_local_cache: Dict[str, Tuple[float, Any]] = {}
_fallback_store: Dict[str, Any] = {
    "products": [],
    "settings": {"business_name": "Sammyreks Business Center", "currency": "₦"},
}


def _cache_get(key: str, ttl: float = CACHE_TTL_SECONDS) -> Optional[Any]:
    entry = _local_cache.get(key)
    if not entry:
        return None
    ts, val = entry
    if (time.time() - ts) <= ttl:
        return val
    return None


def _cache_set(key: str, value: Any) -> None:
    _local_cache[key] = (time.time(), value)


def _cache_invalidate(prefix: str = "") -> None:
    if not prefix:
        _local_cache.clear()
        return
    for k in [key for key in _local_cache if key.startswith(prefix)]:
        _local_cache.pop(k, None)


def make_slug(text: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return clean or f"shop-{uuid.uuid4().hex[:6]}"


def normalize_shop_id(raw_shop_id: Optional[str]) -> str:
    clean = (raw_shop_id or "").strip().lower()
    return clean if clean else MAIN_SHOP_ID


def validate_multi_tier_pricing(
    price_carton: float,
    price_pack: float,
    price_retail: float,
    packs_per_carton: int,
    units_per_pack: int,
) -> List[str]:
    if price_carton < 0 or price_pack < 0 or price_retail < 0:
        raise HTTPException(status_code=400, detail="Prices cannot be negative.")
    if packs_per_carton < 1 or units_per_pack < 1:
        raise HTTPException(status_code=400, detail="Pack and retail unit counts must be at least 1.")

    warnings: List[str] = []
    if packs_per_carton > 1 and price_carton > 0 and price_pack > price_carton:
        warnings.append(
            f"Pack price ({price_pack:.2f}) is higher than carton price ({price_carton:.2f}) for {packs_per_carton} packs/carton."
        )
    if units_per_pack > 1 and price_pack > 0 and price_retail > price_pack:
        warnings.append(
            f"Retail price ({price_retail:.2f}) is higher than pack price ({price_pack:.2f}) for {units_per_pack} units/pack."
        )
    if packs_per_carton > 1 and units_per_pack > 1 and price_carton > 0 and price_retail > price_carton:
        warnings.append(
            f"Retail price ({price_retail:.2f}) exceeds carton price ({price_carton:.2f})."
        )
    return warnings


def _check_rate_limit(key: str, max_attempts: int = 15, window_seconds: int = 300) -> None:
    now = time.time()
    recent = [t for t in _attempt_buckets[key] if now - t < window_seconds]
    if len(recent) >= max_attempts:
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Please wait a few minutes and try again.",
        )
    recent.append(now)
    _attempt_buckets[key] = recent


def _client_ip(request: Request) -> str:
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    if forwarded:
        return forwarded
    return request.client.host if request.client else "unknown"


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def make_ref() -> str:
    return f"SRK-{uuid.uuid4().hex[:6].upper()}"


def make_pin() -> str:
    return f"{secrets.randbelow(9000) + 1000}"


def hash_pin(pin_or_ref: str) -> str:
    clean = (pin_or_ref or "").strip().upper()
    return hashlib.sha256(f"{SESSION_SECRET}:pin:{clean}".encode("utf-8")).hexdigest()


def verify_pin(candidate: str, stored_value: str) -> bool:
    clean = (candidate or "").strip()
    stored = (stored_value or "").strip()
    if not clean or not stored:
        return False
    if len(stored) == 64 and all(ch in "0123456789abcdef" for ch in stored.lower()):
        return hmac.compare_digest(hash_pin(clean), stored.lower()) or hmac.compare_digest(
            hash_pin(clean.upper()), stored.lower()
        )
    return hmac.compare_digest(clean, stored) or hmac.compare_digest(clean.upper(), stored.upper())


def hash_password(plain_password: str) -> str:
    if not plain_password:
        return ""
    salt = secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac(
        "sha256", plain_password.encode("utf-8"), salt.encode("utf-8"), 100_000
    ).hex()
    return f"pbkdf2${salt}${digest}"


def verify_password(plain_password: str, stored_password: str) -> bool:
    if not stored_password:
        return plain_password == ""
    if stored_password.startswith("pbkdf2$"):
        parts = stored_password.split("$", 2)
        if len(parts) != 3:
            return False
        _, salt, expected_hex = parts
        actual_hex = hashlib.pbkdf2_hmac(
            "sha256", plain_password.encode("utf-8"), salt.encode("utf-8"), 100_000
        ).hex()
        return hmac.compare_digest(actual_hex, expected_hex)
    return hmac.compare_digest(plain_password, stored_password)


def issue_staff_token(username: str, role: str, shop_id: str = MAIN_SHOP_ID) -> str:
    payload = {
        "u": username,
        "r": role,
        "s": normalize_shop_id(shop_id),
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
    }
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    sig = hmac.new(
        SESSION_SECRET.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{payload_b64}.{sig}"


def parse_staff_token(request: Request) -> Optional[Dict[str, str]]:
    raw_token = (request.headers.get("X-Staff-Token") or "").strip()
    if not raw_token:
        auth_header = (request.headers.get("Authorization") or "").strip()
        if auth_header.lower().startswith("bearer "):
            raw_token = auth_header[7:].strip()
    if not raw_token or "." not in raw_token:
        return None
    payload_b64, sig = raw_token.split(".", 1)
    expected_sig = hmac.new(
        SESSION_SECRET.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return None
    try:
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if not isinstance(data, dict) or not data.get("u"):
            return None
        exp = data.get("exp")
        if exp is not None and int(time.time()) > int(exp):
            return None
        return {
            "username": str(data["u"]),
            "role": str(data.get("r") or "Staff"),
            "shop_id": normalize_shop_id(str(data.get("s") or MAIN_SHOP_ID)),
        }
    except Exception:
        return None


def is_super_admin(staff: Dict[str, str]) -> bool:
    return staff.get("role", "").strip().upper() == "CEO"


def resolve_effective_shop_id(staff: Dict[str, str], requested_shop_id: Optional[str] = None) -> str:
    """CEO can inspect any shop (or 'all'); all other roles are strictly locked to their token's shop_id."""
    if is_super_admin(staff):
        clean_req = (requested_shop_id or "").strip().lower()
        return clean_req if clean_req else MAIN_SHOP_ID
    return normalize_shop_id(staff.get("shop_id"))


def require_staff(request: Request) -> Dict[str, str]:
    staff = parse_staff_token(request)
    if not staff:
        raise HTTPException(status_code=401, detail="Staff session expired or login required")
    return staff


def require_super_admin(request: Request) -> Dict[str, str]:
    staff = require_staff(request)
    if not is_super_admin(staff):
        raise HTTPException(status_code=403, detail="Super Admin (CEO) permission required")
    return staff


def require_admin_or_ceo(request: Request) -> Dict[str, str]:
    staff = require_staff(request)
    role_upper = staff.get("role", "").strip().upper()
    if role_upper not in {"CEO", "ADMIN", "SHOPOWNER"}:
        raise HTTPException(status_code=403, detail="Admin, CEO, or Shop Owner permission required")
    return staff


def normalize_shop(doc_id: str, raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    data = dict(raw or {})
    slug = normalize_shop_id(str(data.get("slug") or doc_id))
    return {
        "id": doc_id,
        "slug": slug,
        "name": str(data.get("name") or "Partner Shop").strip(),
        "owner_name": str(data.get("owner_name") or "").strip(),
        "owner_username": str(data.get("owner_username") or "").strip(),
        "location": str(data.get("location") or "Calabar").strip(),
        "phone": str(data.get("phone") or "").strip(),
        "bank_details": str(data.get("bank_details") or "").strip(),
        "tagline": str(data.get("tagline") or "").strip(),
        "avatar_url": str(data.get("avatar_url") or "").strip(),
        "active": bool(data.get("active", True)),
        "created_at": str(data.get("created_at") or "").strip(),
    }


def normalize_product(doc_id: str, raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    data = dict(raw or {})
    price_carton = float(
        data.get("price_carton") if data.get("price_carton") is not None else (data.get("price") or 0.0)
    )
    stock_cartons = float(
        data.get("qty_cartons") if data.get("qty_cartons") is not None else (data.get("stock") or 0.0)
    )
    image_url = str(data.get("image_url") or data.get("img_data") or "").strip()
    vendor_name = str(data.get("vendor_name") or data.get("vendor") or data.get("sponsor") or "").strip()
    data["id"] = doc_id
    data["shop_id"] = normalize_shop_id(str(data.get("shop_id") or MAIN_SHOP_ID))
    data["name"] = str(data.get("name") or "Unnamed Product").strip()
    data["category"] = str(data.get("category") or "General").strip()
    data["location"] = str(data.get("location") or "Watt Market, Calabar").strip()
    data["vendor_name"] = vendor_name
    data["unit_main_label"] = str(data.get("unit_main_label") or "Carton").strip()
    data["unit_pack_label"] = str(data.get("unit_pack_label") or "Pack").strip()
    data["unit_retail_label"] = str(data.get("unit_retail_label") or "Retail Unit").strip()
    data["price_carton"] = price_carton
    data["price"] = price_carton
    data["qty_cartons"] = stock_cartons
    data["stock"] = stock_cartons
    data["cost"] = float(data.get("cost") or 0.0)
    data["price_pack"] = float(data.get("price_pack") or 0.0)
    data["price_retail"] = float(
        data.get("price_retail") if data.get("price_retail") is not None else price_carton
    )
    data["packs_per_carton"] = max(1, int(data.get("packs_per_carton") or 1))
    data["units_per_pack"] = max(1, int(data.get("units_per_pack") or 1))
    data["image_url"] = image_url
    data["video_url"] = str(data.get("video_url") or "").strip()
    data["description"] = str(data.get("description") or "").strip()
    data["shelf"] = str(data.get("shelf") or "").strip()
    data["expiry"] = str(data.get("expiry") or "").strip()
    return data


def normalize_order(doc_id: str, raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    data = dict(raw or {})
    data.pop("customer_pin", None)
    data.pop("customer_pin_hash", None)
    data.pop("payment_ref_hash", None)
    phone = str(data.get("contact_phone") or data.get("phone") or "").strip()
    customer = str(data.get("customer") or data.get("customer_name") or "Customer").strip()
    items = data.get("items") if isinstance(data.get("items"), list) else []
    special_items = data.get("special_items") if isinstance(data.get("special_items"), list) else []
    data["id"] = doc_id
    data["shop_id"] = normalize_shop_id(str(data.get("shop_id") or MAIN_SHOP_ID))
    data["customer"] = customer
    data["customer_name"] = customer
    data["contact_phone"] = phone
    data["phone"] = phone
    data["notes"] = str(data.get("notes") or "").strip()
    data["items"] = items
    data["special_items"] = special_items
    data["order_type"] = str(
        data.get("order_type") or ("Multi-Item Request" if special_items else "Cart Order")
    ).strip()
    data["payment_ref"] = str(data.get("payment_ref") or "").strip()
    data["status"] = str(data.get("status") or "Pending").strip()
    data["timestamp"] = str(data.get("timestamp") or "").strip()
    return data


def normalize_message(doc_id: str, raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    data = dict(raw or {})
    data.pop("customer_pin", None)
    data.pop("customer_pin_hash", None)
    data.pop("payment_ref_hash", None)
    phone = str(data.get("customer_phone") or data.get("phone") or "").strip()
    body = str(data.get("message") if data.get("message") is not None else (data.get("text") or "")).strip()
    data["id"] = doc_id
    data["shop_id"] = normalize_shop_id(str(data.get("shop_id") or MAIN_SHOP_ID))
    data["sender"] = str(data.get("sender") or "Customer").strip()
    data["customer_phone"] = phone
    data["phone"] = phone
    data["message"] = body
    data["text"] = body
    data["proof_url"] = str(data.get("proof_url") or "").strip()
    data["timestamp"] = str(data.get("timestamp") or "").strip()
    return data


def _looks_like_valid_media(header: bytes, resource_type: str) -> bool:
    if not header:
        return False
    if resource_type == "image":
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return True
        if header.startswith(b"\xff\xd8\xff"):
            return True
        if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
            return True
        if header.startswith((b"GIF87a", b"GIF89a")):
            return True
        return False
    if resource_type == "video":
        if len(header) >= 12 and header[4:8] == b"ftyp":
            return True
        if header.startswith(b"\x1a\x45\xdf\xa3"):
            return True
        if header.startswith(b"RIFF") and header[8:12] == b"AVI ":
            return True
        return False
    return True


def extract_cloudinary_public_id(url: str) -> Optional[str]:
    clean = (url or "").strip()
    if not clean or "res.cloudinary.com" not in clean:
        return None
    try:
        parsed = urlparse(clean)
        path = unquote(parsed.path or "")
        match = re.search(r"/(?:image|video|raw)/upload/(?:.+?/)?v\d+/(.+)$", path)
        if not match:
            match = re.search(r"/(?:image|video|raw)/upload/(.+)$", path)
        if not match:
            return None
        asset_path = match.group(1)
        public_id, _ = os.path.splitext(asset_path)
        return public_id or None
    except Exception:
        return None


def delete_from_cloudinary(
    public_id: Optional[str] = None,
    url: Optional[str] = None,
    resource_type: str = "image",
) -> bool:
    if not cloudinary_ready:
        return False
    target_id = (public_id or "").strip() or extract_cloudinary_public_id(url or "")
    if not target_id:
        return False
    try:
        res = cloudinary.uploader.destroy(target_id, resource_type=resource_type, invalidate=True)
        return res.get("result") in {"ok", "not found"}
    except Exception as exc:
        print(f"[WARN] Cloudinary destroy failed for {target_id} ({resource_type}): {exc}")
        return False


def upload_to_cloudinary(
    file: Optional[UploadFile],
    folder: str,
    resource_type: str = "image",
    max_dim: Optional[int] = None,
) -> Tuple[Optional[str], Optional[str]]:
    if not file or not file.filename:
        return None, None
    if not cloudinary_ready:
        raise HTTPException(
            status_code=500,
            detail="Cloudinary is not configured. Set CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET.",
        )

    content_type = (file.content_type or "").lower()
    if resource_type == "image" and content_type and not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files are allowed for this upload.")
    if resource_type == "video" and content_type and not content_type.startswith("video/"):
        raise HTTPException(status_code=400, detail="Only video files are allowed for this upload.")

    file.file.seek(0, os.SEEK_END)
    size_bytes = file.file.tell()
    max_allowed = MAX_VIDEO_BYTES if resource_type == "video" else MAX_IMAGE_BYTES
    if size_bytes <= 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if size_bytes > max_allowed:
        limit_mb = max_allowed // (1024 * 1024)
        raise HTTPException(status_code=400, detail=f"Uploaded file exceeds the {limit_mb}MB limit.")

    file.file.seek(0)
    header = file.file.read(32)
    if not _looks_like_valid_media(header, resource_type):
        raise HTTPException(status_code=400, detail=f"Invalid or unsupported {resource_type} file format.")

    upload_kwargs: Dict[str, Any] = {
        "folder": folder,
        "resource_type": resource_type,
    }
    if resource_type == "image" and max_dim:
        upload_kwargs["transformation"] = [
            {
                "width": max_dim,
                "height": max_dim,
                "crop": "limit",
                "quality": "auto",
                "fetch_format": "auto",
            }
        ]

    file.file.seek(0)
    result = cloudinary.uploader.upload(file.file, **upload_kwargs)
    return (result.get("secure_url") or result.get("url")), result.get("public_id")


# ==============================================================================
# REGISTERED PARTNER SHOPS (SIDE-RAIL DIRECTORY & SUPER-ADMIN MANAGEMENT)
# ==============================================================================

@app.get("/api/shops")
def list_shops(request: Request):
    staff = parse_staff_token(request)
    docs = db.collection("shops").stream()
    shops: List[Dict[str, Any]] = []
    for doc in docs:
        shop = normalize_shop(doc.id, doc.to_dict())
        if shop["active"] or (staff and is_super_admin(staff)):
            shops.append(shop)
    shops.sort(key=lambda x: str(x.get("name", "")).lower())
    return {"shops": shops}


@app.post("/api/shops")
async def register_shop(
    request: Request,
    name: str = Form(...),
    owner_name: str = Form(""),
    owner_username: str = Form(...),
    owner_password: str = Form(...),
    location: str = Form("Calabar"),
    phone: str = Form(""),
    bank_details: str = Form(""),
    tagline: str = Form(""),
    avatar_file: Optional[UploadFile] = File(None),
):
    """Super Admin (CEO) registers a business owner with an isolated portfolio & login."""
    require_super_admin(request)
    clean_name = name.strip()
    clean_user = owner_username.strip()
    if not clean_name or not clean_user or not owner_password:
        raise HTTPException(status_code=400, detail="Shop name, owner username, and password are required")

    if hmac.compare_digest(clean_user, ADMIN_USERNAME):
        raise HTTPException(status_code=400, detail="Owner username cannot match the Super Admin username")

    for existing_w in db.collection("workers").where("username", "==", clean_user).stream():
        if existing_w.exists:
            raise HTTPException(status_code=400, detail=f"Username '{clean_user}' is already taken")

    base_slug = make_slug(clean_name)
    slug = base_slug
    if db.collection("shops").document(slug).get().exists:
        slug = f"{base_slug}-{uuid.uuid4().hex[:4]}"

    avatar_url, avatar_public_id = upload_to_cloudinary(
        avatar_file, folder="sammyreks_shops", resource_type="image", max_dim=400
    )

    ts = now_str()
    shop_doc: Dict[str, Any] = {
        "slug": slug,
        "name": clean_name,
        "owner_name": owner_name.strip() or clean_name,
        "owner_username": clean_user,
        "location": location.strip() or "Calabar",
        "phone": phone.strip(),
        "bank_details": bank_details.strip(),
        "tagline": tagline.strip(),
        "avatar_url": avatar_url or "",
        "avatar_public_id": avatar_public_id or "",
        "active": True,
        "created_at": ts,
    }
    db.collection("shops").document(slug).set(shop_doc)

    worker_doc: Dict[str, Any] = {
        "name": owner_name.strip() or clean_name,
        "role": "ShopOwner",
        "shop_id": slug,
        "username": clean_user,
        "password": hash_password(owner_password),
        "timestamp": ts,
    }
    db.collection("workers").document(uuid.uuid4().hex).set(worker_doc)

    return {"ok": True, "shop": normalize_shop(slug, shop_doc)}


@app.delete("/api/shops/{shop_id}")
def delete_shop(shop_id: str, request: Request):
    require_super_admin(request)
    clean_id = normalize_shop_id(shop_id)
    if clean_id == MAIN_SHOP_ID:
        raise HTTPException(status_code=400, detail="Cannot delete the main store")

    shop_ref = db.collection("shops").document(clean_id)
    snap = shop_ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Registered shop not found")

    sdata = snap.to_dict() or {}
    delete_from_cloudinary(
        public_id=sdata.get("avatar_public_id"),
        url=sdata.get("avatar_url"),
        resource_type="image",
    )
    shop_ref.delete()

    for wdoc in db.collection("workers").where("shop_id", "==", clean_id).stream():
        wdoc.reference.delete()

    return {"ok": True, "id": clean_id}


# ==============================================================================
# AUTO-STOCK ROUTES (BARCODE, CATEGORY & NAME-FIRST BATCH SEARCH)
# ==============================================================================

def _http_get_json(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 12) -> Dict[str, Any]:
    req_headers = {
        "User-Agent": "SammyreksBusinessCenter/2.0 (Calabar, Nigeria; contact@sammyreks.local)",
        "Accept": "application/json",
    }
    if headers:
        req_headers.update(headers)
    req = UrlRequest(url, headers=req_headers, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} for {url}")
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw)


def _find_royalty_free_video_url(query_text: str) -> Optional[str]:
    clean_q = (query_text or "product commercial").strip()
    if PEXELS_API_KEY:
        try:
            url = f"https://api.pexels.com/videos/search?query={quote(clean_q)}&per_page=8&orientation=landscape"
            data = _http_get_json(url, headers={"Authorization": PEXELS_API_KEY}, timeout=10)
            for vid in data.get("videos") or []:
                duration = int(vid.get("duration") or 0)
                if duration and not (4 <= duration <= 25):
                    continue
                for vf in vid.get("video_files") or []:
                    w = int(vf.get("width") or 0)
                    h = int(vf.get("height") or 0)
                    link = str(vf.get("link") or "").strip()
                    if link and ".mp4" in link.lower() and (w == 0 or h == 0 or w >= h):
                        return link
        except Exception as exc:
            print(f"[WARN] Pexels video search failed: {exc}")

    if PIXABAY_API_KEY:
        try:
            url = f"https://pixabay.com/api/videos/?key={quote(PIXABAY_API_KEY)}&q={quote(clean_q)}&per_page=8"
            data = _http_get_json(url, timeout=10)
            for hit in data.get("hits") or []:
                duration = int(hit.get("duration") or 0)
                if duration and not (4 <= duration <= 25):
                    continue
                videos_map = hit.get("videos") or {}
                for quality in ("medium", "large", "small"):
                    info = videos_map.get(quality) or {}
                    link = str(info.get("url") or "").strip()
                    w = int(info.get("width") or 0)
                    h = int(info.get("height") or 0)
                    if link and (w == 0 or h == 0 or w >= h):
                        return link
        except Exception as exc:
            print(f"[WARN] Pixabay video search failed: {exc}")

    return None


def _download_video_advert(video_url: str, target_mode: Optional[str] = None) -> str:
    clean_url = (video_url or "").strip()
    if not clean_url:
        return ""
    parsed = urlparse(clean_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""

    mode = (target_mode or AUTO_STOCK_VIDEO_TARGET or "local").strip().lower()
    tmp_path: Optional[str] = None
    try:
        req = UrlRequest(clean_url, headers={"User-Agent": "SammyreksBusinessCenter/2.0"}, method="GET")
        with urlopen(req, timeout=20) as resp:
            if resp.status != 200:
                return ""
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
                tmp_path = tmp.name
                total_written = 0
                first_chunk = True
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    if first_chunk:
                        if not _looks_like_valid_media(chunk[:32], "video"):
                            return ""
                        first_chunk = False
                    total_written += len(chunk)
                    if total_written > MAX_VIDEO_BYTES:
                        return ""
                    tmp.write(chunk)

        if total_written <= 0:
            return ""

        if mode == "cloudinary" and cloudinary_ready:
            res = cloudinary.uploader.upload(
                tmp_path, folder="sammyreks_videos", resource_type="video"
            )
            return str(res.get("secure_url") or res.get("url") or "")

        filename = f"ad_{uuid.uuid4().hex[:12]}.mp4"
        dest_path = os.path.join(LOCAL_ADS_DIR, filename)
        os.replace(tmp_path, dest_path)
        tmp_path = None
        return f"/static/uploads/ads/{filename}"
    except Exception as exc:
        print(f"[WARN] Video advert download failed ({clean_url}): {exc}")
        return ""
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _infer_category_from_name(product_name: str) -> str:
    q = (product_name or "").strip().lower()
    rules: List[Tuple[str, List[str]]] = [
        ("Beverages", ["malt", "maltina", "sprite", "coke", "cola", "fanta", "pepsi", "juice", "drink", "water", "milk", "yoghurt", "yogurt", "tea", "coffee", "bovril", "milo", "bournvita", "peak"]),
        ("Food & Groceries", ["indomie", "noodle", "rice", "garri", "semo", "semolina", "beans", "spaghetti", "pasta", "oil", "sugar", "salt", "flour", "tin", "tomato", "seasoning", "maggi", "knorr", "biscuit", "bread"]),
        ("Household & Cleaning", ["hypo", "bleach", "soap", "detergent", "liquid soap", "dish", "omo", "ariel", "klin", "harpic", "sanitiser", "sanitizer", "tissue", "mop", "broom", "foil", "wrap"]),
        ("Personal Care & Cosmetics", ["lotion", "cream", "body wash", "shower", "shampoo", "hair", "perfume", "spray", "deodorant", "roll on", "toothpaste", "brush", "vaseline", "nivea", "dove"]),
        ("Electronics & Accessories", ["charger", "cable", "earphone", "headset", "power bank", "battery", "bulb", "led", "lamp", "socket", "extension", "speaker", "fan", "remote", "phone"]),
    ]
    for cat_name, keywords in rules:
        if any(kw in q for kw in keywords):
            return cat_name
    return "General"


@app.get("/api/auto-stock/barcode/{barcode:path}")
def auto_stock_by_barcode(
    barcode: str,
    request: Request,
    location: str = Query(default="Watt Market, Calabar"),
    default_stock_cartons: float = Query(default=5.0),
):
    require_staff(request)
    clean_code = unquote(barcode or "").strip()
    if not clean_code:
        raise HTTPException(status_code=400, detail="Valid barcode or product keyword is required")

    try:
        result = lookup_barcode_or_keyword(
            clean_code,
            default_stock_cartons=max(0.0, float(default_stock_cartons)),
            location=(location or "Watt Market, Calabar").strip() or "Watt Market, Calabar",
        )
        return {"ok": True, **result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Barcode/keyword lookup failed: {exc}")


@app.post("/api/auto-stock/search-names")
async def auto_stock_by_names(request: Request):
    require_staff(request)
    payload = await request.json()
    raw_names = payload.get("product_names") or []
    if isinstance(raw_names, str):
        names_list = [s.strip() for s in re.split(r"[,;\r\n]+", raw_names) if s.strip()]
    elif isinstance(raw_names, list):
        names_list = [str(s or "").strip() for s in raw_names if str(s or "").strip()]
    else:
        names_list = []

    if not names_list:
        raise HTTPException(status_code=400, detail="Provide at least one product name to search.")

    names_list = names_list[:MAX_BATCH_ITEMS]
    location = str(payload.get("location") or "Watt Market, Calabar").strip() or "Watt Market, Calabar"
    default_stock = max(
        0.0,
        float(
            payload.get("default_stock_cartons")
            if payload.get("default_stock_cartons") is not None
            else 5.0
        ),
    )

    items: List[Dict[str, Any]] = []
    for raw_name in names_list:
        inferred_cat = _infer_category_from_name(raw_name)
        try:
            lookup_res = lookup_barcode_or_keyword(
                raw_name,
                default_stock_cartons=default_stock,
                location=location,
            )
            item = dict(lookup_res.get("item") or {})
            item["name"] = str(item.get("name") or raw_name).strip() or raw_name
            fetched_cat = str(item.get("category") or "").strip()
            if not fetched_cat or fetched_cat.lower() in {"general", "uncategorized", "other"}:
                item["category"] = inferred_cat
            item["stock_cartons"] = float(
                item.get("stock_cartons") if item.get("stock_cartons") is not None else default_stock
            )
            item["image_url"] = sanitize_url(str(item.get("image_url") or ""))
            item["selected"] = True
            items.append(item)
        except Exception:
            items.append(
                {
                    "name": raw_name,
                    "category": inferred_cat,
                    "vendor_name": "",
                    "main_unit": "Carton",
                    "sub_unit": "Pack",
                    "retail_unit": "Retail Unit",
                    "price_carton": 0.0,
                    "stock_cartons": default_stock,
                    "cost_price_carton": 0.0,
                    "price_pack": 0.0,
                    "packs_per_carton": 1,
                    "price_retail": 0.0,
                    "units_per_pack": 1,
                    "image_url": "",
                    "video_url": "",
                    "description": f"{raw_name} — {location}",
                    "price_benchmark_note": "Enter carton, pack & retail prices before publishing",
                    "selected": True,
                }
            )

    return {
        "ok": True,
        "location": location,
        "count": len(items),
        "items": items,
    }


@app.post("/api/auto-stock/category")
async def auto_stock_by_category(request: Request):
    require_staff(request)
    payload = await request.json()
    category = str(payload.get("category") or "Beverages").strip() or "Beverages"
    location = str(payload.get("location") or "Watt Market, Calabar").strip() or "Watt Market, Calabar"
    count = max(1, min(MAX_BATCH_ITEMS, int(payload.get("count") or 6)))
    default_stock = max(
        0.0,
        float(
            payload.get("default_stock_cartons")
            if payload.get("default_stock_cartons") is not None
            else 5.0
        ),
    )
    fetch_video = bool(payload.get("fetch_video", False))

    try:
        preview = fetch_category_preview_items(
            category=category,
            count=count,
            default_stock_cartons=default_stock,
            fetch_video=fetch_video,
            location=location,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Auto-stock category search failed: {exc}")

    items = list(preview.get("items") or [])
    shared_video_url = ""
    if fetch_video and items:
        source_video = _find_royalty_free_video_url(f"{category} {location} product")
        if source_video:
            shared_video_url = _download_video_advert(source_video)
        if shared_video_url:
            for it in items:
                it["video_url"] = shared_video_url

    return {
        "ok": True,
        "category": category,
        "location": location,
        "count": len(items),
        "video_downloaded": bool(shared_video_url),
        "video_url": shared_video_url,
        "items": items,
    }


# ==============================================================================
# CORE STOREFRONT, AUTH, PRODUCTS, ORDERS & PIN-LOCKED CHAT ROUTES
# ==============================================================================

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"request": request})


@app.post("/api/login")
async def login(request: Request):
    _check_rate_limit(f"login:{_client_ip(request)}", max_attempts=15, window_seconds=300)
    payload = await request.json()
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    mode = str(payload.get("mode", "admin")).strip().lower()

    if hmac.compare_digest(username, ADMIN_USERNAME) and hmac.compare_digest(password, ADMIN_PASSWORD):
        role = "CEO" if mode == "admin" else "Staff"
        token = issue_staff_token(username, role, shop_id=MAIN_SHOP_ID)
        return {
            "ok": True,
            "username": username,
            "role": role,
            "shop_id": MAIN_SHOP_ID,
            "token": token,
        }

    worker_docs = db.collection("workers").where("username", "==", username).stream()
    for doc in worker_docs:
        w = doc.to_dict() or {}
        if verify_password(password, str(w.get("password", ""))):
            resolved_user = w.get("username") or w.get("name") or username
            resolved_role = w.get("role") or "Staff"
            resolved_shop_id = normalize_shop_id(str(w.get("shop_id") or MAIN_SHOP_ID))
            token = issue_staff_token(resolved_user, resolved_role, shop_id=resolved_shop_id)
            return {
                "ok": True,
                "username": resolved_user,
                "role": resolved_role,
                "shop_id": resolved_shop_id,
                "token": token,
            }

    raise HTTPException(status_code=401, detail="Invalid username or password")


@app.get("/api/products")
def get_products(shop_id: str = Query(default=MAIN_SHOP_ID)):
    target_shop = normalize_shop_id(shop_id)
    cache_key = f"products:list:{target_shop}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return {"products": cached, "shop_id": target_shop, "cached": True}

    try:
        docs = db.collection("products").stream()
        products: List[Dict[str, Any]] = []
        for doc in docs:
            p = normalize_product(doc.id, doc.to_dict())
            if target_shop == "all" or p["shop_id"] == target_shop:
                products.append(p)
        products.sort(key=lambda x: str(x.get("name", "")).lower())
        _cache_set(cache_key, products)
        if target_shop == MAIN_SHOP_ID:
            _fallback_store["products"] = products
        return {"products": products, "shop_id": target_shop, "cached": False}
    except Exception as exc:
        print(f"[WARN] /api/products fallback triggered: {exc}")
        fallback = list(_fallback_store.get("products") or []) if target_shop == MAIN_SHOP_ID else []
        return {"products": fallback, "shop_id": target_shop, "cached": True, "fallback": True}


@app.post("/api/products")
async def save_product(
    request: Request,
    action_mode: str = Form("create"),
    existing_product_id: str = Form(""),
    shop_id: str = Form(MAIN_SHOP_ID),
    category: str = Form("General"),
    location: str = Form("Watt Market, Calabar"),
    vendor_name: str = Form(""),
    main_unit: str = Form("Carton"),
    sub_unit: str = Form("Pack"),
    retail_unit: str = Form("Retail Unit"),
    name: str = Form(...),
    price_carton: float = Form(0.0),
    stock_cartons: float = Form(0.0),
    cost_price_carton: float = Form(0.0),
    price_pack: float = Form(0.0),
    packs_per_carton: int = Form(1),
    shelf_location: str = Form(""),
    price_retail: float = Form(0.0),
    retail_per_pack: int = Form(1),
    expiry_date: str = Form(""),
    video_link: str = Form(""),
    image_url: str = Form(""),
    description: str = Form(""),
    image_file: Optional[UploadFile] = File(None),
    video_file: Optional[UploadFile] = File(None),
):
    staff = require_staff(request)
    effective_shop = resolve_effective_shop_id(staff, shop_id)
    if effective_shop == "all":
        effective_shop = MAIN_SHOP_ID

    clean_name = name.strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail="Product name is required")

    clean_packs_per_carton = max(1, int(packs_per_carton or 1))
    clean_retail_per_pack = max(1, int(retail_per_pack or 1))
    pricing_warnings = validate_multi_tier_pricing(
        price_carton=float(price_carton),
        price_pack=float(price_pack),
        price_retail=float(price_retail),
        packs_per_carton=clean_packs_per_carton,
        units_per_pack=clean_retail_per_pack,
    )

    target_id = existing_product_id.strip() if action_mode == "update" else ""
    existing_data: Dict[str, Any] = {}

    if target_id:
        snap = db.collection("products").document(target_id).get()
        if not snap.exists:
            raise HTTPException(status_code=404, detail="Selected product not found")
        existing_data = snap.to_dict() or {}
        existing_shop = normalize_shop_id(str(existing_data.get("shop_id") or MAIN_SHOP_ID))
        if not is_super_admin(staff) and existing_shop != staff["shop_id"]:
            raise HTTPException(status_code=403, detail="You cannot edit another shop's product")
        effective_shop = existing_shop
    else:
        target_id = uuid.uuid4().hex

    clean_vendor = vendor_name.strip()
    if not clean_vendor and existing_data:
        clean_vendor = str(
            existing_data.get("vendor_name")
            or existing_data.get("vendor")
            or existing_data.get("sponsor")
            or ""
        ).strip()

    old_image_url = str(existing_data.get("image_url") or existing_data.get("img_data") or "").strip()
    old_image_public_id = str(existing_data.get("image_public_id") or "").strip()
    incoming_safe_img = sanitize_url(image_url)

    new_image_url, new_image_public_id = upload_to_cloudinary(
        image_file, folder="sammyreks_products", resource_type="image", max_dim=800
    )
    if new_image_url:
        if old_image_url and old_image_url != new_image_url:
            delete_from_cloudinary(
                public_id=old_image_public_id, url=old_image_url, resource_type="image"
            )
        final_image_url = new_image_url
        final_image_public_id = new_image_public_id or ""
    elif incoming_safe_img:
        if old_image_url and old_image_url != incoming_safe_img:
            delete_from_cloudinary(
                public_id=old_image_public_id, url=old_image_url, resource_type="image"
            )
        final_image_url = incoming_safe_img
        final_image_public_id = ""
    else:
        final_image_url = old_image_url
        final_image_public_id = old_image_public_id

    old_video_url = str(existing_data.get("video_url") or "").strip()
    old_video_public_id = str(existing_data.get("video_public_id") or "").strip()

    uploaded_video_url, uploaded_video_public_id = upload_to_cloudinary(
        video_file, folder="sammyreks_videos", resource_type="video"
    )
    clean_video_link = video_link.strip()

    if uploaded_video_url:
        if old_video_url and old_video_url != uploaded_video_url:
            delete_from_cloudinary(
                public_id=old_video_public_id, url=old_video_url, resource_type="video"
            )
        final_video_url = uploaded_video_url
        final_video_public_id = uploaded_video_public_id or ""
    elif clean_video_link:
        if old_video_url and old_video_url != clean_video_link:
            delete_from_cloudinary(
                public_id=old_video_public_id, url=old_video_url, resource_type="video"
            )
        final_video_url = clean_video_link
        final_video_public_id = (
            old_video_public_id
            if clean_video_link == old_video_url
            else (extract_cloudinary_public_id(clean_video_link) or "")
        )
    else:
        if existing_data and old_video_url:
            delete_from_cloudinary(
                public_id=old_video_public_id, url=old_video_url, resource_type="video"
            )
        final_video_url = ""
        final_video_public_id = ""

    product_doc: Dict[str, Any] = {
        "shop_id": effective_shop,
        "name": clean_name,
        "category": category.strip() or "General",
        "location": location.strip() or "Watt Market, Calabar",
        "vendor_name": clean_vendor,
        "unit_main_label": main_unit.strip() or "Carton",
        "unit_pack_label": sub_unit.strip() or "Pack",
        "unit_retail_label": retail_unit.strip() or "Retail Unit",
        "price_carton": float(price_carton),
        "price": float(price_carton),
        "qty_cartons": float(stock_cartons),
        "stock": float(stock_cartons),
        "cost": float(cost_price_carton),
        "price_pack": float(price_pack),
        "packs_per_carton": clean_packs_per_carton,
        "shelf": shelf_location.strip(),
        "price_retail": float(price_retail),
        "units_per_pack": clean_retail_per_pack,
        "expiry": expiry_date.strip(),
        "video_url": final_video_url,
        "video_public_id": final_video_public_id,
        "description": description.strip(),
        "image_url": final_image_url,
        "image_public_id": final_image_public_id,
        "updated_at": now_str(),
    }
    if not existing_data:
        product_doc["created_at"] = now_str()

    db.collection("products").document(target_id).set(product_doc, merge=True)
    _cache_invalidate("products:")
    normalized = normalize_product(target_id, {**existing_data, **product_doc})
    return {
        "ok": True,
        "id": target_id,
        "product": normalized,
        "pricing_warnings": pricing_warnings,
    }


@app.delete("/api/products/{product_id}")
def delete_product(product_id: str, request: Request):
    staff = require_staff(request)
    doc_ref = db.collection("products").document(product_id)
    snap = doc_ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Product not found")

    pdata = snap.to_dict() or {}
    existing_shop = normalize_shop_id(str(pdata.get("shop_id") or MAIN_SHOP_ID))
    if not is_super_admin(staff) and existing_shop != staff["shop_id"]:
        raise HTTPException(status_code=403, detail="You cannot delete another shop's product")

    delete_from_cloudinary(
        public_id=pdata.get("image_public_id"),
        url=pdata.get("image_url") or pdata.get("img_data"),
        resource_type="image",
    )
    delete_from_cloudinary(
        public_id=pdata.get("video_public_id"),
        url=pdata.get("video_url"),
        resource_type="video",
    )

    doc_ref.delete()
    _cache_invalidate("products:")
    return {"ok": True, "id": product_id}


@app.get("/api/orders")
def get_orders(request: Request, shop_id: Optional[str] = Query(default=None)):
    staff = require_staff(request)
    effective_shop = resolve_effective_shop_id(staff, shop_id)
    docs = db.collection("orders").stream()
    orders: List[Dict[str, Any]] = []
    for doc in docs:
        order = normalize_order(doc.id, doc.to_dict())
        if effective_shop == "all" or order["shop_id"] == effective_shop:
            orders.append(order)
    orders.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)
    return {"orders": orders, "shop_id": effective_shop}


@app.post("/api/orders")
async def create_order(request: Request):
    _check_rate_limit(f"order:{_client_ip(request)}", max_attempts=20, window_seconds=300)
    payload = await request.json()
    target_shop = normalize_shop_id(str(payload.get("shop_id") or MAIN_SHOP_ID))
    customer = str(payload.get("customer") or payload.get("customer_name") or "Customer").strip()
    phone = str(payload.get("contact_phone") or payload.get("phone") or "").strip()
    notes = str(payload.get("notes") or "").strip()
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    special_items = payload.get("special_items") if isinstance(payload.get("special_items"), list) else []
    requested_pin = str(payload.get("pin") or "").strip()
    customer_pin = requested_pin if requested_pin else make_pin()
    payment_ref = make_ref()
    ts = now_str()

    if not phone:
        raise HTTPException(status_code=400, detail="Phone number is required")
    if len(items) > MAX_BATCH_ITEMS or len(special_items) > MAX_BATCH_ITEMS:
        raise HTTPException(
            status_code=400,
            detail=f"Batch size cannot exceed {MAX_BATCH_ITEMS} items per submission.",
        )

    if special_items:
        req_summary = "; ".join(str(sp.get("item") or sp.get("name") or "") for sp in special_items if sp)
        order_type = "Multi-Item Request"
        chat_text = f"Multi-Item Request from {customer} ({phone}): {req_summary}. Ref: {payment_ref}"
    else:
        item_parts = []
        total_sum = 0.0
        for it in items:
            it_name = str(it.get("name") or "Item")
            it_unit = str(it.get("unit") or "Unit")
            it_qty = it.get("qty") or 1
            it_total = float(it.get("total_price") or 0.0)
            total_sum += it_total
            item_parts.append(f"{it_qty} {it_unit}(s) of {it_name}")
        items_str = ", ".join(item_parts) if item_parts else "Cart Order"
        note_str = f" Note: {notes}" if notes else ""
        order_type = "Cart Order"
        chat_text = (
            f"Hello! I just submitted an order. Items: {items_str}. "
            f"Total: ₦{total_sum:,.2f}. My Reference Code is '{payment_ref}'.{note_str}"
        )

    pin_hash = hash_pin(customer_pin)
    ref_hash = hash_pin(payment_ref)

    order_doc: Dict[str, Any] = {
        "shop_id": target_shop,
        "order_type": order_type,
        "customer": customer,
        "contact_phone": phone,
        "phone": phone,
        "customer_pin_hash": pin_hash,
        "payment_ref_hash": ref_hash,
        "notes": notes,
        "items": items,
        "special_items": special_items,
        "payment_ref": payment_ref,
        "status": "Pending",
        "timestamp": ts,
    }
    order_id = uuid.uuid4().hex
    db.collection("orders").document(order_id).set(order_doc)

    msg_doc: Dict[str, Any] = {
        "shop_id": target_shop,
        "sender": "Customer",
        "customer_phone": phone,
        "phone": phone,
        "customer_pin_hash": pin_hash,
        "payment_ref_hash": ref_hash,
        "message": chat_text,
        "text": chat_text,
        "payment_ref": payment_ref,
        "timestamp": ts,
    }
    db.collection("messages").document(uuid.uuid4().hex).set(msg_doc)

    return {
        "ok": True,
        "id": order_id,
        "shop_id": target_shop,
        "payment_ref": payment_ref,
        "customer_pin": customer_pin,
    }


@app.post("/api/orders/{order_id}/verify")
async def verify_order(order_id: str, request: Request):
    staff = require_staff(request)
    order_ref = db.collection("orders").document(order_id)
    snap = order_ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Order not found")

    order_data = snap.to_dict() or {}
    order_shop = normalize_shop_id(str(order_data.get("shop_id") or MAIN_SHOP_ID))
    if not is_super_admin(staff) and order_shop != staff["shop_id"]:
        raise HTTPException(status_code=403, detail="You cannot verify orders for another shop")

    payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    verified_by = str(payload.get("verified_by") or staff.get("username") or "admin").strip()

    if order_shop == MAIN_SHOP_ID:
        settings_snap = db.collection("settings").document("business").get()
        settings_data = settings_snap.to_dict() if settings_snap.exists else {}
        raw_biz = str(settings_data.get("business_name") or "SAMMYREKS BUSINESS CENTER")
        currency = html.escape(str(settings_data.get("currency") or "₦"))
    else:
        shop_snap = db.collection("shops").document(order_shop).get()
        shop_data = shop_snap.to_dict() if shop_snap.exists else {}
        raw_biz = str(shop_data.get("name") or order_shop)
        currency = "₦"

    biz_name = html.escape(raw_biz.upper())
    customer = html.escape(str(order_data.get("customer") or order_data.get("customer_name") or "Customer").strip())
    phone_raw = str(order_data.get("contact_phone") or order_data.get("phone") or "").strip()
    phone_esc = html.escape(phone_raw or "N/A")
    ref_code = str(order_data.get("payment_ref") or make_ref()).strip()
    ref_esc = html.escape(ref_code)
    verified_by_esc = html.escape(verified_by)
    ts = now_str()

    items = order_data.get("items") or []
    special_items = order_data.get("special_items") or []
    lines_html = ""
    total_val = 0.0

    if special_items:
        for sp in special_items:
            sp_text = html.escape(str(sp.get("item") or sp.get("name") or "Requested Item"))
            lines_html += f"<p style='margin:2px 0;'>{sp_text}</p>"
    elif items:
        for it in items:
            it_name = html.escape(str(it.get("name") or "Item"))
            it_unit = html.escape(str(it.get("unit") or "Unit"))
            it_qty = html.escape(str(it.get("qty") or 1))
            it_total = float(it.get("total_price") or 0.0)
            total_val += it_total
            lines_html += (
                f"<p style='margin:2px 0;'>{it_qty} {it_unit}(s) of {it_name} &mdash; "
                f"{currency}{it_total:,.2f}</p>"
            )
    elif order_data.get("notes"):
        lines_html += f"<p style='margin:2px 0;'>{html.escape(str(order_data.get('notes')))}</p>"

    receipt_html = (
        f"<div style='background:#fdfcf7; color:#111; padding:16px; border-radius:6px; "
        f"border:1px solid #dcd6cd; font-family:\"Courier New\", Courier, monospace; "
        f"max-width:360px; margin:6px 0;'>"
        f"<div style='text-align:center; border-bottom:1px dashed #333; padding-bottom:8px; "
        f"margin-bottom:8px;'>"
        f"<h5 style='margin:0; font-weight:bold;'>{biz_name}</h5>"
        f"<div style='font-size:0.75rem; color:#444; margin:2px 0;'>OFFICIAL PURCHASE RECEIPT</div>"
        f"<span style='background:#198754; color:#fff; font-size:0.72rem; padding:2px 6px; "
        f"border-radius:3px; font-weight:bold;'>PAID &amp; VERIFIED</span>"
        f"</div>"
        f"<div style='font-size:0.8rem; margin-bottom:8px; border-bottom:1px dashed #333; "
        f"padding-bottom:6px;'>"
        f"<p style='margin:2px 0;'><b>REF CODE:</b> {ref_esc}</p>"
        f"<p style='margin:2px 0;'><b>CUSTOMER:</b> {customer}</p>"
        f"<p style='margin:2px 0;'><b>PHONE:</b> {phone_esc}</p>"
        f"<p style='margin:2px 0;'><b>VERIFIED BY:</b> {verified_by_esc}</p>"
        f"</div>"
        f"<div style='font-size:0.8rem; margin-bottom:8px;'>{lines_html}</div>"
        f"<div style='border-top:1px dashed #333; padding-top:6px; font-size:0.85rem; "
        f"font-weight:bold;'>"
        f"TOTAL: {currency}{total_val:,.2f}"
        f"</div>"
        f"</div>"
    )

    order_ref.set({"status": "Verified", "verified_by": verified_by, "verified_at": ts}, merge=True)

    msg_doc: Dict[str, Any] = {
        "shop_id": order_shop,
        "sender": "System",
        "customer_phone": phone_raw,
        "phone": phone_raw,
        "customer_pin_hash": order_data.get("customer_pin_hash") or "",
        "payment_ref_hash": order_data.get("payment_ref_hash") or hash_pin(ref_code),
        "message": receipt_html,
        "text": receipt_html,
        "payment_ref": ref_code,
        "timestamp": ts,
    }
    db.collection("messages").document(uuid.uuid4().hex).set(msg_doc)

    return {"ok": True, "id": order_id, "status": "Verified", "payment_ref": ref_code}


@app.delete("/api/orders/{order_id}")
def delete_order(order_id: str, request: Request):
    staff = require_staff(request)
    order_ref = db.collection("orders").document(order_id)
    snap = order_ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Order not found")
    order_data = snap.to_dict() or {}
    order_shop = normalize_shop_id(str(order_data.get("shop_id") or MAIN_SHOP_ID))
    if not is_super_admin(staff) and order_shop != staff["shop_id"]:
        raise HTTPException(status_code=403, detail="You cannot delete orders for another shop")
    order_ref.delete()
    return {"ok": True, "id": order_id}


def _doc_matches_pin_or_ref(doc_dict: Dict[str, Any], clean_pin: str) -> bool:
    stored_pin_hash = str(doc_dict.get("customer_pin_hash") or "").strip()
    stored_ref_hash = str(doc_dict.get("payment_ref_hash") or "").strip()
    if stored_pin_hash and verify_pin(clean_pin, stored_pin_hash):
        return True
    if stored_ref_hash and verify_pin(clean_pin, stored_ref_hash):
        return True

    legacy_pin = str(doc_dict.get("customer_pin") or "").strip()
    legacy_ref = str(doc_dict.get("payment_ref") or "").strip()
    if legacy_pin and verify_pin(clean_pin, legacy_pin):
        return True
    if legacy_ref and verify_pin(clean_pin, legacy_ref):
        return True
    return False


def _customer_pin_authorized(phone: str, pin: str, shop_id: str = MAIN_SHOP_ID) -> bool:
    clean_pin = (pin or "").strip()
    target_shop = normalize_shop_id(shop_id)
    if not phone or not clean_pin:
        return False

    for doc in db.collection("orders").where("contact_phone", "==", phone).stream():
        d = doc.to_dict() or {}
        if normalize_shop_id(str(d.get("shop_id") or MAIN_SHOP_ID)) != target_shop:
            continue
        if _doc_matches_pin_or_ref(d, clean_pin):
            return True

    for doc in db.collection("messages").where("customer_phone", "==", phone).stream():
        d = doc.to_dict() or {}
        if normalize_shop_id(str(d.get("shop_id") or MAIN_SHOP_ID)) != target_shop:
            continue
        if _doc_matches_pin_or_ref(d, clean_pin):
            return True

    return False


@app.get("/api/messages")
def get_messages(
    request: Request,
    phone: Optional[str] = Query(default=None),
    pin: Optional[str] = Query(default=None),
    shop_id: Optional[str] = Query(default=MAIN_SHOP_ID),
):
    staff = parse_staff_token(request)
    clean_phone = (phone or "").strip()
    clean_pin = (pin or "").strip()

    if not staff:
        target_shop = normalize_shop_id(shop_id)
        _check_rate_limit(f"pin:{_client_ip(request)}:{target_shop}:{clean_phone}", max_attempts=15, window_seconds=300)
        if not clean_phone or not clean_pin:
            raise HTTPException(status_code=401, detail="Enter your phone number and PIN or Order Ref to view chat")
        if not _customer_pin_authorized(clean_phone, clean_pin, shop_id=target_shop):
            raise HTTPException(status_code=401, detail="Invalid phone number, PIN, or Order Reference Code")
    else:
        target_shop = resolve_effective_shop_id(staff, shop_id)

    docs = db.collection("messages").stream()
    messages: List[Dict[str, Any]] = []
    for doc in docs:
        data = normalize_message(doc.id, doc.to_dict())
        if target_shop != "all" and data["shop_id"] != target_shop:
            continue
        if clean_phone and data["customer_phone"] != clean_phone:
            continue
        messages.append(data)
    messages.sort(key=lambda x: str(x.get("timestamp", "")))
    return {"messages": messages, "shop_id": target_shop}


@app.post("/api/messages")
async def post_message(
    request: Request,
    phone: str = Form(...),
    sender: str = Form("Customer"),
    text: str = Form(""),
    pin: str = Form(""),
    shop_id: str = Form(MAIN_SHOP_ID),
    proof_file: Optional[UploadFile] = File(None),
):
    staff = parse_staff_token(request)
    clean_phone = phone.strip()
    clean_text = text.strip()
    clean_sender = sender.strip() or "Customer"

    if not clean_phone:
        raise HTTPException(status_code=400, detail="Phone number is required")

    if clean_sender.lower() == "staff" and not staff:
        raise HTTPException(status_code=401, detail="Staff login required to send staff replies")

    if not staff:
        target_shop = normalize_shop_id(shop_id)
        _check_rate_limit(f"msg:{_client_ip(request)}:{target_shop}:{clean_phone}", max_attempts=20, window_seconds=300)
        if not _customer_pin_authorized(clean_phone, pin, shop_id=target_shop):
            raise HTTPException(status_code=401, detail="Unlock your chat with your phone number and PIN or Order Ref first")
    else:
        target_shop = resolve_effective_shop_id(staff, shop_id)
        if target_shop == "all":
            target_shop = MAIN_SHOP_ID

    proof_url, proof_public_id = upload_to_cloudinary(
        proof_file, folder="sammyreks_receipts", resource_type="image", max_dim=1000
    )

    if not clean_text and not proof_url:
        raise HTTPException(status_code=400, detail="Enter a message or attach a receipt image")

    msg_doc: Dict[str, Any] = {
        "shop_id": target_shop,
        "sender": clean_sender,
        "customer_phone": clean_phone,
        "phone": clean_phone,
        "message": clean_text,
        "text": clean_text,
        "proof_url": proof_url or "",
        "proof_public_id": proof_public_id or "",
        "timestamp": now_str(),
    }
    msg_id = uuid.uuid4().hex
    db.collection("messages").document(msg_id).set(msg_doc)
    return {"ok": True, "id": msg_id, "shop_id": target_shop, "proof_url": proof_url}


@app.delete("/api/messages/{message_id}/photo")
def delete_message_photo(message_id: str, request: Request):
    staff = require_staff(request)
    doc_ref = db.collection("messages").document(message_id)
    snap = doc_ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Message not found")

    mdata = snap.to_dict() or {}
    msg_shop = normalize_shop_id(str(mdata.get("shop_id") or MAIN_SHOP_ID))
    if not is_super_admin(staff) and msg_shop != staff["shop_id"]:
        raise HTTPException(status_code=403, detail="You cannot modify messages for another shop")

    delete_from_cloudinary(
        public_id=mdata.get("proof_public_id"),
        url=mdata.get("proof_url"),
        resource_type="image",
    )

    doc_ref.set({"proof_url": "", "proof_public_id": ""}, merge=True)
    return {"ok": True, "id": message_id}


@app.delete("/api/messages/photos")
def delete_all_message_photos(request: Request, shop_id: Optional[str] = Query(default=None)):
    staff = require_staff(request)
    effective_shop = resolve_effective_shop_id(staff, shop_id)
    docs = db.collection("messages").stream()
    cleared = 0
    for doc in docs:
        data = doc.to_dict() or {}
        doc_shop = normalize_shop_id(str(data.get("shop_id") or MAIN_SHOP_ID))
        if effective_shop != "all" and doc_shop != effective_shop:
            continue
        if data.get("proof_url") or data.get("proof_public_id"):
            delete_from_cloudinary(
                public_id=data.get("proof_public_id"),
                url=data.get("proof_url"),
                resource_type="image",
            )
            doc.reference.set({"proof_url": "", "proof_public_id": ""}, merge=True)
            cleared += 1
    return {"ok": True, "cleared": cleared}


@app.get("/api/sales")
def get_sales(request: Request, shop_id: Optional[str] = Query(default=None)):
    staff = require_staff(request)
    effective_shop = resolve_effective_shop_id(staff, shop_id)
    docs = db.collection("sales").stream()
    sales: List[Dict[str, Any]] = []
    for doc in docs:
        item = dict(doc.to_dict() or {})
        item["id"] = doc.id
        item["shop_id"] = normalize_shop_id(str(item.get("shop_id") or MAIN_SHOP_ID))
        if effective_shop == "all" or item["shop_id"] == effective_shop:
            sales.append(item)
    sales.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)
    return {"sales": sales, "shop_id": effective_shop}


@app.post("/api/sales")
async def record_sale(request: Request):
    staff = require_staff(request)
    payload = await request.json()
    effective_shop = resolve_effective_shop_id(staff, str(payload.get("shop_id") or MAIN_SHOP_ID))
    if effective_shop == "all":
        effective_shop = MAIN_SHOP_ID

    product_id = str(payload.get("product_id") or "").strip()
    item_name = str(payload.get("item_name") or "Item").strip()
    unit_type = str(payload.get("unit_type") or "Carton").strip()
    qty = max(1.0, float(payload.get("qty") or 1))
    customer_name = str(payload.get("customer_name") or "Walk-in Customer").strip()
    customer_phone = str(payload.get("customer_phone") or "N/A").strip()
    payment_source = str(payload.get("payment_source") or "Cash").strip()
    worker = staff.get("username") or "admin"
    record_debt = bool(payload.get("record_debt", False))

    unit_price = float(payload.get("unit_price") or 0.0)
    total = float(payload.get("total") or (unit_price * qty))
    payment_ref = make_ref()
    ts = now_str()

    if product_id:
        prod_ref = db.collection("products").document(product_id)

        @firestore.transactional
        def _deduct_stock_tx(transaction: firestore.Transaction, ref: firestore.DocumentReference):
            snap = ref.get(transaction=transaction)
            if not snap.exists:
                return
            pdata = snap.to_dict() or {}
            m_lbl = str(pdata.get("unit_main_label") or "Carton")
            p_lbl = str(pdata.get("unit_pack_label") or "Pack")
            packs_per_carton = max(1.0, float(pdata.get("packs_per_carton") or 1))
            units_per_pack = max(1.0, float(pdata.get("units_per_pack") or 1))

            if unit_type == m_lbl:
                deduct_cartons = qty
            elif unit_type == p_lbl:
                deduct_cartons = qty / packs_per_carton
            else:
                deduct_cartons = qty / (packs_per_carton * units_per_pack)

            current_stock = float(
                pdata.get("qty_cartons")
                if pdata.get("qty_cartons") is not None
                else (pdata.get("stock") or 0)
            )
            new_stock = max(0.0, current_stock - deduct_cartons)
            transaction.set(ref, {"qty_cartons": new_stock, "stock": new_stock, "updated_at": ts}, merge=True)

        _deduct_stock_tx(db.transaction(), prod_ref)
        _cache_invalidate("products:")

    if record_debt:
        debt_doc = {
            "shop_id": effective_shop,
            "customer": customer_name,
            "phone": customer_phone,
            "amount": total,
            "items_bought": f"{item_name} ({qty} {unit_type})",
            "payment_ref": payment_ref,
            "worker": worker,
            "status": "unpaid",
            "timestamp": ts,
        }
        db.collection("debtors").document(uuid.uuid4().hex).set(debt_doc)

    sale_doc = {
        "shop_id": effective_shop,
        "product_id": product_id,
        "item_name": item_name,
        "unit_type": unit_type,
        "qty": qty,
        "unit_price": unit_price,
        "total": total,
        "customer_name": customer_name,
        "customer_phone": customer_phone,
        "payment_source": payment_source,
        "worker": worker,
        "record_debt": record_debt,
        "payment_ref": payment_ref,
        "timestamp": ts,
    }
    db.collection("sales").document(uuid.uuid4().hex).set(sale_doc)

    return {"ok": True, "receipt": sale_doc}


@app.get("/api/debtors")
def get_debtors(request: Request, shop_id: Optional[str] = Query(default=None)):
    staff = require_staff(request)
    effective_shop = resolve_effective_shop_id(staff, shop_id)
    docs = db.collection("debtors").stream()
    debtors: List[Dict[str, Any]] = []
    for doc in docs:
        item = dict(doc.to_dict() or {})
        item["id"] = doc.id
        item["shop_id"] = normalize_shop_id(str(item.get("shop_id") or MAIN_SHOP_ID))
        if effective_shop == "all" or item["shop_id"] == effective_shop:
            debtors.append(item)
    debtors.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)
    return {"debtors": debtors}


@app.post("/api/debtors")
async def add_debtor(request: Request):
    staff = require_staff(request)
    payload = await request.json()
    effective_shop = resolve_effective_shop_id(staff, str(payload.get("shop_id") or MAIN_SHOP_ID))
    if effective_shop == "all":
        effective_shop = MAIN_SHOP_ID
    doc = {
        "shop_id": effective_shop,
        "customer": str(payload.get("customer") or "").strip(),
        "phone": str(payload.get("phone") or "").strip(),
        "amount": float(payload.get("amount") or 0.0),
        "items_bought": str(payload.get("items_bought") or "").strip(),
        "status": "unpaid",
        "timestamp": now_str(),
    }
    if not doc["customer"] or not doc["phone"]:
        raise HTTPException(status_code=400, detail="Customer name and phone are required")
    doc_id = uuid.uuid4().hex
    db.collection("debtors").document(doc_id).set(doc)
    return {"ok": True, "id": doc_id}


@app.post("/api/debtors/{debtor_id}/pay")
def mark_debtor_paid(debtor_id: str, request: Request):
    require_staff(request)
    doc_ref = db.collection("debtors").document(debtor_id)
    if not doc_ref.get().exists:
        raise HTTPException(status_code=404, detail="Debtor not found")
    doc_ref.set({"status": "paid", "paid_at": now_str()}, merge=True)
    return {"ok": True, "id": debtor_id, "status": "paid"}


@app.delete("/api/debtors/{debtor_id}")
def delete_debtor(debtor_id: str, request: Request):
    require_staff(request)
    doc_ref = db.collection("debtors").document(debtor_id)
    if not doc_ref.get().exists:
        raise HTTPException(status_code=404, detail="Debtor not found")
    doc_ref.delete()
    return {"ok": True, "id": debtor_id}


@app.get("/api/expenses")
def get_expenses(request: Request, shop_id: Optional[str] = Query(default=None)):
    staff = require_staff(request)
    effective_shop = resolve_effective_shop_id(staff, shop_id)
    docs = db.collection("expenses").stream()
    expenses: List[Dict[str, Any]] = []
    for doc in docs:
        item = dict(doc.to_dict() or {})
        item["id"] = doc.id
        item["shop_id"] = normalize_shop_id(str(item.get("shop_id") or MAIN_SHOP_ID))
        if effective_shop == "all" or item["shop_id"] == effective_shop:
            expenses.append(item)
    expenses.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)
    return {"expenses": expenses}


@app.post("/api/expenses")
async def add_expense(request: Request):
    staff = require_staff(request)
    payload = await request.json()
    effective_shop = resolve_effective_shop_id(staff, str(payload.get("shop_id") or MAIN_SHOP_ID))
    if effective_shop == "all":
        effective_shop = MAIN_SHOP_ID
    doc = {
        "shop_id": effective_shop,
        "category": str(payload.get("category") or "Operations").strip(),
        "amount": float(payload.get("amount") or 0.0),
        "note": str(payload.get("note") or "").strip(),
        "timestamp": now_str(),
    }
    doc_id = uuid.uuid4().hex
    db.collection("expenses").document(doc_id).set(doc)
    return {"ok": True, "id": doc_id}


@app.delete("/api/expenses/{expense_id}")
def delete_expense(expense_id: str, request: Request):
    require_staff(request)
    doc_ref = db.collection("expenses").document(expense_id)
    if not doc_ref.get().exists:
        raise HTTPException(status_code=404, detail="Expense not found")
    doc_ref.delete()
    return {"ok": True, "id": expense_id}


@app.get("/api/workers")
def get_workers(request: Request):
    staff = require_staff(request)
    docs = db.collection("workers").stream()
    workers: List[Dict[str, Any]] = []
    for doc in docs:
        item = dict(doc.to_dict() or {})
        item.pop("password", None)
        item["id"] = doc.id
        item["shop_id"] = normalize_shop_id(str(item.get("shop_id") or MAIN_SHOP_ID))
        if is_super_admin(staff) or item["shop_id"] == staff["shop_id"]:
            workers.append(item)
    workers.sort(key=lambda x: str(x.get("name", "")).lower())
    return {"workers": workers}


@app.post("/api/workers")
async def add_worker(request: Request):
    staff = require_super_admin(request)
    payload = await request.json()
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Worker name is required")
    username = str(payload.get("username") or name.lower().replace(" ", "")).strip()
    raw_pw = str(payload.get("password") or "")
    target_shop = resolve_effective_shop_id(staff, str(payload.get("shop_id") or MAIN_SHOP_ID))
    doc = {
        "name": name,
        "role": str(payload.get("role") or "Staff").strip(),
        "shop_id": target_shop,
        "username": username,
        "password": hash_password(raw_pw),
        "timestamp": now_str(),
    }
    doc_id = uuid.uuid4().hex
    db.collection("workers").document(doc_id).set(doc)
    return {"ok": True, "id": doc_id}


@app.delete("/api/workers/{worker_id}")
def delete_worker(worker_id: str, request: Request):
    require_super_admin(request)
    doc_ref = db.collection("workers").document(worker_id)
    if not doc_ref.get().exists:
        raise HTTPException(status_code=404, detail="Worker not found")
    doc_ref.delete()
    return {"ok": True, "id": worker_id}


@app.get("/api/analytics")
def get_analytics(request: Request, shop_id: Optional[str] = Query(default=None)):
    staff = require_admin_or_ceo(request)
    effective_shop = resolve_effective_shop_id(staff, shop_id)
    products_by_id: Dict[str, Dict[str, Any]] = {}
    products_by_name: Dict[str, Dict[str, Any]] = {}
    for doc in db.collection("products").stream():
        p = normalize_product(doc.id, doc.to_dict())
        if effective_shop == "all" or p["shop_id"] == effective_shop:
            products_by_id[p["id"]] = p
            products_by_name[p["name"].strip().lower()] = p

    total_revenue = 0.0
    total_profit = 0.0
    for doc in db.collection("sales").stream():
        s = doc.to_dict() or {}
        s_shop = normalize_shop_id(str(s.get("shop_id") or MAIN_SHOP_ID))
        if effective_shop != "all" and s_shop != effective_shop:
            continue
        if s.get("record_debt") is True or str(s.get("status") or "").lower() == "debt":
            continue
        qty = max(0.0, float(s.get("qty") or 1.0))
        row_total = float(
            s.get("total") if s.get("total") is not None else (float(s.get("unit_price") or 0.0) * qty)
        )
        total_revenue += row_total

        prod = products_by_id.get(str(s.get("product_id") or "")) or products_by_name.get(
            str(s.get("item_name") or "").strip().lower()
        )
        if prod:
            packs_per_carton = max(1.0, float(prod.get("packs_per_carton") or 1.0))
            units_per_pack = max(1.0, float(prod.get("units_per_pack") or 1.0))
            carton_cost = float(prod.get("cost") or 0.0)
            pack_cost = carton_cost / packs_per_carton
            retail_cost = pack_cost / units_per_pack
            unit_type = str(s.get("unit_type") or "")
            if unit_type == prod.get("unit_main_label") or unit_type.lower() == "carton":
                unit_cost = carton_cost
            elif unit_type == prod.get("unit_pack_label") or unit_type.lower() == "pack":
                unit_cost = pack_cost
            else:
                unit_cost = retail_cost
            total_profit += row_total - (unit_cost * qty)
        else:
            total_profit += row_total

    total_expenses = 0.0
    for doc in db.collection("expenses").stream():
        ex = doc.to_dict() or {}
        ex_shop = normalize_shop_id(str(ex.get("shop_id") or MAIN_SHOP_ID))
        if effective_shop != "all" and ex_shop != effective_shop:
            continue
        total_expenses += float(ex.get("amount") or 0.0)

    net_cashflow = total_profit - total_expenses
    return {
        "ok": True,
        "shop_id": effective_shop,
        "analytics": {
            "total_revenue": total_revenue,
            "total_profit": total_profit,
            "total_expenses": total_expenses,
            "net_cashflow": net_cashflow,
        },
    }


@app.get("/api/settings")
def get_settings():
    cached = _cache_get("settings:business")
    if cached is not None:
        return {"settings": cached, "cached": True}

    default_settings = {"business_name": "Sammyreks Business Center", "currency": "₦"}
    try:
        snap = db.collection("settings").document("business").get()
        settings = snap.to_dict() if snap.exists else default_settings
        resolved = settings or default_settings
        _cache_set("settings:business", resolved)
        _fallback_store["settings"] = resolved
        return {"settings": resolved, "cached": False}
    except Exception as exc:
        print(f"[WARN] /api/settings fallback triggered: {exc}")
        fallback = dict(_fallback_store.get("settings") or default_settings)
        return {"settings": fallback, "cached": True, "fallback": True}


@app.post("/api/settings")
async def save_settings(
    request: Request,
    business_name: str = Form("Sammyreks Business Center"),
    tagline: str = Form(""),
    active_notice: str = Form(""),
    broadcast_video_url: str = Form(""),
    currency: str = Form("₦"),
    extra_settings: str = Form(""),
    broadcast_video_file: Optional[UploadFile] = File(None),
    awareness_image_file: Optional[UploadFile] = File(None),
):
    require_super_admin(request)
    ref = db.collection("settings").document("business")
    existing = ref.get().to_dict() if ref.get().exists else {}

    old_video = str(existing.get("broadcast_video_url") or "").strip()
    old_video_public_id = str(existing.get("broadcast_video_public_id") or "").strip()

    uploaded_video, uploaded_video_public_id = upload_to_cloudinary(
        broadcast_video_file, folder="sammyreks_videos", resource_type="video"
    )
    clean_link = broadcast_video_url.strip()

    if uploaded_video:
        if old_video and old_video != uploaded_video:
            delete_from_cloudinary(
                public_id=old_video_public_id, url=old_video, resource_type="video"
            )
        final_video = uploaded_video
        final_video_public_id = uploaded_video_public_id or ""
    elif clean_link:
        if old_video and old_video != clean_link:
            delete_from_cloudinary(
                public_id=old_video_public_id, url=old_video, resource_type="video"
            )
        final_video = clean_link
        final_video_public_id = (
            old_video_public_id
            if clean_link == old_video
            else (extract_cloudinary_public_id(clean_link) or "")
        )
    else:
        if old_video:
            delete_from_cloudinary(
                public_id=old_video_public_id, url=old_video, resource_type="video"
            )
        final_video = ""
        final_video_public_id = ""

    uploaded_awareness_url, uploaded_awareness_public_id = upload_to_cloudinary(
        awareness_image_file, folder="sammyreks_awareness", resource_type="image", max_dim=800
    )

    parsed_extra: Any = existing.get("extra_settings")
    if extra_settings.strip():
        try:
            parsed_extra = json.loads(extra_settings)
        except json.JSONDecodeError:
            parsed_extra = extra_settings.strip()

    final_awareness_url = str(existing.get("awareness_image_url") or "").strip()
    final_awareness_public_id = str(existing.get("awareness_image_public_id") or "").strip()
    if uploaded_awareness_url:
        if final_awareness_public_id:
            delete_from_cloudinary(
                public_id=final_awareness_public_id, url=final_awareness_url, resource_type="image"
            )
        final_awareness_url = uploaded_awareness_url
        final_awareness_public_id = uploaded_awareness_public_id or ""
        if isinstance(parsed_extra, dict):
            existing_urls = [
                u.strip()
                for u in str(parsed_extra.get("awarenessImageUrl") or "").splitlines()
                if u.strip()
            ]
            if uploaded_awareness_url not in existing_urls:
                existing_urls.append(uploaded_awareness_url)
            parsed_extra["awarenessImageUrl"] = "\n".join(existing_urls)

    doc: Dict[str, Any] = {
        "business_name": business_name.strip() or "Sammyreks Business Center",
        "tagline": tagline.strip(),
        "active_notice": active_notice.strip(),
        "broadcast_video_url": final_video,
        "broadcast_video_public_id": final_video_public_id,
        "awareness_image_url": final_awareness_url,
        "awareness_image_public_id": final_awareness_public_id,
        "currency": currency.strip() or "₦",
        "updated_at": now_str(),
    }
    if parsed_extra is not None:
        doc["extra_settings"] = parsed_extra

    ref.set(doc, merge=True)
    merged_out = {**existing, **doc}
    _cache_invalidate("settings:")
    _fallback_store["settings"] = merged_out
    return {"ok": True, "settings": merged_out}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    print(f"[ERROR] {request.method} {request.url.path}: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Something went wrong on the server. Please try again."},
    )
