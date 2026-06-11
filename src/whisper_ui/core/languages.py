"""Language and model constants for Whisper UI."""

from __future__ import annotations

# Sentinel for "let the model detect the language". Kept out of
# SUPPORTED_LANGUAGES so consumers that treat that list as real ISO codes
# (e.g. alignment-model lookups) never see it; user-facing forms offer
# LANGUAGE_CHOICES instead.
AUTO_LANGUAGE = "auto"

SUPPORTED_LANGUAGES: list[str] = [
    "zh",
    "en",
    "ja",
    "ko",
    "fr",
    "de",
    "es",
    "pt",
    "it",
    "nl",
    "ru",
    "pl",
    "uk",
    "ar",
    "hi",
    "th",
    "vi",
    "id",
    "ms",
    "tr",
    "sv",
    "da",
    "no",
    "fi",
    "cs",
    "sk",
    "el",
    "ro",
    "hu",
    "bg",
    "hr",
    "he",
    "ca",
    "ta",
]

LANGUAGE_CHOICES: list[str] = [AUTO_LANGUAGE, *SUPPORTED_LANGUAGES]

LANGUAGE_LABELS: dict[str, str] = {
    "auto": "自動偵測 Auto-detect (auto)",
    "zh": "中文 Chinese (zh)",
    "en": "English (en)",
    "ja": "日本語 Japanese (ja)",
    "ko": "한국어 Korean (ko)",
    "fr": "Français (fr)",
    "de": "Deutsch (de)",
    "es": "Español (es)",
    "pt": "Português (pt)",
    "it": "Italiano (it)",
    "nl": "Nederlands (nl)",
    "ru": "Русский (ru)",
    "pl": "Polski (pl)",
    "uk": "Українська (uk)",
    "ar": "العربية (ar)",
    "hi": "हिन्दी (hi)",
    "th": "ไทย (th)",
    "vi": "Tiếng Việt (vi)",
    "id": "Bahasa Indonesia (id)",
    "ms": "Bahasa Melayu (ms)",
    "tr": "Türkçe (tr)",
    "sv": "Svenska (sv)",
    "da": "Dansk (da)",
    "no": "Norsk (no)",
    "fi": "Suomi (fi)",
    "cs": "Čeština (cs)",
    "sk": "Slovenčina (sk)",
    "el": "Ελληνικά (el)",
    "ro": "Română (ro)",
    "hu": "Magyar (hu)",
    "bg": "Български (bg)",
    "hr": "Hrvatski (hr)",
    "he": "עברית (he)",
    "ca": "Català (ca)",
    "ta": "தமிழ் (ta)",
}

WHISPER_MODELS: list[str] = [
    "tiny",
    "tiny.en",
    "base",
    "base.en",
    "small",
    "small.en",
    "medium",
    "medium.en",
    "large-v1",
    "large-v2",
    "large-v3",
    "large-v3-turbo",
]

# Single source of truth for the default model. Settings, the Job dataclass,
# the TranscribeStage, and every upload/retry form default reference this so
# changing the shipped default is a one-line edit.
DEFAULT_WHISPER_MODEL = "large-v3"
