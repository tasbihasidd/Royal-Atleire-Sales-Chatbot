#!/bin/bash
# Royal Atelier - Complete Feature Test Script
# Run with: bash test_all_features.sh

BASE_URL="http://localhost:8015"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}=========================================="
echo "Royal Atelier Sales Agent - Feature Tests"
echo -e "==========================================${NC}"
echo ""

# Check if server is running
if ! curl -s "$BASE_URL/health" > /dev/null; then
    echo -e "${YELLOW}⚠️  Server not running! Start with:${NC}"
    echo "   python -m uvicorn app.main:app --host 0.0.0.0 --port 8015 --reload"
    exit 1
fi

echo -e "${GREEN}✅ Server is running${NC}"
echo ""

# ============================================
# TEST 1: Hello Flow → Discovery → Products
# ============================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST 1: Hello Flow (Discovery → Products)"
echo -e "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

SESSION1="hello_flow_$(date +%s)"

echo -e "\n${YELLOW}👤 User: Assalam o Alaikum${NC}"
curl -s -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d "{\"session_id\": \"$SESSION1\", \"message\": \"Assalam o Alaikum\"}" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print('🤖 Bot:', d['reply'][:200])"

sleep 1

echo -e "\n${YELLOW}👤 User: barat hai December main${NC}"
curl -s -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d "{\"session_id\": \"$SESSION1\", \"message\": \"barat hai December main\"}" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print('🤖 Bot:', d['reply'][:200])"

sleep 1

echo -e "\n${YELLOW}👤 User: groom hun, sherwani dikhao${NC}"
RESPONSE=$(curl -s -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d "{\"session_id\": \"$SESSION1\", \"message\": \"groom hun, sherwani dikhao\"}")
echo "$RESPONSE" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print('🤖 Bot:', d['reply'][:300])
print('📦 Products:', len(d.get('state',{}).get('products',[])))
"

echo ""

# ============================================
# TEST 2: Direct Product Request
# ============================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST 2: Direct Product Request"
echo -e "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

SESSION2="direct_$(date +%s)"

echo -e "\n${YELLOW}👤 User: Show me navy blue suits for walima${NC}"
RESPONSE=$(curl -s -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d "{\"session_id\": \"$SESSION2\", \"message\": \"Show me navy blue suits for walima\"}")
echo "$RESPONSE" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print('🤖 Bot:', d['reply'][:400])
print('📦 Products:', len(d.get('state',{}).get('products',[])))
for p in d.get('state',{}).get('products',[])[:3]:
    print(f'   • {p.get(\"name\")} - {p.get(\"currency\",\"PKR\")} {p.get(\"price\",0):,.0f}')
"

echo ""

# ============================================
# TEST 3: Negotiation Flow
# ============================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST 3: Negotiation Flow"
echo -e "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

SESSION3="nego_$(date +%s)"

echo -e "\n${YELLOW}👤 User: barat k liye ivory sherwani dikhao${NC}"
curl -s -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d "{\"session_id\": \"$SESSION3\", \"message\": \"barat k liye ivory sherwani dikhao\"}" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print('🤖 Bot:', d['reply'][:250]); print('📦 Products:', len(d.get('state',{}).get('products',[])))"

sleep 1

echo -e "\n${YELLOW}👤 User: pehli wali achi hai, price kya hai?${NC}"
curl -s -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d "{\"session_id\": \"$SESSION3\", \"message\": \"pehli wali achi hai, price kya hai?\"}" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print('🤖 Bot:', d['reply'][:250])"

sleep 1

echo -e "\n${YELLOW}👤 User: ye thori mehngi hai, kuch discount milega?${NC}"
curl -s -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d "{\"session_id\": \"$SESSION3\", \"message\": \"ye thori mehngi hai, kuch discount milega?\"}" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print('🤖 Bot:', d['reply'][:300])"

sleep 1

echo -e "\n${YELLOW}👤 User: 100000 mein ho jayegi?${NC}"
curl -s -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d "{\"session_id\": \"$SESSION3\", \"message\": \"100000 mein ho jayegi?\"}" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print('🤖 Bot:', d['reply'][:300])"

echo ""

# ============================================
# TEST 4: Inventory Check
# ============================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST 4: Inventory Check"
echo -e "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

SESSION4="inv_$(date +%s)"

echo -e "\n${YELLOW}👤 User: maroon sherwani dikhao${NC}"
curl -s -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d "{\"session_id\": \"$SESSION4\", \"message\": \"maroon sherwani dikhao\"}" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print('🤖 Bot:', d['reply'][:200])"

sleep 1

echo -e "\n${YELLOW}👤 User: size 42 mein maroon color available hai?${NC}"
curl -s -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d "{\"session_id\": \"$SESSION4\", \"message\": \"size 42 mein maroon color available hai?\"}" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print('🤖 Bot:', d['reply'][:250])"

echo ""

# ============================================
# TEST 5: Fabric Search
# ============================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST 5: Fabric Search"
echo -e "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

SESSION5="fabric_$(date +%s)"

echo -e "\n${YELLOW}👤 User: mujhe custom sherwani banwani hai, velvet fabric dikhao${NC}"
curl -s -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d "{\"session_id\": \"$SESSION5\", \"message\": \"mujhe custom sherwani banwani hai, velvet fabric dikhao\"}" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print('🤖 Bot:', d['reply'][:300])"

echo ""

# ============================================
# TEST 6: Handover Request
# ============================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TEST 6: Handover to Consultant"
echo -e "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

SESSION6="handover_$(date +%s)"

echo -e "\n${YELLOW}👤 User: mujhe consultant se baat karni hai${NC}"
curl -s -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d "{\"session_id\": \"$SESSION6\", \"message\": \"mujhe consultant se baat karni hai\"}" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print('🤖 Bot:', d['reply'][:250])"

sleep 1

echo -e "\n${YELLOW}👤 User: mera naam Ali hai aur number 03001234567${NC}"
curl -s -X POST "$BASE_URL/chat" \
  -H "Content-Type: application/json" \
  -d "{\"session_id\": \"$SESSION6\", \"message\": \"mera naam Ali hai aur number 03001234567\"}" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print('🤖 Bot:', d['reply'][:300])"

echo ""
echo -e "${GREEN}=========================================="
echo "✅ All Tests Completed!"
echo -e "==========================================${NC}"
