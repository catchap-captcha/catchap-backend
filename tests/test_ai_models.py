"""운영자 AI 모델 선택(#26) — 슬롯 해석·자동 스왑·토큰/추정비용·CRUD·생성 배선.

핵심 불변식:
- 슬롯 미설정 → 후보 0(호출자가 .env 폴백). 슬롯 모델 Off → 자동 스왑 여부에 따라 대체/없음.
- _post_messages 자동 스왑: 비200(429/529 등)은 다음 후보로, 200 거절/빈응답은 스왑 안 함.
- 삭제 시 그 모델을 가리키던 슬롯도 함께 비운다(죽은 포인터 방지).
- 같은 모델을 두 슬롯에 함께 배정할 수 있다(포인터를 settings에 둔 이유).
"""

import pytest

from app.models import AiModelConfig
from app.services import ai_models_service as svc

from tests.test_captcha_api import _instructor, _ops, auth


# ----------------------------------------------------------------- 서비스: 슬롯 해석
def _mk(db, name, model_id, *, enabled=True):
    m = AiModelConfig(provider="Anthropic", model_id=model_id, name=name, enabled=enabled)
    db.add(m)
    db.flush()
    return m


def test_resolve_candidates_slot_and_fallback(db):
    """슬롯 미설정=후보0, 배정=슬롯 우선, 자동스왑 off면 슬롯만."""
    assert svc.resolve_candidates(db, "generate") == []  # 모델·슬롯 없음
    a = _mk(db, "A", "claude-opus-4-8")
    b = _mk(db, "B", "claude-haiku-4-5")
    db.commit()

    # 아직 슬롯 미설정 + 자동스왑 off → 후보 없음(폴백 신호)
    assert svc.resolve_candidates(db, "generate") == []

    svc.set_slot(db, "generate", a.id, updated_by=None)
    db.commit()
    cands = svc.resolve_candidates(db, "generate")
    assert [m.id for m in cands] == [a.id]  # 자동스왑 off → 슬롯 모델만

    # 자동 스왑 on → 슬롯 모델 우선 + 나머지 켜진 모델
    svc.set_auto_swap(db, True, updated_by=None)
    db.commit()
    cands = svc.resolve_candidates(db, "generate")
    assert cands[0].id == a.id and set(m.id for m in cands) == {a.id, b.id}


def test_resolve_candidates_disabled_slot_model(db):
    """슬롯 모델이 Off면: 자동스왑 on이면 다른 켜진 모델, off면 후보 없음."""
    a = _mk(db, "A", "m-a", enabled=False)
    b = _mk(db, "B", "m-b", enabled=True)
    svc.set_slot(db, "verify", a.id, updated_by=None)
    db.commit()

    # 자동스왑 off + 슬롯 모델 Off → 후보 없음(폴백)
    assert svc.resolve_candidates(db, "verify") == []
    # 자동스왑 on → 꺼진 슬롯 모델 대신 켜진 b
    svc.set_auto_swap(db, True, updated_by=None)
    db.commit()
    assert [m.id for m in svc.resolve_candidates(db, "verify")] == [b.id]


def test_same_model_both_slots(db):
    """같은 모델을 생성·검증 두 슬롯에 함께 배정할 수 있다(포인터 방식의 목적)."""
    a = _mk(db, "A", "m-a")
    svc.set_slot(db, "generate", a.id, updated_by=None)
    svc.set_slot(db, "verify", a.id, updated_by=None)
    db.commit()
    assert svc.get_slot(db, "generate") == a.id
    assert svc.get_slot(db, "verify") == a.id
    assert [m.id for m in svc.resolve_candidates(db, "generate")] == [a.id]
    assert [m.id for m in svc.resolve_candidates(db, "verify")] == [a.id]


