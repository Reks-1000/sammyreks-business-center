import base64
import hashlib
import hmac
import json
import os
import random
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import cloudinary
import cloudinary.uploader
import firebase_admin
from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from firebase_admin import credentials, firestore

app = FastAPI(title="Sammyreks Business Center")
templates = Jinja2Templates(directory="templates")

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_VIDEO_BYTES = 50 * 1024 * 1024


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
APP_SECRET = os.getenv("APP_SECRET", f"srk-secret-{ADMIN_PASSWORD}")


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def make_ref() -> str:
    return f"SRK-{uuid.uuid4().hex[:6].upper()}"


def make_pin() -> str:
    return f"{random.randint(1000, 9999)}"


def normalize_phone(phone: str) -> str:
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    return digits or str(phone or "").strip()


def make_staff_token(username: str, role: str) -> str:
    payload = f"{username}|{role}"
    sig = hmac.new(APP_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    raw = f"{payload}|{sig}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8")


def verify_staff_token(token: Optional[str]) -> Optional[Dict[str, str]]:
    if not token:
        return None
    try:
        decoded = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
        username, role, sig = decoded.split("|", 2)
        expected = hmac.new(
            APP_SECRET.encode("utf-8"), f"{username}|{role}".encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if hmac.compare_digest(sig, expected):
            return {"username": username, "role": role}
    except Exception:
        return None
    return None


def get_or_create_customer_pin(phone: str, requested_pin: str = "") -> str:
    norm = normalize_phone(phone)
    if not norm:
        return ""
    ref = db.collection("customer_profiles").document(norm)
    snap = ref.get()
    clean_req = str(requested_pin or "").strip()

    if snap.exists:
        data = snap.to_dict() or {}
        existing_pin = str(data.get("pin") or "").strip()
        if existing_pin:
            return existing_pin

    pin_to_save = clean_req if (clean_req.isdigit() and len(clean_req) >= 4) else make_pin()
    ref.set({"phone": norm, "pin": pin_to_save, "updated_at": now_str()}, merge=True)
    return pin_to_save


def verify_customer_access(phone: str, access_code: str) -> bool:
    norm = normalize_phone(phone)
    code = str(access_code or "").strip()
    if not norm or not code:
        return False

    prof_snap = db.collection("customer_profiles").document(norm).get()
    if prof_snap.exists:
        saved_pin = str((prof_snap.to_dict() or {}).get("pin") or "").strip()
        if saved_pin and hmac.compare_digest(saved_pin, code):
            return True

    # Also allow unlocking with any valid Order Reference Code (e.g. SRK-4247) belonging to this phone
    code_upper = code.upper()
    for doc in db.collection("orders").stream():
        odata = doc.to_dict() or {}
        ophone = normalize_phone(odata.get("contact_phone") or odata.get("phone") or "")
        oref = str(odata.get("payment_ref") or "").strip().upper()
        if ophone == norm and oref and hmac.compare_digest(oref, code_upper):
            return True

    return False


def normalize_product(doc_id: str, raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    data = dict(raw or {})
    price_carton = float(data.get("price_carton") if data.get("price_carton") is not None else (data.get("price") or 0.0))
    stock_cartons = float(data.get("qty_cartons") if data.get("qty_cartons") is not None else (data.get("stock") or 0.0))
    image_url = str(data.get("image_url") or data.get("img_data") or "").strip()
    data["id"] = doc_id
    data["name"] = str(data.get("name") or "Unnamed Product").strip()
    data["category"] = str(data.get("category") or "General").strip()
    data["unit_main_label"] = str(data.get("unit_main_label") or "Carton").strip()
    data["unit_pack_label"] = str(data.get("unit_pack_label") or "Pack").strip()
    data["unit_retail_label"] = str(data.get("unit_retail_label") or "Retail Unit").strip()
    data["price_carton"] = price_carton
    data["price"] = price_carton
    data["qty_cartons"] = stock_cartons
    data["stock"] = stock_cartons
    data["cost"] = float(data.get("cost") or 0.0)
    data["price_pack"] = float(data.get("price_pack") or 0.0)
    data["price_retail"] = float(data.get("price_retail") if data.get("price_retail") is not None else price_carton)
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
    phone = str(data.get("contact_phone") or data.get("phone") or "").strip()
    customer = str(data.get("customer") or data.get("customer_name") or "Customer").strip()
    items = data.get("items") if isinstance(data.get("items"), list) else []
    special_items = data.get("special_items") if isinstance(data.get("special_items"), list) else []
    data["id"] = doc_id
    data["customer"] = customer
    data["customer_name"] = customer
    data["contact_phone"] = phone
    data["phone"] = phone
    data["notes"] = str(data.get("notes") or "").strip()
    data["items"] = items
    data["special_items"] = special_items
    data["order_type"] = str(data.get("order_type") or ("Multi-Item Request" if special_items else "Cart Order")).strip()
    data["payment_ref"] = str(data.get("payment_ref") or "").strip()
    data["status"] = str(data.get("status") or "Pending").strip()
    data["timestamp"] = str(data.get("timestamp") or "").strip()
    return data


def normalize_message(doc_id: str, raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    data = dict(raw or {})
    phone = str(data.get("customer_phone") or data.get("phone") or "").strip()
    body = str(data.get("message") if data.get("message") is not None else (data.get("text") or "")).strip()
    data["id"] = doc_id
    data["sender"] = str(data.get("sender") or "Customer").strip()
    data["customer_phone"] = phone
    data["phone"] = phone
    data["message"] = body
    data["text"] = body
    data["proof_url"] = str(data.get("proof_url") or "").strip()
    data["timestamp"] = str(data.get("timestamp") or "").strip()
    return data


def upload_to_cloudinary(
    file: Optional[UploadFile],
    folder: str,
    resource_type: str = "image",
    max_dim: Optional[int] = None,
) -> Optional[str]:
    if not file or not file.filename:
        return None
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
    if size_bytes > max_allowed:
        limit_mb = max_allowed // (1024 * 1024)
        raise HTTPException(status_code=400, detail=f"Uploaded file exceeds the {limit_mb}MB limit.")

    upload_kwargs: Dict[str, Any] = {
        "folder": folder,
        "resource_type": resource_type,
    }
    if resource_type == "image" and max_dim:
        upload_kwargs["transformation"] = [
            {"width": max_dim, "height": max_dim, "crop": "limit", "quality": "auto", "fetch_format": "auto"}
        ]

    file.file.seek(0)
    result = cloudinary.uploader.upload(file.file, **upload_kwargs)
    return result.get("secure_url") or result.get("url")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"request": request})


@app.post("/api/login")
async def login(request: Request):
    payload = await request.json()
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    mode = str(payload.get("mode", "admin")).strip().lower()

    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        role = "CEO" if mode == "admin" else "Staff"
        token = make_staff_token(username, role)
        return {"ok": True, "username": username, "role": role, "token": token}

    worker_docs = db.collection("workers").where("username", "==", username).stream()
    for doc in worker_docs:
        w = doc.to_dict() or {}
        if str(w.get("password", "")) == password:
            uname = w.get("username") or w.get("name") or username
            role = w.get("role") or "Staff"
            token = make_staff_token(uname, role)
            return {"ok": True, "username": uname, "role": role, "token": token}

    raise HTTPException(status_code=401, detail="Invalid username or password")


@app.get("/api/products")
def get_products():
    docs = db.collection("products").stream()
    products = [normalize_product(doc.id, doc.to_dict()) for doc in docs]
    products.sort(key=lambda x: str(x.get("name", "")).lower())
    return {"products": products}


@app.post("/api/products")
async def save_product(
    action_mode: str = Form("create"),
    existing_product_id: str = Form(""),
    category: str = Form("General"),
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
    description: str = Form(""),
    image_file: Optional[UploadFile] = File(None),
    video_file: Optional[UploadFile] = File(None),
):
    clean_name = name.strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail="Product name is required")

    target_id = existing_product_id.strip() if action_mode == "update" else ""
    existing_data: Dict[str, Any] = {}

    if target_id:
        snap = db.collection("products").document(target_id).get()
        if not snap.exists:
            raise HTTPException(status_code=404, detail="Selected product not found")
        existing_data = snap.to_dict() or {}
    else:
        target_id = uuid.uuid4().hex

    image_url = upload_to_cloudinary(image_file, folder="sammyreks_products", resource_type="image", max_dim=800)
    if not image_url:
        image_url = existing_data.get("image_url") or existing_data.get("img_data") or ""

    uploaded_video_url = upload_to_cloudinary(video_file, folder="sammyreks_videos", resource_type="video")
    final_video_url = (
        uploaded_video_url
        or video_link.strip()
        or existing_data.get("video_url")
        or ""
    )

    product_doc: Dict[str, Any] = {
        "name": clean_name,
        "category": category.strip() or "General",
        "unit_main_label": main_unit.strip() or "Carton",
        "unit_pack_label": sub_unit.strip() or "Pack",
        "unit_retail_label": retail_unit.strip() or "Retail Unit",
        "price_carton": float(price_carton),
        "price": float(price_carton),
        "qty_cartons": float(stock_cartons),
        "stock": float(stock_cartons),
        "cost": float(cost_price_carton),
        "price_pack": float(price_pack),
        "packs_per_carton": max(1, int(packs_per_carton or 1)),
        "shelf": shelf_location.strip(),
        "price_retail": float(price_retail),
        "units_per_pack": max(1, int(retail_per_pack or 1)),
        "expiry": expiry_date.strip(),
        "video_url": final_video_url,
        "description": description.strip(),
        "image_url": image_url,
        "updated_at": now_str(),
    }
    if not existing_data:
        product_doc["created_at"] = now_str()

    db.collection("products").document(target_id).set(product_doc, merge=True)
    normalized = normalize_product(target_id, {**existing_data, **product_doc})
    return {"ok": True, "id": target_id, "product": normalized}


