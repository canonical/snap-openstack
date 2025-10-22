# SPDX-FileCopyrightText: 2025 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

from typing import Annotated

import pydantic
import pytest

from sunbeam.core.questions import PasswordPromptQuestion, PromptQuestion
from sunbeam.storage.models import SecretDictField
from sunbeam.storage.steps import basemodel_validator, generate_questions_from_config


class SampleConfig(pydantic.BaseModel):
    required_field: Annotated[
        int,
        pydantic.Field(ge=1, description="A positive integer"),
    ]
    secret_field: Annotated[
        str,
        pydantic.Field(description="A secret value"),
        SecretDictField(field="secret"),
    ]
    optional_field: Annotated[
        int | None,
        pydantic.Field(ge=0, description="Optional value"),
    ] = None

    @pydantic.field_validator("secret_field")
    @classmethod
    def no_digits(cls, value: str) -> str:
        if any(ch.isdigit() for ch in value):
            raise ValueError("must not contain digits")
        return value

    @pydantic.model_validator(mode="after")
    def disallow_thirteen(self):
        if getattr(self, "required_field", None) == 13:
            raise ValueError("thirteen is not allowed")
        return self


class TestBasemodelValidator:
    def test_valid_and_invalid_values(self):
        field_validator = basemodel_validator(SampleConfig)

        # Valid value should pass without raising
        field_validator("required_field")(10)

        # Root validator error should be surfaced as ValueError
        with pytest.raises(ValueError, match="thirteen is not allowed"):
            field_validator("required_field")(13)

        # Field-level validation should be applied
        with pytest.raises(ValueError, match="must not contain digits"):
            field_validator("secret_field")("password1")

        # Type enforcement should be handled by pydantic
        with pytest.raises(ValueError):
            field_validator("required_field")("not-an-int")

    def test_unknown_field_raises_value_error(self):
        field_validator = basemodel_validator(SampleConfig)
        with pytest.raises(ValueError, match="has no field named"):
            field_validator("missing")


class TestGenerateQuestionsFromConfig:
    def test_required_questions_include_validation(self):
        questions = generate_questions_from_config(SampleConfig)

        assert set(questions.keys()) == {"required_field", "secret_field"}
        assert all(
            isinstance(question, (PromptQuestion, PasswordPromptQuestion))
            for question in questions.values()
        )

        secret_question = questions["secret_field"]
        assert isinstance(secret_question, PasswordPromptQuestion)
        with pytest.raises(ValueError, match="must not contain digits"):
            secret_question.validation_function("password1")  # type: ignore[arg-type]

        required_question = questions["required_field"]
        assert required_question.validation_function is not None
        with pytest.raises(ValueError):
            required_question.validation_function("bad")  # type: ignore[arg-type]

    def test_optional_questions_include_validation(self):
        questions = generate_questions_from_config(SampleConfig, optional=True)

        assert set(questions.keys()) == {"optional_field"}
        optional_question = questions["optional_field"]
        assert optional_question.validation_function is not None
        optional_question.validation_function(5)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            optional_question.validation_function(-1)  # type: ignore[arg-type]