def test_record_usage_and_estimate_cost(db):
    """토큰 누적 + 추정 비용($/100만 토큰 단가)."""
    m = _mk(db, "A", "m-a")
    m.cost_in_usd = 5.0  # $5/1M in
    m.cost_out_usd = 25.0  # $25/1M out
    db.commit()
    svc.record_usage(db, m.id, 1_000_000, 200_000)
    svc.record_usage(db, m.id, 0, 0)  # 멱등 안전(음수 방어)
    db.commit()
    db.refresh(m)
    assert m.tokens_in == 1_000_000 and m.tokens_out == 200_000
    # 1M*$5 + 0.2M*$25 = 5 + 5 = 10
    assert svc.estimate_cost_usd(m) == 10.0
    # 없는 id는 조용히 무시(파이프라인 안 죽임)
    svc.record_usage(db, "nope", 10, 10)


# ----------------------------------------------------- _post_messages 자동 스왑(실패 대체)
class _FakeResp:
    def __init__(self, status_code, body=None, text=""):
        self.status_code = status_code
        self._body = body or {}
        self.text = text

    def json(self):
        return self._body


def test_post_messages_swaps_on_non200(monkeypatch):
    """앞 후보가 529(overloaded)면 다음 후보로 자동 스왑, 성공 후보만 토큰 기록."""
    import app.clients.ai_client as ai

    seen_models = []

    def fake_post(url, *, headers, json, timeout):
        seen_models.append(json["model"])
        if json["model"] == "m-primary":
            return _FakeResp(529, text="overloaded")
        return _FakeResp(
            200,
            body={
                "content": [{"type": "text", "text": "OK-RESULT"}],
                "usage": {"input_tokens": 30, "output_tokens": 7},
            },
        )

    monkeypatch.setattr(ai.httpx, "post", fake_post)
    usage = []
    text = ai._post_messages(
        "k",
        "prompt",
        max_tokens=100,
        models=[
            {"config_id": "c1", "model_id": "m-primary"},
            {"config_id": "c2", "model_id": "m-backup"},
        ],
        on_usage=lambda cid, tin, tout: usage.append((cid, tin, tout)),
    )
    assert text == "OK-RESULT"
    assert seen_models == ["m-primary", "m-backup"]  # 순서대로 시도
    assert usage == [("c2", 30, 7)]  # 성공한 후보만 기록(실패 후보는 미기록)


def test_post_messages_no_swap_on_refusal(monkeypatch):
    """200 거절은 스왑하지 않는다(요청 내용 문제) — 단, 소비 토큰은 먼저 기록."""
    import app.clients.ai_client as ai

    calls = []

    def fake_post(url, *, headers, json, timeout):
        calls.append(json["model"])
        return _FakeResp(
            200,
            body={"stop_reason": "refusal", "content": [], "usage": {"input_tokens": 12, "output_tokens": 0}},
        )

    monkeypatch.setattr(ai.httpx, "post", fake_post)
    usage = []
    with pytest.raises(ai.AiGenerationError):
        ai._post_messages(
            "k", "p", max_tokens=50,
            models=[{"config_id": "c1", "model_id": "m1"}, {"config_id": "c2", "model_id": "m2"}],
            on_usage=lambda cid, tin, tout: usage.append((cid, tin, tout)),
        )
    assert calls == ["m1"]  # 스왑 안 함(두 번째 후보 미시도)
    assert usage == [("c1", 12, 0)]  # 소비 토큰은 정직하게 기록


def test_post_messages_all_fail_raises(monkeypatch):
    """모든 후보가 실패하면 마지막 오류를 정직하게 전파."""
    import app.clients.ai_client as ai

    monkeypatch.setattr(ai.httpx, "post", lambda *a, **k: _FakeResp(500, text="boom"))
    with pytest.raises(ai.AiGenerationError):
        ai._post_messages("k", "p", max_tokens=10, models=[{"config_id": "c", "model_id": "m"}])


class _NonJsonResp:
    """200인데 본문이 JSON이 아닌 응답(프록시/CDN이 200으로 HTML 반환하는 상황)."""

    status_code = 200
    text = "<html>gateway error</html>"

    def json(self):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")