@app.delete("/api/products/{product_id}")
def delete_product(product_id: str):
    doc_ref = db.collection("products").document(product_id)
    if not doc_ref.get().exists:
        raise HTTPException(status_code=404, detail="Product not found")
    doc_ref.delete()
    return {"ok": True, "id": product_id}


@app.get("/api/orders")
def get_orders():
    docs = db.collection("orders").stream()
    orders = [normalize_order(doc.id, doc.to_dict()) for doc in docs]
    orders.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)
    return {"orders": orders}


@app.post("/api/orders")
async def create_order(request: Request):
    payload = await request.json()
    customer = str(payload.get("customer") or payload.get("customer_name") or "Customer").strip()
    phone = str(payload.get("contact_phone") or payload.get("phone") or "").strip()
    requested_pin = str(payload.get("pin") or "").strip()
    notes = str(payload.get("notes") or "").strip()
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    special_items = payload.get("special_items") if isinstance(payload.get("special_items"), list) else []
    if not phone:
        raise HTTPException(status_code=400, detail="Phone number is required")

    payment_ref = make_ref()
    customer_pin = get_or_create_customer_pin(phone, requested_pin)
    ts = now_str()

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
        chat_text = f"Hello! I just submitted an order. Items: {items_str}. Total: ₦{total_sum:,.2f}. My Reference Code is '{payment_ref}'.{note_str}"

    order_doc: Dict[str, Any] = {
        "order_type": order_type,
        "customer": customer,
        "contact_phone": phone,
        "phone": phone,
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
        "sender": "Customer",
        "customer_phone": phone,
        "phone": phone,
        "message": chat_text,
        "text": chat_text,
        "payment_ref": payment_ref,
        "timestamp": ts,
    }
    db.collection("messages").document(uuid.uuid4().hex).set(msg_doc)

    return {"ok": True, "id": order_id, "payment_ref": payment_ref, "customer_pin": customer_pin}


