import logging
import os
import sys

__import__('os').environ['GEVENT_SUPPORT'] = 'true'


def _setup_logging_for_container() -> None:
    """Configure root logger to write to stdout for Docker.

    - Level comes from LOG_LEVEL env (default INFO)
    - Idempotent: clears existing handlers to avoid duplicate logs
    """
    log_level = os.getenv('LOG_LEVEL', 'INFO').upper()

    # Build formatter
    formatter = logging.Formatter(
        fmt='%(asctime)s %(levelname)s [%(name)s] %(message)s'
    )

    root_logger = logging.getLogger()
    try:
        root_logger.setLevel(log_level)
    except Exception:
        root_logger.setLevel(logging.INFO)

    # Remove existing handlers to prevent duplicate outputs (gunicorn/gevent)
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)


_setup_logging_for_container()


def is_db_command():
    if len(sys.argv) > 1 and sys.argv[0].endswith("flask") and sys.argv[1] == "db":
        return True
    return False


# create app
if is_db_command():
    from app_factory import create_migrations_app

    app = create_migrations_app()
else:
    # It seems that JetBrains Python debugger does not work well with gevent,
    # so we need to disable gevent in debug mode.
    # If you are using debugpy and set GEVENT_SUPPORT=True, you can debug with gevent.
    if (flask_debug := os.environ.get("FLASK_DEBUG", "0")) and flask_debug.lower() in {"false", "0", "no"}:
        from gevent import monkey

        # gevent
        monkey.patch_all()

        from grpc.experimental import gevent as grpc_gevent  # type: ignore

        # grpc gevent
        grpc_gevent.init_gevent()

        import psycogreen.gevent  # type: ignore

        psycogreen.gevent.patch_psycopg()

    from app_factory import create_app

    app = create_app()
    celery = app.extensions["celery"]

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
