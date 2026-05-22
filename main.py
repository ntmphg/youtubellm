from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse
from datetime import datetime, timezone, timedelta
import json

app = FastAPI()
templates = Jinja2Templates(directory="templates")

HK_TZ = timezone(timedelta(hours=8))


def _to_hkt(iso_str):
    if not iso_str:
        return None
    try:
        # Handle trailing "Z" (UTC) plus naive timestamps written via utcnow()
        s = iso_str.rstrip("Z")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(HK_TZ)
    except Exception:
        return None


def hkt_full(iso_str):
    if iso_str == "never":
        return "never"
    dt = _to_hkt(iso_str)
    return dt.strftime("%d %b %Y, %H:%M HKT") if dt else iso_str


def hkt_date(iso_str):
    dt = _to_hkt(iso_str)
    return dt.strftime("%d %b %Y") if dt else (iso_str or "")[:10]


templates.env.filters["hkt_full"] = hkt_full
templates.env.filters["hkt_date"] = hkt_date


def load_data():
    try:
        with open("data/videos.json") as f:
            return json.load(f)
    except Exception:
        return {"items": [], "last_updated": "never"}


def load_relationships():
    try:
        with open("data/relationships.json") as f:
            return json.load(f).get("data")
    except Exception:
        return None


@app.get("/")
def index(request: Request):
    data = load_data()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "videos": data.get("items", []),
            "last_updated": data.get("last_updated", "never"),
            "relationships": load_relationships(),
        },
    )


@app.get("/api/data")
def api_data():
    return JSONResponse(load_data())
