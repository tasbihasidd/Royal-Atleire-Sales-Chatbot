"""Shared pytest hooks for the Royal Atelier suite."""


def pytest_addoption(parser):
    parser.addoption(
        "--llm-provider",
        action="store",
        default=None,
        help="Live AI provider smoke: openrouter or deepseek",
    )
