# Refactoring Suggestions

This document captures safe, incremental refactors to improve structure, naming, and separation of concerns without changing runtime behavior.

## Blueprint boundaries and shared constants
- **Unify workflow state constants**: Payment status strings are duplicated in multiple blueprints (e.g., `blueprints/payments/routes.py` lines 32-39 and `blueprints/main/routes.py` lines 13-20). Consolidate them into a shared module (such as `workflow/statuses.py`) and import them in both blueprints to avoid drift and keep dashboards aligned with the workflow engine.
- **Centralize workflow transition rules**: The `WORKFLOW_TRANSITIONS` map and helpers like `_require_transition` live inside the payments blueprint (`blueprints/payments/routes.py` lines 42-148). Moving them into a service (e.g., `services/workflow.py`) would let other contexts (jobs, API endpoints) reuse the same guardrails without duplicating logic.

## Service / domain layer extraction
- **Isolate payment domain logic**: Route functions currently orchestrate authorization checks, state transitions, attachment I/O, and persistence in one place (see helpers in `blueprints/payments/routes.py` lines 100-199). Introduce a `services/payments.py` module to encapsulate: 
  - Permission checks (`can_view`, `can_edit`, `can_delete`).
  - State transition validation and execution.
  - Attachment path handling (e.g., `_attachments_base_path`).
  This keeps view functions thin and testable and enables reuse from CLI tasks or background workers.
- **Abstract dashboard analytics**: Dashboard aggregations are embedded in `blueprints/main/routes.py` (`dashboard` uses SQLAlchemy queries directly at lines 77-120). Creating a `services/analytics.py` (or similar) to hold query builders would keep the blueprint focused on presentation while allowing unit tests to cover aggregate logic separately.

## Naming and package layout consistency
- **Align blueprint namespaces**: The registration in `app.py` enumerates each blueprint (`app.register_blueprint(...)` at lines 9-71). Consider organizing blueprint packages under a common `apps/` or `modules/` namespace and exporting a registration helper (e.g., `register_blueprints(app)`) to keep the entrypoint concise and consistent.
- **Permission utilities location**: The `permissions.py` module defines `role_required` and is imported throughout the app. Relocating it into a dedicated package (e.g., `core/permissions.py`) alongside new workflow/analytics services would make cross-cutting concerns easier to discover and standardize naming (for example, `from core.permissions import role_required`).

## Testing and migration safeguards
- **Keep behavior unchanged**: When extracting services, wrap new modules with thin adapters that preserve function signatures used by templates and routes. Add unit tests around the new services before swapping the imports to ensure parity.
- **Documentation updates**: Update developer onboarding docs (e.g., README) to show the new module paths and patterns once extractions are complete, so new code follows the refactored structure rather than the legacy one.
