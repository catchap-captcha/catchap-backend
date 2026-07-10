import json

from pydantic import BaseModel, Field, field_validator


class SettingsSave(BaseModel):
    settings: dict

    @field_validator("settings")
    @classmethod
    def _limit_size(cls, v: dict) -> dict:
        # 임의 대용량 JSON blob 저장(스토리지 남용) 차단 — 직렬화 크기·키 수 상한
        if len(v) > 100:
            raise ValueError("설정 항목이 너무 많습니다.")
        if len(json.dumps(v, ensure_ascii=False)) > 20_000:
            raise ValueError("설정 데이터가 너무 큽니다.")
        return v


class ChangePasswordRequest(BaseModel):
    # 강제 변경(임시 비번 첫 로그인) 흐름에선 현재 비번을 다시 받지 않으므로 선택값.
    current_password: str | None = None
    new_password: str = Field(min_length=8, max_length=100)
