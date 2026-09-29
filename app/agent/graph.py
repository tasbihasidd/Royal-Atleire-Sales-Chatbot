from __future__ import annotations

import logging
from langgraph.graph import StateGraph, END

from app.agent.state import SalesAgentState
from app.agent.nodes import (
    planner_node,
    search_products_node,
    search_fabrics_node,
    get_product_details_node,
    check_inventory_node,
    search_accessories_node,
    suggest_cross_sell_node,
    close_sale_node,
    calculate_negotiation_offer_node,
    validate_measurements_node,
    collect_measurements_node,
    create_human_handover_node,
    final_response_node,
    generate_custom_design_node,
)

logger = logging.getLogger(__name__)

STEP_TO_NODE = {
    "search_products": "search_products",
    "search_fabrics": "search_fabrics",
    "get_product_details": "get_product_details",
    "check_inventory": "check_inventory",
    "search_accessories": "search_accessories",
    "suggest_cross_sell": "suggest_cross_sell",
    "calculate_negotiation_offer": "calculate_negotiation_offer",
    "validate_measurements": "validate_measurements",
    "collect_measurements": "collect_measurements",
    "close_sale": "close_sale",
    "create_human_handover": "create_human_handover",
    "generate_custom_design": "generate_custom_design",
}


def route_next_step(state: SalesAgentState) -> str:
    steps = state.get("required_steps", [])
    index = state.get("current_step_index", 0)
    if index >= len(steps):
        next_node = "final_response"
    else:
        next_node = STEP_TO_NODE.get(steps[index], "final_response")
    logger.debug(
        "route_next_step session_id=%s index=%s step=%s next_node=%s",
        state.get("session_id"),
        index,
        steps[index] if index < len(steps) else None,
        next_node,
    )
    return next_node


def increment_step(state: SalesAgentState) -> dict:
    return {"current_step_index": state.get("current_step_index", 0) + 1}


import inspect

def _traced_node(node_name: str, node_fn):
    if inspect.iscoroutinefunction(node_fn):
        async def _async_wrapper(state: SalesAgentState):
            res = await node_fn(state)
            if isinstance(res, dict):
                out = dict(res)
                out["executed_nodes"] = [node_name]
                return out
            return {"executed_nodes": [node_name]}
        return _async_wrapper
    else:
        def _sync_wrapper(state: SalesAgentState):
            res = node_fn(state)
            if isinstance(res, dict):
                out = dict(res)
                out["executed_nodes"] = [node_name]
                return out
            return {"executed_nodes": [node_name]}
        return _sync_wrapper


def build_graph():
    graph = StateGraph(SalesAgentState)

    graph.add_node("planner", _traced_node("planner", planner_node))
    graph.add_node("search_products", _traced_node("search_products", search_products_node))
    graph.add_node("search_fabrics", _traced_node("search_fabrics", search_fabrics_node))
    graph.add_node("get_product_details", _traced_node("get_product_details", get_product_details_node))
    graph.add_node("check_inventory", _traced_node("check_inventory", check_inventory_node))
    graph.add_node("search_accessories", _traced_node("search_accessories", search_accessories_node))
    graph.add_node("suggest_cross_sell", _traced_node("suggest_cross_sell", suggest_cross_sell_node))
    graph.add_node("close_sale", _traced_node("close_sale", close_sale_node))
    graph.add_node("calculate_negotiation_offer", _traced_node("calculate_negotiation_offer", calculate_negotiation_offer_node))
    graph.add_node("validate_measurements", _traced_node("validate_measurements", validate_measurements_node))
    graph.add_node("collect_measurements", _traced_node("collect_measurements", collect_measurements_node))
    graph.add_node("create_human_handover", _traced_node("create_human_handover", create_human_handover_node))
    graph.add_node("generate_custom_design", _traced_node("generate_custom_design", generate_custom_design_node))
    graph.add_node("increment_step", increment_step)
    graph.add_node("final_response", _traced_node("final_response", final_response_node))

    graph.set_entry_point("planner")

    destinations = {
        "search_products": "search_products",
        "search_fabrics": "search_fabrics",
        "get_product_details": "get_product_details",
        "check_inventory": "check_inventory",
        "search_accessories": "search_accessories",
        "suggest_cross_sell": "suggest_cross_sell",
        "close_sale": "close_sale",
        "calculate_negotiation_offer": "calculate_negotiation_offer",
        "validate_measurements": "validate_measurements",
        "collect_measurements": "collect_measurements",
        "create_human_handover": "create_human_handover",
        "generate_custom_design": "generate_custom_design",
        "final_response": "final_response",
    }

    graph.add_conditional_edges("planner", route_next_step, destinations)

    for node_name in STEP_TO_NODE.values():
        graph.add_edge(node_name, "increment_step")

    graph.add_conditional_edges("increment_step", route_next_step, destinations)

    graph.add_edge("final_response", END)

    # Postgres (+ memory_service) is the single source of conversation truth.
    return graph.compile()


sales_agent_graph = build_graph()