def test_post_messages_swaps_on_non_json_200(monkeypatch):
    """200인데 비JSON 본문이면 원시 ValueError를 누수하지 않고 다음 후보로 자동 스왑."""
    import app.clients.ai_client as ai

    seen = []

    def fake_post(url, *, headers, json, timeout):
        seen.append(json["model"])
        if json["model"] == "m-bad":
            return _NonJsonResp()  # 200 + 비JSON
        return _FakeResp(200, body={"content": [{"type": "text", "text": "GOOD"}],
                                    "usage": {"input_tokens": 9, "output_tokens": 3}})

    monkeypatch.setattr(ai.httpx, "post", fake_post)
    usage = []
    text = ai._post_messages(
        "k", "p", max_tokens=10,
        models=[{"config_id": "c1", "model_id": "m-bad"}, {"config_id": "c2", "model_id": "m-ok"}],
        on_usage=lambda cid, i, o: usage.append((cid, i, o)),
    )
    assert text == "GOOD"
    assert seen == ["m-bad", "m-ok"]  # 비JSON 200에서 스왑
    assert usage == [("c2", 9, 3)]  # 실패한 비JSON 후보는 토큰 미기록


def test_post_messages_non_json_200_all_fail_is_wrapped(monkeypatch):
    """비JSON 200만 있으면 원시 ValueError가 아니라 AiGenerationError로 감싸 던진다(500 방지)."""
    import app.clients.ai_client as ai

    monkeypatch.setattr(ai.httpx, "post", lambda *a, **k: _NonJsonResp())
    with pytest.raises(ai.AiGenerationError):  # ValueError 아님
        ai._post_messages("k", "p", max_tokens=10, models=[{"config_id": "c", "model_id": "m"}])


