"""
PROD-008 / F-12 — behavior tests for the production deployment configuration.

These tests make the canonical production deployment (docker-compose.prod.yml
+ Dockerfile.backend.prod + entrypoint + nginx) verifiable in the regular
``python manage.py test`` run (and therefore in CI, which must not be edited —
see docs/production/PRODUCTION_RULES.md).

They verify the acceptance criteria of Issue #15 without starting Docker:

  - exactly one production service topology, all required services present
    (AC-1, AC-6);
  - Gunicorn instead of the development server, non-root runtime (AC-2);
  - the PROD-007 fail-closed settings contract is consumed: DJANGO_DEBUG is
    pinned to "False" by the stack, secrets have no compose-level defaults
    (AC-3, AC-4);
  - PostgreSQL explicitly, no SQLite fallback (AC-5);
  - static/media paths are consistent between settings, containers, volumes
    and nginx (AC-7);
  - health checks for application, database, redis and worker (AC-8);
  - deterministic startup: wait-for-db → migrations → collectstatic → exec
    (AC-9);
  - the development compose file remains usable and does not leak into the
    production stack (AC-10);
  - required variables are all documented in .env.production.example (AC-11).
"""

import re
import stat
from pathlib import Path

import yaml
from django.test import SimpleTestCase

from config import settings

BASE_DIR = Path(settings.BASE_DIR)

PROD_COMPOSE = BASE_DIR / "docker-compose.prod.yml"
DEV_COMPOSE = BASE_DIR / "docker-compose.yml"
PROD_DOCKERFILE = BASE_DIR / "Dockerfile.backend.prod"
ENTRYPOINT = BASE_DIR / "docker" / "production" / "entrypoint.sh"
WAIT_FOR_DB = BASE_DIR / "docker" / "production" / "wait_for_db.py"
NGINX_CONF = BASE_DIR / "docker" / "production" / "nginx.conf"
PROD_ENV_EXAMPLE = BASE_DIR / ".env.production.example"
PROD_SH = BASE_DIR / "scripts" / "prod.sh"

#: Container path that MUST equal settings.MEDIA_ROOT inside the image
#: (BASE_DIR == /app in both Dockerfiles).
MEDIA_CONTAINER_PATH = "/app/media"
STATIC_CONTAINER_PATH = "/app/staticfiles"


class DeploymentFilePresenceTests(SimpleTestCase):
    """The canonical production deployment is fully represented in the repo."""

    REQUIRED_FILES = (
        PROD_COMPOSE,
        PROD_DOCKERFILE,
        ENTRYPOINT,
        WAIT_FOR_DB,
        NGINX_CONF,
        PROD_ENV_EXAMPLE,
        PROD_SH,
        BASE_DIR / "docs" / "production" / "DEPLOYMENT.md",
    )

    def test_all_deployment_files_exist(self):
        for path in self.REQUIRED_FILES:
            with self.subTest(path=path.name):
                self.assertTrue(path.is_file(), f"missing deployment file: {path}")

    def test_entrypoint_is_executable(self):
        if not ENTRYPOINT.exists() or not PROD_SH.exists():
            self.skipTest("deployment scripts not present")
        for path in (ENTRYPOINT, PROD_SH):
            with self.subTest(path=path.name):
                mode = path.stat().st_mode
                self.assertTrue(mode & stat.S_IXUSR, f"{path} must be executable")


def _without_comments(text: str) -> str:
    """Drop comment-only lines so assertions target configuration, not prose."""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


