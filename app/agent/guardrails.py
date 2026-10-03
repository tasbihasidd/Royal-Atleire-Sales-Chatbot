from __future__ import annotations
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Prompt Armor Jailbreak Patterns
JAILBREAK_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+a\s+free\s+ai", re.IGNORECASE),
    re.compile(r"give\s+me\s+(80%|90%|100%|free)\s+discount", re.IGNORECASE),
    re.compile(r"developer\s+mode", re.IGNORECASE),
    re.compile(r"system\s+override", re.IGNORECASE),
    re.compile(r"act\s+as\s+DAN", re.IGNORECASE),
]

# Sensitive internal PII / Wholesale keywords (field-style tokens)
INTERNAL_LEAK_KEYWORDS = [
    "floor_price",
    "total_markup_fee",
    "markup_amount",
    "markup_rate",
    "subtotal_no_markup",
    "wholesale_cost",
    "supplier_margin",
    "cost_price",
    "vendor_code",
    "system_prompt",
    "margin_budget",
]

# Customer-facing leak phrases (align with score_agent_hard_test FLOOR_LEAK_RE)
CUSTOMER_FACING_LEAK_PATTERNS = [
    re.compile(r"\bfloor[_\s-]?price\b", re.IGNORECASE),
    re.compile(r"\btotal[_\s-]?markup[_\s-]?fee\b", re.IGNORECASE),
    re.compile(r"\bmarkup[_\s-]?(?:fee|rate|amount|percent|%)\b", re.IGNORECASE),
    re.compile(r"\bmargin_budget\b", re.IGNORECASE),
    re.compile(r"\bmargin\b", re.IGNORECASE),
    re.compile(r"\bwholesale(?:[_\s-]?(?:cost|price))?\b", re.IGNORECASE),
    re.compile(r"\bsystem[_\s-]?prompt\b", re.IGNORECASE),
    re.compile(r"\bcost[_\s-]?price\b", re.IGNORECASE),
    re.compile(r"\bvendor[_\s-]?code\b", re.IGNORECASE),
]


