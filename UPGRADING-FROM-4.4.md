# Upgrading from Healthchecks 4.4

This fork's `v5.1.0-osa.3` prerelease is based on upstream
`930f239c` (`v5.1-dev`), rather than on the `v4.4` tag. It includes the intervening
upstream changes as well as the local extensions described in the README.

## Database migrations

All schema changes for the extensions are included:

| Migration | Changes |
| --- | --- |
| `api.0124_maintenance_windows` | Adds maintenance windows, service assignments, and the nullable, indexed `Flip.deferred_until` timestamp for durable notification deferral. |
| `api.0125_project_wide_maintenance` | Adds project-wide scope and allows an empty explicit service selection for that scope. |
| `api.0126_global_maintenance` | Allows windows independent of a project and adds a database constraint limiting them to one-time maintenance. |

Multi-project invitations, user-admin memberships, status filtering, and field
selection reuse existing tables and do not require additional schema changes.
Existing checks have no maintenance windows and retain their normal behavior.

The upstream migration directories are unchanged between `v4.4` and this
release's base. A database already fully migrated on 4.4 therefore needs only
the three migrations above. Django determines and applies the dependency order;
do not create tables manually or use `--fake` for this upgrade.

## Before upgrading

- Back up the database, deployment configuration, and any external ping-body
  storage. Keep the existing `SECRET_KEY`, database connection settings,
  `SITE_ROOT`, `PING_ENDPOINT`, and integration credentials.
- Rehearse against a restored copy of the actual production database using the
  same database engine and version as production.
- Review the upstream [changelog](CHANGELOG.md), especially the v5.0 notes below.
- Use Python 3.12 or later and install this branch's pinned requirements.

The database rehearsal performed for this branch used a disposable SQLite
database at the v4.4 migration state, populated with synthetic data. It verified
that upgrading preserves password hashes, profiles, project badge keys,
membership roles, checks, ping bodies, integration assignments, and pending
status changes. It also verified the new maintenance scopes and that a second
migration run has no pending operations. This is not a rehearsal against a
production PostgreSQL or MySQL database.

## Source installations

1. Stop the existing Healthchecks web process and background processes
   (`sendalerts`, `sendreports`, and `smtpd` when used).
2. Fetch this fork's tags and check out `v5.1.0-osa.2`. Keep the existing
   deployment configuration and database settings, and activate the deployment's
   Python environment.
3. Install dependencies and inspect/apply migrations:

   ```sh
   python -m pip install -r requirements.txt
   python manage.py migrate --plan
   python manage.py migrate --noinput
   python manage.py collectstatic --noinput
   python manage.py compress
   python manage.py check
   ```

   Keep any additional database drivers, WSGI server, or integration dependencies
   required by the installation. Run these commands with the production settings
   loaded, including `DEBUG=False` when building production static assets.

4. Restart the web process and every background process using this same code and
   environment. The web process and `sendalerts` both need the new maintenance
   code; restarting only the web process is insufficient.
5. Check login, project visibility, ping ingestion, integrations, and the Badges
   page. Create a short maintenance window for a test check and verify that a
   persistent failure notifies after maintenance ends.

Normal source installations can continue using their existing compatible WSGI
server configuration. The bundled server switch described below concerns the
Docker image.

## Docker installations: upstream v5.0 changes

This release publishes source archives only. Build an image from this fork using
`docker/Dockerfile`; the upstream image does
not include these extensions. The image runs migrations on startup through
`/opt/healthchecks/run.sh`. Start a single instance for the migration, then bring
up the remaining instances after it succeeds.

- If a custom Compose file overrides `command` to run uWSGI, update it to
  `/opt/healthchecks/run.sh`, or remove the override to use the image default.
  The bundled web server is now Gunicorn.
- `GUNICORN_WORKERS` controls web workers; `UWSGI_PROCESSES` is only a fallback
  when `GUNICORN_WORKERS` is unset.
- If a child process exits, the entire container now exits. Configure the
  deployment's container restart policy accordingly.
- The SMTP listener changed to `minismtpd`. Review custom SMTP settings and any
  deployment-specific process overrides.
- The Shell integration changed from command-text substitution to environment
  variables. Review existing Shell integration commands before enabling them
  after the upgrade; consult the v5.0 changelog and Shell integration UI.

## Rollback

Retain the pre-upgrade application version and database backup. If rollback is
needed, stop the upgraded processes and restore the matching application,
configuration, and database backup together. The older application does not
honor the new maintenance windows or deferred-notification state. Restoring a
backup also discards writes made after that backup, so verify the procedure on
the rehearsal copy first.


## Upgrading from v5.1.0-osa.1 to v5.1.0-osa.2

This patch release adds read-only ping history, ping bodies by `unique_key`, and
integration listing. It has no new schema changes or dependency changes. Check
out `v5.1.0-osa.2`, keep the existing production configuration, rebuild static
assets with the commands above, and restart the web process. Existing read-only
and read-write keys remain valid. Agents requesting selected check fields must
include `unique_key` for follow-up diagnostic requests.
