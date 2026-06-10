from flask import Flask, abort, jsonify, request

from .config import settings
from .semantle_engine import RemoteSemantleEngine, SelfHostedSemantleEngine
from .slack_app import handle_slash_command, verify_slack_request
from .storage import StateStore


def create_app() -> Flask:
    app = Flask(__name__)
    engine = create_engine()
    store = StateStore(settings.state_db_path)

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "engine_mode": settings.engine_mode, "port": settings.port}

    @app.post("/slack/commands")
    def slack_commands():
        if not verify_slack_request(request, settings.slack_signing_secret):
            abort(401)
        return jsonify(
            handle_slash_command(
                request.form.to_dict(),
                engine,
                store,
                public_responses=settings.public_responses,
            )
        )

    return app


def create_engine():
    mode = settings.engine_mode.lower()
    if mode == "remote":
        return RemoteSemantleEngine(settings.remote_base_url)
    if mode == "self_hosted":
        return SelfHostedSemantleEngine(settings.data_dir, allow_score_only=settings.allow_score_only)
    raise ValueError(f"unknown KKOMA_ENGINE_MODE: {settings.engine_mode}")


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=settings.port, debug=True)
