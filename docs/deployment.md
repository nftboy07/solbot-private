# Deployment Pipeline Documentation (Phase 6)

## Overview
The automated deployment pipeline for Solbot V3/V4 handles continuous integration and deployment to the production VPS.

## Workflow
1. **Push**: Developer pushes code to `feature/v3.1-production`.
2. **CI**: GitHub Actions runs dependencies install and basic tests.
3. **Validation**: The pipeline checks for required SSH and Telegram secrets. If missing, it fails closed to prevent partial/unsafe deploys.
4. **Deploy**: 
   - Connects to VPS via SSH.
   - Pulls latest changes.
   - Updates virtual environment (`pip install -r requirements.txt`).
   - Restarts the service (supports `systemd` or `tmux`).
5. **Health Check**: Executes `scripts/health_check.py` to verify the bot is responding.
6. **Notification**: Sends a deployment status report to the configured Telegram channel.

## Required Secrets (GitHub Actions)
- `VPS_HOST`: Production server IP/Domain.
- `VPS_USER`: SSH user.
- `VPS_SSH_KEY`: Private SSH key for access.
- `TELEGRAM_TOKEN`: Bot token for notifications.
- `TELEGRAM_TO`: Chat ID for notifications.

## Safety Measures
- Does not modify `.session` files or production databases.
- Fail-closed logic on missing credentials.
- Health check timeout to prevent zombie deployments.
