"""gRPC server implementation for AgentRuntimeService on port 50051."""
from __future__ import annotations

from concurrent import futures
import logging
from typing import Optional

import grpc

from app.agent.loop import run_agent_loop
from app.llm.provider import LLMProvider, get_llm_provider
from app.pb import agent_pb2, agent_pb2_grpc

logger = logging.getLogger(__name__)

RUNTIME_VERSION = "1.0.0"


class AgentRuntimeServicer(agent_pb2_grpc.AgentRuntimeServiceServicer):
    """Implements ExecuteTask and HealthCheck RPCs."""

    def __init__(self, llm_provider: Optional[LLMProvider] = None) -> None:
        self._llm_provider = llm_provider

    @property
    def llm_provider(self) -> LLMProvider:
        if self._llm_provider is None:
            self._llm_provider = get_llm_provider()
        return self._llm_provider

    def ExecuteTask(self, request: agent_pb2.TaskRequest, context: grpc.ServicerContext):
        logger.info(
            "Received ExecuteTask RPC: task_id=%s workspace_id=%s max_iterations=%d",
            request.task_id,
            request.workspace_id,
            request.max_iterations,
        )
        for event in run_agent_loop(request, llm_provider=self.llm_provider):
            if context is not None and not context.is_active():
                logger.info("Client disconnected during ExecuteTask for task_id=%s", request.task_id)
                break
            yield event

    def HealthCheck(
        self, request: agent_pb2.HealthRequest, context: grpc.ServicerContext
    ) -> agent_pb2.HealthResponse:
        provider = self.llm_provider
        configured = provider.is_configured()
        return agent_pb2.HealthResponse(
            status="healthy" if configured else "degraded",
            llm_provider=provider.provider,
            llm_model=provider.model,
            llm_configured=configured,
            version=RUNTIME_VERSION,
        )


def create_grpc_server(
    port: int = 50051,
    max_workers: int = 10,
    llm_provider: Optional[LLMProvider] = None,
) -> grpc.Server:
    """Create and bind the gRPC server on the given port."""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    servicer = AgentRuntimeServicer(llm_provider=llm_provider)
    agent_pb2_grpc.add_AgentRuntimeServiceServicer_to_server(servicer, server)
    server.add_insecure_port(f"0.0.0.0:{port}")
    return server
