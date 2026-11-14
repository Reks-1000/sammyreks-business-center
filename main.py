import os
import hashlib
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from supabase import create_client, Client
import cloudinary
import cloudinary.uploader
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Sammyreks Business Center")

os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL or "", SUPABASE_KEY or "")

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True
)

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

# AUTO-INITIALIZE ADMIN
try:
    check_empty = supabase.table("workers").select("*", count="exact").execute()
    if not check_empty.data:
        supabase.table("workers").insert({
            "name": "admin", 
            "role": "CEO", 
            "password": hash_password("Sammyreks2026!")
        }).execute()
except Exception:
    pass

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse(request, "index.html", {"request": request})

# --- PRODUCTS API ---
@app.get("/api/products")
async def get_products():
    try:
        res = supabase.table("products").select("*").execute()
        return {"products": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/products")
async def save_product(
    name: str = Form(...),
    cost: float = Form(0.0),
    price_carton: float = Form(0.0),
    price_pack: float = Form(0.0),
    price_retail: float = Form(0.0),
    qty_cartons: float = Form(0.0),
    category: str = Form("General"),
    shelf: str = Form(""),
    description: str = Form(""),
    unit_main_label: str = Form("Carton"),
    unit_pack_label: str = Form("Pack"),
    unit_retail_label: str = Form("Retail Unit")
):
    try:
        payload = {
            "name": name, "cost": cost, "price_carton": price_carton,
            "price_pack": price_pack, "price_retail": price_retail,
            "qty_cartons": qty_cartons, "category": category, "shelf": shelf,
            "description": description, "unit_main_label": unit_main_label,
            "unit_pack_label": unit_pack_label, "unit_retail_label": unit_retail_label
        }
        res = supabase.table("products").insert(payload).execute()
        return {"status": "success", "data": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- POS & SALES API ---
@app.get("/api/sales")
async def get_sales():
    try:
        res = supabase.table("sales").select("*").execute()
        return {"sales": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/pos/sale")
async def process_sale(
    item_name: str = Form(...),
    unit: str = Form(...),
    qty: int = Form(...),
    total: float = Form(...),
    customer_name: str = Form("Walk-in Customer"),
    customer_phone: str = Form("N/A"),
    payment_source: str = Form("Moniepoint MFB"),
    cashier: str = Form("admin")
):
    try:
        ref = f"POS-{os.urandom(2).hex().upper()}"
        sale_data = {
            "item": f"{item_name} ({qty} {unit})", "unit_type": unit, "qty": qty,
            "total": total, "profit": total * 0.15, "worker": cashier,
            "customer_name": customer_name, "customer_phone": customer_phone,
            "payment_source": payment_source, "payment_ref": ref
        }
        supabase.table("sales").insert(sale_data).execute()
        
        receipt_item = [{"name": item_name, "unit": unit, "qty": qty, "price": total/qty, "total": total}]
        supabase.table("saved_receipts").insert({
            "payment_ref": ref, "customer_name": customer_name, "customer_phone": customer_phone,
            "items_json": str(receipt_item), "grand_total": total, "payment_source": payment_source,
            "cashier": cashier
        }).execute()
        
        return {"status": "success", "ref": ref}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- SAVED RECEIPTS VAULT API ---
@app.get("/api/saved-receipts")
async def get_saved_receipts(phone: str = None):
    try:
        query = supabase.table("saved_receipts").select("*")
        if phone:
            query = query.eq("customer_phone", phone)
        res = query.execute()
        return {"receipts": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- DEBTORS API ---
@app.get("/api/debtors")
async def get_debtors():
    try:
        res = supabase.table("debtors").select("*").execute()
        return {"debtors": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/debtors")
async def add_debtor(
    customer: str = Form(...), amount: float = Form(...),
    items_bought: str = Form(""), phone: str = Form("")
):
    try:
        res = supabase.table("debtors").insert({"customer": customer, "amount": amount, "items_bought": items_bought, "phone": phone}).execute()
        return {"status": "success", "data": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- CHAT & ORDERS API ---
@app.get("/api/messages")
async def get_messages(phone: str = None):
    try:
        query = supabase.table("messages").select("*")
        if phone:
            query = query.eq("customer_phone", phone)
        res = query.execute()
        return {"messages": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/messages")
async def post_message(
    customer_phone: str = Form(...), customer_name: str = Form("Customer"),
    sender: str = Form("Customer"), message: str = Form(...)
):
    try:
        res = supabase.table("messages").insert({"customer_phone": customer_phone, "customer_name": customer_name, "sender": sender, "message": message}).execute()
        return {"status": "success", "data": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- SETTINGS API ---
@app.get("/api/settings")
async def get_settings():
    try:
        res = supabase.table("app_settings").select("*").execute()
        return {"settings": {item["key"]: item["value"] for item in res.data}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/settings")
async def update_settings(key: str = Form(...), value: str = Form(...)):
    try:
        chk = supabase.table("app_settings").select("*").eq("key", key).execute()
        if chk.data:
            supabase.table("app_settings").update({"value": value}).eq("key", key).execute()
        else:
            supabase.table("app_settings").insert({"key": key, "value": value}).execute()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- WORKERS & AUTH API ---
@app.get("/api/workers")
async def get_workers():
    try:
        res = supabase.table("workers").select("id, name, role").execute()
        return {"workers": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/auth/login")
async def login_user(username: str = Form(...), password: str = Form(...)):
    if username.lower() == "admin" and password == "Sammyreks2026!":
        return {"status": "success", "user": "admin", "role": "CEO"}
    hashed = hash_password(password)
    res = supabase.table("workers").select("*").ilike("name", username).eq("password", hashed).limit(1).execute()
    if res.data:
        return {"status": "success", "user": res.data[0]["name"], "role": res.data[0]["role"]}
    raise HTTPException(status_code=401, detail="Invalid credentials")