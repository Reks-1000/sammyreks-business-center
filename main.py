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


def init_firestore() -> firestore.Client:
    if firebase_admin._apps:
        return firestore.client()

    service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    service_account_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()

    if service_account_json:
        cred = credentials.Certificate(json.loads(service_account_json))
        firebase_admin.initialize_app(cred)
    elif service_account_path and os.path.exists(service_account_path):
        cred = credentials.Certificate(service_account_path)
        firebase_admin.initialize_app(cred)
    else:
        firebase_admin.initialize_app()

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
    return templates.TemplateResponse("index.html", {"request": request})


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
    products: List[Dict[str, Any]] = []
    for doc in docs:
        data = doc.to_dict() or {}
        data["id"] = doc.id
        products.append(data)
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
        "packs_per_carton": int(packs_per_carton or 1),
        "shelf": shelf_location.strip(),
        "price_retail": float(price_retail),
        "units_per_pack": int(retail_per_pack or 1),
        "expiry": expiry_date.strip(),
        "video_url": final_video_url,
        "description": description.strip(),
        "image_url": image_url,
        "updated_at": now_str(),
    }
    if not existing_data:
        product_doc["created_at"] = now_str()

    db.collection("products").document(target_id).set(product_doc, merge=True)
    product_doc["id"] = target_id
    return {"ok": True, "id": target_id, "product": product_doc}


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
    orders: List[Dict[str, Any]] = []
    for doc in docs:
        data = doc.to_dict() or {}
        data["id"] = doc.id
        orders.append(data)
    orders.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)
    return {"orders": orders}


@app.post("/api/orders")
async def create_order(request: Request):
    payload = await request.json()
    customer = str(payload.get("customer") or payload.get("customer_name") or "Customer").strip()
    phone = str(payload.get("contact_phone") or payload.get("phone") or "").strip()
    notes = str(payload.get("notes") or "").strip()
    items = payload.get("items") or []
    special_items = payload.get("special_items") or []
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

    return {"ok": True, "id": order_id, "payment_ref": payment_ref}


@app.get("/api/messages")
def get_messages(phone: Optional[str] = Query(default=None)):
    docs = db.collection("messages").stream()
    clean_phone = (phone or "").strip()
    messages: List[Dict[str, Any]] = []
    for doc in docs:
        data = doc.to_dict() or {}
        data["id"] = doc.id
        msg_phone = str(data.get("customer_phone") or data.get("phone") or "").strip()
        if clean_phone and msg_phone != clean_phone:
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
    proof_url = upload_to_cloudinary(proof_file, folder="sammyreks_receipts", resource_type="image", max_dim=1000)

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
