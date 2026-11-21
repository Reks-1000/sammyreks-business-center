import base64
import json
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import cloudinary
import cloudinary.uploader
import firebase_admin
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
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


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def make_ref() -> str:
    return f"SRK-{uuid.uuid4().hex[:6].upper()}"


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
    data["name"] = str(data.get("name") or "Unnamed Product").strip()
    data["category"] = str(data.get("category") or "General").strip()
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
    data["order_type"] = str(
        data.get("order_type") or ("Multi-Item Request" if special_items else "Cart Order")
    ).strip()
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
        return {"ok": True, "username": username, "role": role}

    worker_docs = db.collection("workers").where("username", "==", username).stream()
    for doc in worker_docs:
        w = doc.to_dict() or {}
        if str(w.get("password", "")) == password:
            return {
                "ok": True,
                "username": w.get("username") or w.get("name") or username,
                "role": w.get("role") or "Staff",
            }

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

    clean_vendor = vendor_name.strip()
    if not clean_vendor and existing_data:
        clean_vendor = str(
            existing_data.get("vendor_name")
            or existing_data.get("vendor")
            or existing_data.get("sponsor")
            or ""
        ).strip()

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
    notes = str(payload.get("notes") or "").strip()
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    special_items = payload.get("special_items") if isinstance(payload.get("special_items"), list) else []
    payment_ref = make_ref()
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
        chat_text = (
            f"Hello! I just submitted an order. Items: {items_str}. "
            f"Total: ₦{total_sum:,.2f}. My Reference Code is '{payment_ref}'.{note_str}"
        )

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

    return {"ok": True, "id": order_id, "payment_ref": payment_ref}


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
            lines_html += (
                f"<p style='margin:2px 0;'>{it_qty} {it_unit}(s) of {it_name} &mdash; "
                f"{currency}{it_total:,.2f}</p>"
            )
    elif order_data.get("notes"):
        lines_html += f"<p style='margin:2px 0;'>{order_data.get('notes')}</p>"

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
        f"<p style='margin:2px 0;'><b>REF CODE:</b> {ref_code}</p>"
        f"<p style='margin:2px 0;'><b>CUSTOMER:</b> {customer}</p>"
        f"<p style='margin:2px 0;'><b>PHONE:</b> {phone or 'N/A'}</p>"
        f"<p style='margin:2px 0;'><b>VERIFIED BY:</b> {verified_by}</p>"
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
def get_messages(phone: Optional[str] = Query(default=None)):
    docs = db.collection("messages").stream()
    clean_phone = (phone or "").strip()
    messages: List[Dict[str, Any]] = []
    for doc in docs:
        data = normalize_message(doc.id, doc.to_dict())
        if clean_phone and data["customer_phone"] != clean_phone:
            continue
        messages.append(data)
    messages.sort(key=lambda x: str(x.get("timestamp", "")))
    return {"messages": messages}


@app.post("/api/messages")
async def post_message(
    phone: str = Form(...),
    sender: str = Form("Customer"),
    text: str = Form(""),
    proof_file: Optional[UploadFile] = File(None),
):
    clean_phone = phone.strip()
    clean_text = text.strip()
    if not clean_phone:
        raise HTTPException(status_code=400, detail="Phone number is required")

    proof_url = upload_to_cloudinary(
        proof_file, folder="sammyreks_receipts", resource_type="image", max_dim=1000
    )

    if not clean_text and not proof_url:
        raise HTTPException(status_code=400, detail="Enter a message or attach a receipt image")

    msg_doc: Dict[str, Any] = {
        "sender": sender.strip() or "Customer",
        "customer_phone": clean_phone,
        "phone": clean_phone,
        "message": clean_text,
        "text": clean_text,
        "proof_url": proof_url or "",
        "timestamp": now_str(),
    }
    msg_id = uuid.uuid4().hex
    db.collection("messages").document(msg_id).set(msg_doc)
    return {"ok": True, "id": msg_id, "proof_url": proof_url}


@app.delete("/api/messages/{message_id}/photo")
def delete_message_photo(message_id: str):
    doc_ref = db.collection("messages").document(message_id)
    if not doc_ref.get().exists:
        raise HTTPException(status_code=404, detail="Message not found")
    doc_ref.set({"proof_url": ""}, merge=True)
    return {"ok": True, "id": message_id}


@app.delete("/api/messages/photos")
def delete_all_message_photos():
    docs = db.collection("messages").stream()
    cleared = 0
    for doc in docs:
        data = doc.to_dict() or {}
        if data.get("proof_url"):
            doc.reference.set({"proof_url": ""}, merge=True)
            cleared += 1
    return {"ok": True, "cleared": cleared}


@app.get("/api/sales")
def get_sales():
    docs = db.collection("sales").stream()
    sales: List[Dict[str, Any]] = []
    for doc in docs:
        item = dict(doc.to_dict() or {})
        item["id"] = doc.id
        sales.append(item)
    sales.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)
    return {"sales": sales}


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
                pdata.get("qty_cartons")
                if pdata.get("qty_cartons") is not None
                else (pdata.get("stock") or 0)
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
            "status": "unpaid",
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


