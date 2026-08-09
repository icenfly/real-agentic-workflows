"""Public Python API for the REAL Framework."""

from .compiler import compile_workflow
from .model import ExecutionPlan, WorkflowSpec
from .runtime import CompositeTraceSink, RunResult, Runtime
from .validation import ValidationIssue, validate_workflow

__all__ = [
    "ExecutionPlan",
    "CompositeTraceSink",
    "RunResult",
    "Runtime",
    "ValidationIssue",
    "WorkflowSpec",
    "compile_workflow",
    "validate_workflow",
]

__version__ = "0.1.0"
