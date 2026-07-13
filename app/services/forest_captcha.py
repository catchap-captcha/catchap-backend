"""forest_captcha.py — 메인 캡차(숲속 마을 동물 방향 맞히기)의 서버측 로직.

capcha_service sw 브랜치 04-forest 이식 + CatChap 보안 하드닝. 프레임워크 비의존
(FastAPI import 없음)이라 단독 테스트·재사용 가능.

보안 속성
--------
- 정답(target_object/target_direction)은 서버에서 생성해 **서버에만** 저장, 응답에 절대 없음.
- challenge_id = UUID4. 챌린지는 **단일 사용** + 만료(기본 120초). verify(성공·실패 무관) 시 즉시 폐기.
- 정답 시 **단일 사용** captcha_token 발급(짧은 수명). 로그인에서 1회 소비.
- InMemoryStore는 threading.Lock으로 직렬화(동시 요청 안전). 단일 프로세스 프로토타입용 —
  다중 워커/프로덕션은 Store 인터페이스를 Redis/DB로 구현해 주입한다(다른 코드 불변).

주의(정직한 보안 한계): 프론트가 회전 UI를 그리려 동물 8방향 프레임을 로컬 로드하므로,
/target 이미지의 "불투명성"만으로 봇을 막지 못한다. 실제 봇 저항력은 상호작용 난이도 +
행동데이터(behavior_traces) 신호 + 레이트리밋 + 단일사용/만료에서 나온다.
"""

from __future__ import annotations

import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional

# 프론트 위젯 animal-config.js와 반드시 동기화한다.
ANIMALS: List[str] = [
    "dog", "rabbit", "chicken", "panda", "capybara", "cat",
    "pig", "quokka", "tiger", "sheep", "giraffe",
]
ANIMAL_NAMES: Dict[str, str] = {
    "dog": "강아지",
    "rabbit": "토끼",
    "chicken": "닭",
    "panda": "판다",
    "capybara": "카피바라",
    "cat": "고양이",
    "pig": "돼지",
    "quokka": "쿼카",
    "tiger": "호랑이",
    "sheep": "양",
    "giraffe": "기린",
}
THEME_ID = "forest"
OBJECTS: List[str] = ["tree", "house", "mushroom"]
OBJECT_COUNT = len(OBJECTS)

DIRECTIONS = 8  # 8방향 턴테이블(0..7)
CHALLENGE_TTL_SECONDS = 120
CAPTCHA_TOKEN_TTL_SECONDS = 120
# 레이트리밋: 한 키(IP)당 이 창(초) 안에 만들 수 있는 챌린지 수
CHALLENGE_RATE_WINDOW = 60
CHALLENGE_RATE_MAX = 20


@dataclass
class ObjectSpec:
    object_id: str
    animal: str
    start_direction: int
    # 정답 방향. 공개 challenge 응답에는 절대 포함하지 않는다.
    heading: int


@dataclass
class ChallengeRecord:
    challenge_id: str
    theme_id: str
    objects: List[ObjectSpec]
    # ---- 정답 — 서버 밖으로 절대 나가면 안 됨 ----
    target_animal: str
    target_object: str
    target_direction: int
    created_at: float
    ttl: int = CHALLENGE_TTL_SECONDS

    @property
    def expires_at(self) -> float:
        return self.created_at + self.ttl

    def is_expired(self, now: Optional[float] = None) -> bool:
        return (now or time.time()) >= self.expires_at

    def seconds_left(self, now: Optional[float] = None) -> int:
        return max(0, int(self.expires_at - (now or time.time())))

    def find_object(self, object_id: str) -> Optional[ObjectSpec]:
        return next((obj for obj in self.objects if obj.object_id == object_id), None)


@dataclass
class TokenRecord:
    token: str
    created_at: float
    ttl: int = CAPTCHA_TOKEN_TTL_SECONDS
    used: bool = False  # 토큰도 단일 사용(로그인에서 소비)

    def is_valid(self, now: Optional[float] = None) -> bool:
        now = now or time.time()
        return (not self.used) and (now < self.created_at + self.ttl)


class Store:
    """모든 백엔드(memory/Redis/DB)가 구현할 인터페이스."""

    def save_challenge(self, rec: ChallengeRecord) -> None: raise NotImplementedError
    def get_challenge(self, cid: str) -> Optional[ChallengeRecord]: raise NotImplementedError
    def delete_challenge(self, cid: str) -> None: raise NotImplementedError
    def save_token(self, rec: TokenRecord) -> None: raise NotImplementedError
    def get_token(self, token: str) -> Optional[TokenRecord]: raise NotImplementedError
    def delete_token(self, token: str) -> None: raise NotImplementedError
    def hit_rate(self, key: str, window: int, now: float) -> int: raise NotImplementedError


