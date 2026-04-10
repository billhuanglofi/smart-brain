# Architecture Overview

## System Components

### Frontend
- React SPA served via Nginx.
- Communicates with the backend exclusively through the REST API.

### Backend API
- Python / FastAPI application running behind a Gunicorn WSGI server.
- Stateless – all session data is stored in Redis.

### Database
- PostgreSQL 15 for persistent data.
- Migrations managed with Alembic; never edit migration files after they are
  applied to production.

### Message Queue
- RabbitMQ for async task processing (email notifications, report generation).
- Celery workers consume tasks; retry logic is configured per task type.

## Security Decisions

- All API endpoints require a JWT bearer token (RS256).
- Tokens expire after 15 minutes; use refresh tokens for longer sessions.
- CORS is restricted to the production frontend domain; open CORS is never
  acceptable in production.

## Deployment

- Kubernetes on GKE; Helm charts live in `infrastructure/helm/`.
- Deployments are blue-green; the old pods are kept alive for 5 minutes to
  allow in-flight requests to complete.
- Rollback by flipping the active colour in the load balancer config.
