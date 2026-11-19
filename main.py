import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import cloudinary
import cloudinary.uploader
import firebase_admin
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
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
# Auth & Admin Credentials (reads from Railway Variables / .env)
# ---------------------------------------------------------------------------
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

# ---------------------------------------------------------------------------
# Cloudinary Setup (reads from Railway Variables / .env)
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
# Google Cloud Firestore Setup
# ---------------------------------------------------------------------------
_db_client = None


def get_db():
    """Lazily initialize and return the Firestore client."""
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


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def serialize_doc(doc) -> Dict[str, Any]:
    data = doc.to_dict() or {}
    data["id"] = data.get("id") or doc.id
    return data


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------
class SalePayload(BaseModel):
    customer_name: str = "Walk-in Customer"
    phone: str = ""
    items: List[Dict[str, Any]]
    total_amount: float
    payment_method: str = "Cash"


class DebtorPayload(BaseModel):
    customer_name: str
    phone: str
    amount_owed: float
    note: str = ""


class ExpensePayload(BaseModel):
    title: str
    amount: float
    category: str = "Operations"
    note: str = ""


class WorkerPayload(BaseModel):
    name: str
    role: str = "Staff"
    phone: str = ""
    shift: str = ""
    username: str = ""
    password: str = ""


class SettingsPayload(BaseModel):
    business_name: str = "Sammyreks Business Center"
    tagline: str = ""
    phone: str = ""
    address: str = ""
    currency: str = "₦"


# ---------------------------------------------------------------------------
# Health, Frontend & Universal Portal Login Routes
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
        or data.get("staff_id")
        or "admin"
    ).strip()
    password = str(
        data.get("password")
        or data.get("passcode")
        or data.get("pin")
        or ""
    ).strip()
    portal_mode = str(data.get("mode") or data.get("role") or "admin").strip()

    if not password:
        raise HTTPException(status_code=400, detail="Please enter a password")

    # 1. Check Admin credentials from Railway Variables (or allow first-time setup if unset)
    if username.lower() == ADMIN_USERNAME.lower():
        if not ADMIN_PASSWORD or password == ADMIN_PASSWORD:
            return {
                "status": "success",
                "success": True,
                "authenticated": True,
                "role": "admin",
                "mode": portal_mode,
                "username": username,
                "name": "Admin",
                "token": f"admin-{uuid.uuid4().hex}",
            }

    # 2. Check Staff/Worker records stored in Firestore
    try:
        db = get_db()
        for doc in db.collection("workers").stream():
            w = doc.to_dict() or {}
            w_user = str(w.get("username") or w.get("name") or "").strip()
            w_pass = str(w.get("password") or "").strip()
            if w_user.lower() == username.lower() and w_pass and w_pass == password:
                return {
                    "status": "success",
                    "success": True,
                    "authenticated": True,
                    "role": w.get("role", "staff").lower(),
                    "mode": portal_mode,
                    "username": w_user,
                    "name": w.get("name", w_user),
                    "token": f"staff-{uuid.uuid4().hex}",
                }
    except Exception:
        pass

    raise HTTPException(status_code=401, detail="Invalid username or password")


# ---------------------------------------------------------------------------
# Products & Media Upload
# ---------------------------------------------------------------------------
@app.get("/api/products")
def list_products():
    try:
        db = get_db()
        docs = db.collection("products").stream()
        items = [serialize_doc(d) for d in docs]
        return {"products": items, "data": items}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Firestore error: {exc}")


@app.post("/api/products")
async def create_product(
    request: Request,
    name: Optional[str] = Form(default=None),
    price: Optional[float] = Form(default=None),
    stock: Optional[int] = Form(default=None),
    category: Optional[str] = Form(default=None),
    description: Optional[str] = Form(default=None),
    image_url: Optional[str] = Form(default=None),
    file: Optional[UploadFile] = File(default=None),
):
    try:
        if name is None and request.headers.get("content-type", "").startswith("application/json"):
            body = await request.json()
            name = body.get("name")
            price = body.get("price")
            stock = body.get("stock", 0)
            category = body.get("category", "General")
            description = body.get("description", "")
            image_url = body.get("image_url", "")

        if not name or price is None:
            raise HTTPException(status_code=400, detail="Product name and price are required.")

        final_image_url = image_url or ""
        if file and file.filename:
            if not (CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET):
                raise HTTPException(status_code=400, detail="Cloudinary environment variables are not configured.")
            content = await file.read()
            upload_res = cloudinary.uploader.upload(
                content,
                folder="sammyreks_business_center/products",
                resource_type="auto",
            )
            final_image_url = upload_res.get("secure_url", final_image_url)

        doc_id = str(uuid.uuid4())
        record = {
            "id": doc_id,
            "name": str(name).strip(),
            "price": float(price),
            "stock": int(stock if stock is not None else 0),
            "category": (category or "General").strip(),
            "description": (description or "").strip(),
            "image_url": final_image_url,
            "created_at": utc_now_iso(),
        }

        db = get_db()
        db.collection("products").document(doc_id).set(record)
        return {"status": "success", "product": record, "data": record}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create product: {exc}")


@app.delete("/api/products/{product_id}")
def delete_product(product_id: str):
    try:
        db = get_db()
        db.collection("products").document(product_id).delete()
        return {"status": "deleted", "id": product_id}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete product: {exc}")