class InMemoryStore(Store):
    """dict 기반 + Lock. 단일 프로세스 프로토타입용(재시작 시 소실·워커 간 미공유).

    프로덕션은 RedisStore(Store)로 교체하면 나머지 코드는 그대로.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._challenges: Dict[str, ChallengeRecord] = {}
        self._tokens: Dict[str, TokenRecord] = {}
        self._rate: Dict[str, List[float]] = {}

    def save_challenge(self, rec: ChallengeRecord) -> None:
        with self._lock:
            self._challenges[rec.challenge_id] = rec

    def get_challenge(self, cid: str) -> Optional[ChallengeRecord]:
        with self._lock:
            return self._challenges.get(cid)

    def delete_challenge(self, cid: str) -> None:
        with self._lock:
            self._challenges.pop(cid, None)

    def save_token(self, rec: TokenRecord) -> None:
        with self._lock:
            self._tokens[rec.token] = rec

    def get_token(self, token: str) -> Optional[TokenRecord]:
        with self._lock:
            return self._tokens.get(token)

    def delete_token(self, token: str) -> None:
        with self._lock:
            self._tokens.pop(token, None)

    def hit_rate(self, key: str, window: int, now: float) -> int:
        """이 키의 최근 window초 히트 수를 1 늘려 반환(레이트리밋용, 슬라이딩 윈도우)."""
        with self._lock:
            hits = [t for t in self._rate.get(key, []) if t > now - window]
            hits.append(now)
            self._rate[key] = hits
            return len(hits)


class ForestCaptchaService:
    def __init__(self, store: Optional[Store] = None) -> None:
        self.store = store or InMemoryStore()

    # ---- 레이트리밋 --------------------------------------------------------
    def rate_limited(self, key: str) -> bool:
        """키(IP)당 챌린지 생성 레이트리밋 — 봇의 대량 챌린지 소진/브루트포스 완화."""
        n = self.store.hit_rate(f"chal:{key}", CHALLENGE_RATE_WINDOW, time.time())
        return n > CHALLENGE_RATE_MAX

    # ---- 챌린지 생명주기 ----------------------------------------------------
    def create_challenge(self) -> ChallengeRecord:
        """오브젝트 3개에 서로 다른 동물을 숨기고 그중 하나를 목표로 정한다."""
        pool = ANIMALS.copy()
        chosen: List[str] = []
        for _ in range(OBJECT_COUNT):
            chosen.append(pool.pop(secrets.randbelow(len(pool))))

        objects: List[ObjectSpec] = []
        for object_id, animal in zip(OBJECTS, chosen):
            heading = secrets.randbelow(DIRECTIONS)
            start_direction = secrets.randbelow(DIRECTIONS)
            if start_direction == heading:
                start_direction = (start_direction + 1) % DIRECTIONS
            objects.append(
                ObjectSpec(
                    object_id=object_id,
                    animal=animal,
                    start_direction=start_direction,
                    heading=heading,
                )
            )

        target = objects[secrets.randbelow(len(objects))]
        rec = ChallengeRecord(
            challenge_id=str(uuid.uuid4()),
            theme_id=THEME_ID,
            objects=objects,
            target_animal=target.animal,
            target_object=target.object_id,
            target_direction=target.heading,
            created_at=time.time(),
        )
        self.store.save_challenge(rec)
        return rec

    def get_active_challenge(self, cid: str) -> Optional[ChallengeRecord]:
        rec = self.store.get_challenge(cid)
        if rec is None:
            return None
        if rec.is_expired():
            self.store.delete_challenge(cid)
            return None
        return rec

    def verify(
        self,
        cid: str,
        selected_object: str,
        selected_direction: int,
        theme_id: Optional[str] = None,
    ) -> bool:
        """오브젝트에 서버가 배정한 동물과 방향을 검증하고 챌린지를 폐기한다."""
        rec = self.get_active_challenge(cid)
        if rec is None:
            return False
        self.store.delete_challenge(cid)  # 성공·실패와 무관하게 단일 사용

        if theme_id is not None and theme_id != rec.theme_id:
            return False
        obj = rec.find_object(selected_object)
        if obj is None:
            return False
        try:
            sel_dir = int(selected_direction)
        except (TypeError, ValueError):
            sel_dir = -1
        correct = (
            selected_object == rec.target_object
            and obj.animal == rec.target_animal
            and sel_dir == rec.target_direction
        )
        return correct

    # ---- 캡차 토큰 ----------------------------------------------------------
    def issue_token(self) -> str:
        token = secrets.token_urlsafe(24)
        self.store.save_token(TokenRecord(token=token, created_at=time.time()))
        return token

    def consume_token(self, token: Optional[str]) -> bool:
        """captcha_token 검증 + 소비(단일 사용). 로그인 게이트에서 호출."""
        if not token:
            return False
        rec = self.store.get_token(token)
        if rec is None or not rec.is_valid():
            return False
        self.store.delete_token(token)  # 소비 — 재사용 불가
        return True


# 프로세스 싱글턴 — 프로덕션은 RedisStore 주입으로 교체.
service = ForestCaptchaService(store=InMemoryStore())
