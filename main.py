import os
import hashlib
import random
import json
import math
from datetime import datetime, date
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from supabase import create_client, Client
import cloudinary
import cloudinary.uploader
from dotenv import load_dotenv

# Load Environment Variables
if os.path.exists(".env"):
    load_dotenv()

app = FastAPI(title="Sammyreks Business Center - Standalone Enterprise")

# Ensure required local directories exist
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Supabase Initialization
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Cloudinary Initialization
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True
)

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def normalize_phone(phone: str) -> str:
    if not phone:
        return ""
    return "".join(c for c in phone if c.isdigit() or c == '+').strip()

def get_now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# Auto-initialize secure default admin account if workers table is empty
try:
    if supabase:
        check_empty = supabase.table("workers").select("*", count="exact").execute()
        if not check_empty.data:
            supabase.table("workers").insert({
                "name": "admin", 
                "role": "CEO", 
                "password": hash_password("Sammyreks2026!")
            }).execute()
except Exception:
    pass

# ==========================================
# API ENDPOINTS & BACKEND WORKFLOWS
# ==========================================

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """Serve the unified enterprise application frontend."""
    return templates.TemplateResponse(request, "index.html", {})

@app.get("/api/settings")
async def get_settings():
    if not supabase:
        return {"notice": "", "broadcast_video": ""}
    try:
        res = supabase.table("app_settings").select("*").execute()
        settings = {item["key"]: item["value"] for item in res.data} if res.data else {}
        return {
            "notice": settings.get("active_notice", ""),
            "broadcast_video": settings.get("broadcast_video_url", "")
        }
    except Exception:
        return {"notice": "", "broadcast_video": ""}

@app.post("/api/settings")
async def update_settings(notice: str = Form(...), broadcast_video_url: str = Form("")):
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    try:
        for key, val in [("active_notice", notice), ("broadcast_video_url", broadcast_video_url)]:
            chk = supabase.table("app_settings").select("*").eq("key", key).execute()
            if chk.data:
                supabase.table("app_settings").update({"value": val}).eq("key", key).execute()
            else:
                supabase.table("app_settings").insert({"key": key, "value": val}).execute()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/products")
