"""Real calls (costs money). Needs OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY and model
names you have access to. Add price entries to your own prices.yaml to see costs.

    AIWATCH_PRICES=./prices.yaml uv run python examples/real_providers.py
"""

import os

import aiwatch

aiwatch.instrument()

if os.environ.get("OPENAI_API_KEY"):
    import openai

    openai.OpenAI().chat.completions.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[{"role": "user", "content": "Say hi in one word."}],
    )

if os.environ.get("ANTHROPIC_API_KEY"):
    import anthropic

    anthropic.Anthropic().messages.create(
        model=os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5"),
        max_tokens=20,
        messages=[{"role": "user", "content": "Say hi in one word."}],
    )

if os.environ.get("GEMINI_API_KEY"):
    from google import genai

    genai.Client().models.generate_content(
        model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"), contents="Say hi in one word."
    )
print("recorded; run `aiwatch report`")
