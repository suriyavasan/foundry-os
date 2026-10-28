"""M16 market radar: sources, keywords, items, briefs, ideas and a polite control-plane fetcher.

The fetcher is deterministic code: no model, no agent. It reads public feeds and documented JSON
APIs through the SSRF guard (``app.core.netguard``), obeys robots.txt, spaces requests per host and
stores plain text. Agents read the result through tools (WP-2); they never see a raw response.

This module holds only constants so that importing ``app.radar`` is free (the migration imports
``app.radar.seed``).
"""

from __future__ import annotations

import os

OFFLINE_ENV = "COMPANY_OS_RADAR_OFFLINE"


def offline() -> bool:
    """``COMPANY_OS_RADAR_OFFLINE=1``: the radar makes no request at all (no job is enqueued, a job
    that runs ends at once, the fetcher's default transport refuses). For air-gapped installs and
    for the test suite, whose spawned worker processes must never reach a real site."""
    return os.environ.get(OFFLINE_ENV, "").strip().lower() in ("1", "true", "yes", "on")


JOB_KIND = "radar_fetch"
# Rate-limit key (``rate_limit_hits.key``): control-plane HTTP requests per company per day.
RADAR_FETCHES_KEY = "radar_fetches_per_day"
RADAR_SOURCE_TESTS_KEY = "radar_source_tests_per_hour"  # owner "test fetch": 10 an hour
RADAR_FETCH_NOW_KEY = "radar_fetch_now_per_hour"  # owner "fetch now": 6 an hour

SEED_VERSION = 1

# Scheduling.
ENQUEUE_INTERVAL_SECONDS = 300  # the scheduler enqueues at most one fetch job per company per slot
DUE_SOURCES_PER_JOB = 25
DEFAULT_INTERVAL_SECONDS = 21_600  # 6 h
DAILY_INTERVAL_SECONDS = 86_400  # research, review and UI inspiration
MIN_INTERVAL_SECONDS = 3_600
MAX_INTERVAL_SECONDS = 30 * 86_400
MAX_BACKOFF_SECONDS = 86_400
DISABLE_AFTER_FAILURES = 10

# Caps.
MAX_QUERIES_PER_SOURCE = 6  # one fetch per enabled keyword, at most this many
MAX_ENABLED_KEYWORDS = 40
MAX_AGENT_KEYWORDS = 15
MAX_AGENT_SOURCES = 20
MAX_ITEMS_PER_SOURCE_FETCH = 50
TITLE_DEDUPE_DAYS = 7
ITEM_RETENTION_DAYS = 90
TEST_FETCH_ITEMS = 5
TEST_FETCHES_PER_HOUR = 10
FETCH_NOW_PER_HOUR = 6

# HTTP.
MAX_REDIRECTS = 3
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 15.0
REQUEST_DEADLINE = 20.0
MAX_FEED_BYTES = 2 * 1024 * 1024
MAX_ROBOTS_BYTES = 64 * 1024
MAX_PAGE_BYTES = 1024 * 1024
ROBOTS_TTL_SECONDS = 24 * 3600
ROBOTS_FAILURE_TTL_SECONDS = 24 * 3600
MAX_CRAWL_DELAY_SECONDS = 60
DEFAULT_HOST_SPACING_SECONDS = 2.0
MAX_SLEEP_SECONDS = 5.0  # longer waits defer the source to the next job instead of blocking
MAX_RETRY_AFTER_SECONDS = 86_400
USER_AGENT_TOKEN = "FoundryOS-Radar"  # the product token robots.txt rules are matched against
USER_AGENT_HOME = "https://github.com/suriyavasan/foundry-os"

# Text caps (see ``normalize``).
MAX_TITLE = 300
MAX_EXCERPT = 1000
MAX_URL = 1000
MAX_AUTHOR = 120
MAX_EXTERNAL_ID = 200
