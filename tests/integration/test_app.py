"""Integration tests — run against real PostgreSQL and Redis.

These tests require DATABASE_URL and REDIS_URL to point at live services.
They are NOT meant to be mocked.

Run locally with docker compose services up, or in CI with service containers.
"""
import os
import time

import psycopg
import pytest
import redis
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app.ranking import thread_order


# ---------------------------------------------------------------------------
# Skip the entire module if the services are not reachable
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://linkboard:linkboard@localhost:5432/linkboard",
)
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def _pg_available():
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=5) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


def _redis_available():
    try:
        r = redis.Redis.from_url(REDIS_URL, socket_connect_timeout=5, socket_timeout=5)
        r.ping()
        return True
    except Exception:
        return False


skip_no_pg = pytest.mark.skipif(not _pg_available(), reason="PostgreSQL not reachable")
skip_no_redis = pytest.mark.skipif(not _redis_available(), reason="Redis not reachable")
skip_no_services = pytest.mark.skipif(
    not _pg_available() or not _redis_available(),
    reason="PostgreSQL and/or Redis not reachable",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _set_env():
    """Ensure environment variables are set for the app modules."""
    os.environ.setdefault("DATABASE_URL", DATABASE_URL)
    os.environ.setdefault("REDIS_URL", REDIS_URL)
    yield


@pytest.fixture(scope="module")
def client():
    """TestClient wrapping the FastAPI application."""
    # Reset the cached Redis client so it picks up the test REDIS_URL
    import app.cache as _cache
    _cache._client = None
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db_conn():
    """Raw psycopg connection for direct SQL assertions."""
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        yield conn


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@skip_no_services
class TestHealth:
    def test_health_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["postgres"] is True
        assert body["redis"] is True


# ---------------------------------------------------------------------------
# Link CRUD
# ---------------------------------------------------------------------------

@skip_no_services
class TestLinks:
    def test_create_link(self, client):
        payload = {
            "title": f"Integration test link {time.time()}",
            "url": f"https://example.com/int-test-{time.time()}",
            "submitted_by": "tester",
        }
        r = client.post("/links", json=payload)
        assert r.status_code == 201
        body = r.json()
        assert body["title"] == payload["title"]
        assert body["url"] == payload["url"]
        assert "id" in body
        assert "hot" in body

    def test_create_duplicate_url(self, client):
        url = f"https://example.com/dup-test-{time.time()}"
        payload = {"title": "Dup A", "url": url}
        r1 = client.post("/links", json=payload)
        assert r1.status_code == 201
        payload2 = {"title": "Dup B", "url": url}
        r2 = client.post("/links", json=payload2)
        assert r2.status_code == 409

    def test_create_bad_url(self, client):
        r = client.post("/links", json={"title": "Bad", "url": "not-a-url"})
        assert r.status_code == 400

    def test_create_missing_title(self, client):
        r = client.post("/links", json={"url": "https://example.com/ok"})
        assert r.status_code == 400

    def test_get_link(self, client):
        # Use seeded link 2 (has comments)
        r = client.get("/links/2")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == 2
        assert "comments" in body
        assert "stats" in body

    def test_get_nonexistent_link(self, client):
        r = client.get("/links/999999")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Voting
# ---------------------------------------------------------------------------

@skip_no_services
class TestVoting:
    def test_upvote(self, client):
        r = client.post("/links/1/vote", json={"direction": "up"})
        assert r.status_code == 200
        body = r.json()
        assert "ups" in body
        assert "hot" in body
        assert "position" in body

    def test_downvote(self, client):
        r = client.post("/links/1/vote", json={"direction": "down"})
        assert r.status_code == 200

    def test_invalid_direction(self, client):
        r = client.post("/links/1/vote", json={"direction": "sideways"})
        assert r.status_code == 400

    def test_vote_nonexistent(self, client):
        r = client.post("/links/999999/vote", json={"direction": "up"})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

@skip_no_services
class TestComments:
    def test_add_comment(self, client):
        r = client.post("/links/1/comments", json={"body": "Integration test comment", "author": "tester"})
        assert r.status_code == 201
        body = r.json()
        assert body["body"] == "Integration test comment"
        assert body["parent_id"] is None

    def test_nested_comment(self, client):
        # Seeded comment 1 exists on link 2
        r = client.post("/links/2/comments", json={"body": "Reply to c1", "parent_id": 1})
        assert r.status_code == 201
        body = r.json()
        assert body["parent_id"] == 1

    def test_empty_body(self, client):
        r = client.post("/links/1/comments", json={"body": ""})
        assert r.status_code == 400

    def test_wrong_link_parent(self, client):
        """Parent comment belongs to link 2 but we post on link 1."""
        r = client.post("/links/1/comments", json={"body": "Mismatched", "parent_id": 1})
        assert r.status_code == 400

    def test_nonexistent_parent(self, client):
        r = client.post("/links/1/comments", json={"body": "Ghost parent", "parent_id": 999999})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Recursive comments — SQL vs Python thread_order
# ---------------------------------------------------------------------------

@skip_no_pg
class TestRecursiveComments:
    def test_sql_matches_python(self, db_conn):
        """The WITH RECURSIVE query and thread_order must produce the same id order."""
        lid = 2  # seeded link with a five-level thread

        # 1. Run the real recursive SQL
        sql_rows = db_conn.execute(
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
            " ORDER BY path",
            (lid, lid),
        ).fetchall()
        sql_ids = [r["id"] for r in sql_rows]

        # 2. Fetch the same rows flat (no recursion) for thread_order
        flat_rows = db_conn.execute(
            "SELECT id, parent_id, body, author, created_at FROM comments WHERE link_id=%s",
            (lid,),
        ).fetchall()
        flat_dicts = [dict(r) for r in flat_rows]

        # 3. Run the Python walk
        py_ordered = thread_order(flat_dicts)
        py_ids = [r["id"] for r in py_ordered if not r.get("unreachable")]

        # 4. Assert identical order
        assert sql_ids == py_ids, f"SQL order {sql_ids} != Python order {py_ids}"

    def test_five_level_depth(self, db_conn):
        """The seeded thread on link 2 reaches depth 5."""
        rows = db_conn.execute(
            "WITH RECURSIVE thread AS ("
            "  SELECT c.id, c.parent_id, 1 AS depth, ARRAY[c.id] AS path"
            "  FROM comments c WHERE c.link_id=2 AND c.parent_id IS NULL"
            "  UNION ALL"
            "  SELECT c.id, c.parent_id, t.depth + 1, t.path || c.id"
            "  FROM comments c JOIN thread t ON c.parent_id = t.id"
            "  WHERE c.link_id=2 AND c.id <> ALL(t.path)"
            ") SELECT max(depth) AS max_depth FROM thread",
            (),
        ).fetchone()
        assert rows["max_depth"] >= 5


# ---------------------------------------------------------------------------
# Cycle handling
# ---------------------------------------------------------------------------

@skip_no_pg
class TestCycleHandling:
    def test_cycle_does_not_hang(self, db_conn):
        """Insert a two-node cycle and verify the recursive query terminates."""
        cur = db_conn.cursor()
        # Temporarily drop the FK constraint to allow a cycle
        cur.execute("ALTER TABLE comments DROP CONSTRAINT IF EXISTS comments_parent_id_fkey")
        # Create a test link
        cur.execute(
            "INSERT INTO links (title, url, submitted_by, ups) VALUES ('cycle-test', %s, 'tester', 1)"
            " RETURNING id",
            (f"https://example.com/cycle-{time.time()}",),
        )
        link_id = cur.fetchone()["id"]
        # Insert two comments that reference each other
        cur.execute(
            "INSERT INTO comments (link_id, body, author) VALUES (%s, 'A', 'x') RETURNING id",
            (link_id,),
        )
        cid_a = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO comments (link_id, body, author) VALUES (%s, 'B', 'x') RETURNING id",
            (link_id,),
        )
        cid_b = cur.fetchone()["id"]
        cur.execute("UPDATE comments SET parent_id=%s WHERE id=%s", (cid_b, cid_a))
        cur.execute("UPDATE comments SET parent_id=%s WHERE id=%s", (cid_a, cid_b))
        db_conn.commit()

        # The recursive query with `<> ALL(path)` guard must terminate
        db_conn.execute(
            "WITH RECURSIVE thread AS ("
            "  SELECT c.id, c.parent_id, 1 AS depth, ARRAY[c.id] AS path"
            "  FROM comments c WHERE c.link_id=%s AND c.parent_id IS NULL"
            "  UNION ALL"
            "  SELECT c.id, c.parent_id, t.depth + 1, t.path || c.id"
            "  FROM comments c JOIN thread t ON c.parent_id = t.id"
            "  WHERE c.link_id=%s AND c.id <> ALL(t.path)"
            ") SELECT id, parent_id, depth FROM thread ORDER BY path",
            (link_id, link_id),
        ).fetchall()
        # The cycle means neither node has parent_id IS NULL, so the recursive
        # seed (parent_id IS NULL) won't reach them. The query terminates with
        # zero or some subset.
        # But thread_order should still handle them:
        flat = db_conn.execute(
            "SELECT id, parent_id, body, author FROM comments WHERE link_id=%s",
            (link_id,),
        ).fetchall()
        py_result = thread_order([dict(r) for r in flat])
        assert {r["id"] for r in py_result} == {cid_a, cid_b}

        # Re-add the FK constraint
        cur.execute(
            "ALTER TABLE comments ADD CONSTRAINT comments_parent_id_fkey "
            "FOREIGN KEY (parent_id) REFERENCES comments(id) ON DELETE CASCADE"
        )
        db_conn.commit()