async def get_products():
    if not supabase:
        return {"products": []}
    try:
        response = supabase.table("products").select("*").execute()
        return {"products": response.data or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/products")
async def add_or_update_product(
    name: str = Form(...),
    cost: float = Form(0.0),
    price_carton: float = Form(0.0),
    price_pack: float = Form(0.0),
    price_retail: float = Form(0.0),
    qty_cartons: float = Form(0.0),
    packs_per_carton: int = Form(1),
    units_per_pack: int = Form(1),
    category: str = Form("General"),
    shelf: str = Form(""),
    description: str = Form(""),
    unit_main_label: str = Form("Carton"),
    unit_pack_label: str = Form("Pack"),
    unit_retail_label: str = Form("Retail Unit"),
    image: UploadFile = File(None),
    video: UploadFile = File(None)
):
    img_url = ""
    if image and image.filename:
        try:
            upload_res = cloudinary.uploader.upload(image.file, folder="sammyreks_products")
            img_url = upload_res.get("secure_url", "")
        except Exception:
            pass

    vid_url = ""
    if video and video.filename:
        try:
            vid_res = cloudinary.uploader.upload(video.file, folder="sammyreks_videos", resource_type="video")
            vid_url = vid_res.get("secure_url", "")
        except Exception:
            pass

    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")

    payload = {
        "name": name.strip(),
        "cost": cost,
        "price_carton": price_carton,
        "price_pack": price_pack,
        "price_retail": price_retail,
        "qty_cartons": qty_cartons,
        "packs_per_carton": packs_per_carton,
        "units_per_pack": units_per_pack,
        "category": category,
        "shelf": shelf,
        "description": description,
        "unit_main_label": unit_main_label,
        "unit_pack_label": unit_pack_label,
        "unit_retail_label": unit_retail_label,
        "img_data": img_url,
        "video_url": vid_url,
        "timestamp": get_now()
    }

    try:
        res = supabase.table("products").insert(payload).execute()
        return {"status": "success", "data": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/analytics")
async def get_analytics():
    if not supabase:
        return {"revenue": 0, "profit": 0, "expenses": 0, "net": 0}
    try:
        sales_res = supabase.table("sales").select("total, profit").execute()
        exp_res = supabase.table("expenses").select("amount").execute()
        
        sales = sales_res.data or []
        expenses = exp_res.data or []
        
        rev = sum([float(s.get('total', 0) or 0) for s in sales])
        prof = sum([float(s.get('profit', 0) or 0) for s in sales])
        exp = sum([float(e.get('amount', 0) or 0) for e in expenses])
        
        return {"revenue": rev, "profit": prof, "expenses": exp, "net": prof - exp}
    except Exception:
        return {"revenue": 0, "profit": 0, "expenses": 0, "net": 0}

@app.get("/api/sales")
async def get_sales():
    if not supabase:
        return {"sales": []}
    res = supabase.table("sales").select("*").order("id", desc=True).execute()
    return {"sales": res.data or []}

@app.post("/api/sales")
async def record_sale(sale_data: dict):
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    try:
        res = supabase.table("sales").insert(sale_data).execute()
        return {"status": "success", "data": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/saved_receipts")
async def get_saved_receipts(phone: str = None):
    if not supabase:
        return {"receipts": []}
    query = supabase.table("saved_receipts").select("*")
    if phone:
        query = query.eq("customer_phone", phone)
    res = query.order("id", desc=True).execute()
    return {"receipts": res.data or []}

@app.post("/api/saved_receipts")
async def save_receipt(receipt_data: dict):
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    res = supabase.table("saved_receipts").insert(receipt_data).execute()
    return {"status": "success", "data": res.data}

@app.get("/api/debtors")
async def get_debtors():
    if not supabase:
        return {"debtors": []}
    res = supabase.table("debtors").select("*").execute()
    return {"debtors": res.data or []}

@app.post("/api/debtors")
async def add_debtor(debt_data: dict):
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    res = supabase.table("debtors").insert(debt_data).execute()
    return {"status": "success", "data": res.data}

@app.delete("/api/debtors/{debt_id}")
async def delete_debtor(debt_id: int):
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    res = supabase.table("debtors").delete().eq("id", debt_id).execute()
    return {"status": "success", "data": res.data}

@app.get("/api/expenses")
async def get_expenses():
    if not supabase:
        return {"expenses": []}
    res = supabase.table("expenses").select("*").execute()
    return {"expenses": res.data or []}

@app.post("/api/expenses")
async def add_expense(exp_data: dict):
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    res = supabase.table("expenses").insert(exp_data).execute()
    return {"status": "success", "data": res.data}

@app.get("/api/workers")
async def get_workers():
    if not supabase:
        return {"workers": []}
    res = supabase.table("workers").select("id, name, role").execute()
    return {"workers": res.data or []}

@app.post("/api/workers")
async def add_worker(worker_data: dict):
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    worker_data["password"] = hash_password(worker_data["password"])
    res = supabase.table("workers").insert(worker_data).execute()
    return {"status": "success", "data": res.data}

@app.post("/api/auth/login")
async def login_worker(credentials: dict):
    username = credentials.get("username", "").strip()
    password = credentials.get("password", "").strip()
    if username.lower() == "admin" and password == "Sammyreks2026!":
        return {"status": "success", "user": "admin", "role": "CEO"}
    if not supabase:
        raise HTTPException(status_code=400, detail="Database not configured")
    hashed = hash_password(password)
    res = supabase.table("workers").select("role, name").ilike("name", username).eq("password", hashed).limit(1).execute()
    if res.data and len(res.data) > 0:
        return {"status": "success", "user": res.data[0]["name"], "role": res.data[0]["role"]}
    raise HTTPException(status_code=401, detail="Invalid username or password")

@app.get("/api/messages")
async def get_messages(phone: str = None):
    if not supabase:
        return {"messages": []}
    query = supabase.table("messages").select("*")
    if phone:
        query = query.eq("customer_phone", phone)
    res = query.order("id").execute()
    return {"messages": res.data or []}

@app.post("/api/messages")
async def send_message(request: Request):
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    try:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            body = await request.json()
            sender = body.get("sender", "Customer")
            customer_phone = body.get("customer_phone", "")
            customer_name = body.get("customer_name", "Customer")
            message = body.get("message", "")
            proof_url = body.get("proof_url", "")
        else:
            form = await request.form()
            sender = form.get("sender", "Customer")
            customer_phone = form.get("customer_phone", "")
            customer_name = form.get("customer_name", "Customer")
            message = form.get("message", "")
            
            proof = form.get("proof")
            proof_url = ""
            if proof and hasattr(proof, "file"):
                try:
                    upload_res = cloudinary.uploader.upload(proof.file, folder="sammyreks_payment_proofs")
                    proof_url = upload_res.get("secure_url", "")
                except Exception:
                    pass

        payload = {
            "sender": sender,
            "customer_phone": customer_phone,
            "customer_name": customer_name,
            "message": message,
            "proof_url": proof_url,
            "timestamp": get_now()
        }
        res = supabase.table("messages").insert(payload).execute()
        return {"status": "success", "data": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/orders")
async def get_orders(phone: str = None):
    if not supabase:
        return {"orders": []}
    query = supabase.table("orders").select("*")
    if phone:
        query = query.eq("contact_phone", phone)
    res = query.execute()
    return {"orders": res.data or []}

@app.post("/api/orders")
async def create_order(order_data: dict):
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    res = supabase.table("orders").insert(order_data).execute()
    return {"status": "success", "data": res.data}