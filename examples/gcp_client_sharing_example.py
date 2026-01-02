"""
Example demonstrating GCP MetricServiceClient sharing across multiple metrics collectors.

This shows:
1. Creating a single MetricServiceClient instance
2. Sharing it across multiple GCPCloudMonitoringMetrics collectors
3. Benefits: connection pooling, reduced resource usage

Note: This example requires GCP credentials and won't run without them.
      It's provided as a reference for production use.
"""

# Uncomment to run (requires: pip install "advanced-caching[gcp-monitoring]")
"""
from advanced_caching import TTLCache, SWRCache
from advanced_caching.exporters import GCPCloudMonitoringMetrics
from google.cloud import monitoring_v3

# Create a single shared MetricServiceClient
# This reduces connection overhead and enables connection pooling
shared_client = monitoring_v3.MetricServiceClient()

# Create separate metrics collectors for different services/namespaces
# All share the same underlying client connection
user_service_metrics = GCPCloudMonitoringMetrics(
    project_id="my-gcp-project",
    metric_prefix="custom.googleapis.com/users",
    flush_interval=60.0,
    client=shared_client,  # Share client
)

product_service_metrics = GCPCloudMonitoringMetrics(
    project_id="my-gcp-project",
    metric_prefix="custom.googleapis.com/products",
    flush_interval=60.0,
    client=shared_client,  # Share client
)

order_service_metrics = GCPCloudMonitoringMetrics(
    project_id="my-gcp-project",
    metric_prefix="custom.googleapis.com/orders",
    flush_interval=60.0,
    client=shared_client,  # Share client
)


# User service functions
@TTLCache.cached("user:{id}", ttl=60, metrics=user_service_metrics)
def get_user(id: int):
    return {"id": id, "name": f"User_{id}"}


# Product service functions
@TTLCache.cached("product:{id}", ttl=300, metrics=product_service_metrics)
def get_product(id: int):
    return {"id": id, "name": f"Product_{id}"}


# Order service functions
@SWRCache.cached("order:{id}", ttl=120, stale_ttl=600, metrics=order_service_metrics)
def get_order(id: int):
    return {"id": id, "status": "shipped"}


# Benefits of client sharing:
# 1. Single TCP connection pool shared across all collectors
# 2. Reduced memory footprint (one client vs multiple)
# 3. Better connection reuse and performance
# 4. Easier credential management (configure once)
# 5. All collectors still use shared APScheduler (no extra threads)

print("GCP client sharing configured!")
print("- user_service_metrics → custom.googleapis.com/users/*")
print("- product_service_metrics → custom.googleapis.com/products/*")
print("- order_service_metrics → custom.googleapis.com/orders/*")
print("- All share one MetricServiceClient connection")
print("- All use shared APScheduler for background flushing")
"""

print(__doc__)
print("\nTo use this pattern:")
print("1. Install: pip install 'advanced-caching[gcp-monitoring]'")
print("2. Set up GCP credentials")
print("3. Uncomment the code above")
