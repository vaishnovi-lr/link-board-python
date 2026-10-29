from datetime import datetime, timezone
from urllib.parse import urlparse

import os
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import cache, db
from .ranking import (
    DEFAULT_FORMULA,
    FORMULAS,
    RankingError,
    age_worth_of,
    explain,
    hot,
    is_order_stable,
    nest,
    rank,
    tree_stats,
)

app = FastAPI(title="link-board")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def serve_frontend():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    raise HTTPException(404, "Frontend index.html not found")


FRONT_KEY = "front"
FRESH_KEY = "front:fresh"
RESCORE_EVERY = 60   # seconds


@app.get("/health")
def health():
    out = {"status": "ok", "postgres": False, "redis": False}
    try:
        db.query("SELECT 1")
        out["postgres"] = True
    except Exception as e:
        out["pg_error"] = str(e)
    try:
        cache.client().ping()
        out["redis"] = True
    except Exception as e:
        out["redis_error"] = str(e)
    return out if out["postgres"] and out["redis"] else JSONResponse(out, status_code=503)


def _now():
    return datetime.now(timezone.utc).timestamp()


def _all_links():
    rows = db.query("SELECT id, title, url, submitted_by, ups, downs, created_at,"
                    " extract(epoch FROM created_at) AS created_epoch FROM links")
    return [{**r, "created_epoch": float(r["created_epoch"])} for r in rows]


def _rescore_if_stale():
    """Rebuild the sorted set from Postgres when the freshness key has gone.

    Also what makes a cold Redis harmless: wipe it and the next request
    rebuilds the page. Because Reddit's score has no `now` in it, this is a
    rescore for VOTES, not for time - the order would hold either way.
    """
    c = cache.client()
    if c.exists(FRESH_KEY) and c.zcard(FRONT_KEY):
        return False
    links = _all_links()
    if links:
        c.zadd(FRONT_KEY, {str(link["id"]): hot(link["ups"], link["downs"], link["created_epoch"])
                           for link in links})
    c.set(FRESH_KEY, str(int(_now())), ex=RESCORE_EVERY)
    return True


def _link(lid):
    row = db.one("SELECT id, title, url, submitted_by, ups, downs, created_at,"
                 " extract(epoch FROM created_at) AS created_epoch"
                 " FROM links WHERE id=%s", (lid,))
    if not row:
        raise HTTPException(404, "no such link")
    return {**row, "created_epoch": float(row["created_epoch"])}


@app.get("/front")
def front(page: int = 1, size: int = 10, formula: str = DEFAULT_FORMULA):
    if formula not in FORMULAS:
        raise HTTPException(400, f"unknown formula {formula!r} - have {sorted(FORMULAS)}")
    if page < 1 or size < 1 or size > 100:
        raise HTTPException(400, "page starts at 1 and size is 1..100")
    rebuilt = _rescore_if_stale()
    now = _now()

    if formula != "reddit":
        # HN's score moves with the clock, so it cannot live in the sorted
        # set - it has to be computed per request. That cost is exactly the
        # argument for the other formula.
        ordered = rank(_all_links(), now, formula)
        window = ordered[(page - 1) * size: page * size]
        return {"page": page, "formula": formula, "from_sorted_set": False,
                "rescored": rebuilt,
                "links": [{**_present(link, now), "hot": link["hot"],
                           "position": link["position"]} for link in window]}

    c = cache.client()
    start = (page - 1) * size
    ids = c.zrevrange(FRONT_KEY, start, start + size - 1, withscores=True)
    if not ids:
        return {"page": page, "formula": formula, "from_sorted_set": True,
                "rescored": rebuilt, "links": []}
    rows = db.query("SELECT id, title, url, submitted_by, ups, downs, created_at,"
                    " extract(epoch FROM created_at) AS created_epoch"
                    " FROM links WHERE id = ANY(%s)", ([int(i) for i, _ in ids],))
    by_id = {r["id"]: r for r in rows}
    out = []
    for pos, (lid, hotscore) in enumerate(ids, start=start + 1):
        r = by_id.get(int(lid))
        if r:
            out.append({**_present({**r, "created_epoch": float(r["created_epoch"])}, now),
                        "hot": round(hotscore, 7), "position": pos})
    return {"page": page, "formula": formula, "from_sorted_set": True,
            "rescored": rebuilt, "links": out}


def _present(link, now):
    age = max(0.0, now - link["created_epoch"])
    return {"id": link["id"], "title": link["title"], "url": link["url"],
            "submitted_by": link["submitted_by"],
            "ups": link["ups"], "downs": link["downs"], "score": link["ups"] - link["downs"],
            "age_hours": round(age / 3600.0, 2),
            "hot": link.get("hot", hot(link["ups"], link["downs"], link["created_epoch"])),
            "comments": db.one("SELECT count(*) AS n FROM comments WHERE link_id=%s",
                               (link["id"],))["n"]}


