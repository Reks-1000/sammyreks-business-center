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

# Safe environment loading for local development vs cloud production
if os.path.exists(".env"):
    load_dotenv()

# Initialize FastAPI app
app = FastAPI(title="Sammyreks Business Center")

# Ensure required directories exist locally
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)

# Mount static files and templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Supabase Credentials Configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Cloudinary Configuration
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET")
)

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """Render the main index HTML frontend interface."""
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/products")
async def get_products():
    """Fetch all business products/inventory from Supabase."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client not initialized")
    try:
        response = supabase.table("products").select("*").execute()
        return {"products": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/sales")
async def process_sale(sale_data: dict):
    """Process a point-of-sale transaction and record it in Supabase."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client not initialized")
    try:
        response = supabase.table("sales").insert(sale_data).execute()
        return {"status": "success", "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/sales")
async def get_sales():
    """Retrieve all recorded sales transactions."""
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase client not initialized")
    try:
        response = supabase.table("sales").select("*").execute()
        return {"sales": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))