"""FastAPI diagnostics server (:8001) and gRPC server (:50051) entrypoint."""
from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import os
import sys
from typing import Optional

from fastapi import FastAPI
import grpc
import uvicorn

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Ensure app/pb is on sys.path for generated protobuf imports
_PB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pb")
if _PB_DIR not in sys.path:
    sys.path.insert(0, _PB_DIR)

from app.grpc_server import RUNTIME_VERSION, create_grpc_server
from app.llm.provider import get_llm_provider
from app.tools.registry import APPROVED_TOOLS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_grpc_server: Optional[grpc.Server] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _grpc_server
    grpc_port = int(os.getenv("GRPC_PORT", "50051"))
    if os.getenv("DISABLE_GRPC_SERVER", "").lower() not in ("1", "true", "yes"):
        _grpc_server = create_grpc_server(port=grpc_port)
        _grpc_server.start()
        logger.info("gRPC AgentRuntimeService started on 0.0.0.0:%d", grpc_port)
    try:
        yield
    finally:
        if _grpc_server is not None:
            logger.info("Stopping gRPC AgentRuntimeService...")
            _grpc_server.stop(grace=2)
            _grpc_server = None


app = FastAPI(
    title="Polyglot Agent Runtime",
    version=RUNTIME_VERSION,
    lifespan=lifespan,
)


@app.get("/health")
@app.get("/api/v1/health")
@app.get("/status")
def health_status():
    provider = get_llm_provider()
    configured = provider.is_configured()
    return {
        "status": "healthy" if configured else "degraded",
        "service": "agent-runtime",
        "grpc_port": int(os.getenv("GRPC_PORT", "50051")),
        "http_port": int(os.getenv("HTTP_PORT", "8001")),
        "llm_provider": provider.provider,
        "llm_model": provider.model,
        "llm_configured": configured,
        "approved_tools": sorted(APPROVED_TOOLS),
        "version": RUNTIME_VERSION,
    }


@app.get("/tools")
def list_registered_tools():
    return {
        "tools": sorted(APPROVED_TOOLS),
    }


if __name__ == "__main__":
    http_port = int(os.getenv("HTTP_PORT", "8001"))
    uvicorn.run(app, host="0.0.0.0", port=http_port, log_level="info")
