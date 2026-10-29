demo brooo
# Link Board with Hot Ranking

**Language:** Python 3.12 (FastAPI) &nbsp;|&nbsp; **Database:** PostgreSQL &nbsp;|&nbsp; **Cache:** Redis

A link aggregator where users submit links, vote, and comment in threaded discussions. The front page uses a **hot-ranking algorithm** — not simple vote sorting — so newer posts with fewer votes can appear above older, more popular ones. Two ranking formulas ship with the app, and they disagree.

---

## Architecture

```
                 ┌─────────────────────┐
                 │   FastAPI (Python)   │
                 │   uvicorn :8080      │
                 └──────┬──────┬───────┘
                        │      │
              ┌─────────┘      └─────────┐
              ▼                          ▼
     ┌────────────────┐        ┌────────────────┐
     │   PostgreSQL   │        │     Redis      │
     │  (persistent)  │        │   (ranking)    │
     └────────────────┘        └────────────────┘
```

**Docker Compose** orchestrates all services:

| Service     | Role                                          |
|-------------|-----------------------------------------------|
| `app`       | FastAPI application on port 8080               |
| `postgres`  | PostgreSQL 16 — persistent storage             |
| `redis`     | Redis 7 — sorted set for the front page        |
| `migration` | Applies SQL migrations before the app starts   |

---

## Endpoints

```
GET  /health                              health check (pg + redis separately)
GET  /front?page=1&size=10&formula=reddit front page (reddit or hn formula)
POST /links                               submit a new link
GET  /links/{id}                          link detail with threaded comments
POST /links/{id}/vote                     upvote or downvote
POST /links/{id}/comments                 add a comment (optionally nested)
GET  /ranking/explain?a=4&b=5             compare two links under both formulas
```

---

## Hot Ranking

### Why not sort by votes?

Sorting by vote count gives a front page that never changes: yesterday's winner keeps accumulating votes while today's post has none. A **hot ranking** trades score against age so fresh content can surface.

### Reddit Formula

```
sign(s) × log₁₀(max(|s|, 1)) + (created_at − epoch) / 45000
```

- `s` = ups − downs
- `epoch` = 1134028003 (2005-12-08)
- **45000 seconds** (12.5 hours) = one order of magnitude of votes
- `age_worth_of(10)` = 45000 — ten times the votes buys 12.5 hours of staying power
- `age_worth_of(2)` ≈ 13548 — doubling votes buys under 4 hours

**Key property:** The formula has **no `now`** in it. A post's score is fixed by its votes and birthday. The relative order of any two posts never changes unless someone votes. The whole page slides down together. This is why it **can live in a Redis sorted set**.

### Hacker News Formula

```
(points − 1) / (age_hours + 2)^1.8
```

- Needs `now` — the score changes with every passing second
- A single upvote scores **exactly zero** forever
- Posts decay at different rates and **overtake each other without any votes**

**Key property:** Because the score depends on `now`, it **cannot use a static Redis sorted set**. The endpoint computes HN scores per-request and says `"from_sorted_set": false`.

### The Brief's Question

> A brand-new post with 1 vote vs. a day-old post with 50 votes?

- **Reddit:** The new post wins (log₁₀(1) = 0, but its time offset is much larger)
- **HN:** The old post wins (49 / 26^1.8 ≈ 0.14 vs. 0 / 2^1.8 = 0)

Neither is wrong. They encode different opinions about what a front page is for.

### Redis Sorted Sets

The Reddit ranking is stored in a Redis sorted set:

```
ZADD front <hot_score> <link_id>
```

Retrieving the front page is one command:

```
ZREVRANGE front 0 9 WITHSCORES
```

A vote is one `ZADD` — the member's score moves and the ordering follows. No cache to invalidate, no page to rebuild.

A short-lived **freshness key** (`front:fresh`, TTL 60s) triggers a full rescore from PostgreSQL when it expires. This means a cold Redis costs one slow request and nothing more.

### Stability

`is_order_stable(posts, t1, t2, formula)` checks whether the page order changes between two moments with no votes cast.

- **Reddit:** Always `True` — order is fixed by votes and creation time
- **HN:** Can be `False` — posts decay at different rates and swap positions over time