@app.post("/api/orders/{order_id}/verify")
async def verify_order(order_id: str, request: Request):
    order_ref = db.collection("orders").document(order_id)
    snap = order_ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Order not found")

    order_data = snap.to_dict() or {}
    payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    verified_by = str(payload.get("verified_by") or "admin").strip()

    settings_snap = db.collection("settings").document("business").get()
    settings_data = settings_snap.to_dict() if settings_snap.exists else {}
    biz_name = str(settings_data.get("business_name") or "SAMMYREKS BUSINESS CENTER").upper()
    currency = str(settings_data.get("currency") or "₦")

    customer = str(order_data.get("customer") or order_data.get("customer_name") or "Customer").strip()
    phone = str(order_data.get("contact_phone") or order_data.get("phone") or "").strip()
    ref_code = str(order_data.get("payment_ref") or make_ref()).strip()
    ts = now_str()

    items = order_data.get("items") or []
    special_items = order_data.get("special_items") or []
    lines_html = ""
    total_val = 0.0

    if special_items:
        for sp in special_items:
            sp_text = str(sp.get("item") or sp.get("name") or "Requested Item")
            lines_html += f"<p style='margin:2px 0;'>{sp_text}</p>"
    elif items:
        for it in items:
            it_name = str(it.get("name") or "Item")
            it_unit = str(it.get("unit") or "Unit")
            it_qty = it.get("qty") or 1
            it_total = float(it.get("total_price") or 0.0)
            total_val += it_total
            lines_html += f"<p style='margin:2px 0;'>{it_qty} {it_unit}(s) of {it_name} &mdash; {currency}{it_total:,.2f}</p>"
    elif order_data.get("notes"):
        lines_html += f"<p style='margin:2px 0;'>{order_data.get('notes')}</p>"

    receipt_html = (
        f"<div style='background:#fdfcf7; color:#111; padding:16px; border-radius:6px; border:1px solid #dcd6cd; "
        f"font-family:\"Courier New\", Courier, monospace; max-width:360px; margin:6px 0;'>"
        f"<div style='text-align:center; border-bottom:1px dashed #333; padding-bottom:8px; margin-bottom:8px;'>"
        f"<h5 style='margin:0; font-weight:bold;'>{biz_name}</h5>"
        f"<div style='font-size:0.75rem; color:#444; margin:2px 0;'>OFFICIAL PURCHASE RECEIPT</div>"
        f"<span style='background:#198754; color:#fff; font-size:0.72rem; padding:2px 6px; border-radius:3px; font-weight:bold;'>PAID &amp; VERIFIED</span>"
        f"</div>"
        f"<div style='font-size:0.8rem; margin-bottom:8px; border-bottom:1px dashed #333; padding-bottom:6px;'>"
        f"<p style='margin:2px 0;'><b>REF CODE:</b> {ref_code}</p>"
        f"<p style='margin:2px 0;'><b>CUSTOMER:</b> {customer}</p>"
        f"<p style='margin:2px 0;'><b>PHONE:</b> {phone or 'N/A'}</p>"
        f"<p style='margin:2px 0;'><b>VERIFIED BY:</b> {verified_by}</p>"
        f"</div>"
        f"<div style='font-size:0.8rem; margin-bottom:8px;'>{lines_html}</div>"
        f"<div style='border-top:1px dashed #333; padding-top:6px; font-size:0.85rem; font-weight:bold;'>"
        f"TOTAL: {currency}{total_val:,.2f}"
        f"</div>"
        f"</div>"
    )

    order_ref.set({"status": "Verified", "verified_by": verified_by, "verified_at": ts}, merge=True)

    msg_doc: Dict[str, Any] = {
        "sender": "System",
        "customer_phone": phone,
        "phone": phone,
        "message": receipt_html,
        "text": receipt_html,
        "payment_ref": ref_code,
        "timestamp": ts,
    }
    db.collection("messages").document(uuid.uuid4().hex).set(msg_doc)

    return {"ok": True, "id": order_id, "status": "Verified", "payment_ref": ref_code}


