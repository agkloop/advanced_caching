# Object Storage Caching (S3 & GCS)

`advanced-caching` supports using cloud object storage (AWS S3 and Google Cloud Storage) as cache backends. This is ideal for:
- **Large datasets**: Storing large serialized objects that don't fit in Redis.
- **Cost efficiency**: Cheaper storage costs compared to managed Redis clusters.
- **Shared caching**: Sharing cache across different services or regions (with appropriate latency considerations).

## Installation

You need to install the respective client libraries:

```bash
# For AWS S3
pip install boto3

# For Google Cloud Storage
pip install google-cloud-storage
```

## S3Cache (AWS)

`S3Cache` uses AWS S3 buckets for storage. It is optimized to minimize API costs by checking object metadata (HEAD request) before downloading the full body.

### Basic Usage

```python
import boto3
from advanced_caching import S3Cache, TTLCache

# Initialize Boto3 client (or let S3Cache create one)
s3_client = boto3.client("s3")

# Create the cache backend
s3_cache = S3Cache(
    bucket="my-app-cache-bucket",
    prefix="prod/users/",
    s3_client=s3_client,
    serializer="json"  # or "pickle" (default)
)

# Use it with a decorator
@TTLCache.cached("user:{}", ttl=3600, cache=s3_cache)
def get_user_report(user_id):
    # ... expensive operation ...
    return generate_pdf_report(user_id)
```

### Configuration Options

| Parameter | Description | Default |
|-----------|-------------|---------|
| `bucket` | Name of the S3 bucket. | Required |
| `prefix` | Folder prefix for keys (e.g., `cache/`). | `""` |
| `s3_client` | Pre-configured `boto3.client("s3")`. | `None` (creates new) |
| `serializer` | Serialization format (`"pickle"`, `"json"`, or custom). | `"pickle"` |
| `compress` | Enable Gzip compression for values. | `True` |
| `compress_level` | Gzip compression level (1-9). | `6` |

## GCSCache (Google Cloud)

`GCSCache` uses Google Cloud Storage buckets. Like `S3Cache`, it leverages metadata to check for freshness efficiently.

### Basic Usage

```python
from google.cloud import storage
from advanced_caching import GCSCache, TTLCache

# Initialize GCS client
client = storage.Client()

# Create the cache backend
gcs_cache = GCSCache(
    bucket="my-app-cache-bucket",
    prefix="reports/",
    client=client,
    compress=True
)

@TTLCache.cached("report:{}", ttl=86400, cache=gcs_cache)
def generate_daily_report(date_str):
    return complex_calculation(date_str)
```

## Key Organization & File Structure

When using object storage, cache keys are mapped directly to file paths (object keys) in the bucket. The final path is constructed as: `prefix + key`.

### Single Function

```python
# Prefix acts as a folder
cache = S3Cache(bucket="my-bucket", prefix="reports/daily/")

@TTLCache.cached("2023-10-25", ttl=3600, cache=cache)
def get_report(date): ...
```

**Resulting S3 Key:** `reports/daily/2023-10-25`

### Multiple Functions (Shared Bucket)

To store data from multiple functions in the same bucket, use different **prefixes** or distinct **key templates** to avoid collisions.

#### Option A: Different Prefixes (Recommended)

Create separate cache instances for different logical groups. This keeps the bucket organized and allows for easier cleanup (e.g., deleting the `users/` folder).

```python
# Cache for User data
user_cache = S3Cache(bucket="my-bucket", prefix="users/")

# Cache for Product data
product_cache = S3Cache(bucket="my-bucket", prefix="products/")

@TTLCache.cached("{user_id}", ttl=300, cache=user_cache)
def get_user(user_id): ...
# File: users/123

@TTLCache.cached("{prod_id}", ttl=300, cache=product_cache)
def get_product(prod_id): ...
# File: products/ABC
```

#### Option B: Shared Prefix with Namespaced Keys

Use a single cache instance but namespace the keys in the decorator.

```python
# Shared cache instance
shared_cache = S3Cache(bucket="my-bucket", prefix="cache/")

@TTLCache.cached("users:{user_id}", ttl=300, cache=shared_cache)
def get_user(user_id): ...
# File: cache/users:123

@TTLCache.cached("products:{prod_id}", ttl=300, cache=shared_cache)
def get_product(prod_id): ...
# File: cache/products:ABC
```

> **Tip**: You can use slashes in your key templates to create subfolders dynamically.
> Example: `@TTLCache.cached("users/{user_id}/profile", ...)` with prefix `v1/` results in `v1/users/123/profile`.

### Single-writer / multi-reader with BGCache

If you only want one place to refresh data but many places to read it, split BGCache into a writer and readers:

```python
from advanced_caching import BGCache, InMemCache

# One writer (enforced: only one writer per key)
@BGCache.register_writer(
    "daily_config", interval_seconds=300, run_immediately=True, cache=InMemCache()
)
def refresh_config():
    return load_big_config()  # expensive

# Many readers; call-time readers without dummy decorators
get_config = BGCache.get_reader("daily_config", cache=InMemCache())

# On a miss the reader returns None (no fallback logic is attached).

# You can also source from a multi-level cache (e.g., ChainCache) if you want object storage behind Redis/L1.
```