@app.post("/links", status_code=201)
def submit(payload: dict = Body(...)):
    title = str(payload.get("title", "")).strip()
    url = str(payload.get("url", "")).strip()
    who = str(payload.get("submitted_by", "")).strip() or "anonymous"
    if not title:
        raise HTTPException(400, "a link needs a title")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(400, "url must be a http(s) address")
    if db.one("SELECT id FROM links WHERE url=%s", (url,)):
        raise HTTPException(409, "that url is already on the board")
    row = db.one("INSERT INTO links (title, url, submitted_by, ups) VALUES (%s,%s,%s,1)"
                 " RETURNING id, title, url, submitted_by, ups, downs, created_at,"
                 " extract(epoch FROM created_at) AS created_epoch", (title, url, who))
    h = hot(row["ups"], row["downs"], float(row["created_epoch"]))
    cache.client().zadd(FRONT_KEY, {str(row["id"]): h})
    return {**{k: v for k, v in row.items() if k != "created_epoch"}, "hot": h}


@app.post("/links/{lid}/vote")
def vote(lid: int, payload: dict = Body(...)):
    direction = str(payload.get("direction", "up")).lower()
    if direction not in ("up", "down"):
        raise HTTPException(400, "direction must be 'up' or 'down'")
    _link(lid)
    col = "ups" if direction == "up" else "downs"
    row = db.one(f"UPDATE links SET {col} = {col} + 1 WHERE id=%s"
                 " RETURNING id, ups, downs, extract(epoch FROM created_at) AS created_epoch",
                 (lid,))
    h = hot(row["ups"], row["downs"], float(row["created_epoch"]))
    c = cache.client()
    # One ZADD is the entire write. No cache to invalidate, no page to
    # rebuild - the member's score moves and the ordering follows.
    c.zadd(FRONT_KEY, {str(lid): h})
    return {"id": lid, "ups": row["ups"], "downs": row["downs"],
            "score": row["ups"] - row["downs"], "hot": h,
            "position": (c.zrevrank(FRONT_KEY, str(lid)) or 0) + 1}


@app.get("/links/{lid}")
def show(lid: int, max_depth: int = 0):
    link = _link(lid)
    # ONE query. The database walks the tree and hands back the rows already
    # in display order; `path` is the array of ids from the root down, and
    # ordering by it is what interleaves each reply under its parent. The
    # `<> ALL(path)` guard is what stops a cycle in the data spinning forever.
    rows = db.query(
        "WITH RECURSIVE thread AS ("
        "  SELECT c.id, c.parent_id, c.body, c.author, c.created_at,"
        "         1 AS depth, ARRAY[c.id] AS path"
        "  FROM comments c WHERE c.link_id=%s AND c.parent_id IS NULL"
        "  UNION ALL"
        "  SELECT c.id, c.parent_id, c.body, c.author, c.created_at,"
        "         t.depth + 1, t.path || c.id"
        "  FROM comments c JOIN thread t ON c.parent_id = t.id"
        "  WHERE c.link_id=%s AND c.id <> ALL(t.path)"
        ") SELECT id, parent_id, body, author, created_at, depth FROM thread"
        " ORDER BY path", (lid, lid))
    flat = [dict(r) for r in rows]
    tree = nest(flat)
    body = {**{k: v for k, v in link.items() if k != "created_epoch"},
            "score": link["ups"] - link["downs"],
            "hot": hot(link["ups"], link["downs"], link["created_epoch"]),
            "stats": tree_stats(flat),
            "comments": tree}
    if max_depth > 0:
        body["flat"] = [c for c in flat if c["depth"] <= max_depth]
    return body


@app.post("/links/{lid}/comments", status_code=201)
def add_comment(lid: int, payload: dict = Body(...)):
    _link(lid)
    text = str(payload.get("body", "")).strip()
    if not text:
        raise HTTPException(400, "a comment needs some words in it")
    author = str(payload.get("author", "")).strip() or "anonymous"
    parent_id = payload.get("parent_id")
    if parent_id is not None:
        parent = db.one("SELECT id, link_id FROM comments WHERE id=%s", (int(parent_id),))
        if not parent:
            raise HTTPException(404, "no such parent comment")
        if parent["link_id"] != lid:
            raise HTTPException(400, "that parent comment belongs to a different link")
    row = db.one("INSERT INTO comments (link_id, parent_id, body, author)"
                 " VALUES (%s,%s,%s,%s)"
                 " RETURNING id, link_id, parent_id, body, author, created_at",
                 (lid, parent_id, text[:2000], author))
    return row


@app.get("/ranking/explain")
def ranking_explain(a: int, b: int):
    """Put two links side by side under both formulas. This is the endpoint
    that answers the brief's question with real rows."""
    la, lb = _link(a), _link(b)
    now = _now()
    try:
        out = explain(la, lb, now)
    except RankingError as e:
        raise HTTPException(400, str(e))
    def label(link):
        return (f"{link['title']!r}: {link['ups'] - link['downs']} votes, "
                f"{round((now - link['created_epoch']) / 3600.0, 1)}h old")
    return {"a": label(la), "b": label(lb),
            **out,
            "ten_times_the_votes_is_worth_seconds": age_worth_of(10),
            "doubling_the_votes_is_worth_seconds": age_worth_of(2),
            "reddit_order_is_stable": is_order_stable([la, lb], now, now + 86400, "reddit"),
            "hn_order_is_stable": is_order_stable([la, lb], now, now + 86400, "hn"),
            "note": ("both formulas put the same link first" if out["agree"] else
                     "the two formulas disagree about these two links - this is the "
                     "trade-off the front page is built on")}
