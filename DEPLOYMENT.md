# Deployment Notes

## Required environment variables
- `SECRET_KEY`: strong secret value (required in production).
- `DATABASE_URL`: connection string for your production database (Postgres or SQLite).
- `APP_ENV=production`: ensures production safeguards such as strict cookie settings are enabled.

## Build & start commands (Render)
- **Build Command:** `pip install -r requirements.txt`
- **Pre-Deploy Command:** `alembic upgrade head`
- **Start Command:** `gunicorn "app:app" --bind 0.0.0.0:$PORT`

Use Render's **Pre-Deploy Command** to apply Alembic migrations before the web service starts. This keeps schema changes in sync while keeping the runtime start command focused on launching the server.

If you add or upgrade dependencies, use **Settings → Clear build cache** in Render before redeploying to ensure a clean environment.

## Optional legacy migration scripts
If you need to run the existing idempotent Python migration helpers (for example, during a transition), you can execute them manually or chain them into a one-off job:

```bash
python scripts/migrate_add_indexes.py \
  && python scripts/migrate_add_payment_submitted_to_pm_at.py \
  && python scripts/migrate_add_payment_updated_at.py \
  && python scripts/migrate_add_purchase_orders_reserved_amount.py \
  && python scripts/migrate_add_purchase_orders_paid_amount.py \
  && python scripts/migrate_add_payment_requests_purchase_order_id.py \
  && python scripts/migrate_add_payment_requests_po_reservation_markers.py \
  && python scripts/migrate_add_user_projects.py
```

These scripts read `DATABASE_URL`, create/patch missing tables or columns safely (Postgres), and backfill data where necessary. They are safe to rerun.

## Optional automatic schema bootstrap
To let the application create any missing tables (e.g., newly added models) during startup, set the environment variable `AUTO_SCHEMA_BOOTSTRAP=1`. When unset or `0`, the app skips the automatic `create_all()` call and relies on your migration scripts instead. The flag is idempotent and safe to enable on platforms like Render where schema drift may occur.
