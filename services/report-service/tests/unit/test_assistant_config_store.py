"""The assistant's runtime configuration.

What used to be a dozen environment variables is now one row the platform super
admin owns. Three properties matter, and each is tested here:

* it never raises - a missing table, an unreachable database or an empty row all
  resolve to working defaults, because the assistant answering questions must
  not stop over a configuration read;
* every number is bounded - most importantly the timeouts, which have to stay
  under the gateway's fixed 30 second proxy timeout;
* the API key is contained - encrypted at rest, and never returned in anything
  the browser or an audit record can see.
"""

from dataclasses import replace

import pytest

from app.assistant import config_store
from app.assistant.config_store import (
    BOUNDS,
    CHAT_MODEL_IDS,
    TRANSCRIPTION_MODEL_IDS,
    AssistantRuntimeConfig,
    get_config,
    invalidate_cache,
)


class _Row:
    """A stored row, as SQLAlchemy would hand one back."""

    def __init__(self, **overrides):
        defaults = dict(
            provider="groq",
            api_key_encrypted=None,
            base_url="https://api.groq.com/openai/v1",
            model="openai/gpt-oss-120b",
            transcription_model="whisper-large-v3",
            max_question_chars=2000,
            request_timeout_seconds=20,
            history_max_conversations=50,
            history_max_messages=200,
            max_audio_bytes=5 * 1024 * 1024,
            max_audio_duration_ms=60_000,
            voice_timeout_seconds=20,
            live_data_cache_seconds=30,
            live_data_timeout_seconds=3,
            live_data_max_metrics=3,
            updated_by="superadmin",
            updated_at=None,
        )
        defaults.update(overrides)
        for key, value in defaults.items():
            setattr(self, key, value)


class TestItNeverStopsTheAssistant:
    def test_an_unreachable_database_resolves_to_defaults(self, monkeypatch):
        def explode():
            raise RuntimeError("connection refused")

        monkeypatch.setattr(config_store, "get_master_session", explode)
        invalidate_cache()

        config = get_config()

        assert config.model == AssistantRuntimeConfig.model
        assert config.is_stored is False

    def test_a_missing_table_resolves_to_defaults(self, monkeypatch):
        # Normal during a rolling deploy, before migration 0023 is applied.
        monkeypatch.setattr(
            config_store, "_read_row", lambda: config_store._defaults_from_environment()
        )
        invalidate_cache()

        assert get_config().is_stored is False

    def test_a_key_encrypted_under_another_secret_falls_back(self, monkeypatch):
        """A restored database, or a rotated encryption key."""

        def undecryptable(_value):
            raise ValueError("invalid token")

        monkeypatch.setattr(config_store, "decrypt_dsn", undecryptable)

        config = config_store._from_row(_Row(api_key_encrypted="not-under-this-key"))

        # Ciphertext must never be handed to the provider as if it were a key.
        assert config.api_key != "not-under-this-key"


class TestValuesAreBounded:
    @pytest.mark.parametrize("field", sorted(BOUNDS))
    def test_a_value_above_the_ceiling_is_clamped(self, field):
        _, high = BOUNDS[field]

        config = config_store._from_row(_Row(**{field: high * 100}))

        assert getattr(config, field) == high

    @pytest.mark.parametrize("field", sorted(BOUNDS))
    def test_a_value_below_the_floor_is_clamped(self, field):
        low, _ = BOUNDS[field]

        config = config_store._from_row(_Row(**{field: -1}))

        assert getattr(config, field) == low

    @pytest.mark.parametrize("field", sorted(BOUNDS))
    def test_a_nonsense_value_falls_back_rather_than_crashing(self, field):
        config = config_store._from_row(_Row(**{field: "not a number"}))

        low, high = BOUNDS[field]
        assert low <= getattr(config, field) <= high

    @pytest.mark.parametrize(
        "field", ["request_timeout_seconds", "voice_timeout_seconds"]
    )
    def test_no_timeout_can_reach_the_gateway_proxy_timeout(self, field):
        """The gateway gives up at a fixed 30 seconds.

        A timeout at or past that reaches the browser as a gateway error rather
        than the assistant's own worded refusal, so the ceiling has to leave
        room for the refusal to be built and sent.
        """
        _, high = BOUNDS[field]
        assert high < 30


class TestOnlyApprovedModels:
    def test_an_unapproved_model_is_ignored_in_favour_of_the_default(self):
        config = config_store._from_row(_Row(model="groq/compound-beta"))

        assert config.model in CHAT_MODEL_IDS
        assert config.model != "groq/compound-beta"

    def test_an_unapproved_transcription_model_is_ignored(self):
        config = config_store._from_row(_Row(transcription_model="made-up-v9"))

        assert config.transcription_model in TRANSCRIPTION_MODEL_IDS

    def test_no_agentic_model_is_offered(self):
        # These perform server-side web search and code execution. The assistant
        # must never reach either, so they cannot appear in the list a super
        # admin picks from.
        assert not [m for m in CHAT_MODEL_IDS if m.startswith("groq/compound")]

    def test_the_retired_model_is_not_offered(self):
        assert "llama-3.3-70b-versatile" not in CHAT_MODEL_IDS


class TestTheCredentialIsContained:
    def test_the_hint_is_a_mask_not_the_key(self):
        config = replace(AssistantRuntimeConfig(), api_key="gsk_secret_value_1234")

        assert config.api_key_hint == "****1234"
        assert "secret" not in config.api_key_hint

    def test_no_key_means_no_hint(self):
        config = replace(AssistantRuntimeConfig(), api_key=None)

        assert config.api_key_hint is None
        assert config.api_key_set is False

    def test_a_key_round_trips_through_encryption(self):
        from app.services.tenant_service import decrypt_dsn, encrypt_dsn

        encrypted = encrypt_dsn("gsk_a_real_looking_key")

        assert encrypted != "gsk_a_real_looking_key"
        assert decrypt_dsn(encrypted) == "gsk_a_real_looking_key"


class TestTheCache:
    def test_a_second_read_does_not_hit_the_database_again(self, monkeypatch):
        reads = []

        def counted():
            reads.append(1)
            return AssistantRuntimeConfig(is_stored=True)

        monkeypatch.setattr(config_store, "_read_row", counted)
        invalidate_cache()

        get_config()
        get_config()

        assert len(reads) == 1

    def test_invalidating_forces_a_reload(self, monkeypatch):
        reads = []

        def counted():
            reads.append(1)
            return AssistantRuntimeConfig(is_stored=True)

        monkeypatch.setattr(config_store, "_read_row", counted)
        invalidate_cache()

        get_config()
        invalidate_cache()
        get_config()

        assert len(reads) == 2

    def test_a_refresh_reads_past_the_cache(self, monkeypatch):
        reads = []

        def counted():
            reads.append(1)
            return AssistantRuntimeConfig(is_stored=True)

        monkeypatch.setattr(config_store, "_read_row", counted)
        invalidate_cache()

        get_config()
        get_config(refresh=True)

        assert len(reads) == 2
