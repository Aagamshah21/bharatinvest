import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import engine, Base, SessionLocal, run_db_migrations
from app.seed_data import seed_database
from app.middleware import ExternalApiMiddleware, SessionSlidingMiddleware
from app.services.api_serializer import format_indian_currency, fmt_iso_ist
from app.services.price_simulator import update_prices_tick

from app.routers import web_auth, web_pages, web_actions, oauth, api_v2, admin

# Background task for price updating every 5 seconds
async def price_simulator_task():
    while True:
        await asyncio.sleep(5)
        try:
            db = SessionLocal()
            update_prices_tick(db)
            db.close()
        except Exception as e:
            print(f"Price simulator error: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Run safe migrations & Seed Database
    run_db_migrations()
    seed_database()
    # Start background price simulator task
    sim_task = asyncio.create_task(price_simulator_task())
    yield
    # Shutdown
    sim_task.cancel()

app = FastAPI(
    title="BharatInvest Mock Broker API",
    description="Simulated Stock Broker Web Application & External Partner API for BharatInvest",
    version="2.0.0",
    lifespan=lifespan
)

# CORS
cors_origins = [o.strip() for o in settings.CORS_ORIGINS.split(",")] if settings.CORS_ORIGINS != "*" else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Starlette SessionMiddleware for authlib state/nonce management
app.add_middleware(SessionMiddleware, secret_key=settings.SESSION_SECRET)

# Custom External API Middleware for /v2/ data endpoints
app.add_middleware(ExternalApiMiddleware)

# Templates & Static files setup
templates = Jinja2Templates(directory="app/templates")

# Custom Jinja Filters
def filter_indian_currency(val, include_symbol=True):
    return format_indian_currency(val, include_symbol)

templates.env.filters["currency"] = filter_indian_currency
app.state.templates = templates

app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Include Routers
app.include_router(web_auth.router)
app.include_router(web_pages.router)
app.include_router(web_actions.router)
app.include_router(oauth.router)
app.include_router(api_v2.router)
app.include_router(admin.router)

@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "broker": settings.BROKER_NAME,
        "timestamp": fmt_iso_ist(),
        "version": "2.0.0"
    }