class ProdComposeTests(SimpleTestCase):
    """docker-compose.prod.yml — the single canonical production stack."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.compose = yaml.safe_load(PROD_COMPOSE.read_text())
        cls.services = cls.compose["services"]
        cls.volumes = cls.compose["volumes"]

    def test_exactly_one_canonical_service_topology(self):
        """AC-1/AC-6: expected services and nothing extra."""
        self.assertEqual(
            set(self.services),
            {"db", "redis", "web", "celery", "celery-beat", "nginx"},
        )

    def test_postgresql_service(self):
        """AC-5: PostgreSQL 18 with persistent storage and healthcheck."""
        db = self.services["db"]
        self.assertTrue(db["image"].startswith("postgres:18"))
        pg_volume = db["volumes"][0]
        self.assertTrue(str(pg_volume).split(":")[1].startswith("/var/lib/postgresql/data"))
        self.assertIn("pgdata", str(pg_volume))
        self.assertIn("volumes", self.compose)
        self.assertIn("pgdata", self.volumes)
        self.assertIn("pg_isready", " ".join(db["healthcheck"]["test"]))
        # The database port is not published to the host.
        self.assertNotIn("ports", db)

    def test_redis_service_with_persistence(self):
        """AC-6: Redis with AOF persistence and healthcheck, no host port."""
        redis = self.services["redis"]
        self.assertTrue(redis["image"].startswith("redis:7"))
        self.assertIn("appendonly", " ".join(redis["command"]))
        self.assertIn("redisdata", str(redis["volumes"][0]))
        self.assertEqual(redis["healthcheck"]["test"], ["CMD", "redis-cli", "ping"])
        self.assertNotIn("ports", redis)

    def test_web_runs_gunicorn_not_dev_server(self):
        """AC-2: production WSGI server, no runserver anywhere in the stack."""
        web = self.services["web"]
        self.assertIn("gunicorn", web["command"])
        self.assertIn("config.wsgi:application", web["command"])
        for name, service in self.services.items():
            with self.subTest(service=name):
                blob = str(service.get("command", "")) + str(service.get("entrypoint", ""))
                self.assertNotIn("runserver", blob)
        # Web itself is not published; nginx is the only public port.
        self.assertNotIn("ports", web)
        self.assertEqual(self.services["nginx"]["ports"], ["${PROD_HTTP_PORT:-8080}:80"])

    def test_web_builds_production_dockerfile(self):
        self.assertEqual(self.services["web"]["build"]["dockerfile"], "Dockerfile.backend.prod")

    def test_production_mode_pinned_by_stack(self):
        """AC-3: DJANGO_DEBUG is forced to "False" on every Django service."""
        for name in ("web", "celery", "celery-beat"):
            env = self.services[name]["environment"]
            with self.subTest(service=name):
                self.assertEqual(str(env["DJANGO_DEBUG"]), "False")
                self.assertEqual(env["DB_ENGINE"], "django.db.backends.postgresql")
                # Stack topology, not user configuration:
                self.assertEqual(env["DB_HOST"], "db")
                self.assertEqual(env["REDIS_URL"], "redis://redis:6379/0")

    def test_no_silent_sqlite_in_production(self):
        """AC-5: every Django service pins the PostgreSQL engine."""
        raw = PROD_COMPOSE.read_text()
        self.assertNotIn("sqlite", raw.lower())
        for name in ("web", "celery", "celery-beat"):
            self.assertEqual(
                self.services[name]["environment"]["DB_ENGINE"],
                "django.db.backends.postgresql",
            )

    def test_secrets_have_no_compose_defaults(self):
        """AC-4: fail-closed ${VAR:?} substitutions, no ':-' fallbacks for secrets."""
        raw = _without_comments(PROD_COMPOSE.read_text())
        for var in ("POSTGRES_PASSWORD", "DJANGO_ALLOWED_HOSTS"):
            with self.subTest(var=var):
                self.assertIn(f"${{{var}:?", raw)
        # Security-sensitive variables must never carry a compose-level default.
        with_defaults = {m.group(1) for m in re.finditer(r"\$\{(\w+):-", raw)}
        for var in ("DJANGO_SECRET_KEY", "DJANGO_DEBUG", "POSTGRES_PASSWORD", "DJANGO_ALLOWED_HOSTS"):
            with self.subTest(var=var):
                self.assertNotIn(var, with_defaults)
        # DJANGO_SECRET_KEY is delivered only through env_file (.env.production,
        # never committed) — it must never be inlined (even as ${VAR:-...}).
        self.assertNotIn("DJANGO_SECRET_KEY", raw)

    def test_django_services_require_env_file(self):
        """AC-4/AC-11: secrets come from .env.production via env_file."""
        for name in ("web", "celery", "celery-beat"):
            self.assertIn(".env.production", self.services[name].get("env_file", []))

    def test_no_source_code_bind_mounts_in_production(self):
        """AC-10: no live-reload bind mounts of the repository in prod.

        The only allowed bind mount is the read-only nginx configuration file
        (versioned in the repository); application source is baked into the image.
        """
        nginx_conf_mount = "./docker/production/nginx.conf:/etc/nginx/nginx.conf:ro"
        for name, service in self.services.items():
            for volume in service.get("volumes", []):
                if not isinstance(volume, str):
                    continue
                source = volume.split(":")[0]
                if not (source.startswith(".") or source.startswith("/")):
                    continue  # named volume, not a bind mount
                with self.subTest(service=name, volume=volume):
                    self.assertEqual(
                        volume, nginx_conf_mount,
                        f"unexpected bind mount in production service {name}",
                    )

    def test_media_paths_consistent_with_settings(self):
        """AC-7: every media mount is exactly the MEDIA_ROOT container path."""
        mounts = []
        for name, service in self.services.items():
            for volume in service.get("volumes", []):
                if isinstance(volume, str) and volume.startswith("media_data:"):
                    mounts.append((name, volume))
        self.assertGreaterEqual(len(mounts), 3)  # web, celery (rw) + nginx (ro)
        for name, volume in mounts:
            parts = volume.split(":")
            with self.subTest(service=name, volume=volume):
                self.assertEqual(parts[1], MEDIA_CONTAINER_PATH)
                if name == "nginx":
                    self.assertEqual(parts[2], "ro")
                else:
                    self.assertEqual(len(parts), 2, "web/celery mount media read-write")
        self.assertEqual(
            str(settings.MEDIA_ROOT),
            str(BASE_DIR / "media"),
            "MEDIA_ROOT must resolve to <base>/media == /app/media in the image",
        )

    def test_static_paths_consistent_with_settings(self):
        """AC-7: static volume shared between web (rw) and nginx (ro)."""
        web_volumes = self.services["web"]["volumes"]
        nginx_volumes = self.services["nginx"]["volumes"]
        self.assertIn(f"static_data:{STATIC_CONTAINER_PATH}", web_volumes)
        self.assertIn(f"static_data:{STATIC_CONTAINER_PATH}:ro", nginx_volumes)
        self.assertEqual(
            str(settings.STATIC_ROOT),
            str(BASE_DIR / "staticfiles"),
            "STATIC_ROOT must resolve to <base>/staticfiles == /app/staticfiles in the image",
        )

    def test_celery_worker_independent_and_queues_covered(self):
        """AC-6: worker service consumes every queue defined by task_routes."""
        worker = self.services["celery"]
        self.assertIn("celery -A config worker", worker["command"])
        self.assertIn("-Q celery,orders,cart,reviews", worker["command"])
        self.assertNotIn("gunicorn", worker["command"])
        # Worker is a separate service (not a web command):
        self.assertNotEqual(worker["container_name"], self.services["web"]["container_name"])

    def test_celery_beat_independent_and_persistent(self):
        """AC-6: beat runs separately from the worker with persistent schedule."""
        beat = self.services["celery-beat"]
        self.assertIn("celery -A config beat", beat["command"])
        self.assertNotIn("worker", beat["command"])
        self.assertTrue(
            any("beatdata" in str(v) for v in beat.get("volumes", [])),
            "beat schedule file must live on a persistent volume",
        )

    def test_startup_ordering_and_healthchecks(self):
        """AC-8/AC-9: dependencies gated on health; probes present."""
        web = self.services["web"]
        self.assertEqual(
            web["depends_on"]["db"]["condition"], "service_healthy")
        self.assertEqual(
            web["depends_on"]["redis"]["condition"], "service_healthy")
        self.assertEqual(
            self.services["nginx"]["depends_on"]["web"]["condition"],
            "service_healthy",
        )
        for name in ("db", "redis", "web", "celery", "nginx"):
            with self.subTest(service=name):
                self.assertIn("healthcheck", self.services[name])
        # Web readiness probes the application health endpoint (app + DB).
        probe = " ".join(self.services["web"]["healthcheck"]["test"])
        self.assertIn("/api/v1/health/", probe)

    def test_migrations_and_collectstatic_gated_to_web_only(self):
        """AC-9: deterministic startup — only the web service migrates/collects."""
        env_by_service = {
            name: service["environment"]
            for name, service in self.services.items()
            if "environment" in service
        }
        for name in ("web", "celery", "celery-beat"):
            with self.subTest(service=name):
                self.assertIn("RUN_MIGRATIONS", env_by_service[name])
                self.assertIn("RUN_COLLECTSTATIC", env_by_service[name])
        self.assertEqual(env_by_service["web"]["RUN_MIGRATIONS"], "true")
        self.assertEqual(env_by_service["web"]["RUN_COLLECTSTATIC"], "true")
        for name in ("celery", "celery-beat"):
            self.assertEqual(env_by_service[name]["RUN_MIGRATIONS"], "false")

    def test_restart_policies_defined(self):
        """AC-6 (service restart behaviour): every service restarts unless stopped."""
        for name, service in self.services.items():
            with self.subTest(service=name):
                self.assertEqual(service.get("restart"), "unless-stopped")

    def test_persistent_volumes_declared(self):
        for volume in ("pgdata", "media_data", "static_data", "redisdata", "beatdata"):
            with self.subTest(volume=volume):
                self.assertIn(volume, self.volumes)

    def test_allowed_hosts_extended_only_with_internal_names(self):
        """The stack may only append explicit internal host names."""
        raw_value = self.services["web"]["environment"]["DJANGO_ALLOWED_HOSTS"]
        self.assertTrue(raw_value.startswith("${DJANGO_ALLOWED_HOSTS:?"))
        for internal in ("web", "localhost", "127.0.0.1"):
            self.assertIn(internal, raw_value)
        self.assertNotIn("*", raw_value)


class ProdDockerfileTests(SimpleTestCase):
    """Dockerfile.backend.prod — production runtime (AC-2, AC-11)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dockerfile = _without_comments(PROD_DOCKERFILE.read_text())

    def test_non_root_user(self):
        self.assertRegex(self.dockerfile, r"(?m)^USER app")
        self.assertNotRegex(self.dockerfile, r"(?m)^USER root")

    def test_wsgi_server_entrypoint_and_command(self):
        self.assertIn("gunicorn", self.dockerfile)
        self.assertIn("config.wsgi:application", self.dockerfile)
        self.assertNotIn("runserver", self.dockerfile)
        self.assertIn('ENTRYPOINT ["/app/docker/production/entrypoint.sh"]', self.dockerfile)

    def test_reproducible_layer_order(self):
        """requirements.txt is installed before the source code is copied."""
        self.assertLess(
            self.dockerfile.index("COPY requirements.txt"),
            self.dockerfile.index("COPY . ."),
        )

    def test_writable_dirs_created_for_non_root(self):
        """Named volumes inherit ownership from these directories (AC-7)."""
        mkdir_lines = [
            line for line in self.dockerfile.splitlines() if "mkdir -p" in line
        ]
        self.assertTrue(mkdir_lines, "expected a mkdir -p RUN step")
        for path in ("/app/media", "/app/staticfiles", "/app/celerybeat-data"):
            with self.subTest(path=path):
                self.assertTrue(
                    any(path in line for line in mkdir_lines),
                    f"{path} must be created by the image",
                )
        self.assertIn("chown -R app:app", self.dockerfile)

    def test_base_image_python(self):
        self.assertRegex(self.dockerfile, r"(?m)^FROM python:3\.13-slim")


