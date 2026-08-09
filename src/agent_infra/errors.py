class AgentInfraError(Exception):
    """Base exception for errors safe to show to CLI users."""


class SpecError(AgentInfraError):
    pass


class ValidationFailed(AgentInfraError):
    pass


class ExecutionFailed(AgentInfraError):
    pass


class StoreError(AgentInfraError):
    pass
