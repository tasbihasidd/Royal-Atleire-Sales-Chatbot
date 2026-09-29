"""
Main entry point for running the Royal Atelier E2E Persona Test Suite.
"""
import asyncio
import logging
import sys
import uuid
from pathlib import Path
from colorama import init, Fore, Style

# Add the project root to sys.path so we can import 'tests'
project_root = str(Path(__file__).resolve().parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tests.e2e_personas import chatbot_client
from tests.e2e_personas.personas import ALL_PERSONAS
from tests.e2e_personas.conversation_runner import run_persona_conversation
from tests.e2e_personas.report_generator import generate_report

# Setup basic logging to file for debug info, keep console clean
logging.basicConfig(
    filename='test_suite_debug.log',
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

init(autoreset=True)

async def main():
    print(f"{Fore.CYAN}{Style.BRIGHT}======================================================================")
    print(f"{Fore.CYAN}{Style.BRIGHT}      🧪 Royal Atelier Agentic E2E Test Suite (Live LLM Judge)      ")
    print(f"{Fore.CYAN}{Style.BRIGHT}======================================================================")
    
    # 1. Health check
    print(f"\n{Fore.YELLOW}Checking if Chatbot is running at localhost:8015...")
    is_healthy = await chatbot_client.health_check()
    if not is_healthy:
        print(f"{Fore.RED}❌ Chatbot server is not responding at localhost:8015/health")
        print(f"{Fore.RED}Please start it in another terminal: python -m uvicorn app.main:app --host 0.0.0.0 --port 8015")
        sys.exit(1)
    
    print(f"{Fore.GREEN}✅ Chatbot is running.")
    
    # User requested NOT to clear sessions, so we skip clear-all here.
    print(f"{Fore.BLUE}ℹ️  Preserving previous sessions as requested.")

    all_results = []
    
    # 2. Run each persona sequentially
    for persona in ALL_PERSONAS:
        # Generate a unique session ID for this test run
        session_id = f"e2e_{persona.name.lower()}_{uuid.uuid4().hex[:8]}"
        
        result = await run_persona_conversation(persona, session_id)
        all_results.append(result)
        
        # Small delay between personas
        await asyncio.sleep(2)
        
    # 3. Generate Report
    print(f"\n{Fore.CYAN}{Style.BRIGHT}======================================================================")
    print(f"{Fore.CYAN}Generating Diagnostic Reports...")
    md_path, json_path = generate_report(all_results)
    print(f"{Fore.GREEN}✅ Markdown Report: {md_path}")
    print(f"{Fore.GREEN}✅ JSON Report: {json_path}")
    
    print(f"\n{Fore.YELLOW}{Style.BRIGHT}Test Sessions Used (for manual review):")
    for r in all_results:
        print(f"  - {r.persona_name}: {r.session_id}")

    print(f"\n{Fore.CYAN}{Style.BRIGHT}======================================================================")
    print(f"{Fore.GREEN}{Style.BRIGHT}Test Suite Completed Successfully!")
    print(f"{Fore.CYAN}{Style.BRIGHT}======================================================================")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{Fore.RED}Test suite aborted by user.")
        sys.exit(1)