---

## Recursive Comments

### Storage

Comments are stored as flat rows with a self-referencing `parent_id`:

```
ID    PARENT_ID    BODY
1     NULL         top-level comment
2     1            reply to 1
3     2            reply to 2 (depth 3)
4     2            another reply to 2
5     4            reply to 4 (depth 4)
```

### PostgreSQL `WITH RECURSIVE`

A single recursive CTE retrieves the entire thread in display order:

```sql
WITH RECURSIVE thread AS (
  SELECT c.id, c.parent_id, c.body, c.author, c.created_at,
         1 AS depth, ARRAY[c.id] AS path
  FROM comments c WHERE c.link_id = %s AND c.parent_id IS NULL
  UNION ALL
  SELECT c.id, c.parent_id, c.body, c.author, c.created_at,
         t.depth + 1, t.path || c.id
  FROM comments c JOIN thread t ON c.parent_id = t.id
  WHERE c.link_id = %s AND c.id <> ALL(t.path)
) SELECT id, parent_id, body, author, created_at, depth
  FROM thread ORDER BY path
```

- `path` is the array of ids from root to current node
- `ORDER BY path` interleaves each reply under its parent
- `c.id <> ALL(t.path)` **prevents cycles** from spinning the recursion forever

### Python `thread_order`

`thread_order` in `app/ranking.py` performs the equivalent traversal using an **explicit stack** (no Python recursion):

1. Builds a children map and finds roots
2. Pushes roots onto the stack
3. Pops, marks as seen, pushes children in reverse order
4. Any node not reached (inside a cycle) is appended as `unreachable`

### SQL vs Python Comparison Test

The integration tests:
1. Run the real recursive SQL query against PostgreSQL
2. Fetch the same rows flat and run `thread_order`
3. Assert that the id ordering is **identical**

This proves the SQL and the Python walk agree.

### Cycle Handling

- **SQL:** The `<> ALL(path)` guard stops the recursion when a node is already in the path
- **Python:** The `seen` set in `thread_order` prevents revisiting nodes
- **Tests:** A two-node cycle (A→B→A) is inserted and both implementations are verified to terminate

### `nest`

`nest` converts flat rows (already in display order from the recursive query) into a nested tree structure in two passes, no recursion. Cycle nodes are surfaced as `unreachable` roots rather than silently lost.

---

## Docker

### Dockerfile

```dockerfile
FROM python:3.12.4-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd --create-home appuser
USER appuser
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

**Design decisions:**

| Feature | Why |
|---|---|
| `python:3.12.4-slim` | Pinned base image — reproducible builds |
| `COPY requirements.txt` first | Dependency layer is cached; code changes don't re-install packages |
| Non-root `appuser` | Principle of least privilege |
| Port 8080 | Assignment requirement |

### Docker Compose

```yaml
services:
  postgres:    # PostgreSQL 16
  redis:       # Redis 7
  migration:   # Runs SQL files in order, then exits
  app:         # FastAPI on :8080
```

**Startup ordering:**

```
postgres (healthcheck: pg_isready)
    ↓
