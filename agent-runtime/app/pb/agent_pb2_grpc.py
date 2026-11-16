# Generated gRPC Python definitions for proto/agent.proto
import grpc
from . import agent_pb2 as agent__pb2


class AgentRuntimeServiceStub(object):
    """Canonical gRPC client stub for AgentRuntimeService."""

    def __init__(self, channel):
        """Constructor.

        Args:
            channel: A grpc.Channel.
        """
        self.ExecuteTask = channel.unary_stream(
            "/agent.AgentRuntimeService/ExecuteTask",
            request_serializer=agent__pb2.TaskRequest.SerializeToString,
            response_deserializer=agent__pb2.AgentEvent.FromString,
        )
        self.HealthCheck = channel.unary_unary(
            "/agent.AgentRuntimeService/HealthCheck",
            request_serializer=agent__pb2.HealthRequest.SerializeToString,
            response_deserializer=agent__pb2.HealthResponse.FromString,
        )


class AgentRuntimeServiceServicer(object):
    """Canonical gRPC servicer for AgentRuntimeService."""

    def ExecuteTask(self, request, context):
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details("Method not implemented!")
        raise NotImplementedError("Method not implemented!")

    def HealthCheck(self, request, context):
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details("Method not implemented!")
        raise NotImplementedError("Method not implemented!")


def add_AgentRuntimeServiceServicer_to_server(servicer, server):
    rpc_method_handlers = {
        "ExecuteTask": grpc.unary_stream_rpc_method_handler(
            servicer.ExecuteTask,
            request_deserializer=agent__pb2.TaskRequest.FromString,
            response_serializer=agent__pb2.AgentEvent.SerializeToString,
        ),
        "HealthCheck": grpc.unary_unary_rpc_method_handler(
            servicer.HealthCheck,
            request_deserializer=agent__pb2.HealthRequest.FromString,
            response_serializer=agent__pb2.HealthResponse.SerializeToString,
        ),
    }
    generic_handler = grpc.method_handlers_generic_handler(
        "agent.AgentRuntimeService", rpc_method_handlers
    )
    server.add_generic_rpc_handlers((generic_handler,))