This pattern keeps writes centralized while allowing multiple call-sites to share the cached value.

## Multi-level chain (InMem -> Redis -> S3/GCS)

Use `ChainCache` to compose multiple storage layers:

```python
from advanced_caching import InMemCache, RedisCache, S3Cache, ChainCache

chain = ChainCache([
    (InMemCache(), 60),
    (RedisCache(redis_client), 300),
    (S3Cache(bucket="my-cache"), 3600),
])

# Write-through all levels (TTL capped per level)
chain.set("daily_config", load_config(), ttl=7200)

# Read-through promotes to faster levels
cfg = chain.get("daily_config")
```

### Dedupe writes (optional)

- `S3Cache(..., dedupe_writes=True)` stores a hash in object metadata (`ac-hash`) and skips uploads when content is unchanged (adds a HEAD check).
- `GCSCache(..., dedupe_writes=True)` stores `ac-hash` metadata and skips uploads when unchanged.
- `RedisCache(..., dedupe_writes=True)` skips rewriting identical payloads and refreshes TTL when provided.

Use dedupe when bandwidth/object-write cost matters and an extra HEAD/reload is acceptable.

## Best Practices

### 1. Use HybridCache for Performance & Cost

Object storage has higher latency (50-200ms) compared to Redis (<5ms) or memory (nanoseconds). It also charges per API request.

To mitigate this, wrap your object storage cache in a `HybridCache`. This uses local memory as L1 and S3/GCS as L2.

```python
from advanced_caching import HybridCache, InMemCache, S3Cache

# L1: Memory (fast, free reads)
# L2: S3 (persistent, shared, slower)
hybrid_cache = HybridCache(
    l1_cache=InMemCache(),
    l2_cache=S3Cache(bucket="my-cache"),
    l1_ttl=60,      # Keep in memory for 1 minute
    l2_ttl=86400    # Keep in S3 for 1 day
)

# 1. First call: Miss L1 -> Miss L2 -> Run Function -> Write S3 -> Write L1
# 2. Second call (0-60s): Hit L1 (Instant, no S3 cost)
# 3. Third call (61s+): Miss L1 -> Hit L2 (Slower, S3 read cost) -> Write L1
```

### 2. Enable Compression

Both `S3Cache` and `GCSCache` enable Gzip compression by default (`compress=True`).
- **Pros**: Reduces storage costs and network transfer time.
- **Cons**: Slight CPU overhead for compression/decompression.
- **Recommendation**: Keep it enabled unless you are storing already-compressed data (like images or zip files).

### 3. Cost Optimization (Metadata Checks)

`advanced-caching` implements a "Metadata First" strategy:
- **`get()`**: Checks object metadata (freshness timestamp) *before* downloading the body. If the item is expired, it aborts the download, saving data transfer costs.
- **`exists()`**: Uses `HEAD` requests (S3) or metadata lookups (GCS) which are cheaper and faster than downloading the object.

### 4. Serialization

- **Pickle (Default)**: Fastest and supports almost any Python object. **Security Warning**: Only use pickle if you trust the data source (i.e., your own bucket).
- **JSON**: Portable and human-readable. Use this if other non-Python services need to read the cache. Requires `orjson` (installed automatically with `advanced-caching`).

### 5. Permissions

Ensure your application has the correct IAM permissions.

**AWS S3 (IAM Policy):**
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "s3:GetObject",
                "s3:PutObject",
                "s3:DeleteObject",
                "s3:ListBucket"
            ],
            "Resource": [
                "arn:aws:s3:::my-cache-bucket",
                "arn:aws:s3:::my-cache-bucket/*"
            ]
        }
    ]
}
```

**Google Cloud Storage:**
Ensure the Service Account has `Storage Object Admin` or `Storage Object User` roles on the bucket.

## FAQ

### Why not store all keys for a function in a single file?

You might wonder if it's better to store all cached results for `get_user` in a single `users.json` file instead of thousands of small files.

**This is generally NOT recommended for dynamic caching.**

1.  **Race Conditions**: Object storage does not support partial updates. To update one user, you must download the whole file, update the dict, and re-upload. If two requests happen simultaneously, one will overwrite the other's changes.
2.  **Performance**: Reading a single key requires downloading the entire dataset.
3.  **Cost**: Re-uploading a 10MB file to update a 1KB record incurs unnecessary bandwidth and request costs.

**Exception: Read-Only Static Data**
If you have a dataset that is generated once (e.g., a daily export) and only read by your app, storing it as a single file is efficient. In this case, use `BGCache` to load the entire file into memory at once, rather than using `S3Cache` as a backend.

```python
# Efficient for single-file read-only datasets
@BGCache.register_loader("daily_config", interval_seconds=3600)
def load_config():
    # Download big JSON once, keep in memory
    obj = s3.get_object(Bucket="...", Key="config.json")
    return json.loads(obj["Body"].read())
```