# -------------------------------------------------- 멀티 프로바이더(OpenAI/GPT) 라우팅
def test_post_messages_routes_openai(monkeypatch):
    """provider=OpenAI 후보는 OpenAI Chat Completions(Bearer 인증)로 호출하고 응답을 파싱한다."""
    import app.clients.ai_client as ai

    seen = {}

    def fake_post(url, *, headers, json, timeout):
        seen["url"] = url
        seen["auth"] = headers.get("Authorization")
        seen["model"] = json["model"]
        seen["mct"] = "max_completion_tokens" in json
        return _FakeResp(
            200,
            body={
                "choices": [{"message": {"content": "OAI-OUT"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 8},
            },
        )

    monkeypatch.setattr(ai.httpx, "post", fake_post)
    usage = []
    text = ai._post_messages(
        "anthropic-key", "p", max_tokens=100,
        models=[{"config_id": "c1", "model_id": "gpt-5", "provider": "OpenAI"}],
        on_usage=lambda cid, i, o: usage.append((cid, i, o)),
        openai_key="sk-openai",
    )
    assert text == "OAI-OUT"
    assert seen["url"].endswith("/chat/completions")
    assert seen["auth"] == "Bearer sk-openai"  # Anthropic 키가 아니라 OpenAI 키를 씀
    assert seen["model"] == "gpt-5" and seen["mct"] is True  # 신형 토큰 파라미터
    assert usage == [("c1", 40, 8)]  # OpenAI usage(prompt/completion_tokens) 파싱


def test_post_messages_cross_provider_swap(monkeypatch):
    """Anthropic 후보가 529면 OpenAI 후보로 자동 스왑(provider를 넘나든다)."""
    import app.clients.ai_client as ai

    seen = []

    def fake_post(url, *, headers, json, timeout):
        seen.append(json["model"])
        if "anthropic" in url:
            return _FakeResp(529, text="overloaded")
        return _FakeResp(200, body={
            "choices": [{"message": {"content": "OK"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1},
        })

    monkeypatch.setattr(ai.httpx, "post", fake_post)
    usage = []
    text = ai._post_messages(
        "ak", "p", max_tokens=10,
        models=[
            {"config_id": "a", "model_id": "claude-opus-4-8", "provider": "Anthropic"},
            {"config_id": "b", "model_id": "gpt-5", "provider": "OpenAI"},
        ],
        on_usage=lambda cid, i, o: usage.append((cid, i, o)),
        openai_key="sk-oa",
    )
    assert text == "OK"
    assert seen == ["claude-opus-4-8", "gpt-5"]
    assert usage == [("b", 3, 1)]  # 성공한 OpenAI 후보만 기록


def test_post_messages_openai_missing_key_skips_then_falls_back(monkeypatch):
    """OpenAI 후보인데 openai_key가 없으면 호출조차 안 하고 스킵 → 다음 Anthropic 후보로."""
    import app.clients.ai_client as ai

    seen = []

    def fake_post(url, *, headers, json, timeout):
        seen.append(json["model"])
        return _FakeResp(200, body={
            "content": [{"type": "text", "text": "ANT"}],
            "usage": {"input_tokens": 2, "output_tokens": 1},
        })

    monkeypatch.setattr(ai.httpx, "post", fake_post)
    text = ai._post_messages(
        "ak", "p", max_tokens=10,
        models=[
            {"config_id": "g", "model_id": "gpt-5", "provider": "OpenAI"},
            {"config_id": "a", "model_id": "claude-opus-4-8", "provider": "Anthropic"},
        ],
        openai_key="",  # OpenAI 키 미설정
    )
    assert text == "ANT"
    assert seen == ["claude-opus-4-8"]  # gpt는 키가 없어 호출 시도조차 안 함

    # OpenAI 후보만 있고 키 없으면 원시 예외가 아니라 AiGenerationError
    with pytest.raises(ai.AiGenerationError):
        ai._post_messages(
            "ak", "p", max_tokens=10,
            models=[{"config_id": "g", "model_id": "gpt-5", "provider": "OpenAI"}],
            openai_key="",
        )


# ------------------------------------------------------------------- 엔드포인트 CRUD
def test_ai_runtime_crud_and_slot_delete(client, db):
    """등록→슬롯 배정→삭제 시 슬롯 자동 해제, 비운영자 거부."""
    ops_tok = _ops(client, db)

    # 초기: 비어 있고 폴백 모델이 안전망으로 노출
    r = client.get("/api/v1/ops/ai-runtime", headers=auth(ops_tok))
    assert r.status_code == 200, r.text
    assert r.json()["models"] == []
    assert r.json()["slots"] == {"generate": None, "verify": None}
    assert r.json()["fallback_model"]  # .env LLM_MODEL

    # 등록
    r = client.post(
        "/api/v1/ops/ai-runtime/models",
        json={"provider": "Anthropic", "name": "오퍼스", "model_id": "claude-opus-4-8",
              "cost_in_usd": 5, "cost_out_usd": 25},
        headers=auth(ops_tok),
    )
    assert r.status_code == 200, r.text
    mid = r.json()["id"]
    assert r.json()["enabled"] is True and r.json()["est_cost_usd"] == 0

    # 슬롯 배정(생성=이 모델) + 자동스왑 on
    r = client.put(
        "/api/v1/ops/ai-runtime/config",
        json={"generate_model_id": mid, "auto_swap": True},
        headers=auth(ops_tok),
    )
    assert r.status_code == 200, r.text
    assert r.json()["slots"]["generate"] == mid and r.json()["auto_swap"] is True

    # On/Off 토글
    r = client.patch(
        f"/api/v1/ops/ai-runtime/models/{mid}", json={"enabled": False}, headers=auth(ops_tok)
    )
    assert r.status_code == 200 and r.json()["enabled"] is False

    # 삭제 → 슬롯도 함께 비워진다
    r = client.delete(f"/api/v1/ops/ai-runtime/models/{mid}", headers=auth(ops_tok))
    assert r.status_code == 200, r.text
    r = client.get("/api/v1/ops/ai-runtime", headers=auth(ops_tok))
    assert r.json()["models"] == []
    assert r.json()["slots"]["generate"] is None  # 죽은 포인터 없음


def test_ai_runtime_config_rejects_unknown_model(client, db):
    """존재하지 않는 모델을 슬롯에 배정하면 404(끊긴 포인터 저장 방지)."""
    ops_tok = _ops(client, db)
    r = client.put(
        "/api/v1/ops/ai-runtime/config",
        json={"verify_model_id": "does-not-exist"},
        headers=auth(ops_tok),
    )
    assert r.status_code == 404


def test_ai_runtime_requires_ops(client, db, seed_org):
    """비운영자(교사)는 조회·변경 모두 거부."""
    _ops(client, db)  # 운영자 시드(로그인용 계정 존재 보장)
    t = client.post("/api/v1/auth/login", json={"email": "t1@test.dev", "password": "Password123!"})
    assert t.status_code == 200, t.text
    tt = t.json()["access_token"]
    assert client.get("/api/v1/ops/ai-runtime", headers=auth(tt)).status_code in (401, 403)
    assert client.post(
        "/api/v1/ops/ai-runtime/models",
        json={"provider": "x", "name": "y", "model_id": "z"},
        headers=auth(tt),
    ).status_code in (401, 403)


# --------------------------------------------------- 생성 배선: 슬롯 모델·토큰 기록 실증
def test_generate_uses_slot_model_and_records_tokens(client, db, monkeypatch, tmp_path):
    """생성 엔드포인트가 '생성 슬롯 모델'을 실제로 호출하고 토큰을 누적하는지 —
    httpx를 가로채 model 필드와 usage 누적을 고정한다(자기검증은 비활성=슬롯 미설정)."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", "")
    monkeypatch.setattr(get_settings(), "LECTURE_MEDIA_DIR", str(tmp_path))
    ops_tok = _ops(client, db)
    itok = _instructor(client, db)
    client.put(
        "/api/v1/ops/settings/ai",
        json={"anthropic_api_key": "sk-console-7777"},
        headers=auth(ops_tok),
    )
    # 생성 슬롯에 특정 모델 배정(검증 슬롯은 비워 자기검증 네트워크 호출 방지)
    r = client.post(
        "/api/v1/ops/ai-runtime/models",
        json={"provider": "Anthropic", "name": "생성용", "model_id": "gen-model-XYZ"},
        headers=auth(ops_tok),
    )
    gen_id = r.json()["id"]
    client.put(
        "/api/v1/ops/ai-runtime/config",
        json={"generate_model_id": gen_id},
        headers=auth(ops_tok),
    )

    import app.clients.ai_client as ai

    seen = {}

    def fake_post(url, *, headers, json, timeout):
        seen["model"] = json["model"]
        return _FakeResp(
            200,
            body={
                "content": [{"type": "text", "text":
                    '[{"prompt":"슬롯 모델로 낸 문제","options":["가","나"],"answer_index":0,"explain":""}]'}],
                "usage": {"input_tokens": 111, "output_tokens": 22},
            },
        )

    monkeypatch.setattr(ai.httpx, "post", fake_post)
    # 자기검증은 슬롯 미설정+자동스왑 off라 후보0 → .env 폴백을 타지만, 네트워크는 위 fake_post가
    # 받으므로 안전. 다만 판정 자체는 이 테스트 관심 밖이라 verify를 비활성화한다.
    monkeypatch.setattr(ai, "verify_questions", lambda items, **k: None)

    up = client.post(
        "/api/v1/ops/lectures",
        data={"title": "슬롯 생성 강의", "subject": "국어", "duration_sec": "300"},
        files={"file": ("v.mp4", b"0" * 1024, "video/mp4")},
        headers=auth(itok),
    )
    lec_id = up.json()["id"]
    # 비동기 전환(0720): POST는 잡만 만든다 — 생성 로직(추출 헬퍼)을 직접 구동해 검증한다.
    from app.api.v1.endpoints.lectures import _generate_questions_now
    from app.models import Lecture

    _generate_questions_now(db, db.get(Lecture, lec_id), 1, "actor")
    assert seen["model"] == "gen-model-XYZ", "생성 슬롯 모델이 실제 호출에 쓰이지 않았다"

    # 생성 슬롯 모델에 토큰이 누적됐는지(추정 비용 근거)
    rt = client.get("/api/v1/ops/ai-runtime", headers=auth(ops_tok)).json()
    row = next(m for m in rt["models"] if m["id"] == gen_id)
    assert row["tokens_in"] == 111 and row["tokens_out"] == 22