class EntrypointTests(SimpleTestCase):
    """docker/production/entrypoint.sh — deterministic startup (AC-9)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.script = ENTRYPOINT.read_text()

    def test_waits_for_database_first(self):
        self.assertIn("wait_for_db.py", self.script)

    def test_conditional_migrations_and_collectstatic(self):
        self.assertIn("RUN_MIGRATIONS", self.script)
        self.assertIn("RUN_COLLECTSTATIC", self.script)
        self.assertIn("python manage.py migrate --noinput", self.script)
        self.assertIn("python manage.py collectstatic --noinput", self.script)

    def test_execs_the_real_command(self):
        """Signals (SIGTERM) must reach gunicorn/celery for graceful shutdown."""
        self.assertIn('exec "$@"', self.script)

    def test_wait_for_db_is_posix_python(self):
        source = WAIT_FOR_DB.read_text()
        self.assertIn("ensure_connection", source)
        self.assertIn("DB_WAIT_TIMEOUT", source)


class NginxConfigTests(SimpleTestCase):
    """Edge configuration: static/media serving + proxy (AC-7, AC-8)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.conf = NGINX_CONF.read_text()

    def test_proxies_to_gunicorn(self):
        self.assertIn("upstream backend", self.conf)
        self.assertIn("server web:8000", self.conf)
        self.assertIn("proxy_pass http://backend", self.conf)
        self.assertIn("proxy_set_header Host $host", self.conf)

    def test_serves_static_and_media_from_shared_volumes(self):
        self.assertIn("location /static/", self.conf)
        self.assertIn(f"alias {STATIC_CONTAINER_PATH}/", self.conf)
        self.assertIn("location /media/", self.conf)
        self.assertIn(f"alias {MEDIA_CONTAINER_PATH}/", self.conf)

    def test_liveness_endpoint(self):
        self.assertIn("location = /healthz", self.conf)

    def test_upload_limit_present(self):
        self.assertIn("client_max_body_size", self.conf)


