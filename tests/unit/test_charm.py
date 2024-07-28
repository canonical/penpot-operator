# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests."""


def test_postgresql_config(harness):
    harness.begin_with_initial_hooks()
    assert harness.charm._get_postgresql_credentials() is None
    harness.setup_postgresql_integration()
    assert harness.charm._get_postgresql_credentials() == {
        "PENPOT_DATABASE_PASSWORD": "postgresql-password",
        "PENPOT_DATABASE_URI": "postgresql://postgresql-endpoint:5432/penpot",
        "PENPOT_DATABASE_USERNAME": "postgresql-username",
    }


def test_redis_config(harness):
    harness.begin_with_initial_hooks()
    assert harness.charm._get_redis_credentials() is None
    harness.setup_redis_integration()
    assert harness.charm._get_redis_credentials() == {
        "PENPOT_REDIS_URI": "redis://redis-hostname:6379"
    }


def test_s3_config(harness):
    harness.begin_with_initial_hooks()
    assert harness.charm._get_s3_credentials() is None
    harness.setup_s3_integration()
    assert harness.charm._get_s3_credentials() == {
        "AWS_ACCESS_KEY_ID": "s3-access-key",
        "AWS_SECRET_ACCESS_KEY": "s3-secret-key",
        "PENPOT_ASSETS_STORAGE_BACKEND": "assets-s3",
        "PENPOT_STORAGE_ASSETS_S3_BUCKET": "penpot",
        "PENPOT_STORAGE_ASSETS_S3_ENDPOINT": "s3-endpoint",
        "PENPOT_STORAGE_ASSETS_S3_REGION": "us-east-1",
    }


def test_smtp_config(harness):
    harness.begin_with_initial_hooks()
    assert harness.charm._get_smtp_credentials() == {}
    harness.setup_smtp_integration()
    assert harness.charm._get_smtp_credentials() == {
        "PENPOT_SMTP_DEFAULT_FROM": "no-reply@example.com",
        "PENPOT_SMTP_DEFAULT_REPLY_TO": "no-reply@example.com",
        "PENPOT_SMTP_HOST": "smtp-host",
        "PENPOT_SMTP_PORT": "1025",
        "PENPOT_SMTP_SSL": "false",
        "PENPOT_SMTP_TLS": "false",
    }


def test_smtp_config_with_password(harness):
    harness.begin_with_initial_hooks()
    harness.setup_smtp_integration(use_password=True)
    assert harness.charm._get_smtp_credentials() == {
        "PENPOT_SMTP_DEFAULT_FROM": "smtp-user@example.com",
        "PENPOT_SMTP_DEFAULT_REPLY_TO": "smtp-user@example.com",
        "PENPOT_SMTP_HOST": "smtp-host",
        "PENPOT_SMTP_PASSWORD": "smtp-password",
        "PENPOT_SMTP_PORT": "1025",
        "PENPOT_SMTP_SSL": "false",
        "PENPOT_SMTP_TLS": "false",
        "PENPOT_SMTP_USERNAME": "smtp-user",
    }


def test_smtp_config_override_from_address(harness):
    harness.begin_with_initial_hooks()
    harness.harness.update_config({"email-address": "test@test.com"})
    harness.setup_smtp_integration(use_password=True)
    assert harness.charm._get_smtp_credentials() == {
        "PENPOT_SMTP_DEFAULT_FROM": "test@test.com",
        "PENPOT_SMTP_DEFAULT_REPLY_TO": "test@test.com",
        "PENPOT_SMTP_HOST": "smtp-host",
        "PENPOT_SMTP_PASSWORD": "smtp-password",
        "PENPOT_SMTP_PORT": "1025",
        "PENPOT_SMTP_SSL": "false",
        "PENPOT_SMTP_TLS": "false",
        "PENPOT_SMTP_USERNAME": "smtp-user",
    }


def test_smtp_penpot_option(harness):
    harness.begin_with_initial_hooks()
    assert harness.charm._get_penpot_backend_options() == [
        "disable-log-emails",
        "disable-onboarding-questions",
        "disable-registration",
        "disable-secure-session-cookies",
        "disable-smtp",
        "disable-telemetry",
        "enable-login-with-password",
        "enable-prepl-server",
    ]
    harness.setup_smtp_integration(use_password=True)
    assert harness.charm._get_penpot_backend_options() == [
        "disable-log-emails",
        "disable-onboarding-questions",
        "disable-registration",
        "disable-secure-session-cookies",
        "disable-telemetry",
        "enable-login-with-password",
        "enable-prepl-server",
        "enable-smtp",
    ]


def test_public_uri(harness):
    harness.begin_with_initial_hooks()
    assert harness.charm._get_public_uri() is None
    harness.setup_ingress_integration()
    assert harness.charm._get_public_uri() == "http://penpot.local/"


