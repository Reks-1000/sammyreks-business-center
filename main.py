import hashlib
import hmac
import json
import os
import random
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

try:
    import cloudinary
    import cloudinary.uploader
except Exception:
    cloudinary = None

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
UPLOADS_DIR = STATIC_DIR / "uploads"
DATA_DIR = BASE_DIR / "data"

UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_FILE = DATA_DIR / "store_db.json"

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
TOKEN_SECRET = os.getenv("APP_TOKEN_SECRET", "sammyreks-hmac-secret-change-in-prod")
TOKEN_TTL_SECONDS = int(os.getenv("TOKEN_TTL_SECONDS", "86400"))

if cloudinary and os.getenv("CLOUDINARY_CLOUD_NAME"):
    cloudinary.config(
        cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
        api_key=os.getenv("CLOUDINARY_API_KEY"),
        api_secret=os.getenv("CLOUDINARY_API_SECRET"),
        secure=True,
    )

app = FastAPI(title="Sammyreks Business Center API", version="25.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _hash_password(password: str) -> str:
    salt = os.getenv("PASSWORD_SALT", "sammyreks-salt")
    return hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()


def _slugify_shop_id(name: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(name or "").strip())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    cleaned = cleaned.strip("-") or f"shop-{uuid.uuid4().hex[:6]}"
    if cleaned == "main":
        cleaned = f"shop-{cleaned}"
    return cleaned


def _default_db() -> Dict[str, Any]:
    return {
        "settings": {
            "business_name": "Sammyreks Business Center",
            "tagline": "Quality products, fast orders & live support",
            "currency": "\u20a6",
            "active_notice": "",
            "broadcast_video_url": "",
            "awareness_image_url": "",
            "extra_settings": {},
        },
        "shops": [],
        "workers": [
            {
                "id": "wrk-main-staff",
                "name": "Main Store Staff",
                "role": "Staff",
                "username": "staff",
                "password_hash": _hash_password("staff123"),
                "shop_id": "main",
            }
        ],
        "products": [
            {
                "id": "prod-1",
                "shop_id": "main",
                "name": "Maltina 330ml",
                "category": "Beverages & Drinks",
                "vendor_name": "",
                "unit_main_label": "Carton",
                "unit_pack_label": "Pack",
                "unit_retail_label": "Bottle",
                "price_carton": 14500.0,
                "price": 14500.0,
                "qty_cartons": 12.0,
                "stock": 12.0,
                "cost": 12800.0,
                "price_pack": 2500.0,
                "price_retail": 500.0,
                "packs_per_carton": 6,
                "units_per_pack": 4,
                "shelf": "A1",
                "expiry": "",
                "image_url": "",
                "video_url": "",
                "description": "Chilled & ambient malt drink.",
            }
        ],
        "orders": [],
        "messages": [],
        "sales": [],
        "debtors": [],
        "expenses": [],
    }


def load_db() -> Dict[str, Any]:
    if not DB_FILE.exists():
        data = _default_db()
        save_db(data)
        return data
    try:
        with DB_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _default_db()
        defaults = _default_db()
        for k, v in defaults.items():
            if k not in data:
                data[k] = v
        return data
    except Exception:
        return _default_db()


def save_db(data: Dict[str, Any]) -> None:
    tmp = DB_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(DB_FILE)


# =============================================================================
# AUTH & TOKEN HELPERS (THREE-TIER RBAC + shop_id ENFORCEMENT)
# =============================================================================
def sign_staff_token(payload: Dict[str, Any]) -> str:
    body = {
        "u": payload.get("username", ""),
        "r": payload.get("role", "Staff"),
        "s": payload.get("shop_id", "main"),
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
    }
    raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
    sig = hmac.new(TOKEN_SECRET.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return f"{raw.hex()}.{sig}"


def parse_staff_token(token: str) -> Optional[Dict[str, Any]]:
    if not token or "." not in token:
        return None
    hex_part, sig_part = token.split(".", 1)
    try:
        raw = bytes.fromhex(hex_part)
    except Exception:
        return None
    expected_sig = hmac.new(TOKEN_SECRET.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig_part, expected_sig):
        return None
    try:
        obj = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    if int(obj.get("exp", 0)) < int(time.time()):
        return None
    return {
        "username": str(obj.get("u", "")),
        "role": str(obj.get("r", "Staff")),
        "shop_id": str(obj.get("s", "main")),
    }


def get_optional_staff(x_staff_token: Optional[str] = Header(default=None)) -> Optional[Dict[str, Any]]:
    if not x_staff_token:
        return None
    return parse_staff_token(x_staff_token.strip())


def require_staff(staff: Optional[Dict[str, Any]] = Depends(get_optional_staff)) -> Dict[str, Any]:
    if not staff:
        raise HTTPException(status_code=401, detail="Staff authentication required.")
    return staff


def is_ceo_role(role: str) -> bool:
    return str(role or "").strip().lower() in {"ceo", "super_admin", "superadmin", "admin"}


def require_ceo(staff: Dict[str, Any] = Depends(require_staff)) -> Dict[str, Any]:
    if not is_ceo_role(staff.get("role", "")):
        raise HTTPException(status_code=403, detail="CEO / Super-Admin access required.")
    return staff


def resolve_effective_shop_id(staff: Optional[Dict[str, Any]], requested_shop_id: Optional[str]) -> str:
    req = str(requested_shop_id or "main").strip() or "main"
    if not staff:
        return "main" if req == "all" else req
    if is_ceo_role(staff.get("role", "")):
        return req
    return str(staff.get("shop_id") or "main")


def matches_shop_filter(record_shop_id: Optional[str], effective_shop_id: str) -> bool:
    rec_shop = str(record_shop_id or "main").strip() or "main"
    if effective_shop_id == "all":
        return True
    return rec_shop == effective_shop_id


# =============================================================================
# MEDIA UPLOAD HELPER (CLOUDINARY OR LOCAL STATIC FALLBACK)
# =============================================================================
async def save_uploaded_media(upload_file: Optional[UploadFile], folder: str = "sammyreks") -> str:
    if not upload_file or not upload_file.filename:
        return ""
    content = await upload_file.read()
    if not content:
        return ""

    if cloudinary and os.getenv("CLOUDINARY_CLOUD_NAME"):
        try:
            res = cloudinary.uploader.upload(
                content,
                folder=folder,
                resource_type="auto",
            )
            secure_url = res.get("secure_url") or res.get("url")
            if secure_url:
                return str(secure_url)
        except Exception:
            pass

    ext = Path(upload_file.filename).suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mov", ".webm", ".mkv"}:
        ext = ".jpg"
    fname = f"{uuid.uuid4().hex}{ext}"
    out_path = UPLOADS_DIR / fname
    out_path.write_bytes(content)
    return f"/static/uploads/{fname}"


# =============================================================================
# FRONTEND ENTRY ROUTE
# =============================================================================
@app.get("/", response_class=HTMLResponse)
async def serve_index():
    html_file = TEMPLATES_DIR / "index.html"
    if not html_file.exists():
        html_file = BASE_DIR / "index.html"
    if not html_file.exists():
        raise HTTPException(status_code=404, detail="index.html not found.")
    return HTMLResponse(content=html_file.read_text(encoding="utf-8"))


# =============================================================================
# AUTH ENDPOINT
# =============================================================================
class LoginRequest(BaseModel):
    username: str
    password: str
    mode: Optional[str] = "admin"


@app.post("/api/login")
async def login(payload: LoginRequest):
    username = (payload.username or "").strip()
    password = payload.password or ""
    db = load_db()

    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        role = "CEO"
        shop_id = "main"
        token = sign_staff_token({"username": username, "role": role, "shop_id": shop_id})
        return {
            "ok": True,
            "username": username,
            "role": role,
            "shop_id": shop_id,
            "token": token,
        }

    pw_hash = _hash_password(password)
    for w in db.get("workers", []):
        if str(w.get("username", "")).strip() == username and str(w.get("password_hash", "")) == pw_hash:
            role = str(w.get("role", "Staff"))
            shop_id = str(w.get("shop_id", "main"))
            token = sign_staff_token({"username": username, "role": role, "shop_id": shop_id})
            return {
                "ok": True,
                "username": username,
                "role": role,
                "shop_id": shop_id,
                "token": token,
            }

    raise HTTPException(status_code=401, detail="Invalid username or password.")


# =============================================================================
# MULTI-TENANT SHOPS ENDPOINTS (APPLY + PENDING APPROVAL + CEO MANAGEMENT)
# =============================================================================
@app.get("/api/shops")
async def list_shops(staff: Optional[Dict[str, Any]] = Depends(get_optional_staff)):
    db = load_db()
    shops = db.get("shops", [])
    ceo = bool(staff and is_ceo_role(staff.get("role", "")))

    result_shops = []
    for s in shops:
        status = str(s.get("status", "active")).lower()
        if not ceo and status != "active":
            continue
        item = {
            "id": s.get("id"),
            "slug": s.get("slug", s.get("id")),
            "name": s.get("name"),
            "location": s.get("location", ""),
            "phone": s.get("phone", ""),
            "avatar_url": s.get("avatar_url", ""),
            "payment_info": s.get("payment_info", ""),
            "payment_ref": s.get("payment_ref", ""),
            "proof_url": s.get("proof_url", ""),
            "status": s.get("status", "active"),
        }
        if ceo:
            item["owner_username"] = s.get("owner_username", "")
            item["created_at"] = s.get("created_at", "")
        result_shops.append(item)

    return {"shops": result_shops}


@app.post("/api/shops/apply")
async def apply_to_register_shop(
    shop_name: str = Form(...),
    location: str = Form(default=""),
    phone: str = Form(default=""),
    username: str = Form(...),
    password: str = Form(...),
    payment_info: str = Form(default=""),
    payment_ref: str = Form(default=""),
    proof_file: Optional[UploadFile] = File(default=None),
):
    clean_name = (shop_name or "").strip()
    clean_user = (username or "").strip()
    clean_pass = password or ""
    if not clean_name or not clean_user or not clean_pass:
        raise HTTPException(status_code=400, detail="Shop name, desired username, and password are required.")

    db = load_db()
    shops = db.setdefault("shops", [])
    workers = db.setdefault("workers", [])

    if (
        any(str(w.get("username", "")).strip().lower() == clean_user.lower() for w in workers)
        or clean_user.lower() == ADMIN_USERNAME.lower()
    ):
        raise HTTPException(status_code=400, detail="That username is already in use. Choose another username.")

    if any(
        str(s.get("owner_username", "")).strip().lower() == clean_user.lower()
        and str(s.get("status", "")).lower() == "pending_approval"
        for s in shops
    ):
        raise HTTPException(status_code=400, detail="An application with that username is already pending approval.")

    base_id = _slugify_shop_id(clean_name)
    shop_id = base_id
    existing_ids = {str(s.get("id")) for s in shops}
    counter = 2
    while shop_id in existing_ids:
        shop_id = f"{base_id}-{counter}"
        counter += 1

    proof_url = await save_uploaded_media(proof_file, folder="sammyreks/shop_registrations")

    pending_shop = {
        "id": shop_id,
        "slug": shop_id,
        "name": clean_name,
        "location": (location or "").strip(),
        "phone": (phone or "").strip(),
        "avatar_url": "",
        "payment_info": (payment_info or "").strip(),
        "payment_ref": (payment_ref or "").strip(),
        "proof_url": proof_url,
        "owner_username": clean_user,
        "owner_password_hash": _hash_password(clean_pass),
        "status": "pending_approval",
        "created_at": _now_str(),
    }
    shops.append(pending_shop)
    save_db(db)

    return {
        "ok": True,
        "status": "pending_approval",
        "shop": {k: v for k, v in pending_shop.items() if k != "owner_password_hash"},
    }


@app.post("/api/shops/{shop_id}/approve")
async def approve_shop_application(
    shop_id: str,
    staff: Dict[str, Any] = Depends(require_ceo),
):
    db = load_db()
    shops = db.setdefault("shops", [])
    workers = db.setdefault("workers", [])

    target = next((s for s in shops if str(s.get("id")) == str(shop_id)), None)
    if not target:
        raise HTTPException(status_code=404, detail="Shop application not found.")

    target["status"] = "active"
    target["approved_at"] = _now_str()

    owner_user = str(target.get("owner_username") or "").strip()
    owner_hash = str(target.get("owner_password_hash") or "").strip()
    if owner_user and not any(str(w.get("username", "")).strip().lower() == owner_user.lower() for w in workers):
        workers.append(
            {
                "id": f"wrk-{uuid.uuid4().hex[:8]}",
                "name": f"{target.get('name', 'Shop')} Owner",
                "role": "ShopOwner",
                "username": owner_user,
                "password_hash": owner_hash or _hash_password("1234"),
                "shop_id": target["id"],
            }
        )

    save_db(db)
    return {"ok": True, "shop": {k: v for k, v in target.items() if k != "owner_password_hash"}}


@app.post("/api/shops")
async def register_shop(
    shop_name: str = Form(...),
    location: str = Form(""),
    username: str = Form(...),
    password: str = Form(...),
    payment_info: str = Form(""),
    avatar_file: Optional[UploadFile] = File(default=None),
    staff: Dict[str, Any] = Depends(require_ceo),
):
    clean_name = (shop_name or "").strip()
    clean_user = (username or "").strip()
    clean_pass = password or ""
    if not clean_name or not clean_user or not clean_pass:
        raise HTTPException(status_code=400, detail="Shop name, owner username, and password are required.")

    db = load_db()
    shops = db.setdefault("shops", [])
    workers = db.setdefault("workers", [])

    if (
        any(str(w.get("username", "")).strip().lower() == clean_user.lower() for w in workers)
        or clean_user.lower() == ADMIN_USERNAME.lower()
    ):
        raise HTTPException(status_code=400, detail="That owner login username is already in use.")

    base_id = _slugify_shop_id(clean_name)
    shop_id = base_id
    existing_ids = {str(s.get("id")) for s in shops}
    counter = 2
    while shop_id in existing_ids:
        shop_id = f"{base_id}-{counter}"
        counter += 1

    avatar_url = await save_uploaded_media(avatar_file, folder="sammyreks/shops")

    new_shop = {
        "id": shop_id,
        "slug": shop_id,
        "name": clean_name,
        "location": (location or "").strip(),
        "avatar_url": avatar_url,
        "payment_info": (payment_info or "").strip(),
        "owner_username": clean_user,
        "status": "active",
        "created_at": _now_str(),
    }
    shops.append(new_shop)

    workers.append(
        {
            "id": f"wrk-{uuid.uuid4().hex[:8]}",
            "name": f"{clean_name} Owner",
            "role": "ShopOwner",
            "username": clean_user,
            "password_hash": _hash_password(clean_pass),
            "shop_id": shop_id,
        }
    )

    save_db(db)
    return {"ok": True, "shop": new_shop}


class PatchShopRequest(BaseModel):
    status: Optional[str] = None
    name: Optional[str] = None
    location: Optional[str] = None
    payment_info: Optional[str] = None


@app.patch("/api/shops/{shop_id}")
async def update_shop(
    shop_id: str,
    payload: PatchShopRequest,
    staff: Dict[str, Any] = Depends(require_ceo),
):
    db = load_db()
    for s in db.get("shops", []):
        if str(s.get("id")) == str(shop_id):
            if payload.status is not None:
                s["status"] = payload.status
            if payload.name is not None:
                s["name"] = payload.name
            if payload.location is not None:
                s["location"] = payload.location
            if payload.payment_info is not None:
                s["payment_info"] = payload.payment_info
            save_db(db)
            return {"ok": True, "shop": s}
    raise HTTPException(status_code=404, detail="Shop not found.")


@app.delete("/api/shops/{shop_id}")
async def delete_shop(
    shop_id: str,
    staff: Dict[str, Any] = Depends(require_ceo),
):
    if str(shop_id) == "main":
        raise HTTPException(status_code=400, detail="Main store cannot be deleted.")
    db = load_db()
    before = len(db.get("shops", []))
    db["shops"] = [s for s in db.get("shops", []) if str(s.get("id")) != str(shop_id)]
    db["workers"] = [
        w
        for w in db.get("workers", [])
        if not (str(w.get("shop_id")) == str(shop_id) and str(w.get("role")) == "ShopOwner")
    ]
    if len(db["shops"]) == before:
        raise HTTPException(status_code=404, detail="Shop not found.")
    save_db(db)
    return {"ok": True, "deleted_shop_id": shop_id}


# =============================================================================
# PRODUCTS ENDPOINTS (shop_id SCOPED)
# =============================================================================
@app.get("/api/products")
async def get_products(
    shop_id: str = Query(default="main"),
    staff: Optional[Dict[str, Any]] = Depends(get_optional_staff),
):
    db = load_db()
    eff_shop = resolve_effective_shop_id(staff, shop_id)
    filtered = [
        p for p in db.get("products", []) if matches_shop_filter(p.get("shop_id", "main"), eff_shop)
    ]
    return {"products": filtered, "shop_id": eff_shop}


@app.post("/api/products")
async def save_product(
    action_mode: str = Form(default="create"),
    existing_product_id: str = Form(default=""),
    shop_id: str = Form(default="main"),
    category: str = Form(default="General"),
    vendor_name: str = Form(default=""),
    main_unit: str = Form(default="Carton"),
    sub_unit: str = Form(default="Pack"),
    retail_unit: str = Form(default="Retail Unit"),
    name: str = Form(...),
    price_carton: float = Form(default=0.0),
    stock_cartons: float = Form(default=0.0),
    cost_price_carton: float = Form(default=0.0),
    price_pack: float = Form(default=0.0),
    packs_per_carton: int = Form(default=1),
    price_retail: float = Form(default=0.0),
    retail_per_pack: int = Form(default=1),
    shelf_location: str = Form(default=""),
    expiry_date: str = Form(default=""),
    video_link: str = Form(default=""),
    description: str = Form(default=""),
    image_url: str = Form(default=""),
    image_file: Optional[UploadFile] = File(default=None),
    video_file: Optional[UploadFile] = File(default=None),
    staff: Dict[str, Any] = Depends(require_staff),
):
    db = load_db()
    eff_shop = resolve_effective_shop_id(staff, shop_id)
    if eff_shop == "all":
        eff_shop = "main"

    uploaded_img = await save_uploaded_media(image_file, folder=f"sammyreks/{eff_shop}/products")
    uploaded_vid = await save_uploaded_media(video_file, folder=f"sammyreks/{eff_shop}/videos")

    final_img = uploaded_img or (image_url or "").strip()
    final_vid = uploaded_vid or (video_link or "").strip()

    products = db.setdefault("products", [])

    if action_mode == "update" and existing_product_id:
        for p in products:
            if str(p.get("id")) == str(existing_product_id):
                if not is_ceo_role(staff.get("role", "")) and str(p.get("shop_id", "main")) != str(
                    staff.get("shop_id", "main")
                ):
                    raise HTTPException(status_code=403, detail="Cannot edit another shop's product.")
                p["name"] = name.strip()
                p["category"] = (category or "General").strip()
                p["vendor_name"] = (vendor_name or "").strip()
                p["unit_main_label"] = (main_unit or "Carton").strip()
                p["unit_pack_label"] = (sub_unit or "Pack").strip()
                p["unit_retail_label"] = (retail_unit or "Retail Unit").strip()
                p["price_carton"] = float(price_carton)
                p["price"] = float(price_carton)
                p["qty_cartons"] = float(stock_cartons)
                p["stock"] = float(stock_cartons)
                p["cost"] = float(cost_price_carton)
                p["price_pack"] = float(price_pack)
                p["packs_per_carton"] = max(1, int(packs_per_carton))
                p["price_retail"] = float(price_retail)
                p["units_per_pack"] = max(1, int(retail_per_pack))
                p["shelf"] = (shelf_location or "").strip()
                p["expiry"] = (expiry_date or "").strip()
                if final_img:
                    p["image_url"] = final_img
                if final_vid:
                    p["video_url"] = final_vid
                p["description"] = (description or "").strip()
                save_db(db)
                return {"ok": True, "product": p}

    new_prod = {
        "id": f"prod-{uuid.uuid4().hex[:8]}",
        "shop_id": eff_shop,
        "name": name.strip(),
        "category": (category or "General").strip(),
        "vendor_name": (vendor_name or "").strip(),
        "unit_main_label": (main_unit or "Carton").strip(),
        "unit_pack_label": (sub_unit or "Pack").strip(),
        "unit_retail_label": (retail_unit or "Retail Unit").strip(),
        "price_carton": float(price_carton),
        "price": float(price_carton),
        "qty_cartons": float(stock_cartons),
        "stock": float(stock_cartons),
        "cost": float(cost_price_carton),
        "price_pack": float(price_pack),
        "packs_per_carton": max(1, int(packs_per_carton)),
        "price_retail": float(price_retail),
        "units_per_pack": max(1, int(retail_per_pack)),
        "shelf": (shelf_location or "").strip(),
        "expiry": (expiry_date or "").strip(),
        "image_url": final_img,
        "video_url": final_vid,
        "description": (description or "").strip(),
    }
    products.append(new_prod)
    save_db(db)
    return {"ok": True, "product": new_prod}


@app.delete("/api/products/{product_id}")
async def delete_product(
    product_id: str,
    staff: Dict[str, Any] = Depends(require_staff),
):
    db = load_db()
    products = db.get("products", [])
    target = next((p for p in products if str(p.get("id")) == str(product_id)), None)
    if not target:
        raise HTTPException(status_code=404, detail="Product not found.")
    if not is_ceo_role(staff.get("role", "")) and str(target.get("shop_id", "main")) != str(
        staff.get("shop_id", "main")
    ):
        raise HTTPException(status_code=403, detail="Cannot delete another shop's product.")
    db["products"] = [p for p in products if str(p.get("id")) != str(product_id)]
    save_db(db)
    return {"ok": True, "deleted_id": product_id}


# =============================================================================
# ORDERS & PRIVATE PIN-LOCKED CHAT ENDPOINTS (shop_id SCOPED)
# =============================================================================
class CreateOrderRequest(BaseModel):
    shop_id: Optional[str] = "main"
    customer: str
    contact_phone: str
    pin: Optional[str] = ""
    notes: Optional[str] = ""
    items: Optional[list] = None
    special_items: Optional[list] = None


def _normalize_phone(phone: str) -> str:
    return "".join(ch for ch in str(phone or "").strip() if ch.isdigit())


def _find_or_create_customer_pin(db: Dict[str, Any], phone: str, shop_id: str, requested_pin: str = "") -> str:
    clean_pin = "".join(ch for ch in str(requested_pin or "").strip() if ch.isdigit())
    if len(clean_pin) >= 4:
        return clean_pin[:6]
    norm_phone = _normalize_phone(phone)
    for o in db.get("orders", []):
        if _normalize_phone(o.get("contact_phone", "")) == norm_phone and str(o.get("shop_id", "main")) == str(shop_id):
            if o.get("customer_pin"):
                return str(o["customer_pin"])
    return f"{random.randint(1000, 9999)}"


@app.post("/api/orders")
async def create_order(payload: CreateOrderRequest):
    db = load_db()
    shop_id = str(payload.shop_id or "main").strip() or "main"
    phone = (payload.contact_phone or "").strip()
    customer = (payload.customer or "Customer").strip()
    if not phone:
        raise HTTPException(status_code=400, detail="Phone number is required.")

    pin = _find_or_create_customer_pin(db, phone, shop_id, payload.pin or "")
    ref_code = f"SRK-{random.randint(1000, 9999)}"
    has_special = bool(payload.special_items and len(payload.special_items) > 0)
    order_type = "Multi-Item Request" if has_special else "Cart Order"

    order_doc = {
        "id": f"ord-{uuid.uuid4().hex[:8]}",
        "shop_id": shop_id,
        "customer": customer,
        "customer_name": customer,
        "contact_phone": phone,
        "phone": phone,
        "customer_pin": pin,
        "payment_ref": ref_code,
        "order_type": order_type,
        "notes": (payload.notes or "").strip(),
        "items": payload.items or [],
        "special_items": payload.special_items or [],
        "status": "Pending",
        "timestamp": _now_str(),
    }
    db.setdefault("orders", []).insert(0, order_doc)

    summary_lines = []
    if has_special:
        for sp in payload.special_items or []:
            summary_lines.append(f"- {sp.get('item', '')}")
    else:
        total_amt = 0.0
        for it in payload.items or []:
            line_tot = float(it.get("total_price", 0) or 0)
            total_amt += line_tot
            summary_lines.append(
                f"- {it.get('name', 'Item')} ({it.get('unit', 'Unit')}) x {it.get('qty', 1)} = \u20a6{line_tot:.2f}"
            )
        if total_amt > 0:
            summary_lines.append(f"Total: \u20a6{total_amt:.2f}")

    msg_body = (
        f"[{order_type} | Ref: {ref_code}]\n"
        f"Customer: {customer} ({phone})\n"
        + ("\n".join(summary_lines) + "\n" if summary_lines else "")
        + (f"Note: {payload.notes}" if payload.notes else "")
    ).strip()

    db.setdefault("messages", []).append(
        {
            "id": f"msg-{uuid.uuid4().hex[:8]}",
            "shop_id": shop_id,
            "sender": "Customer",
            "customer_phone": phone,
            "phone": phone,
            "message": msg_body,
            "text": msg_body,
            "proof_url": "",
            "timestamp": _now_str(),
        }
    )

    save_db(db)
    return {
        "ok": True,
        "order": order_doc,
        "customer_pin": pin,
        "payment_ref": ref_code,
        "shop_id": shop_id,
    }


@app.get("/api/orders")
async def list_orders(
    shop_id: str = Query(default="main"),
    staff: Dict[str, Any] = Depends(require_staff),
):
    db = load_db()
    eff_shop = resolve_effective_shop_id(staff, shop_id)
    orders = [o for o in db.get("orders", []) if matches_shop_filter(o.get("shop_id", "main"), eff_shop)]
    return {"orders": orders, "shop_id": eff_shop}


@app.post("/api/orders/{order_id}/verify")
async def verify_order(
    order_id: str,
    staff: Dict[str, Any] = Depends(require_staff),
):
    db = load_db()
    target = next((o for o in db.get("orders", []) if str(o.get("id")) == str(order_id)), None)
    if not target:
        raise HTTPException(status_code=404, detail="Order not found.")
    if not is_ceo_role(staff.get("role", "")) and str(target.get("shop_id", "main")) != str(
        staff.get("shop_id", "main")
    ):
        raise HTTPException(status_code=403, detail="Cannot verify another shop's order.")

    target["status"] = "Verified"
    phone = str(target.get("contact_phone") or target.get("phone") or "")
    ref = str(target.get("payment_ref") or "")
    shop_id = str(target.get("shop_id") or "main")

    items_html = ""
    total_val = 0.0
    for it in target.get("items", []):
        line_p = float(it.get("total_price", 0) or 0)
        total_val += line_p
        items_html += f"<div>{it.get('name', 'Item')} ({it.get('unit', 'Unit')}) x {it.get('qty', 1)} — \u20a6{line_p:.2f}</div>"

    receipt_html = (
        f"<div class='border rounded p-2 bg-light small'>"
        f"<strong>OFFICIAL PURCHASE RECEIPT</strong><br/>"
        f"Ref: <strong>{ref}</strong> | Customer: {target.get('customer', 'Customer')}<br/>"
        f"{items_html}"
        f"{f'<strong>Total: \u20a6{total_val:.2f}</strong>' if total_val > 0 else ''}"
        f"</div>"
    )

    db.setdefault("messages", []).append(
        {
            "id": f"msg-{uuid.uuid4().hex[:8]}",
            "shop_id": shop_id,
            "sender": "System",
            "customer_phone": phone,
            "phone": phone,
            "message": receipt_html,
            "text": receipt_html,
            "proof_url": "",
            "timestamp": _now_str(),
        }
    )
    save_db(db)
    return {"ok": True, "order": target}


@app.delete("/api/orders/{order_id}")
async def delete_order(
    order_id: str,
    staff: Dict[str, Any] = Depends(require_staff),
):
    db = load_db()
    target = next((o for o in db.get("orders", []) if str(o.get("id")) == str(order_id)), None)
    if not target:
        raise HTTPException(status_code=404, detail="Order not found.")
    if not is_ceo_role(staff.get("role", "")) and str(target.get("shop_id", "main")) != str(
        staff.get("shop_id", "main")
    ):
        raise HTTPException(status_code=403, detail="Cannot delete another shop's order.")
    db["orders"] = [o for o in db.get("orders", []) if str(o.get("id")) != str(order_id)]
    save_db(db)
    return {"ok": True, "deleted_id": order_id}


@app.get("/api/messages")
async def get_messages(
    phone: Optional[str] = Query(default=None),
    pin: Optional[str] = Query(default=None),
    shop_id: str = Query(default="main"),
    staff: Optional[Dict[str, Any]] = Depends(get_optional_staff),
):
    db = load_db()
    all_msgs = db.get("messages", [])

    if staff:
        eff_shop = resolve_effective_shop_id(staff, shop_id)
        msgs = [m for m in all_msgs if matches_shop_filter(m.get("shop_id", "main"), eff_shop)]
        if phone:
            norm_p = _normalize_phone(phone)
            msgs = [m for m in msgs if _normalize_phone(m.get("customer_phone") or m.get("phone") or "") == norm_p]
        return {"messages": msgs, "shop_id": eff_shop}

    norm_p = _normalize_phone(phone or "")
    clean_pin = (pin or "").strip()
    if not norm_p or not clean_pin:
        raise HTTPException(status_code=401, detail="Enter your phone number and PIN (or Order Ref) to unlock chat.")

    authorized = False
    for o in db.get("orders", []):
        if _normalize_phone(o.get("contact_phone") or o.get("phone") or "") == norm_p:
            if str(o.get("shop_id", "main")) == str(shop_id or "main"):
                if (
                    str(o.get("customer_pin", "")).strip() == clean_pin
                    or str(o.get("payment_ref", "")).strip().upper() == clean_pin.upper()
                ):
                    authorized = True
                    break

    if not authorized:
        raise HTTPException(status_code=403, detail="Invalid phone number, PIN, or Order Reference for this shop.")

    msgs = [
        m
        for m in all_msgs
        if str(m.get("shop_id", "main")) == str(shop_id or "main")
        and _normalize_phone(m.get("customer_phone") or m.get("phone") or "") == norm_p
    ]
    return {"messages": msgs, "shop_id": shop_id or "main"}


@app.post("/api/messages")
async def post_message(
    phone: str = Form(...),
    pin: str = Form(default=""),
    shop_id: str = Form(default="main"),
    sender: str = Form(default="Customer"),
    text: str = Form(default=""),
    proof_file: Optional[UploadFile] = File(default=None),
    staff: Optional[Dict[str, Any]] = Depends(get_optional_staff),
):
    db = load_db()
    norm_p = _normalize_phone(phone)
    if not norm_p:
        raise HTTPException(status_code=400, detail="Phone number is required.")

    if staff:
        eff_shop = resolve_effective_shop_id(staff, shop_id)
        if eff_shop == "all":
            eff_shop = "main"
        actual_sender = sender or staff.get("username") or "Staff"
    else:
        eff_shop = str(shop_id or "main").strip() or "main"
        clean_pin = (pin or "").strip()
        ok_cust = any(
            _normalize_phone(o.get("contact_phone") or o.get("phone") or "") == norm_p
            and str(o.get("shop_id", "main")) == eff_shop
            and (
                str(o.get("customer_pin", "")).strip() == clean_pin
                or str(o.get("payment_ref", "")).strip().upper() == clean_pin.upper()
            )
            for o in db.get("orders", [])
        )
        if not ok_cust:
            raise HTTPException(status_code=403, detail="Unlock chat with your phone and PIN before sending messages.")
        actual_sender = "Customer"

    proof_url = await save_uploaded_media(proof_file, folder=f"sammyreks/{eff_shop}/proofs")
    if not (text or "").strip() and not proof_url:
        raise HTTPException(status_code=400, detail="Message text or proof image is required.")

    msg_doc = {
        "id": f"msg-{uuid.uuid4().hex[:8]}",
        "shop_id": eff_shop,
        "sender": actual_sender,
        "customer_phone": phone.strip(),
        "phone": phone.strip(),
        "message": (text or "").strip(),
        "text": (text or "").strip(),
        "proof_url": proof_url,
        "timestamp": _now_str(),
    }
    db.setdefault("messages", []).append(msg_doc)
    save_db(db)
    return {"ok": True, "message": msg_doc}


@app.delete("/api/messages/{message_id}/photo")
async def delete_message_photo(message_id: str, staff: Dict[str, Any] = Depends(require_staff)):
    db = load_db()
    for m in db.get("messages", []):
        if str(m.get("id")) == str(message_id):
            m["proof_url"] = ""
            save_db(db)
            return {"ok": True}
    raise HTTPException(status_code=404, detail="Message not found.")


@app.delete("/api/messages/photos")
async def delete_all_messages_photos(staff: Dict[str, Any] = Depends(require_staff)):
    db = load_db()
    eff_shop = resolve_effective_shop_id(
        staff, "all" if is_ceo_role(staff.get("role", "")) else staff.get("shop_id")
    )
    for m in db.get("messages", []):
        if matches_shop_filter(m.get("shop_id", "main"), eff_shop):
            m["proof_url"] = ""
    save_db(db)
    return {"ok": True}


# =============================================================================
# POS SALES, DEBTORS, EXPENSES, WORKERS & SETTINGS (shop_id SCOPED)
# =============================================================================
class SaleRequest(BaseModel):
    shop_id: Optional[str] = "main"
    product_id: Optional[str] = None
    item_name: str
    unit_type: str = "Carton"
    qty: float = 1.0
    unit_price: Optional[float] = None
    total: Optional[float] = None
    customer_name: Optional[str] = "Walk-in Customer"
    customer_phone: Optional[str] = ""
    payment_source: Optional[str] = "Cash"
    worker: Optional[str] = "admin"
    record_debt: Optional[bool] = False


@app.post("/api/sales")
async def create_sale(payload: SaleRequest, staff: Dict[str, Any] = Depends(require_staff)):
    db = load_db()
    eff_shop = resolve_effective_shop_id(staff, payload.shop_id or "main")
    if eff_shop == "all":
        eff_shop = "main"

    qty = max(0.01, float(payload.qty or 1))
    unit_price = float(payload.unit_price or 0)
    total_amt = float(payload.total if payload.total is not None else unit_price * qty)
    ref_code = f"POS-{random.randint(10000, 99999)}"

    if payload.product_id:
        for p in db.get("products", []):
            if str(p.get("id")) == str(payload.product_id):
                if unit_price <= 0:
                    unit_price = float(p.get("price_carton") or p.get("price") or 0)
                    total_amt = unit_price * qty
                packs_per_carton = max(1, int(p.get("packs_per_carton") or 1))
                units_per_pack = max(1, int(p.get("units_per_pack") or 1))
                u_lbl = str(payload.unit_type or "")
                deduct_cartons = qty
                if u_lbl == str(p.get("unit_pack_label") or "Pack") or u_lbl.lower() == "pack":
                    deduct_cartons = qty / packs_per_carton
                elif u_lbl != str(p.get("unit_main_label") or "Carton") and u_lbl.lower() != "carton":
                    deduct_cartons = qty / (packs_per_carton * units_per_pack)
                new_stock = max(0.0, float(p.get("qty_cartons") or p.get("stock") or 0) - deduct_cartons)
                p["qty_cartons"] = round(new_stock, 4)
                p["stock"] = p["qty_cartons"]
                break

    sale_record = {
        "id": f"sal-{uuid.uuid4().hex[:8]}",
        "shop_id": eff_shop,
        "item_name": payload.item_name,
        "unit_type": payload.unit_type,
        "qty": qty,
        "unit_price": unit_price,
        "total": total_amt,
        "customer_name": payload.customer_name or "Walk-in Customer",
        "customer_phone": payload.customer_phone or "",
        "payment_source": payload.payment_source or "Cash",
        "worker": payload.worker or staff.get("username") or "admin",
        "record_debt": bool(payload.record_debt),
        "status": "Debt" if payload.record_debt else "Paid",
        "payment_ref": ref_code,
        "timestamp": _now_str(),
    }
    db.setdefault("sales", []).insert(0, sale_record)

    if payload.record_debt:
        db.setdefault("debtors", []).insert(
            0,
            {
                "id": f"deb-{uuid.uuid4().hex[:8]}",
                "shop_id": eff_shop,
                "customer": payload.customer_name or "Customer",
                "phone": payload.customer_phone or "",
                "amount": total_amt,
                "items_bought": f"{payload.item_name} ({payload.unit_type}) x {qty}",
                "status": "Unpaid",
                "timestamp": _now_str(),
            },
        )

    save_db(db)
    return {"ok": True, "sale": sale_record, "receipt": {"payment_ref": ref_code, "total": total_amt}}


@app.get("/api/sales")
async def list_sales(shop_id: str = Query(default="main"), staff: Dict[str, Any] = Depends(require_staff)):
    db = load_db()
    eff_shop = resolve_effective_shop_id(staff, shop_id)
    sales = [s for s in db.get("sales", []) if matches_shop_filter(s.get("shop_id", "main"), eff_shop)]
    return {"sales": sales}


class DebtorRequest(BaseModel):
    shop_id: Optional[str] = "main"
    customer: str
    phone: str
    amount: float
    items_bought: Optional[str] = ""


@app.get("/api/debtors")
async def list_debtors(shop_id: str = Query(default="main"), staff: Dict[str, Any] = Depends(require_staff)):
    db = load_db()
    eff_shop = resolve_effective_shop_id(staff, shop_id)
    debtors = [d for d in db.get("debtors", []) if matches_shop_filter(d.get("shop_id", "main"), eff_shop)]
    return {"debtors": debtors}


@app.post("/api/debtors")
async def add_debtor(payload: DebtorRequest, staff: Dict[str, Any] = Depends(require_staff)):
    db = load_db()
    eff_shop = resolve_effective_shop_id(staff, payload.shop_id or "main")
    if eff_shop == "all":
        eff_shop = "main"
    doc = {
        "id": f"deb-{uuid.uuid4().hex[:8]}",
        "shop_id": eff_shop,
        "customer": payload.customer,
        "phone": payload.phone,
        "amount": float(payload.amount),
        "items_bought": payload.items_bought or "",
        "status": "Unpaid",
        "timestamp": _now_str(),
    }
    db.setdefault("debtors", []).insert(0, doc)
    save_db(db)
    return {"ok": True, "debtor": doc}


@app.post("/api/debtors/{debtor_id}/pay")
async def mark_debtor_paid(debtor_id: str, staff: Dict[str, Any] = Depends(require_staff)):
    db = load_db()
    for d in db.get("debtors", []):
        if str(d.get("id")) == str(debtor_id):
            d["status"] = "Paid"
            save_db(db)
            return {"ok": True}
    raise HTTPException(status_code=404, detail="Debtor not found.")


class ExpenseRequest(BaseModel):
    shop_id: Optional[str] = "main"
    category: str = "Operations"
    amount: float
    note: Optional[str] = ""


@app.get("/api/expenses")
async def list_expenses(shop_id: str = Query(default="main"), staff: Dict[str, Any] = Depends(require_staff)):
    db = load_db()
    eff_shop = resolve_effective_shop_id(staff, shop_id)
    expenses = [e for e in db.get("expenses", []) if matches_shop_filter(e.get("shop_id", "main"), eff_shop)]
    return {"expenses": expenses}


@app.post("/api/expenses")
async def add_expense(payload: ExpenseRequest, staff: Dict[str, Any] = Depends(require_staff)):
    db = load_db()
    eff_shop = resolve_effective_shop_id(staff, payload.shop_id or "main")
    if eff_shop == "all":
        eff_shop = "main"
    doc = {
        "id": f"exp-{uuid.uuid4().hex[:8]}",
        "shop_id": eff_shop,
        "category": payload.category or "Operations",
        "amount": float(payload.amount),
        "note": payload.note or "",
        "timestamp": _now_str(),
    }
    db.setdefault("expenses", []).insert(0, doc)
    save_db(db)
    return {"ok": True, "expense": doc}


@app.delete("/api/expenses/{expense_id}")
async def delete_expense(expense_id: str, staff: Dict[str, Any] = Depends(require_staff)):
    db = load_db()
    db["expenses"] = [e for e in db.get("expenses", []) if str(e.get("id")) != str(expense_id)]
    save_db(db)
    return {"ok": True}


class WorkerRequest(BaseModel):
    shop_id: Optional[str] = "main"
    name: str
    role: str = "Staff"
    username: str
    password: str


@app.get("/api/workers")
async def list_workers(shop_id: str = Query(default="main"), staff: Dict[str, Any] = Depends(require_staff)):
    db = load_db()
    eff_shop = resolve_effective_shop_id(staff, shop_id)
    workers = [
        {k: v for k, v in w.items() if k != "password_hash"}
        for w in db.get("workers", [])
        if matches_shop_filter(w.get("shop_id", "main"), eff_shop)
    ]
    return {"workers": workers}


@app.post("/api/workers")
async def add_worker(payload: WorkerRequest, staff: Dict[str, Any] = Depends(require_staff)):
    db = load_db()
    eff_shop = resolve_effective_shop_id(staff, payload.shop_id or "main")
    if eff_shop == "all":
        eff_shop = "main"
    doc = {
        "id": f"wrk-{uuid.uuid4().hex[:8]}",
        "shop_id": eff_shop,
        "name": payload.name,
        "role": payload.role or "Staff",
        "username": payload.username,
        "password_hash": _hash_password(payload.password or "1234"),
    }
    db.setdefault("workers", []).append(doc)
    save_db(db)
    return {"ok": True, "worker": {k: v for k, v in doc.items() if k != "password_hash"}}


@app.delete("/api/workers/{worker_id}")
async def delete_worker(worker_id: str, staff: Dict[str, Any] = Depends(require_staff)):
    db = load_db()
    db["workers"] = [w for w in db.get("workers", []) if str(w.get("id")) != str(worker_id)]
    save_db(db)
    return {"ok": True}


@app.get("/api/settings")
async def get_settings():
    db = load_db()
    return {"settings": db.get("settings", {})}


@app.post("/api/settings")
async def update_settings(
    business_name: str = Form(default="Sammyreks Business Center"),
    tagline: str = Form(default=""),
    currency: str = Form(default="\u20a6"),
    active_notice: str = Form(default=""),
    broadcast_video_url: str = Form(default=""),
    extra_settings: str = Form(default="{}"),
    broadcast_video_file: Optional[UploadFile] = File(default=None),
    awareness_image_file: Optional[UploadFile] = File(default=None),
    staff: Dict[str, Any] = Depends(require_staff),
):
    db = load_db()
    s = db.setdefault("settings", {})
    s["business_name"] = (business_name or "Sammyreks Business Center").strip()
    s["tagline"] = (tagline or "").strip()
    s["currency"] = (currency or "\u20a6").strip()
    s["active_notice"] = (active_notice or "").strip()

    uploaded_vid = await save_uploaded_media(broadcast_video_file, folder="sammyreks/broadcasts")
    s["broadcast_video_url"] = uploaded_vid or (broadcast_video_url or "").strip()

    uploaded_aware = await save_uploaded_media(awareness_image_file, folder="sammyreks/awareness")
    if uploaded_aware:
        s["awareness_image_url"] = uploaded_aware

    try:
        parsed_extra = json.loads(extra_settings or "{}")
        if isinstance(parsed_extra, dict):
            s["extra_settings"] = parsed_extra
    except Exception:
        pass

    save_db(db)
    return {"ok": True, "settings": s}
