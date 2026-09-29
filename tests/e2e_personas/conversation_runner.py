"""
Core conversation loop — drives one persona through a full chat session.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from tests.e2e_personas.config import MAX_TURNS_PER_PERSONA
from tests.e2e_personas.personas import Persona
from tests.e2e_personas import chatbot_client, llm_client, judge

logger = logging.getLogger(__name__)


@dataclass
class TurnRecord:
    """Record for a single conversation turn."""
    turn_number: int
    user_message: str
    bot_reply: str
    bot_state: dict
    executed_nodes: list[str]
    image_url: str
    verdict: dict
    latency_ms: int


@dataclass
class ConversationResult:
    """Complete result of one persona's conversation."""
    persona_name: str
    persona_description: str
    session_id: str
    turns: list[TurnRecord] = field(default_factory=list)
    total_score: float = 0.0
    pass_rate: float = 0.0
    defects: list[dict] = field(default_factory=list)


async def run_persona_conversation(persona: Persona, session_id: str) -> ConversationResult:
    """
    Run a full conversation between a synthetic persona and the live chatbot.

    Loop:
    1. Ask DeepSeek (as persona) to generate next user message
    2. Send message to live chatbot
    3. Ask Judge to evaluate the turn
    4. Feed chatbot response back to persona for next turn
    5. Repeat until persona signals [END] or max_turns hit
    """
    result = ConversationResult(
        persona_name=persona.name,
        persona_description=persona.description,
        session_id=session_id,
    )

    # Conversation history for the persona LLM (so it knows what happened before)
    persona_history: list[dict] = []
    # Full conversation for the judge
    full_conversation: list[dict] = []

    print(f"\n{'='*70}")
    print(f"  PERSONA: {persona.name} — {persona.description}")
    print(f"  SESSION: {session_id}")
    print(f"{'='*70}")

    for turn_num in range(1, MAX_TURNS_PER_PERSONA + 1):
        # ── Step 1: Generate user message ────────────────────────────
        try:
            user_msg = await llm_client.generate_user_message(
                system_prompt=persona.system_prompt,
                conversation_history=persona_history,
            )
        except Exception as e:
            logger.error("Persona %s turn %d: LLM failed: %s", persona.name, turn_num, e)
            print(f"  ❌ Turn {turn_num}: Persona LLM failed: {e}")
            break

        # Check for end signal
        if "[END]" in user_msg.upper():
            # Strip the end marker and send final message if there's content
            clean_msg = user_msg.replace("[END]", "").replace("[end]", "").strip()
            if clean_msg:
                user_msg = clean_msg
            else:
                print(f"  🏁 Persona {persona.name} ended conversation after {turn_num - 1} turns")
                break

        print(f"\n  ┌─ Turn {turn_num} ────────────────────────────────────")
        print(f"  │ 👤 {persona.name}: {user_msg}")

        # ── Step 2: Send to chatbot ──────────────────────────────────
        t0 = time.monotonic()
        try:
            bot_response = await chatbot_client.send_message(session_id, user_msg)
        except Exception as e:
            logger.error("Chatbot call failed turn %d: %s", turn_num, e)
            print(f"  │ ❌ Chatbot error: {e}")
            break
        latency_ms = int((time.monotonic() - t0) * 1000)

        bot_reply = bot_response.get("reply", "")
        bot_state = bot_response.get("state", {})
        executed_nodes = bot_response.get("executed_nodes", [])
        image_url = bot_response.get("imageurl", "")

        print(f"  │ 🤖 Bot: {bot_reply[:150]}{'...' if len(bot_reply) > 150 else ''}")
        print(f"  │ ⚙️  Nodes: {executed_nodes}")
        print(f"  │ ⏱️  {latency_ms}ms")

        # ── Step 3: Judge evaluates ──────────────────────────────────
        full_conversation.append({"role": "user", "content": user_msg})
        full_conversation.append({"role": "assistant", "content": bot_reply})

        try:
            verdict = await judge.evaluate_turn(
                persona_name=persona.name,
                persona_strategy=persona.system_prompt[:500],
                turn_number=turn_num,
                user_message=user_msg,
                bot_reply=bot_reply,
                bot_state=bot_state,
                executed_nodes=executed_nodes,
                conversation_so_far=full_conversation,
            )
        except Exception as e:
            logger.error("Judge failed turn %d: %s", turn_num, e)
            verdict = {
                "turn_number": turn_num,
                "persona": persona.name,
                "overall_pass": None,
                "score": 0,
                "dimensions": {},
                "defects": [f"Judge evaluation failed: {e}"],
                "suggestions": [],
            }

        # Print verdict summary
        v_pass = verdict.get("overall_pass")
        v_score = verdict.get("score", 0)
        pass_icon = "✅" if v_pass else ("❌" if v_pass is False else "⚠️")
        print(f"  │ {pass_icon} Judge: score={v_score}/10  pass={v_pass}")

        if verdict.get("defects"):
            for d in verdict["defects"]:
                print(f"  │ 🐛 DEFECT: {d}")

        # Record failed dimensions
        for dim_name, dim_data in verdict.get("dimensions", {}).items():
            if isinstance(dim_data, dict) and dim_data.get("pass") is False:
                print(f"  │ ⚠️  {dim_name}: {dim_data.get('note', '')}")

        print(f"  └────────────────────────────────────────────────────")

        # ── Step 4: Record turn ──────────────────────────────────────
        turn_record = TurnRecord(
            turn_number=turn_num,
            user_message=user_msg,
            bot_reply=bot_reply,
            bot_state=bot_state,
            executed_nodes=executed_nodes,
            image_url=image_url,
            verdict=verdict,
            latency_ms=latency_ms,
        )
        result.turns.append(turn_record)

        # Collect defects
        for defect in verdict.get("defects", []):
            result.defects.append({
                "turn": turn_num,
                "persona": persona.name,
                "defect": defect,
            })

        # ── Step 5: Feed response back to persona ───────────────────
        persona_history.append({"role": "assistant", "content": user_msg})
        persona_history.append({"role": "user", "content": f"[Chatbot replied]: {bot_reply}"})

        # Check for end signal in the user message we sent
        if "[END]" in user_msg.upper():
            print(f"  🏁 Persona {persona.name} ended conversation after {turn_num} turns")
            break

    # ── Compute summary stats ────────────────────────────────────────
    if result.turns:
        scores = [t.verdict.get("score", 0) for t in result.turns if t.verdict.get("score")]
        result.total_score = sum(scores) / len(scores) if scores else 0.0
        passes = [t.verdict.get("overall_pass") for t in result.turns]
        passed = sum(1 for p in passes if p is True)
        result.pass_rate = (passed / len(passes)) * 100 if passes else 0.0

    print(f"\n  📊 {persona.name} Summary: avg_score={result.total_score:.1f}/10  pass_rate={result.pass_rate:.0f}%  defects={len(result.defects)}")

    return result
