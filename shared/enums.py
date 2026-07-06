from enum import Enum


class AIProvider(str, Enum):
    CLAUDE = "claude"
    GEMINI = "gemini"


class ClaudeModel(str, Enum):
    HAIKU  = "claude-haiku-4-5"
    SONNET = "claude-sonnet-4-6"


class GeminiModel(str, Enum):
    FLASH    = "gemini-2.5-flash"
    PRO      = "gemini-2.5-pro"
    FLASH_20 = "gemini-2.0-flash"