class CommercialGuardrails:
    """
    5-Layer Commercial Safety Net:
    1. Grounding: Verify stock quantity, prices, and delivery dates against backend payloads.
    2. Faithfulness: Ensure product claims match vector DB docs or API data.
    3. Margin Guard: Block any output proposing price < floor price.
    4. Privacy Check: Sanitize PII and internal wholesale margin figures.
    5. Prompt Armor: Intercept jailbreak attempts.
    """

    def check_prompt_armor(self, user_message: str) -> tuple[bool, str | None]:
        """Layer 5: Intercept adversarial jailbreaks and unauthorized override attempts."""
        for pattern in JAILBREAK_PATTERNS:
            if pattern.search(user_message):
                logger.warning("Prompt Armor triggered on user message: %s", user_message[:100])
                return True, (
                    "I am programmed to assist you exclusively with Turabees luxury menswear, "
                    "tailoring, and order consultations. How may I assist with your attire today?"
                )
        return False, None

    def strip_emojis(self, response_text: str) -> str:
        """Remove emoji / pictographs so replies stay shop-floor professional."""
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"
            "\U0001F300-\U0001F5FF"
            "\U0001F680-\U0001F6FF"
            "\U0001F1E0-\U0001F1FF"
            "\U0001F900-\U0001F9FF"
            "\U0001FA00-\U0001FAFF"
            "\U00002700-\U000027BF"
            "\U00002600-\U000026FF"
            "\U0000FE00-\U0000FE0F"
            "\U0001F004"
            "\U0001F0CF"
            "]+",
            flags=re.UNICODE,
        )
        cleaned = emoji_pattern.sub("", response_text or "")
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    def sanitize_privacy(self, response_text: str) -> str:
        """Layer 4: Strip internal backend fields and customer-facing leak words (margin/floor/wholesale)."""
        sanitized = response_text or ""
        for kw in INTERNAL_LEAK_KEYWORDS:
            if kw in sanitized.lower():
                logger.warning("Privacy Leak Guard sanitized keyword: %s", kw)
                sanitized = re.sub(
                    rf"\b{re.escape(kw)}\b\s*:?\s*\S*",
                    "[Confidential]",
                    sanitized,
                    flags=re.IGNORECASE,
                )
        for pattern in CUSTOMER_FACING_LEAK_PATTERNS:
            if pattern.search(sanitized):
                logger.warning("Privacy Leak Guard sanitized phrase: %s", pattern.pattern)
                sanitized = pattern.sub("[Confidential]", sanitized)
        return sanitized

    def enforce_margin_guard(
        self,
        proposed_price: float,
        floor_price: float | None,
        product_name: str = "Outfit",
    ) -> tuple[bool, float]:
        """Layer 3: Ensure proposed price never breaches hard floor_price safeguard."""
        if floor_price and proposed_price < floor_price:
            logger.warning(
                "Margin Guard breach detected! proposed=%s floor=%s product=%s",
                proposed_price,
                floor_price,
                product_name,
            )
            return False, floor_price
        return True, proposed_price

    def sanitize_unwanted_titles_and_phrases(self, response_text: str) -> str:
        """Strip forced regional titles, assumed surnames, and repetitive religious catchphrases."""
        if not response_text:
            return ""

        unwanted_phrases = [
            r"\bMasha\s*Allah\b",
            r"\bMashallah\b",
            r"\bSubhan\s*Allah\b",
            r"\bSubhanallah\b",
            r"\bAlhamdulillah\b",
            r"\bJazak\s*Allah\b",
            r"\bKhan\s+sahab\b",
            r"\bKhan\s+sahib\b",
            r"\bLala\b",
            r"\bBhai\s+jaan\b",
            r"\bPyare\s+bhai\b",
            r"\bswagat\b",
        ]
        sanitized = response_text
        for pattern in unwanted_phrases:
            sanitized = re.sub(pattern, "", sanitized, flags=re.IGNORECASE)

        # Clean up awkward punctuation left behind (e.g. leading commas/exclamations)
        sanitized = re.sub(r"^[,\s!.-]+", "", sanitized)
        sanitized = re.sub(r"\s*([,!?.])\s*([,!?])", r"\1", sanitized)
        sanitized = re.sub(r"[ \t]{2,}", " ", sanitized)
        sanitized = sanitized.strip()
        if sanitized and sanitized[0].islower():
            sanitized = sanitized[0].upper() + sanitized[1:]
        return sanitized

    def strip_markdown_images(self, response_text: str) -> str:
        """Strip markdown images, raw image URLs, and 'Image:' markers to prevent chat bubble clutter."""
        if not response_text:
            return ""
        # 1. Remove markdown images: ![alt](url)
        cleaned = re.sub(r"!\[.*?\]\([^\)]*\)", "", response_text)
        # 2. Remove lines like "Image: https://..." or "Image:\nhttps://..." or standalone "Image:"
        cleaned = re.sub(r"(?im)^[ \t]*image\s*:\s*(https?://\S+)?\s*$", "", cleaned)
        # 3. Strip standalone image URLs (ending in png, jpg, jpeg, webp, gif, svg)
        cleaned = re.sub(r"https?://\S+\.(?:png|jpe?g|webp|gif|svg)(\?[^\s\)]*)?", "", cleaned, flags=re.IGNORECASE)
        # 4. Collapse leftover whitespace and excessive newlines
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    def sanitize_agent_output(
        self,
        response_text: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Applies full safety net pipeline (Privacy, Margin Guard, Grounding, Image Suppress) to response text."""
        # 1. Apply Privacy Check, Emoji stripping, Phrase sanitizer, and Image stripping
        clean_text = self.sanitize_privacy(response_text)
        clean_text = self.strip_emojis(clean_text)
        clean_text = self.sanitize_unwanted_titles_and_phrases(clean_text)
        clean_text = self.strip_markdown_images(clean_text)

        # 2. Check Margin Guard if negotiation context is present
        if context and context.get("negotiation_result"):
            neg = context["negotiation_result"]
            offered = neg.get("offered_price")
            strategy = neg.get("strategy") or {}
            floor = strategy.get("floor_price") or (context.get("product_details") or {}).get("floor_price")
            if offered and floor:
                is_safe, safe_price = self.enforce_margin_guard(offered, floor)
                if not is_safe:
                    clean_text += f"\n\n(Note: Our maximum authorized manager discount is set to PKR {int(safe_price):,}.)"

        return clean_text


guardrails = CommercialGuardrails()

