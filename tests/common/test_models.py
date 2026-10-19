from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from common.errors import AppError, ErrorCode
from common.models import Article, Stage, StageResult


def test_article_rejects_extra_fields():
    with pytest.raises(ValidationError):
        Article(
            site_id="s",
            article_id="1",
            title="t",
            url="https://a.example.com/1",
            body="b",
            cookie="sid=x",
        )


def test_stage_result_defaults_and_serialization():
    result = StageResult(
        run_id="r1",
        owner="zack",
        stage=Stage.LAYER1,
        pipeline_id="p",
        generated_at=datetime(2026, 10, 19, tzinfo=UTC),
    )
    dumped = result.model_dump(mode="json")
    assert dumped["stage"] == "layer1"
    assert dumped["stats"] == {"fetched": 0, "kept": 0, "failed_sites": []}


def test_stage_rejects_unknown():
    with pytest.raises(ValidationError):
        StageResult(
            run_id="r",
            owner="o",
            stage="layer9",
            pipeline_id="p",
            generated_at=datetime.now(UTC),
        )


def test_app_error_carries_code():
    err = AppError(ErrorCode.STAGE_NOT_SUPPORTED)
    assert err.code == ErrorCode.STAGE_NOT_SUPPORTED
    assert str(err) == "STAGE_NOT_SUPPORTED"
