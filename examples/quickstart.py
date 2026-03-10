"""
Quick-start examples for advanced-caching.

Covers: TTL, SWR, bg refresh, Redis, ChainCache, invalidation, serializers, metrics.

Run:
    uv run python examples/quickstart.py
"""

from __future__ import annotations

import asyncio
import time

# ── 1. TTL Cache ─────────────────────────────────────────────────────────────
from advanced_caching import cache, InMemCache


@cache(60, key="user:{user_id}")
async def get_user(user_id: int) -> dict:
    print(f"  [db] fetching user {user_id}")
    await asyncio.sleep(0.01)
    return {"id": user_id, "name": f"User{user_id}"}


# ── 2. Stale-While-Revalidate ────────────────────────────────────────────────
@cache(0.05, stale=10, key="price:{symbol}")
async def get_price(symbol: str) -> float:
    print(f"  [api] fetching {symbol}")
    return 100.0 + len(symbol)


# ── 3. Background refresh ────────────────────────────────────────────────────
from advanced_caching import bg


@bg(0.1, key="flags")
def load_flags() -> dict:
    print("  [bg] refreshing flags")
    return {"dark_mode": True, "v": time.time()}


# ── 4. Custom store (InMemCache used here; swap for RedisCache in production) ─
custom_store = InMemCache()


@cache(120, key="catalog:{page}", store=custom_store)
async def get_catalog(page: int) -> list:
    return [{"id": i} for i in range(page * 10, page * 10 + 10)]


# ── 5. Metrics ───────────────────────────────────────────────────────────────
from advanced_caching import InMemoryMetrics

metrics = InMemoryMetrics()


@cache(60, key="product:{}", metrics=metrics)
async def get_product(product_id: int) -> dict:
    await asyncio.sleep(0.01)
    return {"id": product_id, "price": 9.99}


# ── 6. Invalidation ──────────────────────────────────────────────────────────
@cache(60, key="session:{sid}")
def get_session(sid: str) -> dict:
    return {"sid": sid, "valid": True}


# ── 7. Callable Keys ──────────────────────────────────────────────────────────
# A callable receives the same *args/**kwargs as the decorated function.
# Use when you need conditional namespacing, hashing, or complex composition.
import hashlib


# 7a. Simple lambda — mirrors a named-placeholder template but with full Python
@cache(60, key=lambda user_id: f"user:v2:{user_id}")
async def get_user_v2(user_id: int) -> dict:
    return {"id": user_id, "source": "callable-key"}


# 7b. Multi-argument — combine tenant + resource for namespace isolation
@cache(60, key=lambda tenant, resource_id: f"{tenant}:resource:{resource_id}")
async def get_resource(tenant: str, resource_id: int) -> dict:
    return {"tenant": tenant, "id": resource_id}


# 7c. Conditional key — different prefix for admin vs public access
@cache(
    60,
    key=lambda resource_id, admin=False: (
        ("admin" if admin else "public") + f":res:{resource_id}"
    ),
)
async def get_protected(resource_id: int, admin: bool = False) -> dict:
    return {"id": resource_id, "admin": admin}


# 7d. Hash long / complex inputs (e.g. raw SQL query strings)
def _query_key(query: str) -> str:
    digest = hashlib.sha256(query.encode()).hexdigest()[:16]
    return f"query:{digest}"


@cache(30, key=_query_key)
async def run_query(query: str) -> list:
    print(f"  [db] running query: {query[:40]}…")
    return [{"row": 1}]


# 7e. Variadic args — pick lang from positional or keyword
@cache(300, key=lambda *a, **k: f"i18n:{k.get('lang', a[0] if a else 'en')}")
async def get_translations(lang: str = "en") -> dict:
    return {"lang": lang, "hello": "hello"}


# ── Runner ───────────────────────────────────────────────────────────────────
async def main():
    print("\n=== 1. TTL Cache ===")
    u = await get_user(1)
    print(f"  miss → {u}")
    u = await get_user(1)
    print(f"  hit  → {u}")
    u = await get_user(2)
    print(f"  miss → {u}")

    print("\n=== 2. Stale-While-Revalidate ===")
    p = await get_price("BTC")
    print(f"  miss  → {p}")
    await asyncio.sleep(0.06)  # go stale
    p = await get_price("BTC")
    print(f"  stale → {p} (background refresh triggered)")
    await asyncio.sleep(0.05)
    p = await get_price("BTC")
    print(f"  fresh → {p}")

    print("\n=== 3. Background refresh ===")
    f = load_flags()
    print(f"  first → {f}")
    await asyncio.sleep(0.25)
    f = load_flags()
    print(f"  after bg refresh → {f}")
    bg.shutdown()

    print("\n=== 4. Custom store ===")
    c = await get_catalog(0)
    print(f"  page 0: {c[:2]}…")
    c = await get_catalog(0)
    print(f"  page 0 (cached): {c[:2]}…")

    print("\n=== 5. Metrics ===")
    for i in range(3):
        await get_product(i)
    for i in range(3):
        await get_product(i)  # all hits
    stats = metrics.get_stats()
    for name, s in stats.get("caches", {}).items():
        print(
            f"  {name}: {s['hits']} hits, {s['misses']} misses, {s['hit_rate_percent']:.0f}% hit rate"
        )

    print("\n=== 6. Invalidation ===")
    get_session("abc")
    get_session("abc")  # hit
    get_session.invalidate("abc")
    s = get_session("abc")  # miss again
    print(f"  after invalidate → {s}")
    get_session.clear()
    print("  store cleared")

    print("\n=== 7. Callable Keys ===")
    # 7a. simple lambda
    u = await get_user_v2(1)
    u_hit = await get_user_v2(1)
    print(f"  lambda key miss  → {u}")
    print(f"  lambda key hit   → {u_hit}")

    # 7b. multi-arg tenant isolation
    r_a = await get_resource("acme", 42)
    r_b = await get_resource("beta", 42)  # different key → independent cache
    print(f"  tenant acme → {r_a}")
    print(f"  tenant beta → {r_b}  (separate cache entry)")

    # 7c. conditional prefix
    pub = await get_protected(7)
    adm = await get_protected(7, admin=True)  # different key → independent cache
    print(f"  public  → {pub}")
    print(f"  admin   → {adm}  (separate cache entry)")

    # 7d. hash key
    q = "SELECT * FROM orders WHERE status = 'pending' AND created_at > '2024-01-01'"
    rows = await run_query(q)
    rows_hit = await run_query(q)
    print(f"  hash key miss → {rows}")
    print(f"  hash key hit  → {rows_hit}")

    # 7e. variadic (positional or keyword)
    t1 = await get_translations("fr")
    t2 = await get_translations(lang="de")
    t3 = await get_translations()  # default "en"
    print(f"  lang=fr → {t1}")
    print(f"  lang=de → {t2}")
    print(f"  default → {t3}")


if __name__ == "__main__":
    asyncio.run(main())
