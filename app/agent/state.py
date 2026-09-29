from __future__ import annotations

from typing import Any, TypedDict
from operator import add
from typing_extensions import Annotated, NotRequired


class SalesAgentState(TypedDict):
    """
    LangGraph state for Royal Atelier Sales Agent.
    
    Note on `NotRequired`:
    In Python's TypedDict typing system, `NotRequired[T]` means that the key is optional
    when constructing or updating the state dict (i.e., state.get("key") can be None if not present).
    """
    # Core turn data
    session_id: str
    user_message: str

    # Conversation memory (Annotated add appends new messages automatically within one turn)
    messages: Annotated[list[dict[str, str]], add]

    # Node execution tracing (Annotated add appends nodes executed in the turn)
    executed_nodes: Annotated[list[str], add]

    # Planner sequencing
    intent: NotRequired[str]
    required_steps: NotRequired[list[str]]
    current_step_index: NotRequired[int]

    # Sales FSM Journey & Planner State
    sales_stage: NotRequired[str]
    buying_intent: NotRequired[str]
    objection_type: NotRequired[str | None]

    # Customer Profile & Extracted Attributes (Synced with CustomerProfileSchema)
    customer_profile: NotRequired[dict[str, Any]]
    event_type: NotRequired[str | None]
    product_type: NotRequired[str | None]
    color: NotRequired[str | None]
    size: NotRequired[str | None]
    quantity: NotRequired[int | None]
    budget: NotRequired[float | None]
    wedding_date: NotRequired[str | None]
    height: NotRequired[str | None]
    chest: NotRequired[str | None]
    waist: NotRequired[str | None]
    # Chart-driven body measurements (from GET /api/v2/size-charts columns only)
    shoulder: NotRequired[str | None]
    sleeve: NotRequired[str | None]
    jacket_length: NotRequired[str | None]
    size_chart: NotRequired[dict[str, Any] | None]
    body_measurements: NotRequired[dict[str, Any] | None]
    measurement_path: NotRequired[str | None]  # "standard_size" | "body_measurements"
    measurement_prompt: NotRequired[str | None]
    selected_product_id: NotRequired[str | None]
    target_product_query: NotRequired[str | None]
    selected_fabric_catalog_code: NotRequired[str | None]

    # Negotiation & Commercial State
    negotiation_state: NotRequired[dict[str, Any]]
    scoring_reasons: NotRequired[list[dict[str, Any]]]
    held_reservation_id: NotRequired[str | None]

    # Customer Contact & Handover Metadata
    customer_contact: NotRequired[dict[str, Any] | None]
    handover_reason: NotRequired[str | None]
    handover_pending: NotRequired[bool]

    # Tool execution outputs
    products: NotRequired[list[dict[str, Any]]]
    fabrics: NotRequired[list[dict[str, Any]]]
    product_details: NotRequired[dict[str, Any] | None]
    inventory_result: NotRequired[dict[str, Any] | None]
    style_context: NotRequired[list[dict[str, Any]]]
    negotiation_result: NotRequired[dict[str, Any] | None]
    measurement_result: NotRequired[dict[str, Any] | None]
    handover_result: NotRequired[dict[str, Any] | None]
    recommendations: NotRequired[list[dict[str, Any]]]
    cross_sell_items: NotRequired[list[dict[str, Any]]]
    close_result: NotRequired[dict[str, Any] | None]

    # Agent output
    final_response: NotRequired[str | None]
    catalog_categories: NotRequired[list[dict[str, Any]]]
    discovery_next_slot: NotRequired[str | None]
    catalog_search_note: NotRequired[str | None]
    available_colors_summary: NotRequired[list[str]]
    checkout_hand_off_note: NotRequired[str | None]
    wants_more_options: NotRequired[bool | None]
    product_interest_note: NotRequired[str | None]
    catalog_variations: NotRequired[list[dict[str, Any]]]
    category_variations: NotRequired[list[dict[str, Any]]]
    selected_variation_id: NotRequired[str | None]
    selected_variation_name: NotRequired[str | None]
    product_variations: NotRequired[list[dict[str, Any]]]
    selected_product_variation_id: NotRequired[str | None]
    selected_product_variation_name: NotRequired[str | None]
    product_variation_note: NotRequired[str | None]

    # Bespoke / Custom Design State
    custom_image_url: NotRequired[str | None]
    custom_design_result: NotRequired[dict[str, Any] | None]
    custom_instructions: NotRequired[str | None]
    customization_stage: NotRequired[str | None]  # preferences | fabric_selection | cut_style | generation | measurements
    cut_style: NotRequired[str | None]

    # Seen products tracking across turns
    shown_product_ids: NotRequired[list[str]]
