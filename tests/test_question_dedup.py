"""생성 문항 중복 감지(문제은행 2단계) — 유사/동일 프롬프트 문항을 생성 단계에서 제외.

대량 생성·같은 강의 반복 생성 시 유사 문항이 검수 대기·문제은행을 오염시키는 것을 막는다.
_dedupe_generated가 verify 전에 걸러(검증 LLM 비용도 절약) created_count에 반영된다.
"""
import app.clients.ai_client as ai_client
from app.core.config import get_settings
from tests.test_ai_settings import _gen_now
from tests.test_captcha_api import _instructor, _ops, auth


def _setup_key(client, db, monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", "")
    monkeypatch.setattr(get_settings(), "LECTURE_MEDIA_DIR", str(tmp_path))
    ops_tok = _ops(client, db)
    itok = _instructor(client, db)
    client.put(
        "/api/v1/ops/settings/ai",
        json={"anthropic_api_key": "sk-console-key-7777"},
        headers=auth(ops_tok),
    )
    # 검증은 모든 문항 bank로(중복 로직만 검증) — 개수는 items에 맞춰 동적으로
    monkeypatch.setattr(
        ai_client,
        "verify_questions",
        lambda items, **k: [
            {"blind_passed": True, "transcript_passed": None, "verdict": "bank"} for _ in items
        ],
    )
    return itok


def _mk_lecture(client, itok, title):
    up = client.post(
        "/api/v1/ops/lectures",
        data={"title": title, "subject": "국어", "duration_sec": "300"},
        files={"file": ("v.mp4", b"0" * 1024, "video/mp4")},
        headers=auth(itok),
    )
    return up.json()["id"]


def test_duplicate_in_batch_skipped(client, db, monkeypatch, tmp_path):
    itok = _setup_key(client, db, monkeypatch, tmp_path)
    monkeypatch.setattr(
        ai_client,
        "generate_lecture_questions",
        lambda **k: [
            {"prompt": "이벤트 로그 저장 주기는 무엇인가요?", "options": ["가", "나", "다", "라"], "answer_index": 0, "explain": ""},
            {"prompt": "이벤트 로그 저장 주기는 무엇인가요?", "options": ["A", "B", "C", "D"], "answer_index": 1, "explain": ""},  # 동일 프롬프트(다른 보기)
            {"prompt": "조직과 프로젝트 이벤트를 볼 수 있는 역할 조합은 무엇인가요?", "options": ["마", "바", "사", "아"], "answer_index": 0, "explain": ""},
        ],
    )
    lec_id = _mk_lecture(client, itok, "중복 강의")
    body = _gen_now(db, lec_id, 3)
    assert body["created"] == 2, body  # 3개 중 중복 1개 제외
    prompts = [q["prompt"] for q in body["questions"]]
    assert prompts.count("이벤트 로그 저장 주기는 무엇인가요?") == 1
    assert "조직과 프로젝트 이벤트를 볼 수 있는 역할 조합은 무엇인가요?" in prompts


def test_dedup_against_existing_lecture_questions(client, db, monkeypatch, tmp_path):
    """이미 그 강의에 있는 문항과 유사하면 새 생성분도 제외된다(반복 생성 방어)."""
    itok = _setup_key(client, db, monkeypatch, tmp_path)
    lec_id = _mk_lecture(client, itok, "반복 생성 강의")
    monkeypatch.setattr(
        ai_client, "generate_lecture_questions",
        lambda **k: [{"prompt": "저장 주기는 무엇인가요?", "options": ["가", "나"], "answer_index": 0, "explain": ""}],
    )
    b1 = _gen_now(db, lec_id, 1)
    assert b1["created"] == 1
    # 2차: 같은 프롬프트 재생성 → 기존과 중복이라 제외 → 0개
    monkeypatch.setattr(
        ai_client, "generate_lecture_questions",
        lambda **k: [{"prompt": "저장 주기는 무엇인가요?", "options": ["다", "라"], "answer_index": 1, "explain": ""}],
    )
    b2 = _gen_now(db, lec_id, 1)
    assert b2["created"] == 0, b2


def test_distinct_questions_all_kept(client, db, monkeypatch, tmp_path):
    """서로 다른 문항은 그대로 유지(오탐 방지)."""
    itok = _setup_key(client, db, monkeypatch, tmp_path)
    monkeypatch.setattr(
        ai_client, "generate_lecture_questions",
        lambda **k: [
            {"prompt": "저장 주기는 무엇인가요?", "options": ["가", "나"], "answer_index": 0, "explain": ""},
            {"prompt": "필터 속성 값은 무엇인가요?", "options": ["다", "라"], "answer_index": 1, "explain": ""},
            {"prompt": "역할 조합은 무엇인가요?", "options": ["마", "바"], "answer_index": 0, "explain": ""},
        ],
    )
    lec_id = _mk_lecture(client, itok, "고유 강의")
    body = _gen_now(db, lec_id, 3)
    assert body["created"] == 3, body