class EnvExampleTests(SimpleTestCase):
    """.env.production.example documents every required variable (AC-4, AC-11)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.example = PROD_ENV_EXAMPLE.read_text()
        cls.keys = {
            line.split("=", 1)[0].strip()
            for line in cls.example.splitlines()
            if "=" in line and not line.strip().startswith("#")
        }

    def test_required_variables_documented(self):
        for var in (
            "DJANGO_SECRET_KEY",
            "DJANGO_ALLOWED_HOSTS",
            "POSTGRES_DB",
            "POSTGRES_USER",
            "POSTGRES_PASSWORD",
            "CORS_ALLOW_ALL_ORIGINS",
            "CORS_ALLOWED_ORIGINS",
            "PAYMENT_WEBHOOK_SECRET",
        ):
            with self.subTest(var=var):
                self.assertIn(var, self.keys)

    def test_every_compose_substitution_is_documented_or_internal(self):
        """No ${VAR} in prod compose may be undocumented in the example file."""
        raw = _without_comments(PROD_COMPOSE.read_text())
        referenced = {m.group(1) for m in re.finditer(r"\$\{(\w+)", raw)}
        undocumented = referenced - self.keys
        self.assertEqual(undocumented, set(), "undocumented compose variables")

    def test_example_contains_only_safe_values(self):
        """No real-looking secrets: the secret key is empty, no wildcard hosts."""
        for line in self.example.splitlines():
            if line.startswith("DJANGO_SECRET_KEY="):
                self.assertEqual(line.strip(), "DJANGO_SECRET_KEY=")
            if line.startswith("POSTGRES_PASSWORD="):
                self.assertEqual(line.strip(), "POSTGRES_PASSWORD=")
            if line.startswith("DJANGO_ALLOWED_HOSTS="):
                self.assertNotIn("*", line)

    def test_production_env_file_is_gitignored(self):
        gitignore = (BASE_DIR / ".gitignore").read_text()
        self.assertRegex(gitignore, r"(?m)^\.env\.production$")

    def test_env_files_excluded_from_image_build_context(self):
        """AC-4: secrets must never be baked into the image layers."""
        dockerignore = (BASE_DIR / ".dockerignore").read_text()
        self.assertRegex(dockerignore, r"(?m)^\.env$")
        self.assertRegex(dockerignore, r"(?m)^\.env\.\*")


class DevComposeIsolationTests(SimpleTestCase):
    """AC-10: development compose remains usable and consistent with prod."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.compose = yaml.safe_load(DEV_COMPOSE.read_text())
        cls.services = cls.compose["services"]

    def test_dev_services_unchanged_and_usable(self):
        self.assertEqual(
            set(self.services),
            {"db", "redis", "backend", "celery", "celery-beat", "frontend"},
        )

    def test_dev_compose_media_mount_matches_media_root(self):
        """F-12 §6: the historical /app/uploads mismatch is gone."""
        for name in ("backend", "celery"):
            volumes = self.services[name]["volumes"]
            with self.subTest(service=name):
                self.assertIn("media:/app/media", volumes)
                self.assertNotIn("media:/app/uploads", str(volumes))

    def test_dev_uses_dev_server_and_debug(self):
        """Development permissiveness is confined to the dev file."""
        self.assertIn("runserver", self.services["backend"]["command"])
        self.assertEqual(str(self.services["backend"]["environment"]["DJANGO_DEBUG"]), "True")
        prod = yaml.safe_load(PROD_COMPOSE.read_text())
        for name, service in prod["services"].items():
            env = service.get("environment", {})
            with self.subTest(service=name):
                self.assertNotEqual(str(env.get("DJANGO_DEBUG", "")), "True")


class DeploymentHelperScriptTests(SimpleTestCase):
    """scripts/prod.sh always pins the canonical compose and env files."""

    def test_script_pins_canonical_files(self):
        script = PROD_SH.read_text()
        self.assertIn("docker-compose.prod.yml", script)
        self.assertIn("--env-file .env.production", script)
        self.assertIn(".env.production.example", script)  # helpful error message
