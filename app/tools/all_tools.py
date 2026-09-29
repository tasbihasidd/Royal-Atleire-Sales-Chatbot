"""
Legacy LangChain tool registry.

The live graph uses NegotiationEngine via calculate_negotiation_offer_node,
not the calculate_negotiation_offer tool wrapper. Keep product/inventory tools
here for optional agent bindings; do not reintroduce unused scoring_engine.
"""
from app.tools.product_tools import search_products, get_product_details
from app.tools.fabric_tools import search_fabrics, get_fabric_details
from app.tools.inventory_tools import check_inventory
from app.tools.cross_sell_tools import suggest_cross_sell
from app.tools.measurement_tools import validate_measurements
from app.tools.handover_tools import create_human_handover

SALES_AGENT_TOOLS = [
    search_products,
    get_product_details,
    search_fabrics,
    get_fabric_details,
    check_inventory,
    suggest_cross_sell,
    validate_measurements,
    create_human_handover,
]