migration (depends_on: postgres healthy)
    ↓  runs all migrations/*.sql in order
app (depends_on: migration completed, redis healthy)
```

**Networking:** Inside Docker Compose, containers reach each other by **service name**:
- `DATABASE_URL` → `postgresql://linkboard:linkboard@postgres:5432/linkboard`
- `REDIS_URL` → `redis://redis:6379/0`

`localhost` inside a container refers to that container itself — **not** to other services.

---

## CircleCI

```
secret scan  →  lint  →  unit tests  →  integration tests  →  docker build
```

| Stage | What it does |
|---|---|
| `secret-scan` | `gitleaks detect` — no hardcoded passwords or tokens |
| `lint` | `ruff check app/ tests/` |
| `unit-tests` | `pytest tests/unit/ --cov=app` (no external services) |
| `integration-tests` | `pytest tests/integration/` with Postgres + Redis **service containers** |
| `docker-build` | `docker build` + optional `docker push` to Docker Hub |

### CI Networking

In CircleCI, service containers are accessed via **`localhost`** (not service names like in Docker Compose). The integration test job sets:

```
DATABASE_URL: postgresql://linkboard:linkboard@localhost:5432/linkboard
REDIS_URL:    redis://localhost:6379/0
```

This is the **opposite** of Docker Compose, where the host is the service name.

---

## Running Locally

### Start all services

```bash
docker compose up --build
```

### Verify health

```bash
curl http://localhost:8080/health
```

Expected:

```json
{"status": "ok", "postgres": true, "redis": true}
```

### Try the API

```bash
# Front page (Reddit ranking)
curl "http://localhost:8080/front?page=1&size=5&formula=reddit"

# Front page (HN ranking)
curl "http://localhost:8080/front?page=1&size=5&formula=hn"

# Submit a link
curl -X POST http://localhost:8080/links \
  -H "Content-Type: application/json" \
  -d '{"title":"My Link","url":"https://example.com/new","submitted_by":"me"}'

# Get link with comments
curl http://localhost:8080/links/2

# Vote
curl -X POST http://localhost:8080/links/1/vote \
  -H "Content-Type: application/json" \
  -d '{"direction":"up"}'

# Add a comment
curl -X POST http://localhost:8080/links/2/comments \
  -H "Content-Type: application/json" \
  -d '{"body":"Great post!","author":"reader"}'

# Compare two links
curl "http://localhost:8080/ranking/explain?a=4&b=5"
```

---

## Running Tests

### Unit tests (no services needed)

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/unit/ --cov=app --cov-report=term-missing
```

### Integration tests (need Postgres + Redis)

```bash
# With Docker Compose services running:
export DATABASE_URL="postgresql://linkboard:linkboard@localhost:5432/linkboard"
export REDIS_URL="redis://localhost:6379/0"
pytest tests/integration/ -v
```

### Full suite

```bash
pytest --cov=app --cov-report=term-missing
```

---

## Docker Hub & Environments

```bash
# QA
docker build -t YOUR_DOCKERHUB_USERNAME/link-board:qa .
docker push YOUR_DOCKERHUB_USERNAME/link-board:qa

# Staging
docker build -t YOUR_DOCKERHUB_USERNAME/link-board:stage .
docker push YOUR_DOCKERHUB_USERNAME/link-board:stage

# Production
docker build -t YOUR_DOCKERHUB_USERNAME/link-board:prod .
docker push YOUR_DOCKERHUB_USERNAME/link-board:prod
```

---

## Kubernetes (Minikube)

Manifests for `qa`, `stage`, and `prod` environments are located in [`k8s/`](file:///c:/codes/hot%20link/k8s/README.md).

### Deploying to an environment:

```bash
# Start Minikube
minikube start

# Deploy to QA (Namespace: qa)
kubectl apply -f k8s/qa/

# Deploy to Staging (Namespace: stage)
kubectl apply -f k8s/stage/

# Deploy to Production (Namespace: prod)
kubectl apply -f k8s/prod/
```

### Accessing the application:

```bash
# QA NodePort (30080)
minikube service link-board -n qa --url

# Stage NodePort (30081)
minikube service link-board -n stage --url

# Prod NodePort (30082)
minikube service link-board -n prod --url
```

---

## Project Structure

```
link-board/
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI application and endpoints
│   ├── ranking.py       # Pure ranking logic and comment-tree functions
│   ├── db.py            # PostgreSQL access (psycopg)
│   ├── cache.py         # Redis access
│   └── static/          # Web frontend
├── k8s/                 # Kubernetes manifests
│   ├── qa/              # QA environment manifests
│   ├── stage/           # Staging environment manifests
│   ├── prod/            # Production environment manifests
│   └── README.md        # Kubernetes guide
├── migrations/
│   ├── 001_schema.sql   # Table definitions
│   └── 002_seed.sql     # Sample data
├── tests/
│   ├── unit/
│   │   └── test_ranking.py    # Pure logic tests (no external services)
│   └── integration/
│       └── test_app.py        # Full-stack tests (real Postgres + Redis)
├── Dockerfile
├── docker-compose.yml
├── .dockerignore
├── .circleci/
│   └── config.yml
├── requirements.txt
├── requirements-dev.txt
├── ruff.toml
├── .gitignore
└── README.md
```
