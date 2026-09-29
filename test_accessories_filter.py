"""
Test script to verify accessories filtering for Suits category.
Run this to see what accessories are being filtered for RIVIERA SUIT III.
"""

import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.backend_api import backend_api
from app.services.accessories_service import (
    filter_free_gift_candidates,
    free_gift_margin_budget,
    category_matches_pairs,
)


async def test_suit_accessories():
    """Test accessories filtering for Suits category."""
    
    # RIVIERA SUIT III details
    product_name = "RIVIERA SUIT III"
    product_category = "Suits"
    product_price = 850.0  # GBP
    floor_price = 700.0    # GBP
    event_type = "Walima"
    currency = "GBP"
    
    print(f"\n{'='*80}")
    print(f"Testing Accessories for: {product_name}")
    print(f"Category: {product_category}")
    print(f"Price: £{product_price}, Floor: £{floor_price}")
    print(f"Margin Budget: £{product_price - floor_price}")
    print(f"{'='*80}\n")
    
    # Search accessories
    print("🔍 Searching accessories from backend API...")
    accessories = await backend_api.search_accessories({
        "category": product_category,
        "event_type": event_type,
        "limit": 50,
    })
    
    print(f"✓ Found {len(accessories)} accessories from API\n")
    
    # Show all accessories with their details
    print(f"{'='*80}")
    print("ALL ACCESSORIES RETURNED:")
    print(f"{'='*80}")
    for i, acc in enumerate(accessories, 1):
        name = acc.get("name", "N/A")
        acc_type = acc.get("accessory_type", "N/A")
        price = acc.get("price", 0)
        pairs_with = acc.get("pairs_with_categories", [])
        matches = category_matches_pairs(product_category, pairs_with)
        
        print(f"\n{i}. {name}")
        print(f"   Type: {acc_type}")
        print(f"   Price: £{price}")
        print(f"   Pairs With: {pairs_with}")
        print(f"   ✓ Matches {product_category}: {'YES ✅' if matches else 'NO ❌'}")
    
    # Filter for free gifts
    print(f"\n{'='*80}")
    print("FILTERED FREE GIFT CANDIDATES:")
    print(f"{'='*80}")
    
    filtered = filter_free_gift_candidates(
        accessories,
        list_price=product_price,
        floor_price=floor_price,
        product_category=product_category,
        event_type=event_type,
        currency=currency,
        limit=10,
    )
    
    print(f"\n✓ {len(filtered)} accessories pass the filter\n")
    
    for i, acc in enumerate(filtered, 1):
        name = acc.get("name", "N/A")
        acc_type = acc.get("accessory_type", "N/A")
        price = acc.get("price", 0)
        
        print(f"{i}. {name}")
        print(f"   Type: {acc_type}")
        print(f"   Price: £{price}")
        print(f"   ✓ Within margin (£0 < £{price} <= £{product_price - floor_price})")
        print()
    
    # Show which ones were EXCLUDED and why
    excluded = [a for a in accessories if a not in filtered]
    if excluded:
        print(f"\n{'='*80}")
        print(f"EXCLUDED ACCESSORIES ({len(excluded)}):")
        print(f"{'='*80}\n")
        
        for i, acc in enumerate(excluded, 1):
            name = acc.get("name", "N/A")
            price = acc.get("price", 0)
            pairs_with = acc.get("pairs_with_categories", [])
            matches = category_matches_pairs(product_category, pairs_with)
            margin = product_price - floor_price
            
            reasons = []
            if not matches:
                reasons.append(f"❌ Doesn't pair with {product_category}")
            if price <= 0:
                reasons.append(f"❌ Price is £{price} (must be > 0)")
            if price > margin:
                reasons.append(f"❌ Price £{price} > margin £{margin}")
            
            print(f"{i}. {name} (£{price})")
            for reason in reasons:
                print(f"   {reason}")
            print()


if __name__ == "__main__":
    asyncio.run(test_suit_accessories())
