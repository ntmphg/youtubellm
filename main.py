from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse
import json, threading, time

app = FastAPI()
templates = Jinja2Templates(directory="templates")

def load_data():
    try:
        with open("data/videos.json") as f:
            return json.load(f)
    except:
        return {"items": [], "last_updated": "never"}


def load_relationships():
    try:
        with open("data/relationships.json") as f:
            return json.load(f).get("data")
    except:
        return None

def background_watcher():
    from runner import run_watcher
    while True:
        try:
            run_watcher()
        except Exception as e:
            print(f"Watcher error: {e}")
        time.sleep(60 * 60 * 6)  # every 6 hours

@app.on_event("startup")
def start_watcher():
    t = threading.Thread(target=background_watcher, daemon=True)
    t.start()

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