# ---------------------------------------------------------------------------
# Front page
# ---------------------------------------------------------------------------

@skip_no_services
class TestFrontPage:
    def test_reddit_formula(self, client):
        r = client.get("/front", params={"page": 1, "size": 10, "formula": "reddit"})
        assert r.status_code == 200
        body = r.json()
        assert body["formula"] == "reddit"
        assert body["from_sorted_set"] is True
        assert isinstance(body["links"], list)

    def test_hn_formula(self, client):
        r = client.get("/front", params={"page": 1, "size": 10, "formula": "hn"})
        assert r.status_code == 200
        body = r.json()
        assert body["formula"] == "hn"
        assert body["from_sorted_set"] is False

    def test_unknown_formula(self, client):
        r = client.get("/front", params={"formula": "myspace"})
        assert r.status_code == 400

    def test_bad_page(self, client):
        r = client.get("/front", params={"page": 0})
        assert r.status_code == 400

    def test_reddit_links_have_position(self, client):
        r = client.get("/front", params={"page": 1, "size": 5, "formula": "reddit"})
        body = r.json()
        for link in body["links"]:
            assert "position" in link
            assert "hot" in link


# ---------------------------------------------------------------------------
# Redis sorted set verification
# ---------------------------------------------------------------------------

@skip_no_services
class TestRedisIntegration:
    def test_redis_sorted_set_populated(self, client):
        """After a front page request, the Redis sorted set must contain entries."""
        # Force a request to populate Redis
        client.get("/front", params={"page": 1, "size": 10, "formula": "reddit"})
        r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        count = r.zcard("front")
        assert count > 0

    def test_vote_updates_redis(self, client):
        """A vote should update the member's score in the sorted set."""
        r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        score_before = r.zscore("front", "1")
        client.post("/links/1/vote", json={"direction": "up"})
        score_after = r.zscore("front", "1")
        # Score should change (increase) after an upvote
        if score_before is not None:
            assert score_after != score_before


# ---------------------------------------------------------------------------
# Ranking explain
# ---------------------------------------------------------------------------

@skip_no_services
class TestRankingExplain:
    def test_explain_seeded_data(self, client):
        """Use the seeded links 4 and 5 — the brief's own comparison."""
        r = client.get("/ranking/explain", params={"a": 4, "b": 5})
        assert r.status_code == 200
        body = r.json()
        assert "formulas" in body
        assert "reddit" in body["formulas"]
        assert "hn" in body["formulas"]
        assert "agree" in body
        assert "reddit_order_is_stable" in body
        assert "hn_order_is_stable" in body
        assert body["reddit_order_is_stable"] is True
        assert "ten_times_the_votes_is_worth_seconds" in body
        assert "note" in body

    def test_explain_nonexistent_link(self, client):
        r = client.get("/ranking/explain", params={"a": 1, "b": 999999})
        assert r.status_code == 404
