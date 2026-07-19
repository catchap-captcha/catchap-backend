"""오답노트 전 유형 기록 — 정답 텍스트 렌더(선택/타이핑/십자말/순서) + 개념 폴백(route/trace)."""

from app.api.v1.endpoints.students import _correct_answer_text


def test_correct_answer_text_covers_all_types():
    # 선택형(단일): answer=옵션 id → 옵션 텍스트
    q_single = {"type": "single", "prompt": "?", "answer": "o2",
                "options": [{"id": "o1", "text": "사과"}, {"id": "o2", "text": "바나나"}]}
    assert _correct_answer_text(q_single) == "바나나"

    # 복수: answer=[id] → 조인
    q_multi = {"type": "multi", "prompt": "?", "answer": ["o1", "o2"],
               "options": [{"id": "o1", "text": "강아지"}, {"id": "o2", "text": "고양이"}, {"id": "o3", "text": "나무"}]}
    assert _correct_answer_text(q_multi) == "강아지, 고양이"

    # 순서: answer=[id 순서] → 옵션 텍스트 순서
    q_order = {"type": "order", "prompt": "?", "answer": ["c1", "c2"],
               "options": [{"id": "c1", "text": "물 묻히기"}, {"id": "c2", "text": "비누칠"}],
               "explain": "물 묻히기 → 비누칠"}
    assert "물 묻히기" in _correct_answer_text(q_order)

    # 타이핑: answer=문자열 그대로
    q_dict = {"type": "dictation", "prompt": "?", "answer": "나는 사과를 먹었다."}
    assert _correct_answer_text(q_dict) == "나는 사과를 먹었다."
    q_typein = {"type": "type_in", "prompt": "?", "answer": "드신다"}
    assert _correct_answer_text(q_typein) == "드신다"

    # 수학 입력: answers 목록 첫 값
    q_input = {"type": "input", "prompt": "?", "answers": ["4927"], "explain": "..."}
    assert _correct_answer_text(q_input) == "4927"

    # 십자말: answer={슬롯: 낱말} → 낱말 조인
    q_cw = {"type": "crossword", "prompt": "?", "answer": {"w0": "무늬", "w1": "무지개"}}
    txt = _correct_answer_text(q_cw)
    assert "무늬" in txt and "무지개" in txt

    # 정답 텍스트 없는 유형 → 개념(explain) 폴백
    q_trace = {"type": "trace", "prompt": "?", "explain": "알파벳 L"}
    assert _correct_answer_text(q_trace) == "알파벳 L"
    q_route = {"type": "route", "prompt": "?", "explain": "미아 안전"}
    assert _correct_answer_text(q_route) == "미아 안전"
    q_swipe = {"type": "swipe", "prompt": "?", "answer": None, "explain": "사실과 의견"}
    assert _correct_answer_text(q_swipe) == "사실과 의견"

    # explain·hint 다 없어도 빈 문자열 아닌 안전 폴백
    q_bare = {"type": "route", "prompt": "?"}
    assert _correct_answer_text(q_bare)


def test_wrong_view_from_srs_via_verify(client, db, seed_org):
    """'틀린 문제' 뷰(Q 통합 3단계, 결정 ④) — 오답이 SRS wrong 상자에 남아 화면에 노출되고,
    다시 맞히면 자동 이탈한다. 별도 WrongAnswer 기록은 더 이상 생기지 않는다."""
    from app.models import WrongAnswer
    from app.services import bank_mode
    from app.services import captcha_service as cs
    from app.services import subject_banks
    from tests.test_bank_mode import _first_party_key, _student_token

    _first_party_key(db)
    tok = _student_token(client)
    student = seed_org["student"]
    headers = {"X-Site-Key": "ck_edu_testfp", "Authorization": f"Bearer {tok}"}

    # trace(따라그리기) 문항 — 정답 텍스트가 없어 개념(explain) 폴백을 검증하는 대표 유형
    trace_q = next(
        (q for s in sorted(subject_banks.LIVE_SUBJECTS)
         for q in subject_banks.playable_pool(s) if q["type"] == "trace"),
        None,
    )
    assert trace_q is not None
    subj = next(
        s for s in sorted(subject_banks.LIVE_SUBJECTS)
        if any(q["id"] == trace_q["id"] for q in subject_banks.playable_pool(s))
    )
    ch = cs._wrap_bank_question(subj, trace_q, {"subj": subj, "bank": True})
    # 일부러 빗나간 답(오답)
    r = client.post(
        "/api/v1/captcha/v1/verify",
        json={"challenge_token": ch["challenge_token"], "answer": [[0.1, 0.1], [0.2, 0.2]]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    if r.json()["success"]:
        return  # 우연히 통과하면(가능성 낮음) 오답 시나리오가 아님 — 스킵

    # 옛 오답노트에는 더 이상 쌓이지 않는다(쓰기 중단 — 데이터는 보존이지만 신규 없음)
    assert (
        db.query(WrongAnswer)
        .filter(WrongAnswer.student_id == student.id, WrongAnswer.question == trace_q["prompt"])
        .first()
        is None
    )

    # '틀린 문제' 화면(SRS 뷰)에 문항+정답(개념 폴백)이 노출된다
    res = client.get(
        "/api/v1/students/me/wrong-notes", headers={"Authorization": f"Bearer {tok}"}
    ).json()
    mine = next((i for i in res["items"] if i["id"] == trace_q["id"]), None)
    assert mine is not None, "오답이 틀린 문제 목록에 보여야 한다"
    assert mine["answer"], "정답(개념 설명 폴백)이 채워져야 한다"
    assert mine["wrong_count"] >= 1
    assert res["summary"]["total"] >= 1

    # 다시 맞히면(SRS 갱신) 목록에서 자동으로 사라진다 — '복습완료 승격'의 대체
    bank_mode.record_answer(db, student.id, subj, trace_q["id"], True)
    db.commit()
    res2 = client.get(
        "/api/v1/students/me/wrong-notes", headers={"Authorization": f"Bearer {tok}"}
    ).json()
    assert all(i["id"] != trace_q["id"] for i in res2["items"])
