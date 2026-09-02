import asyncio
import json
import logging
import os

import asyncpg
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None

# Neon's serverless compute can be suspended between sessions and takes a
# few seconds to wake on the next connection — long enough to blow past
# asyncpg's connect timeout on the very first attempt at cold start (seen
# repeatedly in dev: `TimeoutError` out of `asyncpg.create_pool`, which
# previously crashed the whole app with "Application startup failed").
# Retrying a couple of times with a short backoff is the standard mitigation.
_POOL_INIT_RETRIES = 3
_POOL_INIT_BACKOFF_SECONDS = 3.0
_POOL_INIT_ERRORS = (TimeoutError, ConnectionError, OSError)

# Errors asyncpg raises when a pooled connection was silently closed on
# Neon's side (idle-connection recycling, compute suspend/resume) between
# being handed out and actually used — the pool has no way to know the
# connection is dead until it tries it. Retrying once against a fresh
# connection is the standard mitigation for serverless Postgres.
_STALE_CONNECTION_ERRORS = (
    asyncpg.exceptions.ConnectionDoesNotExistError,
    asyncpg.exceptions.InterfaceError,
    asyncpg.exceptions.ConnectionFailureError,
)


async def _init_connection(conn: asyncpg.Connection) -> None:
    for typename in ("json", "jsonb"):
        await conn.set_type_codec(
            typename, encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        # statement_cache_size=0: Neon's pooled endpoint is PgBouncer in transaction
        # mode, which doesn't support asyncpg's server-side prepared statements.
        # max_inactive_connection_lifetime: proactively recycle idle connections
        # before Neon's own pooler closes them out from under us (see
        # _STALE_CONNECTION_ERRORS above) — reduces how often that retry is needed,
        # doesn't eliminate the race entirely.
        # min_size=1: asyncpg's default (10) opens ten connections concurrently on
        # startup, which just multiplies how many can individually time out while
        # Neon's compute is still waking up — one connection to retry is cheaper
        # and just as effective at unblocking startup.
        for attempt in range(_POOL_INIT_RETRIES + 1):
            try:
                _pool = await asyncpg.create_pool(
                    dsn=os.environ["DATABASE_URL"],
                    min_size=1,
                    statement_cache_size=0,
                    max_inactive_connection_lifetime=60,
                    init=_init_connection,
                )
                break
            except _POOL_INIT_ERRORS as e:
                if attempt == _POOL_INIT_RETRIES:
                    raise
                logger.warning(
                    "DB pool init attempt %d/%d failed (%s) — Neon compute is "
                    "probably still waking up, retrying in %.0fs",
                    attempt + 1, _POOL_INIT_RETRIES + 1, e, _POOL_INIT_BACKOFF_SECONDS,
                )
                await asyncio.sleep(_POOL_INIT_BACKOFF_SECONDS)
    return _pool


async def with_db_retry(coro_fn, *args, **kwargs):
    """Runs a pool-bound call (e.g. pool.fetchrow), retrying once if the
    connection it got handed was stale. Use this for call sites that run on
    every request (auth) rather than accepting an occasional unhandled 500."""
    try:
        return await coro_fn(*args, **kwargs)
    except _STALE_CONNECTION_ERRORS:
        return await coro_fn(*args, **kwargs)


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