# ---------------------------------------------------------------------------
# Live Chat & Orders (/api/messages)
# ---------------------------------------------------------------------------
@app.get("/api/messages")
def get_messages(phone: Optional[str] = None):
    try:
        db = get_db()
        query = db.collection("messages")
        if phone:
            clean_phone = phone.strip()
            docs = query.where("phone", "==", clean_phone).stream()
        else:
            docs = query.stream()

        messages = [serialize_doc(d) for d in docs]
        messages.sort(key=lambda m: m.get("created_at", ""))
        return {"messages": messages, "data": messages}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load messages: {exc}")


@app.post("/api/messages")
async def post_message(request: Request):
    try:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            payload = await request.json()
        else:
            form_data = await request.form()
            payload = dict(form_data)
            if "order_items" in payload and isinstance(payload["order_items"], str) and payload["order_items"]:
                try:
                    payload["order_items"] = json.loads(payload["order_items"])
                except json.JSONDecodeError:
                    pass

        phone = str(payload.get("phone") or payload.get("customer_phone") or "").strip()
        text = str(payload.get("text") or payload.get("message") or "").strip()
        sender = str(payload.get("sender") or "customer").strip()
        order_items = payload.get("order_items") or payload.get("items")
        total_amount = payload.get("total_amount")

        if not text and not order_items:
            raise HTTPException(status_code=400, detail="Message text or order items are required.")

        doc_id = str(uuid.uuid4())
        record = {
            "id": doc_id,
            "phone": phone,
            "sender": sender,
            "text": text or "Submitted an order",
            "order_items": order_items,
            "total_amount": float(total_amount) if total_amount is not None and total_amount != "" else None,
            "created_at": utc_now_iso(),
        }

        db = get_db()
        db.collection("messages").document(doc_id).set(record)
        return {"status": "success", "message": record, "data": record}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to send message: {exc}")


# ---------------------------------------------------------------------------
# POS Sales, Debtors, Expenses, Workers & Settings
# ---------------------------------------------------------------------------
@app.get("/api/sales")
def list_sales():
    try:
        db = get_db()
        records = [serialize_doc(d) for d in db.collection("sales").stream()]
        records.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return {"sales": records, "data": records}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load sales: {exc}")


@app.post("/api/sales")
def create_sale(sale: SalePayload):
    try:
        db = get_db()
        doc_id = str(uuid.uuid4())
        record = sale.model_dump()
        record["id"] = doc_id
        record["created_at"] = utc_now_iso()
        db.collection("sales").document(doc_id).set(record)

        for item in sale.items:
            prod_id = item.get("id") or item.get("product_id")
            qty = int(item.get("qty") or item.get("quantity") or 0)
            if prod_id and qty > 0:
                prod_ref = db.collection("products").document(str(prod_id))
                prod_snap = prod_ref.get()
                if prod_snap.exists:
                    current_stock = int((prod_snap.to_dict() or {}).get("stock", 0))
                    prod_ref.update({"stock": max(0, current_stock - qty)})

        return {"status": "success", "sale": record, "data": record}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to record sale: {exc}")


@app.get("/api/debtors")
def list_debtors():
    try:
        db = get_db()
        records = [serialize_doc(d) for d in db.collection("debtors").stream()]
        return {"debtors": records, "data": records}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load debtors: {exc}")


@app.post("/api/debtors")
def create_debtor(debtor: DebtorPayload):
    try:
        db = get_db()
        doc_id = str(uuid.uuid4())
        record = debtor.model_dump()
        record["id"] = doc_id
        record["created_at"] = utc_now_iso()
        db.collection("debtors").document(doc_id).set(record)
        return {"status": "success", "debtor": record, "data": record}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save debtor: {exc}")


@app.get("/api/expenses")
def list_expenses():
    try:
        db = get_db()
        records = [serialize_doc(d) for d in db.collection("expenses").stream()]
        return {"expenses": records, "data": records}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load expenses: {exc}")


@app.post("/api/expenses")
def create_expense(expense: ExpensePayload):
    try:
        db = get_db()
        doc_id = str(uuid.uuid4())
        record = expense.model_dump()
        record["id"] = doc_id
        record["created_at"] = utc_now_iso()
        db.collection("expenses").document(doc_id).set(record)
        return {"status": "success", "expense": record, "data": record}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save expense: {exc}")


@app.get("/api/workers")
def list_workers():
    try:
        db = get_db()
        records = [serialize_doc(d) for d in db.collection("workers").stream()]
        return {"workers": records, "data": records}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load workers: {exc}")


@app.post("/api/workers")
def create_worker(worker: WorkerPayload):
    try:
        db = get_db()
        doc_id = str(uuid.uuid4())
        record = worker.model_dump()
        record["id"] = doc_id
        record["created_at"] = utc_now_iso()
        db.collection("workers").document(doc_id).set(record)
        return {"status": "success", "worker": record, "data": record}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save worker: {exc}")


@app.get("/api/settings")
def get_settings():
    try:
        db = get_db()
        doc = db.collection("settings").document("business_profile").get()
        if doc.exists:
            data = doc.to_dict() or {}
            return {"settings": data, "data": data}
        default_data = {
            "business_name": "Sammyreks Business Center",
            "currency": "₦",
        }
        return {"settings": default_data, "data": default_data}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load settings: {exc}")


@app.post("/api/settings")
def save_settings(payload: SettingsPayload):
    try:
        db = get_db()
        data = payload.model_dump()
        data["updated_at"] = utc_now_iso()
        db.collection("settings").document("business_profile").set(data, merge=True)
        return {"status": "success", "settings": data, "data": data}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save settings: {exc}")


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