@app.get("/api/debtors")
def get_debtors():
    docs = db.collection("debtors").stream()
    debtors: List[Dict[str, Any]] = []
    for doc in docs:
        item = dict(doc.to_dict() or {})
        item["id"] = doc.id
        debtors.append(item)
    debtors.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)
    return {"debtors": debtors}


@app.post("/api/debtors")
async def add_debtor(request: Request):
    payload = await request.json()
    doc = {
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
def mark_debtor_paid(debtor_id: str):
    doc_ref = db.collection("debtors").document(debtor_id)
    if not doc_ref.get().exists:
        raise HTTPException(status_code=404, detail="Debtor not found")
    doc_ref.set({"status": "paid", "paid_at": now_str()}, merge=True)
    return {"ok": True, "id": debtor_id, "status": "paid"}


@app.delete("/api/debtors/{debtor_id}")
def delete_debtor(debtor_id: str):
    doc_ref = db.collection("debtors").document(debtor_id)
    if not doc_ref.get().exists:
        raise HTTPException(status_code=404, detail="Debtor not found")
    doc_ref.delete()
    return {"ok": True, "id": debtor_id}


@app.get("/api/expenses")
def get_expenses():
    docs = db.collection("expenses").stream()
    expenses: List[Dict[str, Any]] = []
    for doc in docs:
        item = dict(doc.to_dict() or {})
        item["id"] = doc.id
        expenses.append(item)
    expenses.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)
    return {"expenses": expenses}


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


@app.delete("/api/expenses/{expense_id}")
def delete_expense(expense_id: str):
    doc_ref = db.collection("expenses").document(expense_id)
    if not doc_ref.get().exists:
        raise HTTPException(status_code=404, detail="Expense not found")
    doc_ref.delete()
    return {"ok": True, "id": expense_id}


@app.get("/api/workers")
def get_workers():
    docs = db.collection("workers").stream()
    workers: List[Dict[str, Any]] = []
    for doc in docs:
        item = dict(doc.to_dict() or {})
        item.pop("password", None)
        item["id"] = doc.id
        workers.append(item)
    workers.sort(key=lambda x: str(x.get("name", "")).lower())
    return {"workers": workers}


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


@app.delete("/api/workers/{worker_id}")
def delete_worker(worker_id: str):
    doc_ref = db.collection("workers").document(worker_id)
    if not doc_ref.get().exists:
        raise HTTPException(status_code=404, detail="Worker not found")
    doc_ref.delete()
    return {"ok": True, "id": worker_id}


@app.get("/api/analytics")
def get_analytics():
    products_by_id: Dict[str, Dict[str, Any]] = {}
    products_by_name: Dict[str, Dict[str, Any]] = {}
    for doc in db.collection("products").stream():
        p = normalize_product(doc.id, doc.to_dict())
        products_by_id[p["id"]] = p
        products_by_name[p["name"].strip().lower()] = p

    total_revenue = 0.0
    total_profit = 0.0
    for doc in db.collection("sales").stream():
        s = doc.to_dict() or {}
        if s.get("record_debt") is True or str(s.get("status") or "").lower() == "debt":
            continue
        qty = max(0.0, float(s.get("qty") or 1.0))
        row_total = float(s.get("total") if s.get("total") is not None else (float(s.get("unit_price") or 0.0) * qty))
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
        total_expenses += float(ex.get("amount") or 0.0)

    net_cashflow = total_profit - total_expenses
    return {
        "ok": True,
        "analytics": {
            "total_revenue": total_revenue,
            "total_profit": total_profit,
            "total_expenses": total_expenses,
            "net_cashflow": net_cashflow,
        },
    }


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
    extra_settings: str = Form(""),
    broadcast_video_file: Optional[UploadFile] = File(None),
):
    ref = db.collection("settings").document("business")
    existing = ref.get().to_dict() if ref.get().exists else {}

    uploaded_video = upload_to_cloudinary(
        broadcast_video_file, folder="sammyreks_videos", resource_type="video"
    )
    final_video = (
        uploaded_video
        or broadcast_video_url.strip()
        or existing.get("broadcast_video_url")
        or ""
    )

    parsed_extra: Any = existing.get("extra_settings")
    if extra_settings.strip():
        try:
            parsed_extra = json.loads(extra_settings)
        except json.JSONDecodeError:
            parsed_extra = extra_settings.strip()

    doc: Dict[str, Any] = {
        "business_name": business_name.strip() or "Sammyreks Business Center",
        "tagline": tagline.strip(),
        "active_notice": active_notice.strip(),
        "broadcast_video_url": final_video,
        "currency": currency.strip() or "₦",
        "updated_at": now_str(),
    }
    if parsed_extra is not None:
        doc["extra_settings"] = parsed_extra

    ref.set(doc, merge=True)
    merged_out = {**existing, **doc}
    return {"ok": True, "settings": merged_out}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return JSONResponse(status_code=500, content={"detail": str(exc)})
