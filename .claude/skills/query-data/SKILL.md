---
name: query-data
description: Query tables on this platform's Trino/Iceberg lakehouse from a JupyterHub notebook or any script running on the platform image, using the platform_sdk Python package. Use this whenever the user wants to look at data, query a table, explore what's in the lakehouse, check row counts, or debug a "table not found" / "MissingCredential" / connection error while doing so — even if they don't mention Trino, Iceberg, or platform_sdk by name. Also trigger if the user is writing a notebook cell or script that needs to read from the platform's data warehouse and hasn't set up a connection yet.
---

# Querying data on this platform

The platform ships a small Python package, `platform_sdk`, that is already installed
in the unified notebook/job image (`local/platform-runtime`). It exists specifically
so nobody has to hand-roll a Trino connection string — see
`docs/decisions/058-lightweight-developer-experience.md` for why this exists and what
it deliberately does NOT do (no ORM, no query builder — it's a thin wrapper).

## The one thing to reach for

```python
from platform_sdk import query

df = query("select * from iceberg.demo.orders limit 10")
```

`query()` runs the SQL against Trino and returns a pandas DataFrame (or, if pandas
isn't installed in that environment, a `(columns, rows)` tuple — this only matters for
non-interactive job scripts, notebooks always have pandas).

Table names are fully qualified `catalog.schema.table`. The only catalog on this
platform is `iceberg`; ask the user (or check `docs/operations/troubleshooting.md` /
OpenMetadata at `http://openmetadata.local-lite.test`) which schema their table lives
in if they don't know.

## Before it'll work: credentials

`query()` deliberately has no built-in account — every consumer of Trino on this
platform (Superset, dbt, this SDK, etc.) uses its own named service account, per
ADR-021, so access can be revoked per-caller without breaking everyone else. If the
required environment variables aren't set, `query()` raises `MissingCredential` with
the exact variable name and a hint of where to find the value — read that error
message, it's written to be actionable, don't guess.

You need:

```bash
export PLATFORM_TRINO_USER=<service account name>
export PLATFORM_TRINO_PASSWORD=<its password>
```

These live in the `trino-service-account` Kubernetes Secret (namespace `trino`), one
`password-<account>` key per account (e.g. `password-superset_service`). If the user
doesn't have a dedicated account yet, that's a real gap to flag, not something to
paper over by reusing someone else's — ADR-021's whole point is per-caller isolation.

## Where this runs from matters

- **Inside a JupyterHub notebook**: the unified image has `platform_sdk` pre-installed
  and the Trino/MLflow/MinIO *addresses* already default correctly (cluster-internal
  DNS). Only the credentials above need setting, typically via the notebook's own env
  or a `.env` the user loads.
- **On the user's own machine**: `pip install -e platform-sdk/` (or
  `platform-sdk[submit]` if they'll also submit jobs), then `kubectl port-forward` the
  Trino service and override `PLATFORM_TRINO_HOST`/`PLATFORM_TRINO_PORT` to point at
  `localhost:<forwarded-port>`. See `scripts/09-train-demo-model.sh` for the exact
  port-forward invocation this project already uses.
- **Inside a submitted job** (via `submit_job()`, see the submit-job skill): same code,
  same import — the whole design point of `platform_sdk` is that this file doesn't
  change based on where it runs.

If a query fails with a *connection* error (not `MissingCredential`, and not a SQL
error from Trino itself) from inside a notebook specifically, don't assume Trino is
down — check `docs/BACKLOG.md` section 2.6 first. The JupyterHub singleuser
NetworkPolicy has bitten this exact class of problem before (it blocks all
cluster-internal traffic by default; Trino/MLflow/MinIO were explicitly allow-listed,
but a newly-added target might not be yet).

## Lower-level access

If the user needs raw cursor control (streaming large results, multiple statements in
one transaction, non-SELECT statements where the return shape of `query()` doesn't
fit), use `trino_connection()` instead, which returns a standard DBAPI connection:

```python
from platform_sdk import trino_connection

with trino_connection() as conn:
    cursor = conn.cursor()
    cursor.execute("...")
```

Don't reach for this by default — `query()` covers the vast majority of real usage and
is one line instead of five.