@app.delete("/api/orders/{order_id}")
def delete_order(order_id: str):
    order_ref = db.collection("orders").document(order_id)
    if not order_ref.get().exists:
        raise HTTPException(status_code=404, detail="Order not found")
    order_ref.delete()
    return {"ok": True, "id": order_id}


@app.get("/api/messages")
def get_messages(
    phone: Optional[str] = Query(default=None),
    pin: Optional[str] = Query(default=None),
    x_staff_token: Optional[str] = Header(default=None),
):
    staff_user = verify_staff_token(x_staff_token)
    clean_phone = (phone or "").strip()
    norm_target = normalize_phone(clean_phone)

    if not staff_user:
        if not norm_target or not (pin or "").strip():
            raise HTTPException(
                status_code=401,
                detail="Enter both your Phone Number and your 4-digit Chat PIN (or Order Reference Code) to access your private chat.",
            )
        if not verify_customer_access(norm_target, pin or ""):
            raise HTTPException(
                status_code=403,
                detail="Invalid Phone Number or PIN / Reference Code.",
            )

    docs = db.collection("messages").stream()
    messages: List[Dict[str, Any]] = []
    for doc in docs:
        data = normalize_message(doc.id, doc.to_dict())
        if norm_target and normalize_phone(data["customer_phone"]) != norm_target:
            continue
        messages.append(data)
    messages.sort(key=lambda x: str(x.get("timestamp", "")))
    return {"messages": messages}