def test_penpot_pebble_layer(harness):
    harness.set_leader()
    harness.begin_with_initial_hooks()
    assert not harness.charm._check_ready()
    harness.setup_postgresql_integration()
    assert not harness.charm._check_ready()
    harness.setup_redis_integration()
    assert not harness.charm._check_ready()
    harness.setup_s3_integration()
    assert not harness.charm._check_ready()
    harness.setup_ingress_integration()
    assert harness.charm._check_ready()
    harness.setup_smtp_integration()
    assert harness.charm._check_ready()
    plan = harness.charm._gen_pebble_plan()
    del plan["services"]["backend"]["environment"]["PENPOT_SECRET_KEY"]
    assert plan == {
        "description": "penpot services",
        "services": {
            "backend": {
                "command": "/opt/penpot/backend/run.sh",
                "environment": {
                    "AWS_ACCESS_KEY_ID": "s3-access-key",
                    "AWS_SECRET_ACCESS_KEY": "s3-secret-key",
                    "JAVA_HOME": "/usr/lib/jvm/java-21-openjdk-amd64",
                    "PENPOT_ASSETS_STORAGE_BACKEND": "assets-s3",
                    "PENPOT_DATABASE_PASSWORD": "postgresql-password",
                    "PENPOT_DATABASE_URI": "postgresql://postgresql-endpoint:5432/penpot",
                    "PENPOT_DATABASE_USERNAME": "postgresql-username",
                    "PENPOT_FLAGS": "disable-log-emails "
                    "disable-onboarding-questions "
                    "disable-registration "
                    "disable-secure-session-cookies "
                    "disable-telemetry "
                    "enable-login-with-password "
                    "enable-prepl-server "
                    "enable-smtp",
                    "PENPOT_PUBLIC_URI": "http://penpot.local/",
                    "PENPOT_REDIS_URI": "redis://redis-hostname:6379",
                    "PENPOT_SMTP_DEFAULT_FROM": "no-reply@example.com",
                    "PENPOT_SMTP_DEFAULT_REPLY_TO": "no-reply@example.com",
                    "PENPOT_SMTP_HOST": "smtp-host",
                    "PENPOT_SMTP_PORT": "1025",
                    "PENPOT_SMTP_SSL": "false",
                    "PENPOT_SMTP_TLS": "false",
                    "PENPOT_STORAGE_ASSETS_S3_BUCKET": "penpot",
                    "PENPOT_STORAGE_ASSETS_S3_ENDPOINT": "s3-endpoint",
                    "PENPOT_STORAGE_ASSETS_S3_REGION": "us-east-1",
                    "PENPOT_TELEMETRY_ENABLED": "false",
                },
                "override": "replace",
                "working-dir": "/opt/penpot/backend/",
            },
            "exporter": {
                "after": ["backend", "frontend"],
                "command": "node app.js",
                "environment": {
                    "PENPOT_PUBLIC_URI": "http://127.0.0.1:8080",
                    "PENPOT_REDIS_URI": "redis://redis-hostname:6379",
                    "PLAYWRIGHT_BROWSERS_PATH": "/opt/penpot/exporter/browsers",
                },
                "override": "replace",
                "working-dir": "/opt/penpot/exporter/",
            },
            "frontend": {
                "after": ["backend"],
                "command": './nginx-entrypoint.sh nginx -g "daemon ' 'off;"',
                "environment": {
                    "PENPOT_BACKEND_URI": "http://127.0.0.1:6060",
                    "PENPOT_EXPORTER_URI": "http://penpot-0.penpot-endpoints.test.svc.cluster.local:6061",
                    "PENPOT_FLAGS": "disable-onboarding-questions "
                    "disable-registration "
                    "enable-login-with-password",
                    "PENPOT_INTERNAL_RESOLVER": "192.168.127.1",
                },
                "override": "replace",
                "working-dir": "/opt/penpot/frontend/",
            },
        },
        "summary": "penpot services",
    }


def test_penpot_create_profile_action(harness):
    harness.set_leader()
    harness.begin_with_initial_hooks()
    harness.setup_integration()
    harness.harness.set_can_connect("penpot", True)

    def handler(args):
        assert args.command == [
            "python3",
            "manage.py",
            "create-profile",
            "--email",
            "test@test.com",
            "--fullname",
            "test",
        ]
        assert args.stdin

    harness.harness.handle_exec("penpot", [], handler=handler)
    harness.harness.run_action("create-profile", {"email": "test@test.com", "fullname": "test"})


def test_penpot_delete_profile_action(harness):
    harness.set_leader()
    harness.begin_with_initial_hooks()
    harness.setup_integration()
    harness.harness.set_can_connect("penpot", True)

    def handler(args):
        assert args.command == [
            "python3",
            "manage.py",
            "delete-profile",
            "--email",
            "test@test.com",
        ]

    harness.harness.handle_exec("penpot", [], handler=handler)
    harness.harness.run_action("delete-profile", {"email": "test@test.com"})
