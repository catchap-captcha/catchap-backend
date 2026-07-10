"""원본 유형 복원(위젯 업그레이드) — 유형별 서버 채점 + 페이로드 정답 미유출 검증.

복원 유형: 국어 dictation(TTS 받아쓰기)·type_in(높임말 입력)·punct(문장부호 자리탭)·
crossword(십자말 격자)·swipe(사실의견 카드), 과학·수학 drag(카드 드래그)·position(장면 부위 탭).
"""
from app.services import captcha_service as cs
from app.services.korean_bank import KOREAN_FULL
from app.services.math_bank import MATH_FULL
from app.services.science_bank import SCIENCE_FULL


def _q(bank, qtype):
    return next(q for q in bank if q["type"] == qtype)


def _wrap(subject, q):
    return cs._wrap_bank_question(subject, q, {"subj": subject})


def test_dictation_text_grading(db):
    q = _q(KOREAN_FULL, "dictation")
    ch = _wrap("국어", q)
    # 원본 설계상 tts(들려줄 문장)=정답이 페이로드에 필요 — 그 외 정답 필드는 없어야 한다
    assert ch["type"] == "dictation" and ch["tts"] == q["answer"]
    assert "answer" not in ch and "explain" not in ch
    ok = cs.verify_challenge(db, ch["challenge_token"], "  " + q["answer"] + " ")
    assert ok["success"] is True  # trim 후 정확 일치
    ch2 = _wrap("국어", q)
    bad = cs.verify_challenge(db, ch2["challenge_token"], q["answer"].replace(" ", ""))
    assert bad["success"] is False  # 띄어쓰기 전부 붙이면 오답
    ch3 = _wrap("국어", q)
    assert cs.verify_challenge(db, ch3["challenge_token"], "")["success"] is False
    ch4 = _wrap("국어", q)
    assert cs.verify_challenge(db, ch4["challenge_token"], 12345)["success"] is False  # 비문자열 500 방지


def test_type_in_no_answer_leak(db):
    q = _q(KOREAN_FULL, "type_in")
    ch = _wrap("국어", q)
    assert ch["type"] == "type_in" and ch["highlight"] == q["highlight"]
    blob = str(ch).replace(ch["challenge_token"], "")
    assert q["answer"] not in blob  # 높임말 정답은 토큰 밖 어디에도 없다
    ok = cs.verify_challenge(db, ch["challenge_token"], q["answer"] + "  ")
    assert ok["success"] is True
    ch2 = _wrap("국어", q)
    assert cs.verify_challenge(db, ch2["challenge_token"], q["highlight"])["success"] is False


def test_crossword_match_grading(db):
    q = _q(KOREAN_FULL, "crossword")
    ch = _wrap("국어", q)
    assert ch["type"] == "crossword" and ch["size"] == q["size"] and ch["tiles"]
    for w in ch["words"]:
        assert "answer" not in w  # 낱말 정답은 슬롯에 미포함(길이·힌트만)
    blob = str(ch).replace(ch["challenge_token"], "")
    for ans in q["answer"].values():
        assert f"'{ans}'" not in blob  # 정답 낱말 문자열이 통째로 노출되지 않는다
    ok = cs.verify_challenge(db, ch["challenge_token"], dict(q["answer"]))
    assert ok["success"] is True
    wrong = dict(q["answer"])
    first = next(iter(wrong))
    wrong[first] = wrong[first][::-1] if len(wrong[first]) > 1 else "틀림"
    ch2 = _wrap("국어", q)
    assert cs.verify_challenge(db, ch2["challenge_token"], wrong)["success"] is False
    ch3 = _wrap("국어", q)
    assert cs.verify_challenge(db, ch3["challenge_token"], "낱말")["success"] is False  # 비dict


def test_swipe_single_statement(db):
    q = _q(KOREAN_FULL, "swipe")
    tag_by_text = {s["text"]: s["tag"] for s in q["statements"]}
    ch = _wrap("국어", q)
    assert ch["type"] == "swipe" and ch["card"] in tag_by_text
    assert "statements" not in ch  # 태그(정답) 포함 원본 목록은 비공개
    correct = tag_by_text[ch["card"]]
    other = "의견" if correct == "사실" else "사실"
    ok = cs.verify_challenge(db, ch["challenge_token"], correct)
    assert ok["success"] is True
    ch2 = _wrap("국어", q)
    wrong_tag = "의견" if tag_by_text[ch2["card"]] == "사실" else "사실"
    assert cs.verify_challenge(db, ch2["challenge_token"], wrong_tag)["success"] is False
    assert other  # lint 억제용 아님 — 두 태그가 상보임을 명시


def test_drag_pick_grading(db):
    for subject, bank in (("과학", SCIENCE_FULL), ("수학", MATH_FULL)):
        q = _q(bank, "drag")
        ch = _wrap(subject, q)
        assert ch["type"] == "drag_pick" and ch["items"] and ch["target"] and ch["zone"]
        assert "answer" not in ch
        z = ch["zone"]
        ok = cs.verify_challenge(
            db, ch["challenge_token"], {"item": q["answer"], "x": z["cx"], "y": z["cy"]}
        )
        assert ok["success"] is True and "drop_distance_norm" in ok
        # 오답 카드를 존 안에 놓아도 실패
        wrong_item = next(it["id"] for it in q["items"] if it["id"] != q["answer"])
        ch2 = _wrap(subject, q)
        z2 = ch2["zone"]
        bad = cs.verify_challenge(
            db, ch2["challenge_token"], {"item": wrong_item, "x": z2["cx"], "y": z2["cy"]}
        )
        assert bad["success"] is False
        # 정답 카드라도 존 밖이면 실패
        ch3 = _wrap(subject, q)
        z3 = ch3["zone"]
        far_x = 0.05 if z3["cx"] > 0.5 else 0.95
        miss = cs.verify_challenge(
            db, ch3["challenge_token"], {"item": q["answer"], "x": far_x, "y": z3["cy"]}
        )
        assert miss["success"] is False


def test_position_scene_grading(db):
    for subject, bank in (("과학", SCIENCE_FULL), ("수학", MATH_FULL)):
        q = _q(bank, "position")
        ch = _wrap(subject, q)
        assert ch["type"] == "position"
        assert "<svg" in ch["scene_svg"] and "data-region" in ch["scene_svg"]
        assert {r["id"] for r in ch["regions"]} >= {q["answer"]}
        assert "answer" not in ch
        ok = cs.verify_challenge(db, ch["challenge_token"], q["answer"])
        assert ok["success"] is True
        wrong = next(r["id"] for r in q["regions"] if r["id"] != q["answer"])
        ch2 = _wrap(subject, q)
        assert cs.verify_challenge(db, ch2["challenge_token"], wrong)["success"] is False


def test_all_restored_types_wrap_clean(db):
    """복원 유형 전 문항이 예외 없이 발급되고 정답·해설이 응답에 없다."""
    restored = {"dictation", "type_in", "punct", "crossword", "swipe", "drag", "position"}
    banks = (("국어", KOREAN_FULL), ("과학", SCIENCE_FULL), ("수학", MATH_FULL))
    n = 0
    for subject, bank in banks:
        for q in bank:
            if q["type"] not in restored:
                continue
            ch = _wrap(subject, q)
            assert "explain" not in ch and "playable" not in ch
            if q["type"] != "dictation":  # dictation은 tts=정답이 설계상 포함
                assert "answer" not in ch
            n += 1
    assert n == 25 * 5 + 13 + 8  # 국어 5유형×25 + 과학 13 + 수학 8