@app.post("/api/messages")
async def post_message(
    phone: str = Form(...),
    pin: str = Form(""),
    sender: str = Form("Customer"),
    text: str = Form(""),
    proof_file: Optional[UploadFile] = File(None),
    x_staff_token: Optional[str] = Header(default=None),
):
    staff_user = verify_staff_token(x_staff_token)
    clean_phone = phone.strip()
    clean_text = text.strip()
    if not clean_phone:
        raise HTTPException(status_code=400, detail="Phone number is required")

    customer_pin = ""
    if not staff_user:
        norm = normalize_phone(clean_phone)
        prof_snap = db.collection("customer_profiles").document(norm).get()
        if prof_snap.exists:
            if not verify_customer_access(norm, pin):
                raise HTTPException(
                    status_code=403,
                    detail="Please enter your valid 4-digit Chat PIN (or Order Ref) for this phone number before sending.",
                )
            customer_pin = str((prof_snap.to_dict() or {}).get("pin") or "")
        else:
            customer_pin = get_or_create_customer_pin(clean_phone, pin)

    proof_url = upload_to_cloudinary(proof_file, folder="sammyreks_receipts", resource_type="image", max_dim=1000)

    if not clean_text and not proof_url:
        raise HTTPException(status_code=400, detail="Enter a message or attach a receipt image")

    final_sender = "Staff" if staff_user else "Customer"
    msg_doc: Dict[str, Any] = {
        "sender": final_sender,
        "customer_phone": clean_phone,
        "phone": clean_phone,
        "message": clean_text,
        "text": clean_text,
        "proof_url": proof_url or "",
        "timestamp": now_str(),
    }
    msg_id = uuid.uuid4().hex
    db.collection("messages").document(msg_id).set(msg_doc)
    return {"ok": True, "id": msg_id, "proof_url": proof_url, "customer_pin": customer_pin}


