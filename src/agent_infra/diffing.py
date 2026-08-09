from __future__ import annotations

from typing import Any

from .compiler import compile_workflow
from .model import WorkflowSpec


def diff_workflows(before: WorkflowSpec, after: WorkflowSpec) -> dict[str, Any]:
    old_nodes = {node.id: node for node in before.nodes}
    new_nodes = {node.id: node for node in after.nodes}
    old_edges = {(edge.source, edge.target): edge for edge in before.edges}
    new_edges = {(edge.source, edge.target): edge for edge in after.edges}
    changed_nodes = []
    for node_id in sorted(old_nodes.keys() & new_nodes.keys()):
        if old_nodes[node_id].to_dict() != new_nodes[node_id].to_dict():
            changed_nodes.append(
                {"id": node_id, "before": old_nodes[node_id].to_dict(), "after": new_nodes[node_id].to_dict()}
            )
    changed_edges = []
    for key in sorted(old_edges.keys() & new_edges.keys()):
        if old_edges[key].to_dict() != new_edges[key].to_dict():
            changed_edges.append(
                {"edge": list(key), "before": old_edges[key].to_dict(), "after": new_edges[key].to_dict()}
            )
    result = {
        "before": {
            "name": before.name,
            "version": before.version,
            "digest": compile_workflow(before).digest,
        },
        "after": {"name": after.name, "version": after.version, "digest": compile_workflow(after).digest},
        "nodes": {
            "added": sorted(new_nodes.keys() - old_nodes.keys()),
            "removed": sorted(old_nodes.keys() - new_nodes.keys()),
            "changed": changed_nodes,
        },
        "edges": {
            "added": [new_edges[key].to_dict() for key in sorted(new_edges.keys() - old_edges.keys())],
            "removed": [old_edges[key].to_dict() for key in sorted(old_edges.keys() - new_edges.keys())],
            "changed": changed_edges,
        },
        "schemas_changed": before.input_schema != after.input_schema or before.output_schema != after.output_schema,
        "entry_changed": before.entry != after.entry,
    }
    result["changed"] = any(
        (
            result["nodes"]["added"],
            result["nodes"]["removed"],
            result["nodes"]["changed"],
            result["edges"]["added"],
            result["edges"]["removed"],
            result["edges"]["changed"],
            result["schemas_changed"],
            result["entry_changed"],
        )
    )
    return result
