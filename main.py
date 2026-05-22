from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse
import json

app = FastAPI()
templates = Jinja2Templates(directory="templates")


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