@app.post("/api/sales")
async def record_sale(request: Request):
    payload = await request.json()
    product_id = str(payload.get("product_id") or "").strip()
    item_name = str(payload.get("item_name") or "Item").strip()
    unit_type = str(payload.get("unit_type") or "Carton").strip()
    qty = max(1.0, float(payload.get("qty") or 1))
    customer_name = str(payload.get("customer_name") or "Walk-in Customer").strip()
    customer_phone = str(payload.get("customer_phone") or "N/A").strip()
    payment_source = str(payload.get("payment_source") or "Cash").strip()
    worker = str(payload.get("worker") or "admin").strip()
    record_debt = bool(payload.get("record_debt", False))

    unit_price = float(payload.get("unit_price") or 0.0)
    total = float(payload.get("total") or (unit_price * qty))
    payment_ref = make_ref()
    ts = now_str()

    if product_id:
        prod_ref = db.collection("products").document(product_id)
        prod_snap = prod_ref.get()
        if prod_snap.exists:
            pdata = prod_snap.to_dict() or {}
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
                pdata.get("qty_cartons") if pdata.get("qty_cartons") is not None else (pdata.get("stock") or 0)
            )
            new_stock = max(0.0, current_stock - deduct_cartons)
            prod_ref.set({"qty_cartons": new_stock, "stock": new_stock, "updated_at": ts}, merge=True)

    if record_debt:
        debt_doc = {
            "customer": customer_name,
            "phone": customer_phone,
            "amount": total,
            "items_bought": f"{item_name} ({qty} {unit_type})",
            "payment_ref": payment_ref,
            "worker": worker,
            "timestamp": ts,
        }
        db.collection("debtors").document(uuid.uuid4().hex).set(debt_doc)

    sale_doc = {
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


@app.post("/api/debtors")
async def add_debtor(request: Request):
    payload = await request.json()
    doc = {
        "customer": str(payload.get("customer") or "").strip(),
        "phone": str(payload.get("phone") or "").strip(),
        "amount": float(payload.get("amount") or 0.0),
        "items_bought": str(payload.get("items_bought") or "").strip(),
        "timestamp": now_str(),
    }
    if not doc["customer"] or not doc["phone"]:
        raise HTTPException(status_code=400, detail="Customer name and phone are required")
    doc_id = uuid.uuid4().hex
    db.collection("debtors").document(doc_id).set(doc)
    return {"ok": True, "id": doc_id}


@app.post("/api/expenses")
async def add_expense(request: Request):
    payload = await request.json()
    doc = {
        "category": str(payload.get("category") or "Operations").strip(),
        "amount": float(payload.get("amount") or 0.0),
        "note": str(payload.get("note") or "").strip(),
        "timestamp": now_str(),
    }
    doc_id = uuid.uuid4().hex
    db.collection("expenses").document(doc_id).set(doc)
    return {"ok": True, "id": doc_id}


@app.post("/api/workers")
async def add_worker(request: Request):
    payload = await request.json()
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Worker name is required")
    username = str(payload.get("username") or name.lower().replace(" ", "")).strip()
    doc = {
        "name": name,
        "role": str(payload.get("role") or "Staff").strip(),
        "username": username,
        "password": str(payload.get("password") or ""),
        "timestamp": now_str(),
    }
    doc_id = uuid.uuid4().hex
    db.collection("workers").document(doc_id).set(doc)
    return {"ok": True, "id": doc_id}


@app.get("/api/settings")
def get_settings():
    snap = db.collection("settings").document("business").get()
    settings = snap.to_dict() if snap.exists else {}
    return {"settings": settings or {"business_name": "Sammyreks Business Center", "currency": "₦"}}


@app.post("/api/settings")
async def save_settings(
    business_name: str = Form("Sammyreks Business Center"),
    tagline: str = Form(""),
    active_notice: str = Form(""),
    broadcast_video_url: str = Form(""),
    currency: str = Form("₦"),
    broadcast_video_file: Optional[UploadFile] = File(None),
):
    ref = db.collection("settings").document("business")
    existing = ref.get().to_dict() if ref.get().exists else {}

    uploaded_video = upload_to_cloudinary(broadcast_video_file, folder="sammyreks_videos", resource_type="video")
    final_video = (
        uploaded_video
        or broadcast_video_url.strip()
        or existing.get("broadcast_video_url")
        or ""
    )

    doc = {
        "business_name": business_name.strip() or "Sammyreks Business Center",
        "tagline": tagline.strip(),
        "active_notice": active_notice.strip(),
        "broadcast_video_url": final_video,
        "currency": currency.strip() or "₦",
        "updated_at": now_str(),
    }
    ref.set(doc, merge=True)
    return {"ok": True, "settings": doc}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return JSONResponse(status_code=500, content={"detail": str(exc)})
