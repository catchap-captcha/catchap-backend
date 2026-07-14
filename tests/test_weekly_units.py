"""전체학습 유닛=2주차 정렬 회귀 테스트 (사용자 결정 0714).

영어·사회는 원본 저장소 1유닛(25문항)을 2주차(본편 20문항)로 편성한다 —
subject_banks._weekly_reorder가 DB/파일 어느 소스든 로드 직후 보장해야 한다.
"""
from app.services import subject_banks as sb
from app.services.chapters import chapter_all_question_ids, max_chapters


def _unit_of(subject: str, qid: str) -> int | None:
    for pre, n in sb._UNIT_PREFIX[subject].items():
        if qid.startswith(pre + "-"):
            return n
    return None


def test_weekly_reorder_core_alignment():
    """본편 1~200번 = 유닛 1..10 × 20문항(단계별 4문항) 정렬."""
    for subject in ("영어", "사회"):
        bank = sb.BANKS[subject]
        assert len(bank) >= 200, f"{subject} 은행이 20주차(200문항)에 못 미침"
        got = [_unit_of(subject, q["id"]) for q in bank[:200]]
        want = [n for n in range(1, 11) for _ in range(20)]
        assert got == want, f"{subject} 본편 유닛 정렬 어긋남"


def test_weekly_reorder_chapter_units():
    """주차 N의 문항은 전부 유닛 ceil(N/2)에서 나온다 — 유닛=2주차."""
    for subject in ("영어", "사회"):
        assert max_chapters(subject) == 20
        for ch in range(1, 21):
            ids = chapter_all_question_ids(subject, ch)
            units = {_unit_of(subject, qid) for qid in ids}
            assert units == {(ch + 1) // 2}, f"{subject} {ch}주차가 유닛 경계를 벗어남: {units}"


def test_weekly_reorder_no_content_loss():
    """재배열은 순서만 바꾼다 — 문항 집합·playable 수는 원본 그대로."""
    for subject in ("영어", "사회"):
        bank = sb.BANKS[subject]
        assert len(bank) == len({q["id"] for q in bank}), f"{subject} 중복 id"
        # 잔여 문항(뒤 50개)도 여전히 playable 풀에 남아 오늘의퀴즈에 쓰인다
        assert len(sb.playable_pool(subject)) == len([q for q in bank if q["playable"]])
