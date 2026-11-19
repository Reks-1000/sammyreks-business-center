import json
import os
import random
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

import cloudinary
import cloudinary.uploader
import firebase_admin
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from firebase_admin import credentials, firestore
from pydantic import BaseModel

load_dotenv()

app = FastAPI(title="Sammyreks Business Center")

if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")

# ---------------------------------------------------------------------------
# Auth & Admin Credentials
# ---------------------------------------------------------------------------
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "Sammyreks2026!")

# ---------------------------------------------------------------------------
# Cloudinary Configuration
# ---------------------------------------------------------------------------
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY", "")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET", "")

if CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET:
    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET,
        secure=True,
    )

# ---------------------------------------------------------------------------
# Google Cloud Firestore Lazy Initialization
# ---------------------------------------------------------------------------
_db_client = None


def get_db():
    global _db_client
    if _db_client is not None:
        return _db_client

    if not firebase_admin._apps:
        raw_creds = os.getenv("FIREBASE_CREDENTIALS_JSON", "").strip()
        local_key_path = os.getenv("FIREBASE_CREDENTIALS_PATH", "serviceAccountKey.json")

        if raw_creds:
            cred_dict = json.loads(raw_creds)
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
        elif os.path.isfile(local_key_path):
            cred = credentials.Certificate(local_key_path)
            firebase_admin.initialize_app(cred)
        else:
            firebase_admin.initialize_app()

    _db_client = firestore.client()
    return _db_client


def now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def normalize_phone(phone: str) -> str:
    if not phone:
        return ""
    return "".join(c for c in str(phone) if c.isdigit() or c == "+").strip()


def serialize_doc(doc) -> Dict[str, Any]:
    data = doc.to_dict() or {}
    data["id"] = data.get("id") or doc.id
    return data


def safe_float(val: Any, default: float = 0.0) -> float:
    try:
        if val is None or str(val).strip() == "":
            return default
        f = float(val)
        return default if (f != f or f == float("inf") or f == float("-inf")) else f
    except (ValueError, TypeError):
        return default


def safe_int(val: Any, default: int = 0) -> int:
    try:
        if val is None or str(val).strip() == "":
            return default
        f = float(val)
        return default if (f != f or f == float("inf") or f == float("-inf")) else int(f)
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------
class PosSalePayload(BaseModel):
    product_id: Optional[str] = None
    item_name: str = ""
    unit_type: str = "Carton"
    qty: int = 1
    unit_price: Optional[float] = None
    total: Optional[float] = None
    customer_name: str = "Walk-in Customer"
    customer_phone: str = "N/A"
    payment_source: str = "Moniepoint MFB"
    worker: str = "admin"
    record_debt: bool = False


class DebtorPayload(BaseModel):
    customer: str
    phone: str = ""
    items_bought: str = ""
    amount: float
    debt_id: Optional[str] = None
    clear_debt: bool = False


class ExpensePayload(BaseModel):
    category: str = "Other"
    amount: float
    note: str = ""


class WorkerPayload(BaseModel):
    name: str
    role: str = "Staff"
    password: str = ""


class SettingsPayload(BaseModel):
    business_name: str = "Sammyreks Business Center"
    tagline: str = "Your trusted plug for quality provisions, groceries, and goods!"
    currency: str = "₦"
    active_notice: str = ""
    broadcast_video_url: str = ""


# ---------------------------------------------------------------------------
# Health, Home & Login Routes
# ---------------------------------------------------------------------------
@app.get("/health")
def health_check():
    try:
        db = get_db()
        _ = list(db.collections())
        return {"status": "ok", "database": "firestore"}
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "detail": str(exc)},
        )


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "business_name": "Sammyreks Business Center",
            "currency": "₦",
        },
    )


@app.post("/api/login")
@app.post("/login")
@app.post("/api/auth/login")
@app.post("/auth/login")
@app.post("/api/staff/login")
@app.post("/staff/login")
async def portal_login(request: Request):
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        data = await request.json()
    else:
        data = dict(await request.form())

    username = str(
        data.get("username")
        or data.get("user")
        or data.get("email")
        or data.get("name")
        or "admin"
    ).strip()
    password = str(
        data.get("password")
        or data.get("passcode")
        or data.get("pin")
        or ""
    ).strip()

    if not password:
        raise HTTPException(status_code=400, detail="Please enter both Username and Password.")

    if username.lower() == ADMIN_USERNAME.lower() and password == ADMIN_PASSWORD:
        return {
            "status": "success",
            "success": True,
            "authenticated": True,
            "username": username,
            "user": username,
            "role": "CEO",
        }

    try:
        db = get_db()
        for doc in db.collection("workers").stream():
            w = doc.to_dict() or {}
            w_name = str(w.get("name") or "").strip()
            w_pass = str(w.get("password") or "").strip()
            if w_name.lower() == username.lower() and w_pass and w_pass == password:
                return {
                    "status": "success",
                    "success": True,
                    "authenticated": True,
                    "username": w_name,
                    "user": w_name,
                    "role": str(w.get("role") or "Staff"),
                }
    except Exception:
        pass

    raise HTTPException(status_code=401, detail="Incorrect username or password.")


