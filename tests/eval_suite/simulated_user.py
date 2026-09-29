"""
The simulated customer.

The previous harness fed history back with *swapped roles* (the persona's own
lines as `assistant`, the bot's replies as `user`). That confused the model into
emitting meta-commentary as if it were the user's message. Here the transcript is
rendered as plain text inside a single user message and the model is asked for one
line only, which is far more stable and lets us post-filter leakage.
"""
from __future__ import annotations

import logging
import re

from tests.eval_suite import config
from tests.eval_suite.llm import LLMError, call
from tests.eval_suite.models import LLMCall, Persona, Turn

logger = logging.getLogger(__name__)

END_TOKEN = "[END]"

# Meta-text that means the model broke character.
_LEAK_PREFIXES = re.compile(
    r"^\s*(?:okay|ok|sure|alright|here(?:'s| is)|as\s+\w+|response|message|user|customer|turn\s*\d+)\s*[:\-—]\s*",
    re.IGNORECASE,
)
_STAGE_DIRECTION = re.compile(r"^\s*[\(\[\*].*?[\)\]\*]\s*", re.DOTALL)


def _clean(text: str) -> str:
    """Strip role prefixes, quotes, stage directions and stray markdown."""
    out = text.strip()
    out = _STAGE_DIRECTION.sub("", out).strip()
    out = _LEAK_PREFIXES.sub("", out).strip()
    if len(out) > 1 and out[0] in "\"'“‘" and out[-1] in "\"'”’":
        out = out[1:-1].strip()
    # Models sometimes emit several candidate lines; a real user sends one message.
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if len(lines) > 3:
        lines = lines[:3]
    out = "\n".join(lines)
    return out.strip()


def _render_transcript(turns: list[Turn]) -> str:
    if not turns:
        return "(No messages yet — this is your very first message to the website chat.)"
    lines = []
    for turn in turns:
        lines.append(f"YOU TYPED: {turn.user_message}")
        lines.append(f"ASSISTANT REPLIED: {turn.bot_reply}")
    return "\n\n".join(lines)


async def next_message(persona: Persona, turns: list[Turn]) -> tuple[str, bool, LLMCall]:
    """Produce the persona's next message.

    Returns ``(message, should_end, telemetry)``.
    """
    system = f"""You are role-playing a real customer on the Royal Atelier / Turabees
wedding-menswear website chat widget.

YOUR CHARACTER:
{persona.strategy}"""

    turn_no = len(turns) + 1
    user = f"""Conversation so far:

{_render_transcript(turns)}

---
Write message number {turn_no} that you would now type into the chat box.
Remember: output ONLY the message text, nothing else."""

    try:
        text, telemetry = await call(
            config.USER_SIM_MODEL,
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            role="user_sim",
            temperature=config.USER_SIM_TEMPERATURE,
            max_tokens=config.USER_SIM_MAX_TOKENS,
        )
    except LLMError as exc:
        logger.error("Persona %s could not generate turn %s: %s", persona.name, turn_no, exc)
        return "", False, LLMCall(model=config.USER_SIM_MODEL, role="user_sim", error=str(exc)[:300])

    should_end = END_TOKEN.lower() in text.lower()
    message = _clean(re.sub(re.escape(END_TOKEN), "", text, flags=re.IGNORECASE))

    # A persona that ends with nothing left to say still needs a final utterance,
    # otherwise we would POST an empty message (a bug in the old harness).
    if not message and should_end:
        message = "theek hai, shukriya" if persona.language == "roman_urdu" else "Alright, thanks!"
    if not message:
        message = "acha" if persona.language == "roman_urdu" else "okay"

    return message, should_end, telemetry
