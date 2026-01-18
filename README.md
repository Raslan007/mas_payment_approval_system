# MAS Payment Approval System

## Database migrations on Render
- **Local:** run `alembic upgrade head` to apply the latest migrations.
- **Render:** configure the web service **Pre-Deploy Command** to run `alembic upgrade head` so migrations are applied before the server starts.