# ---------------------------------------------------------------------------
# Products & Inventory (Firestore `products` + Cloudinary Image/Video)
# ---------------------------------------------------------------------------
@app.get("/api/products")
@app.get("/products")
@app.get("/api/inventory")
@app.get("/inventory")
def list_products():
    try:
        db = get_db()
        docs = db.collection("products").stream()
        items = [serialize_doc(d) for d in docs]
        items.sort(key=lambda item: item.get("timestamp", ""), reverse=True)
        return {"products": items, "items": items, "data": items}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Firestore error: {exc}")


@app.post("/api/products")
@app.post("/products")
@app.post("/api/inventory")
@app.post("/inventory")
async def save_product(request: Request):
    try:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            body = await request.json()
            image_file = None
            video_file = None
        else:
            form = await request.form()
            body = dict(form)
            image_file = form.get("image_file") or form.get("file") or form.get("image")
            video_file = form.get("video_file") or form.get("video")

        action_mode = str(body.get("action_mode") or "create").strip().lower()
        existing_id = str(body.get("existing_product_id") or body.get("id") or "").strip()

        name = str(
            body.get("name")
            or body.get("title")
            or body.get("product_name")
            or ""
        ).strip()
        category = str(body.get("category") or "General").strip().title() or "General"

        main_unit = str(body.get("unit_main_label") or body.get("main_unit") or "Carton").strip() or "Carton"
        pack_unit = str(body.get("unit_pack_label") or body.get("sub_unit") or "Pack").strip() or "Pack"
        retail_unit = str(body.get("unit_retail_label") or body.get("retail_unit") or "Retail Unit").strip() or "Retail Unit"

        cost = safe_float(body.get("cost") or body.get("cost_price_carton"), 0.0)
        price_carton = safe_float(body.get("price_carton") or body.get("price"), 0.0)
        price_pack = safe_float(body.get("price_pack"), 0.0)
        price_retail = safe_float(body.get("price_retail"), 0.0)
        qty_cartons = safe_float(
            body.get("qty_cartons")
            if body.get("qty_cartons") not in (None, "")
            else body.get("stock_cartons")
            if body.get("stock_cartons") not in (None, "")
            else body.get("stock"),
            0.0,
        )
        packs_per_carton = max(1, safe_int(body.get("packs_per_carton"), 1))
        units_per_pack = max(1, safe_int(body.get("units_per_pack") or body.get("retail_per_pack"), 1))
        shelf = str(body.get("shelf") or body.get("shelf_location") or "").strip()
        expiry = str(body.get("expiry") or body.get("expiry_date") or date.today().isoformat()).strip()
        video_link = str(body.get("video_url") or body.get("video_link") or "").strip()
        description = str(body.get("description") or "").strip()

        final_img_data = str(body.get("img_data") or body.get("image_url") or "").strip()
        final_video_url = video_link

        has_cloud = bool(CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET)

        if hasattr(image_file, "filename") and image_file.filename:
            if not has_cloud:
                raise HTTPException(status_code=400, detail="Cloudinary environment variables are not configured.")
            img_bytes = await image_file.read()
            res = cloudinary.uploader.upload(
                img_bytes,
                folder="sammyreks_products",
                resource_type="image",
                transformation=[
                    {"width": 800, "height": 800, "crop": "limit"},
                    {"quality": "auto", "fetch_format": "auto"},
                ],
            )
            final_img_data = res.get("secure_url", final_img_data)

        if hasattr(video_file, "filename") and video_file.filename:
            if not has_cloud:
                raise HTTPException(status_code=400, detail="Cloudinary environment variables are not configured.")
            vid_bytes = await video_file.read()
            res = cloudinary.uploader.upload(
                vid_bytes,
                folder="sammyreks_videos",
                resource_type="video",
                chunk_size=6000000,
            )
            final_video_url = res.get("secure_url", final_video_url)

        db = get_db()
        existing_doc = {}
        if action_mode == "update" and existing_id:
            snap = db.collection("products").document(existing_id).get()
            if snap.exists:
                existing_doc = snap.to_dict() or {}
                if not name:
                    name = str(existing_doc.get("name") or "").strip()
                if not final_img_data:
                    final_img_data = str(existing_doc.get("img_data") or existing_doc.get("image_url") or "")
                if not final_video_url:
                    final_video_url = str(existing_doc.get("video_url") or "")

        if not name:
            raise HTTPException(status_code=400, detail="Product Name is required.")

        primary_price = price_carton if price_carton > 0 else (price_pack if price_pack > 0 else price_retail)
        doc_id = existing_id if (action_mode == "update" and existing_id) else str(uuid.uuid4())

        record = {
            "id": doc_id,
            "name": name,
            "title": name,
            "category": category,
            "unit_main_label": main_unit,
            "unit_pack_label": pack_unit,
            "unit_retail_label": retail_unit,
            "cost": cost,
            "price_carton": price_carton,
            "price_pack": price_pack,
            "price_retail": price_retail,
            "price": primary_price,
            "qty_cartons": qty_cartons,
            "stock": int(round(qty_cartons)),
            "packs_per_carton": packs_per_carton,
            "units_per_pack": units_per_pack,
            "shelf": shelf,
            "expiry": expiry,
            "description": description,
            "img_data": final_img_data,
            "image_url": final_img_data,
            "video_url": final_video_url,
            "timestamp": now_str(),
        }

        db.collection("products").document(doc_id).set(record, merge=True)
        return {"status": "success", "success": True, "id": doc_id, "product": record, "data": record}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save product: {exc}")


@app.delete("/api/products/{product_id}")
@app.delete("/products/{product_id}")
def delete_product(product_id: str):
    try:
        db = get_db()
        db.collection("products").document(product_id).delete()
        return {"status": "deleted", "success": True, "id": product_id}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete product: {exc}")


# ---------------------------------------------------------------------------
# POS Sales, Orders, Saved Receipts Vault & Debtors
# ---------------------------------------------------------------------------
@app.get("/api/sales")
def list_sales():
    try:
        db = get_db()
        sales = [serialize_doc(d) for d in db.collection("sales").stream()]
        sales.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        expenses = [serialize_doc(d) for d in db.collection("expenses").stream()]
        total_revenue = sum(safe_float(r.get("total"), 0.0) for r in sales)
        total_profit = sum(safe_float(r.get("profit"), 0.0) for r in sales)
        total_expenses = sum(safe_float(r.get("amount"), 0.0) for r in expenses)
        return {
            "sales": sales,
            "data": sales,
            "summary": {
                "total_revenue": total_revenue,
                "total_profit": total_profit,
                "total_expenses": total_expenses,
                "net_cashflow": total_profit - total_expenses,
            },
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load sales: {exc}")


@app.post("/api/sales")
def create_pos_sale(payload: PosSalePayload):
    try:
        db = get_db()
        qty = max(1, safe_int(payload.qty, 1))
        cust_name = (payload.customer_name or "Walk-in Customer").strip()
        cust_phone = normalize_phone(payload.customer_phone) or "N/A"
        pay_source = (payload.payment_source or "Moniepoint MFB").strip()
        worker = (payload.worker or "admin").strip()

        prod_data: Dict[str, Any] = {}
        prod_ref = None
        if payload.product_id:
            prod_ref = db.collection("products").document(str(payload.product_id))
            snap = prod_ref.get()
            if snap.exists:
                prod_data = snap.to_dict() or {}

        item_name = (payload.item_name or prod_data.get("name") or "Item").strip()
        m_label = prod_data.get("unit_main_label") or "Carton"
        p_label = prod_data.get("unit_pack_label") or "Pack"
        unit_type = (payload.unit_type or m_label).strip()

        if payload.unit_price is not None:
            unit_price = safe_float(payload.unit_price, 0.0)
        elif unit_type == m_label:
            unit_price = safe_float(prod_data.get("price_carton") or prod_data.get("price"), 0.0)
        elif unit_type == p_label:
            unit_price = safe_float(prod_data.get("price_pack"), 0.0)
        else:
            unit_price = safe_float(prod_data.get("price_retail"), 0.0)

        grand_total = safe_float(payload.total, unit_price * qty)
        cost_carton = safe_float(prod_data.get("cost"), 0.0)
        packs_per_c = max(1, safe_int(prod_data.get("packs_per_carton"), 1))
        units_per_p = max(1, safe_int(prod_data.get("units_per_pack"), 1))

        if unit_type == m_label:
            unit_cost = cost_carton
            cartons_deducted = float(qty)
        elif unit_type == p_label:
            unit_cost = cost_carton / packs_per_c
            cartons_deducted = float(qty) / packs_per_c
        else:
            unit_cost = (cost_carton / packs_per_c) / units_per_p
            cartons_deducted = float(qty) / (packs_per_c * units_per_p)

        if prod_ref is not None and prod_data:
            current_cartons = safe_float(prod_data.get("qty_cartons", prod_data.get("stock", 0)), 0.0)
            new_cartons = max(0.0, current_cartons - cartons_deducted)
            prod_ref.update({"qty_cartons": new_cartons, "stock": int(round(new_cartons))})

        ts = now_str()
        today_str = date.today().isoformat()

        if payload.record_debt:
            if not cust_name or cust_name == "Walk-in Customer":
                raise HTTPException(status_code=400, detail="Customer name is required for Debt Evidence.")
            debt_id = str(uuid.uuid4())
            debt_record = {
                "id": debt_id,
                "customer": cust_name,
                "amount": float(grand_total),
                "items_bought": f"{qty} {unit_type}(s) of {item_name}",
                "phone": cust_phone,
                "date": today_str,
                "timestamp": ts,
            }
            db.collection("debtors").document(debt_id).set(debt_record)
            return {"status": "success", "debt_logged": True, "debtor": debt_record}

        profit_final = grand_total - (unit_cost * qty)
        pos_ref = f"POS-{random.randint(1000, 9999)}"
        sold_item_entry = [
            {
                "name": item_name,
                "unit": unit_type,
                "qty": qty,
                "price": unit_price,
                "total": grand_total,
            }
        ]

        receipt_id = str(uuid.uuid4())
        receipt_record = {
            "id": receipt_id,
            "payment_ref": pos_ref,
            "customer_name": cust_name,
            "customer_phone": cust_phone,
            "items_json": json.dumps(sold_item_entry),
            "grand_total": float(grand_total),
            "payment_source": pay_source,
            "cashier": worker,
            "timestamp": ts,
        }
        db.collection("saved_receipts").document(receipt_id).set(receipt_record)

        sale_id = str(uuid.uuid4())
        sale_record = {
            "id": sale_id,
            "item": f"{item_name} ({qty} {unit_type})",
            "unit_type": unit_type,
            "qty": qty,
            "total": float(grand_total),
            "profit": float(profit_final),
            "date": today_str,
            "timestamp": ts,
            "worker": worker,
            "customer_name": cust_name,
            "customer_phone": cust_phone,
            "payment_source": pay_source,
            "payment_ref": pos_ref,
        }
        db.collection("sales").document(sale_id).set(sale_record)

        receipt_payload = {
            "type": "Official Sales & Delivery Receipt",
            "customer": cust_name,
            "phone": cust_phone,
            "date": ts,
            "sold_items": sold_item_entry,
            "grand_total": grand_total,
            "cashier": worker,
            "payment_source": pay_source,
            "payment_ref": pos_ref,
        }
        return {"status": "success", "sale": sale_record, "receipt": receipt_payload}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to record POS sale: {exc}")


@app.get("/api/receipts")
def list_saved_receipts(phone: Optional[str] = None):
    try:
        db = get_db()
        docs = db.collection("saved_receipts").stream()
        items = [serialize_doc(d) for d in docs]
        if phone and normalize_phone(phone):
            clean_p = normalize_phone(phone)
            items = [i for i in items if normalize_phone(i.get("customer_phone", "")) == clean_p]
        items.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        return {"receipts": items, "data": items}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load saved receipts: {exc}")


@app.get("/api/debtors")
def list_debtors():
    try:
        db = get_db()
        items = [serialize_doc(d) for d in db.collection("debtors").stream()]
        items.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        return {"debtors": items, "data": items}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load debtors: {exc}")


@app.post("/api/debtors")
def manage_debtor(payload: DebtorPayload):
    try:
        db = get_db()
        if payload.clear_debt and payload.debt_id:
            db.collection("debtors").document(payload.debt_id).delete()
            return {"status": "success", "cleared": True, "id": payload.debt_id}

        if payload.debt_id and not payload.clear_debt:
            ref = db.collection("debtors").document(payload.debt_id)
            ref.update({"amount": safe_float(payload.amount, 0.0), "timestamp": now_str()})
            return {"status": "success", "updated": True, "id": payload.debt_id}

        if not payload.customer.strip() or safe_float(payload.amount, 0.0) <= 0:
            raise HTTPException(status_code=400, detail="Customer name and positive amount are required.")

        doc_id = str(uuid.uuid4())
        record = {
            "id": doc_id,
            "customer": payload.customer.strip(),
            "phone": normalize_phone(payload.phone),
            "items_bought": payload.items_bought.strip(),
            "amount": safe_float(payload.amount, 0.0),
            "date": date.today().isoformat(),
            "timestamp": now_str(),
        }
        db.collection("debtors").document(doc_id).set(record)
        return {"status": "success", "debtor": record, "data": record}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to manage debtor: {exc}")


# ---------------------------------------------------------------------------
# Customer Orders, Multi-Item Requests & Live Chat
# ---------------------------------------------------------------------------
@app.post("/api/orders")
async def create_customer_order(request: Request):
    try:
        data = await request.json()
        cust_name = str(data.get("customer") or data.get("customer_name") or "").strip()
        cust_phone = normalize_phone(str(data.get("contact_phone") or data.get("phone") or ""))
        notes = str(data.get("notes") or "").strip()
        items = data.get("items") or []
        special_items = data.get("special_items") or []

        if not cust_name or not cust_phone:
            raise HTTPException(status_code=400, detail="Your Full Name and Phone Number are required.")

        db = get_db()
        ts = now_str()
        today_str = date.today().isoformat()
        unique_ref = f"SRK-{random.randint(1000, 9999)}"
        created_orders = []

        if items:
            total_cart = sum(safe_float(i.get("total_price"), 0.0) for i in items)
            summary = ", ".join(
                [f"{safe_int(i.get('qty'), 1)} {i.get('unit', 'Unit')}(s) of {i.get('name', 'Item')}" for i in items]
            )
            for item in items:
                oid = str(uuid.uuid4())
                orec = {
                    "id": oid,
                    "customer": cust_name,
                    "item": f"[Cart Order] {item.get('name', 'Item')} ({item.get('unit', 'Unit')})",
                    "qty": safe_int(item.get("qty"), 1),
                    "status": "Order Placed - Pending Staff Quote",
                    "contact_phone": cust_phone,
                    "total_price": safe_float(item.get("total_price"), 0.0),
                    "payment_ref": unique_ref,
                    "notes": notes,
                    "date": today_str,
                    "timestamp": ts,
                }
                db.collection("orders").document(oid).set(orec)
                created_orders.append(orec)

            intro = f"Hello! I just submitted an order. Items: {summary}. Total: ₦{total_cart:,.2f}. My Reference Code is `{unique_ref}`."
            if notes:
                intro += f" Notes: {notes}"
        elif special_items:
            req_names = []
            for sp in special_items:
                sp_name = str(sp.get("item") or "").strip()
                sp_qty = max(1, safe_int(sp.get("qty"), 1))
                if sp_name:
                    req_names.append(f"{sp_qty} of {sp_name}")
                    oid = str(uuid.uuid4())
                    orec = {
                        "id": oid,
                        "customer": cust_name,
                        "item": f"SPECIAL: {sp_name}",
                        "qty": sp_qty,
                        "status": "Order Placed - Pending Staff Quote",
                        "contact_phone": cust_phone,
                        "total_price": 0.0,
                        "payment_ref": unique_ref,
                        "date": today_str,
                        "timestamp": ts,
                    }
                    db.collection("orders").document(oid).set(orec)
                    created_orders.append(orec)

            if not req_names:
                raise HTTPException(status_code=400, detail="Please enter at least one requested item.")
            intro = f"Hello! I submitted a special request for: {', '.join(req_names)}. My Reference Code is `{unique_ref}`."
        else:
            raise HTTPException(status_code=400, detail="No order items provided.")

        msg_id = str(uuid.uuid4())
        db.collection("messages").document(msg_id).set(
            {
                "id": msg_id,
                "sender": "Customer",
                "customer_phone": cust_phone,
                "customer_name": cust_name,
                "message": intro,
                "proof_url": "",
                "timestamp": ts,
            }
        )

        return {
            "status": "success",
            "payment_ref": unique_ref,
            "customer_phone": cust_phone,
            "customer_name": cust_name,
            "orders": created_orders,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to submit order: {exc}")


@app.get("/api/messages")
def get_messages(phone: Optional[str] = None):
    try:
        db = get_db()
        docs = db.collection("messages").stream()
        all_msgs = [serialize_doc(d) for d in docs]
        all_msgs.sort(key=lambda m: m.get("timestamp", ""))

        threads: Dict[str, Dict[str, str]] = {}
        for m in all_msgs:
            ph = normalize_phone(m.get("customer_phone", ""))
            nm = str(m.get("customer_name") or "Customer").strip()
            if ph and ph not in threads:
                threads[ph] = {"phone": ph, "name": nm, "label": f"{nm} ({ph})"}

        if phone and normalize_phone(phone):
            clean_p = normalize_phone(phone)
            filtered = [m for m in all_msgs if normalize_phone(m.get("customer_phone", "")) == clean_p]
            pending_orders = [
                serialize_doc(o)
                for o in db.collection("orders").stream()
                if normalize_phone((o.to_dict() or {}).get("contact_phone", "")) == clean_p
                and any(
                    k in str((o.to_dict() or {}).get("status", ""))
                    for k in ("Pending", "Submitted", "Order Placed")
                )
            ]
            return {
                "messages": filtered,
                "data": filtered,
                "threads": list(threads.values()),
                "pending_orders": pending_orders,
            }

        return {"messages": all_msgs, "data": all_msgs, "threads": list(threads.values())}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load messages: {exc}")


@app.post("/api/messages")
async def post_message(request: Request):
    try:
        content_type = request.headers.get("content-type", "")
        proof_file = None
        if "application/json" in content_type:
            payload = await request.json()
        else:
            form = await request.form()
            payload = dict(form)
            proof_file = form.get("proof_file") or form.get("file")

        cust_phone = normalize_phone(
            str(payload.get("customer_phone") or payload.get("phone") or "")
        )
        cust_name = str(payload.get("customer_name") or "Customer").strip() or "Customer"
        sender = str(payload.get("sender") or "Customer").strip()
        msg_text = str(payload.get("message") or payload.get("text") or "").strip()
        proof_url = str(payload.get("proof_url") or "").strip()

        if hasattr(proof_file, "filename") and proof_file.filename:
            if not (CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET):
                raise HTTPException(status_code=400, detail="Cloudinary environment variables are not configured.")
            img_bytes = await proof_file.read()
            res = cloudinary.uploader.upload(
                img_bytes,
                folder="sammyreks_payment_proofs",
                resource_type="image",
                transformation=[
                    {"width": 800, "height": 800, "crop": "limit"},
                    {"quality": "auto", "fetch_format": "auto"},
                ],
            )
            proof_url = res.get("secure_url", proof_url)

        if not msg_text and not proof_url:
            raise HTTPException(status_code=400, detail="Please enter a message or upload payment proof.")

        if proof_url and "[Payment Proof Image Attached]" not in msg_text:
            msg_text = (msg_text + "\n[Payment Proof Image Attached]").strip()

        db = get_db()
        ts = now_str()
        doc_id = str(uuid.uuid4())
        record = {
            "id": doc_id,
            "sender": sender,
            "customer_phone": cust_phone,
            "customer_name": cust_name,
            "message": msg_text,
            "proof_url": proof_url,
            "timestamp": ts,
        }
        db.collection("messages").document(doc_id).set(record)

        if proof_url and cust_phone:
            for odoc in db.collection("orders").stream():
                odata = odoc.to_dict() or {}
                if normalize_phone(odata.get("contact_phone", "")) == cust_phone:
                    odoc.reference.update({"status": "Payment Submitted - Pending Verification"})

        return {"status": "success", "success": True, "message": record, "data": record}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to send message: {exc}")


@app.post("/api/receipts/approve")
async def one_tap_approve_receipt(request: Request):
    try:
        data = await request.json()
        active_phone = normalize_phone(str(data.get("customer_phone") or ""))
        pay_source = str(data.get("payment_source") or "Moniepoint MFB").strip()
        cashier = str(data.get("cashier") or "admin").strip()

        if not active_phone:
            raise HTTPException(status_code=400, detail="Customer phone is required.")

        db = get_db()
        all_orders = [serialize_doc(o) for o in db.collection("orders").stream()]
        pending_orders = [
            o
            for o in all_orders
            if normalize_phone(o.get("contact_phone", "")) == active_phone
            and any(k in str(o.get("status", "")) for k in ("Pending", "Submitted", "Order Placed"))
        ]

        if not pending_orders:
            raise HTTPException(status_code=400, detail="No active pending orders requiring approval.")

        order_ref = str(pending_orders[0].get("payment_ref") or f"SRK-{random.randint(1000, 9999)}")
        cust_nm = str(pending_orders[0].get("customer") or "Customer")
        grand_sum = sum(safe_float(o.get("total_price"), 0.0) for o in pending_orders)
        ts = now_str()
        today_str = date.today().isoformat()

        saved_items_list = []
        receipt_rows_html = ""

        for o in pending_orders:
            db.collection("orders").document(str(o["id"])).update({"status": "Completed / Paid"})
            clean_nm = str(o.get("item") or "Item").replace("SPECIAL:", "").replace("[Cart Order]", "").strip()
            q_val = float(safe_int(o.get("qty"), 1))
            t_val = safe_float(o.get("total_price"), 0.0)
            u_price = (t_val / q_val) if q_val > 0 else t_val

            receipt_rows_html += (
                f"<tr><td style='padding:6px 0; font-size:0.9rem;'><b>{clean_nm}</b><br>"
                f"<span style='font-size:0.75rem; color:#555;'>{int(q_val)} x ₦{u_price:,.2f}</span></td>"
                f"<td style='padding:6px 0; text-align:right; font-size:0.9rem; vertical-align:top; font-weight:bold;'>₦{t_val:,.2f}</td></tr>"
            )
            saved_items_list.append(
                {"name": clean_nm, "unit": "Unit", "qty": int(q_val), "price": u_price, "total": t_val}
            )

            sale_id = str(uuid.uuid4())
            db.collection("sales").document(sale_id).set(
                {
                    "id": sale_id,
                    "item": clean_nm,
                    "unit_type": "Unit",
                    "qty": int(q_val),
                    "total": float(t_val),
                    "profit": float(t_val * 0.15),
                    "date": today_str,
                    "timestamp": ts,
                    "worker": cashier,
                    "customer_name": cust_nm,
                    "customer_phone": active_phone,
                    "payment_source": pay_source,
                    "payment_ref": order_ref,
                }
            )

        receipt_id = str(uuid.uuid4())
        db.collection("saved_receipts").document(receipt_id).set(
            {
                "id": receipt_id,
                "payment_ref": order_ref,
                "customer_name": cust_nm,
                "customer_phone": active_phone,
                "items_json": json.dumps(saved_items_list),
                "grand_total": float(grand_sum),
                "payment_source": pay_source,
                "cashier": cashier,
                "timestamp": ts,
            }
        )

        professional_receipt_html = (
            f"<div style='background:#fdfcf7; color:#111; padding:20px; border-radius:6px; border:1px solid #dcd6cd; font-family:\"Courier New\", Courier, monospace; max-width:380px; margin:10px 0;'>"
            f"<div style='text-align:center; border-bottom:1px dashed #333; padding-bottom:10px; margin-bottom:10px;'>"
            f"<h4 style='margin:0;'>SAMMYREKS BUSINESS CENTER</h4>"
            f"<p style='margin:2px 0; font-size:0.75rem; color:#444;'>OFFICIAL PURCHASE RECEIPT</p>"
            f"<span style='background:#222; color:#fff; font-size:0.75rem; padding:2px 6px; border-radius:3px; font-weight:bold;'>PAID &amp; VERIFIED</span>"
            f"</div>"
            f"<div style='font-size:0.8rem; margin-bottom:10px; border-bottom:1px dashed #333; padding-bottom:6px;'>"
            f"<p style='margin:2px 0;'><b>REF CODE:</b> {order_ref}</p>"
            f"<p style='margin:2px 0;'><b>CUSTOMER:</b> {cust_nm}</p>"
            f"<p style='margin:2px 0;'><b>PHONE:</b> {active_phone}</p>"
            f"<p style='margin:2px 0;'><b>GATEWAY:</b> {pay_source}</p>"
            f"<p style='margin:2px 0;'><b>DATE:</b> {ts}</p>"
            f"</div>"
            f"<table style='width:100%; border-collapse:collapse; margin-bottom:12px;'><tbody>{receipt_rows_html}</tbody></table>"
            f"<div style='border-top:1px dashed #333; padding-top:8px; font-weight:bold;'>TOTAL PAID: ₦{grand_sum:,.2f}</div>"
            f"</div>"
        )

        sys_msg_id = str(uuid.uuid4())
        db.collection("messages").document(sys_msg_id).set(
            {
                "id": sys_msg_id,
                "sender": "System",
                "customer_phone": active_phone,
                "customer_name": cust_nm,
                "message": professional_receipt_html,
                "proof_url": "",
                "timestamp": ts,
            }
        )

        return {"status": "success", "payment_ref": order_ref, "grand_total": grand_sum}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Receipt approval error: {exc}")


# ---------------------------------------------------------------------------
# Expenses, Workers & App Settings
# ---------------------------------------------------------------------------
@app.get("/api/expenses")
def list_expenses():
    try:
        db = get_db()
        items = [serialize_doc(d) for d in db.collection("expenses").stream()]
        items.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        return {"expenses": items, "data": items}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load expenses: {exc}")


@app.post("/api/expenses")
def create_expense(payload: ExpensePayload):
    try:
        db = get_db()
        doc_id = str(uuid.uuid4())
        record = {
            "id": doc_id,
            "category": payload.category.strip() or "Other",
            "amount": safe_float(payload.amount, 0.0),
            "note": payload.note.strip(),
            "date": date.today().isoformat(),
            "timestamp": now_str(),
        }
        db.collection("expenses").document(doc_id).set(record)
        return {"status": "success", "expense": record, "data": record}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save expense: {exc}")


@app.get("/api/workers")
def list_workers():
    try:
        db = get_db()
        items = []
        for d in db.collection("workers").stream():
            row = serialize_doc(d)
            row.pop("password", None)
            items.append(row)
        return {"workers": items, "data": items}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load workers: {exc}")


@app.post("/api/workers")
def create_worker(payload: WorkerPayload):
    try:
        if not payload.name.strip():
            raise HTTPException(status_code=400, detail="Worker Name is required.")
        if len(payload.password) < 6:
            raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")
        db = get_db()
        doc_id = str(uuid.uuid4())
        record = {
            "id": doc_id,
            "name": payload.name.strip(),
            "role": payload.role.strip() or "Staff",
            "password": payload.password,
            "timestamp": now_str(),
        }
        db.collection("workers").document(doc_id).set(record)
        safe_row = {k: v for k, v in record.items() if k != "password"}
        return {"status": "success", "worker": safe_row, "data": safe_row}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create worker: {exc}")


@app.get("/api/settings")
def get_settings():
    try:
        db = get_db()
        defaults = {
            "business_name": "Sammyreks Business Center",
            "tagline": "Your trusted plug for quality provisions, groceries, and goods!",
            "currency": "₦",
            "active_notice": "",
            "broadcast_video_url": "",
        }
        doc = db.collection("settings").document("app_settings").get()
        if doc.exists:
            data = {**defaults, **(doc.to_dict() or {})}
        else:
            data = defaults
        return {"settings": data, "data": data}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load settings: {exc}")


@app.post("/api/settings")
async def save_settings(request: Request):
    try:
        content_type = request.headers.get("content-type", "")
        broadcast_file = None
        if "application/json" in content_type:
            body = await request.json()
        else:
            form = await request.form()
            body = dict(form)
            broadcast_file = form.get("broadcast_video_file") or form.get("file")

        db = get_db()
        curr_snap = db.collection("settings").document("app_settings").get()
        existing = curr_snap.to_dict() or {} if curr_snap.exists else {}

        video_url = str(
            body.get("broadcast_video_url")
            if body.get("broadcast_video_url") is not None
            else existing.get("broadcast_video_url", "")
        ).strip()

        if hasattr(broadcast_file, "filename") and broadcast_file.filename:
            if not (CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET):
                raise HTTPException(status_code=400, detail="Cloudinary environment variables are not configured.")
            vid_bytes = await broadcast_file.read()
            res = cloudinary.uploader.upload(
                vid_bytes,
                folder="sammyreks_videos",
                resource_type="video",
                chunk_size=6000000,
            )
            video_url = res.get("secure_url", video_url)

        data = {
            "business_name": str(body.get("business_name") or existing.get("business_name") or "Sammyreks Business Center").strip(),
            "tagline": str(body.get("tagline") or existing.get("tagline") or "Your trusted plug for quality provisions, groceries, and goods!").strip(),
            "currency": str(body.get("currency") or existing.get("currency") or "₦").strip(),
            "active_notice": str(
                body.get("active_notice")
                if body.get("active_notice") is not None
                else existing.get("active_notice", "")
            ).strip(),
            "broadcast_video_url": video_url,
            "updated_at": now_str(),
        }
        db.collection("settings").document("app_settings").set(data, merge=True)
        return {"status": "success", "settings": data, "data": data}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save settings: {exc}")


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
