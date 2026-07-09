"""디자인 원본(.dc.html)에서 추출한 화면 데이터 상수.

seed와 대시보드 blob 응답이 같은 값을 쓰도록 한 곳에 모았다.
(화면이 디자인과 동일하게 보이는 것이 목표 — 실명/실기관 아님, 디자인 속 가명 그대로)
"""

SUBJECT_ORDER = ["국어", "영어", "수학", "과학", "사회", "생활"]

SUBJECT_META = {
    "국어": {"color": "#FF5A4D", "soft": "#FFE0DB", "grad": "linear-gradient(150deg,#FF7A7A,#FF5A6E)", "icon": "ph-fill ph-book-open"},
    "영어": {"color": "#FF922E", "soft": "#FFEDD6", "grad": "linear-gradient(150deg,#FFB43C,#FF922E)", "icon": "ph-fill ph-translate"},
    "수학": {"color": "#17B08C", "soft": "#DFF6EE", "grad": "linear-gradient(150deg,#33C892,#17B0A0)", "icon": "ph-fill ph-plus-minus"},
    "과학": {"color": "#2E7BFF", "soft": "#E1EDFF", "grad": "linear-gradient(150deg,#4AA6FF,#2E7BFF)", "icon": "ph-fill ph-flask"},
    "사회": {"color": "#8B6BFF", "soft": "#EAE2FF", "grad": "linear-gradient(150deg,#A98CFF,#8B6BFF)", "icon": "ph-fill ph-scroll"},
    "생활": {"color": "#FF6DA6", "soft": "#FFE3EF", "grad": "linear-gradient(150deg,#FF93BE,#FF6DA6)", "icon": "ph-fill ph-house-line"},
}

# 챕터지도 CHAPTERS (이름/문제수) — 6과목 x 5챕터
CHAPTERS = {
    "국어": [("자음·모음", 4), ("낱말 읽기", 5), ("짧은 문장", 4), ("받아쓰기", 3), ("종합 복습", 5)],
    "영어": [("알파벳", 4), ("파닉스 소리", 5), ("쉬운 단어", 5), ("짧은 문장", 4), ("종합 복습", 5)],
    "수학": [("수 세기", 4), ("더하기", 5), ("빼기", 5), ("모양과 규칙", 4), ("종합 복습", 5)],
    "과학": [("동물 친구", 4), ("식물 관찰", 4), ("날씨와 계절", 5), ("물과 공기", 4), ("종합 복습", 5)],
    "사회": [("옛날 사람들", 4), ("위인 이야기", 5), ("우리 문화", 4), ("나라의 시작", 4), ("종합 복습", 5)],
    "생활": [("교통 안전", 4), ("우리 집 안전", 4), ("친구 사이", 5), ("건강 습관", 4), ("종합 복습", 5)],
}

# 챕터지도 DEFAULT_DONE (하은 진도)
DEFAULT_DONE = {"국어": 2, "영어": 1, "수학": 3, "과학": 0, "사회": 1, "생활": 2}

# 나의기록 과목별 정답률 흐름(최근 6회) — '전체' 포함
RECORD_ACC_SERIES = {
    "전체": {"color": "#17B08C", "data": [72, 78, 75, 84, 88, 92]},
    "국어": {"color": "#FF5A6E", "data": [80, 84, 82, 88, 90, 93]},
    "영어": {"color": "#FF922E", "data": [60, 66, 70, 68, 74, 79]},
    "수학": {"color": "#2E7BFF", "data": [70, 74, 72, 80, 83, 86]},
    "과학": {"color": "#8B6BFF", "data": [55, 62, 60, 68, 72, 77]},
    "사회": {"color": "#33C892", "data": [64, 68, 72, 75, 79, 84]},
    "생활": {"color": "#FF6DA6", "data": [78, 80, 79, 85, 88, 91]},
}

# 개념설명 CONCEPTS — chapters.concept JSON 소스
CONCEPTS = {
    "국어": [
        {"icon": "ph-fill ph-text-aa", "summary": "글자는 자음과 모음이 만나 소리가 돼요.", "points": ["ㄱ, ㄴ, ㄷ 같은 자음이 있어요.", "ㅏ, ㅑ, ㅓ 같은 모음이 있어요.", "자음과 모음이 만나면 '가, 나, 다'가 돼요."], "example": "ㄱ + ㅏ = 가 🐱"},
        {"icon": "ph-fill ph-book-open", "summary": "글자가 모이면 낱말이 돼요.", "points": ["글자를 하나씩 이어서 읽어봐요.", "그림을 보면 낱말을 쉽게 떠올릴 수 있어요.", "소리 내어 읽으면 더 잘 외워져요."], "example": "고 + 양 + 이 = 고양이 🐈"},
        {"icon": "ph-fill ph-chat-text", "summary": "낱말이 모이면 문장이 돼요.", "points": ["누가 무엇을 하는지 담겨 있어요.", "문장 끝에는 마침표(.)를 찍어요.", "읽고 나서 어떤 장면인지 떠올려봐요."], "example": "고양이가 잠을 자요. 😴"},
        {"icon": "ph-fill ph-pencil-simple", "summary": "소리 나는 대로 쓰지 않는 낱말이 있어요.", "points": ["'같이'는 [가치]로 소리 나요.", "'꽃'은 받침 ㅊ을 잊지 말아요.", "헷갈리면 또박또박 천천히 써봐요."], "example": "'같이'는 [가치]로 읽지만 '같이'로 써요."},
        {"icon": "ph-fill ph-star", "summary": "지금까지 배운 국어를 모아 살펴봐요.", "points": ["자음·모음, 낱말, 문장을 떠올려봐요.", "좋아하는 낱말을 소리 내어 읽어봐요.", "어렵던 낱말은 다시 한 번 써봐요."], "example": "오늘 배운 낱말로 짧은 문장을 만들어봐요!"},
    ],
    "영어": [
        {"icon": "ph-fill ph-translate", "summary": "영어에는 26개의 알파벳이 있어요.", "points": ["A부터 Z까지 순서가 있어요.", "큰 글자(대문자)와 작은 글자(소문자)가 있어요.", "A-a, B-b처럼 짝이 있어요."], "example": "A a   B b   C c 🐱"},
        {"icon": "ph-fill ph-speaker-high", "summary": "알파벳마다 소리가 있어요.", "points": ["A는 '애', B는 '브' 소리가 나요.", "소리를 이어 붙이면 단어를 읽을 수 있어요.", "입 모양을 따라 하면 더 쉬워요."], "example": "c-a-t → cat (고양이) 🐈"},
        {"icon": "ph-fill ph-cards", "summary": "그림과 함께 쉬운 단어를 익혀요.", "points": ["cat, dog, sun처럼 짧은 단어부터 시작해요.", "그림을 보면 뜻을 쉽게 알 수 있어요.", "소리 내어 여러 번 말해봐요."], "example": "sun ☀️ = 해"},
        {"icon": "ph-fill ph-chat-text", "summary": "단어가 모이면 영어 문장이 돼요.", "points": ["'I am ~', 'It is ~'로 시작해봐요.", "단어 사이는 띄어 써요.", "문장 끝에는 마침표(.)를 붙여요."], "example": "It is a cat. 🐱"},
        {"icon": "ph-fill ph-star", "summary": "배운 영어를 함께 정리해봐요.", "points": ["알파벳 노래를 불러봐요.", "좋아하는 단어를 말해봐요.", "짧은 문장 하나를 소리 내어 읽어봐요."], "example": "Hello! I am happy. 😊"},
    ],
    "수학": [
        {"icon": "ph-fill ph-hash", "summary": "물건을 하나씩 세어 수를 알아봐요.", "points": ["하나, 둘, 셋… 순서대로 세요.", "마지막에 센 수가 전체 개수예요.", "0은 '아무것도 없다'는 뜻이에요."], "example": "🍎🍎🍎 = 사과 3개"},
        {"icon": "ph-fill ph-plus", "summary": "두 수를 모으면 더 커져요.", "points": ["＋는 '더한다'는 뜻이에요.", "그림을 합쳐서 세어봐요.", "2 + 3 은 5가 돼요."], "example": "🐱🐱 + 🐱🐱🐱 = 5마리"},
        {"icon": "ph-fill ph-minus", "summary": "있던 것에서 덜어내면 작아져요.", "points": ["−는 '뺀다'는 뜻이에요.", "하나씩 지우며 세어봐요.", "5 − 2 는 3이 남아요."], "example": "🍪🍪🍪🍪🍪 에서 2개 먹으면 3개"},
        {"icon": "ph-fill ph-shapes", "summary": "모양에는 규칙이 숨어 있어요.", "points": ["동그라미, 세모, 네모를 찾아봐요.", "반복되는 순서를 규칙이라 해요.", "다음에 올 모양을 맞혀봐요."], "example": "🔴🔵🔴🔵 다음은? 🔴"},
        {"icon": "ph-fill ph-star", "summary": "배운 수학을 놀이처럼 정리해요.", "points": ["수를 세고, 더하고, 빼봐요.", "우리 주변의 모양을 찾아봐요.", "좋아하는 수로 문제를 만들어봐요."], "example": "간식을 세고 나눠보면 그게 수학이에요!"},
    ],
    "과학": [
        {"icon": "ph-fill ph-paw-print", "summary": "동물마다 사는 곳과 특징이 달라요.", "points": ["새는 날고, 물고기는 헤엄쳐요.", "다리 수와 먹이가 서로 달라요.", "우리 주변 동물을 관찰해봐요."], "example": "고양이는 다리가 4개, 야옹 하고 울어요 🐈"},
        {"icon": "ph-fill ph-plant", "summary": "식물은 자라면서 모습이 바뀌어요.", "points": ["씨앗 → 싹 → 잎 → 꽃 순서로 자라요.", "물과 햇빛이 있어야 잘 자라요.", "잎, 줄기, 뿌리가 있어요."], "example": "씨앗을 심고 물을 주면 싹이 나요 🌱"},
        {"icon": "ph-fill ph-cloud-sun", "summary": "계절마다 날씨가 달라져요.", "points": ["봄·여름·가을·겨울이 있어요.", "맑음, 비, 눈 같은 날씨가 있어요.", "날씨에 맞게 옷을 입어요."], "example": "겨울에는 눈이 오고 추워요 ❄️"},
        {"icon": "ph-fill ph-drop", "summary": "물과 공기는 보이거나 안 보여요.", "points": ["물은 얼면 얼음, 끓으면 김이 돼요.", "공기는 안 보여도 우리 곁에 있어요.", "바람은 움직이는 공기예요."], "example": "물이 얼면 단단한 얼음이 돼요 🧊"},
        {"icon": "ph-fill ph-star", "summary": "배운 과학을 떠올려봐요.", "points": ["동물, 식물, 날씨를 관찰해봐요.", "궁금한 걸 '왜?' 하고 물어봐요.", "오늘 본 것 하나를 그려봐요."], "example": "창밖 날씨를 관찰하고 말해봐요!"},
    ],
    "사회": [
        {"icon": "ph-fill ph-users-three", "summary": "옛날 사람들은 지금과 다르게 살았어요.", "points": ["동굴이나 초가집에서 살았어요.", "돌과 나무로 도구를 만들었어요.", "불을 사용하며 생활이 편해졌어요."], "example": "아주 먼 옛날엔 돌로 도구를 만들었어요 🪨"},
        {"icon": "ph-fill ph-crown", "summary": "훌륭한 일을 한 위인이 있어요.", "points": ["세종대왕은 한글을 만드셨어요.", "이순신 장군은 나라를 지켰어요.", "위인의 노력을 배울 수 있어요."], "example": "세종대왕님 덕분에 한글을 써요 👑"},
        {"icon": "ph-fill ph-scroll", "summary": "우리나라만의 멋진 문화가 있어요.", "points": ["한복, 한옥, 한글이 있어요.", "설날·추석 같은 명절이 있어요.", "전통 놀이와 음식이 있어요."], "example": "설날에는 세배를 하고 떡국을 먹어요 🥢"},
        {"icon": "ph-fill ph-flag", "summary": "우리나라는 아주 오래전에 시작됐어요.", "points": ["단군 이야기가 전해져요.", "여러 나라가 있다가 하나가 됐어요.", "옛 이야기로 시작을 알 수 있어요."], "example": "먼 옛날의 이야기가 전해 내려와요 📜"},
        {"icon": "ph-fill ph-star", "summary": "배운 사회를 이야기처럼 정리해요.", "points": ["옛날 사람들의 생활을 떠올려봐요.", "기억나는 위인을 말해봐요.", "좋아하는 옛이야기를 골라봐요."], "example": "가장 기억에 남는 이야기를 말해봐요!"},
    ],
    "생활": [
        {"icon": "ph-fill ph-traffic-sign", "summary": "길에서는 안전 약속을 지켜요.", "points": ["초록불에 손 들고 건너요.", "좌우를 살피고 건너요.", "횡단보도로만 건너요."], "example": "초록불이 켜지면 좌우를 보고 건너요 🚦"},
        {"icon": "ph-fill ph-house-line", "summary": "집에서도 조심할 것이 있어요.", "points": ["뜨거운 것, 날카로운 것은 조심해요.", "콘센트에 손을 넣지 않아요.", "위험할 땐 어른께 말해요."], "example": "가스나 불은 어른과 함께 다뤄요 🔥"},
        {"icon": "ph-fill ph-hand-heart", "summary": "친구와 사이좋게 지내는 방법이 있어요.", "points": ["'고마워', '미안해'를 말해요.", "차례를 지키고 함께 나눠요.", "친구 이야기를 잘 들어줘요."], "example": "함께 놀 때는 차례를 지켜요 🤝"},
        {"icon": "ph-fill ph-heartbeat", "summary": "건강을 지키는 생활 습관이 있어요.", "points": ["손을 자주 깨끗이 씻어요.", "골고루 먹고 물을 마셔요.", "일찍 자고 일찍 일어나요."], "example": "밥 먹기 전에 손을 깨끗이 씻어요 🧼"},
        {"icon": "ph-fill ph-star", "summary": "배운 생활 약속을 떠올려봐요.", "points": ["안전 약속을 하나씩 말해봐요.", "오늘 지킨 좋은 습관을 칭찬해봐요.", "친구에게 다정하게 말해봐요."], "example": "오늘 지킨 안전 약속을 자랑해봐요!"},
    ],
}

# 배지 화면 B (12종)
BADGES = [
    {"name": "첫 걸음", "desc": "첫 학습을 완료했어요", "icon": "ph-fill ph-sneaker-move", "color": "#FF5A6E", "bg": "#FFE3E9"},
    {"name": "매의 눈", "desc": "그림 찾기 정답률 85%", "icon": "ph-fill ph-eye", "color": "#2E7BFF", "bg": "#E6F0FF"},
    {"name": "한글 박사", "desc": "낱말 50개 맞히기", "icon": "ph-fill ph-text-aa", "color": "#FF922E", "bg": "#FFEDE0"},
    {"name": "계산 왕", "desc": "숫자 놀이터 30문제", "icon": "ph-fill ph-plus-minus", "color": "#17B08C", "bg": "#DFF6ED"},
    {"name": "드래그 마스터", "desc": "끌어놓기 100% 달성", "icon": "ph-fill ph-hand-grabbing", "color": "#33C892", "bg": "#DFF6ED"},
    {"name": "꾸준왕", "desc": "7일 연속 학습", "icon": "ph-fill ph-calendar-check", "color": "#8B6BFF", "bg": "#EDE6FF"},
    {"name": "하트 부자", "desc": "하트 잃지 않고 클리어", "icon": "ph-fill ph-heart", "color": "#FF5A6E", "bg": "#FFE3E9"},
    {"name": "별 수집가", "desc": "별 500개 모으기", "icon": "ph-fill ph-star", "color": "#F0A400", "bg": "#FFF3D6"},
    {"name": "불꽃 학습왕", "desc": "14일 연속 학습", "icon": "ph-fill ph-fire", "color": "#FF922E", "bg": "#FFEDE0"},
    {"name": "안전 지킴이", "desc": "생활 안전 전부 완료", "icon": "ph-fill ph-shield-check", "color": "#8B6BFF", "bg": "#EDE6FF"},
    {"name": "미로 탐험가", "desc": "냥이 미로 클리어", "icon": "ph-fill ph-path", "color": "#FF6DA6", "bg": "#FFE9F1"},
    {"name": "완벽주의자", "desc": "정답률 100% 5번", "icon": "ph-fill ph-crown-simple", "color": "#F0A400", "bg": "#FFF3D6"},
]

# 하은의 배지 상태 (획득 8 / 도전 중 4) — foot: 화면 하단 라벨
BADGE_STATE = {
    "첫 걸음": {"earned": True, "foot": "6월 12일 획득"},
    "매의 눈": {"earned": True, "foot": "오늘 획득"},
    "한글 박사": {"earned": True, "foot": "6월 28일 획득"},
    "계산 왕": {"earned": True, "foot": "6월 25일 획득"},
    "드래그 마스터": {"earned": True, "foot": "6월 30일 획득"},
    "꾸준왕": {"earned": True, "foot": "6월 20일 획득"},
    "하트 부자": {"earned": True, "foot": "6월 22일 획득"},
    "별 수집가": {"earned": True, "foot": "어제 획득"},
    "불꽃 학습왕": {"earned": False, "foot": "12/14일", "progress": 12 / 14},
    "안전 지킴이": {"earned": False, "foot": "0/4 단계", "progress": 0.0},
    "미로 탐험가": {"earned": False, "foot": "곧 열려요", "progress": 0.0},
    "완벽주의자": {"earned": False, "foot": "2/5 회", "progress": 0.4},
}

# 프로필 꾸미기 CATALOG (모자 8 / 배경 8 / 스티커 8) — key: 디자인 id
SHOP_CATALOG = {
    "hat": [
        {"key": "none", "name": "없음", "icon": "ph-fill ph-prohibit-inset", "color": "#C0B6A9", "price": 0},
        {"key": "cap", "name": "야구모자", "icon": "ph-fill ph-baseball-cap", "color": "#FF5A4D", "price": 0},
        {"key": "crown", "name": "왕관", "icon": "ph-fill ph-crown", "color": "#F0A400", "price": 120},
        {"key": "party", "name": "파티모자", "icon": "ph-fill ph-confetti", "color": "#8B6BFF", "price": 80},
        {"key": "grad", "name": "학사모", "icon": "ph-fill ph-graduation-cap", "color": "#2E7BFF", "price": 200},
        {"key": "flower", "name": "꽃", "icon": "ph-fill ph-flower", "color": "#FF6DA6", "price": 60},
        {"key": "santa", "name": "눈꽃", "icon": "ph-fill ph-snowflake", "color": "#4AA6FF", "price": 90},
        {"key": "detective", "name": "탐정모자", "icon": "ph-fill ph-detective", "color": "#8A6D3B", "price": 150},
    ],
    "bg": [
        {"key": "peach", "name": "복숭아", "icon": "ph-fill ph-circle", "color": "#FF8A5B", "price": 0, "css": "linear-gradient(150deg,#FFE6BE,#FFCFC9)"},
        {"key": "sky", "name": "하늘", "icon": "ph-fill ph-circle", "color": "#4AA6FF", "price": 0, "css": "linear-gradient(150deg,#DDEEFF,#C2DBFF)"},
        {"key": "mint", "name": "민트", "icon": "ph-fill ph-circle", "color": "#17B08C", "price": 50, "css": "linear-gradient(150deg,#C7F0E2,#B3E8D8)"},
        {"key": "grape", "name": "포도", "icon": "ph-fill ph-circle", "color": "#8B6BFF", "price": 50, "css": "linear-gradient(150deg,#E7DAFF,#D6C7FF)"},
        {"key": "star", "name": "별밤", "icon": "ph-fill ph-circle", "color": "#3A3340", "price": 150, "css": "linear-gradient(150deg,#4A4258,#2E2A3A)"},
        {"key": "rose", "name": "장미", "icon": "ph-fill ph-circle", "color": "#FF6DA6", "price": 90, "css": "linear-gradient(150deg,#FFDCE8,#FFC2D6)"},
        {"key": "ocean", "name": "바다", "icon": "ph-fill ph-circle", "color": "#0EA5B5", "price": 120, "css": "linear-gradient(150deg,#C2F0F5,#9DE0EA)"},
        {"key": "sunset", "name": "노을", "icon": "ph-fill ph-circle", "color": "#FF7A5B", "price": 130, "css": "linear-gradient(150deg,#FFD9B0,#FFB0C4)"},
    ],
    "sticker": [
        {"key": "none", "name": "없음", "icon": "ph-fill ph-prohibit-inset", "color": "#C0B6A9", "price": 0},
        {"key": "star", "name": "별", "icon": "ph-fill ph-star", "color": "#F0A400", "price": 0},
        {"key": "heart", "name": "하트", "icon": "ph-fill ph-heart", "color": "#FF5A6E", "price": 40},
        {"key": "fish", "name": "생선", "icon": "ph-fill ph-fish", "color": "#2E7BFF", "price": 40},
        {"key": "medal", "name": "메달", "icon": "ph-fill ph-medal", "color": "#17B08C", "price": 100},
        {"key": "rainbow", "name": "무지개", "icon": "ph-fill ph-rainbow", "color": "#8B6BFF", "price": 110},
        {"key": "butterfly", "name": "나비", "icon": "ph-fill ph-butterfly", "color": "#FF6DA6", "price": 70},
        {"key": "lightning", "name": "번개", "icon": "ph-fill ph-lightning", "color": "#F0A400", "price": 90},
    ],
}

# 검색 화면 ITEMS 14건 (kw: 검색 키워드)
SEARCH_ITEMS = [
    {"title": "국어", "tag": "과목", "desc": "낱말·문장·글의 속뜻을 익히는 국어 한 판", "icon": "ph-fill ph-book-open", "subject": "국어", "href": "/student/game?subject=국어", "kw": "국어 한글 낱말 글자 읽기 kor"},
    {"title": "영어", "tag": "과목", "desc": "단어·문장·문법으로 배우는 영어 한 판", "icon": "ph-fill ph-translate", "subject": "영어", "href": "/student/game?subject=영어", "kw": "영어 알파벳 단어 eng english"},
    {"title": "수학", "tag": "과목", "desc": "수·연산·도형·측정을 배우는 수학 한 판", "icon": "ph-fill ph-plus-minus", "subject": "수학", "href": "/student/game?subject=수학", "kw": "수학 숫자 셈 덧셈 뺄셈 연산 math"},
    {"title": "과학", "tag": "과목", "desc": "관찰하고 탐구하는 과학 한 판", "icon": "ph-fill ph-flask", "subject": "과학", "href": "/student/game?subject=과학", "kw": "과학 관찰 탐구 실험 sci"},
    {"title": "사회", "tag": "과목", "desc": "지도·지역·공공기관을 알아가는 사회 한 판", "icon": "ph-fill ph-scroll", "subject": "사회", "href": "/student/game?subject=사회", "kw": "사회 이야기 옛날 soc"},
    {"title": "생활", "tag": "과목", "desc": "생활 속 안전과 지혜를 배우는 생활 한 판", "icon": "ph-fill ph-house-line", "subject": "생활", "href": "/student/game?subject=생활", "kw": "생활 안전 지혜 life"},
    {"title": "한글 낱말 찾기", "tag": "놀이", "desc": "그림을 보고 알맞은 낱말 고르기", "icon": "ph-fill ph-text-aa", "subject": "국어", "href": "/student/game?subject=국어", "kw": "한글 낱말 찾기 글자 단어"},
    {"title": "숫자 놀이터", "tag": "놀이", "desc": "더하기·빼기 답을 상자에 담기", "icon": "ph-fill ph-calculator", "subject": "수학", "href": "/student/game?subject=수학", "kw": "숫자 놀이터 더하기 빼기 계산"},
    {"title": "끌어놓기 놀이", "tag": "놀이", "desc": "정답 카드를 목표 칸으로 드래그", "icon": "ph-fill ph-hand-grabbing", "subject": "수학", "href": "/student/game?subject=수학", "kw": "끌어놓기 드래그 카드 분류"},
    {"title": "그림 찾기 퀴즈", "tag": "놀이", "desc": "조건에 맞는 그림을 골라요", "icon": "ph-fill ph-image", "subject": "과학", "href": "/student/game?subject=과학", "kw": "그림 찾기 퀴즈 이미지 사진"},
    {"title": "안전 지킴이", "tag": "놀이", "desc": "안전한 행동과 위험한 것 구분", "icon": "ph-fill ph-shield-check", "subject": "생활", "href": "/student/game?subject=생활", "kw": "안전 지킴이 위험 생활안전"},
    {"title": "냥이 미로 탐험", "tag": "놀이", "desc": "고양이를 생선가게까지 데려가기", "icon": "ph-fill ph-path", "subject": "생활", "href": "/student/game?subject=생활", "kw": "미로 탐험 냥이 길찾기 경로"},
    {"title": "오늘의 퀴즈", "tag": "바로가기", "desc": "오늘 할당된 퀴즈 풀기", "icon": "ph-fill ph-lightning", "subject": None, "href": "/student/daily-quiz", "kw": "오늘 퀴즈 할당 데일리"},
    {"title": "배지", "tag": "바로가기", "desc": "모은 배지와 보상 확인", "icon": "ph-fill ph-medal", "subject": None, "href": "/student/badges", "kw": "배지 보상 상장 트로피"},
]

# 게임화면 SUBJECTS 프리셋
GAME_SUBJECTS = {
    "국어": {"gameTitle": "한글 낱말 찾기", "gameSub": "그림 보고 낱말 고르기", "catLabel": "낱말·한글", "cheer": "천천히, 잘 하고 있어요! 🐾", "current": 3, "total": 5, "score": 210, "correct": 2, "wrong": 0, "streak": 2},
    "영어": {"gameTitle": "Word Match", "gameSub": "그림 보고 영어 단어 고르기", "catLabel": "Word·English", "cheer": "한 문제씩 차근차근 가볼까요? ✨", "current": 1, "total": 5, "score": 150, "correct": 0, "wrong": 0, "streak": 0},
    "수학": {"gameTitle": "숫자 세기", "gameSub": "그림 세고 숫자 고르기", "catLabel": "수·셈", "cheer": "집중력이 대단해요! 👏", "current": 4, "total": 5, "score": 320, "correct": 3, "wrong": 0, "streak": 3},
    "과학": {"gameTitle": "과학 관찰 퀴즈", "gameSub": "잘 보고 알맞은 답 고르기", "catLabel": "관찰·과학", "cheer": "궁금한 걸 잘 찾아내고 있어요! 🔍", "current": 1, "total": 5, "score": 40, "correct": 0, "wrong": 0, "streak": 0},
    "사회": {"gameTitle": "사회 탐구 퀴즈", "gameSub": "이야기 읽고 답 고르기", "catLabel": "지역·사회", "cheer": "우리 지역, 참 잘 아네요! 🗺️", "current": 2, "total": 5, "score": 120, "correct": 1, "wrong": 0, "streak": 1},
    "생활": {"gameTitle": "생활 안전 퀴즈", "gameSub": "상황 보고 바른 행동 고르기", "catLabel": "안전·생활", "cheer": "안전을 잘 챙기고 있어요! 🚸", "current": 4, "total": 5, "score": 260, "correct": 2, "wrong": 1, "streak": 1},
}

GAME_QUESTIONS = {
    "국어": {"q": "이 그림은 무슨 낱말일까요? 📖", "pre": "그림을 잘 보고, 알맞은 ", "hi": "낱말 카드", "post": "를 눌러요."},
    "영어": {"q": "이 그림은 영어로 뭘까요? 🔤", "pre": "그림을 잘 보고, 알맞은 ", "hi": "영어 단어", "post": "를 눌러요."},
    "수학": {"q": "별이 모두 몇 개일까요? ⭐", "pre": "별을 하나씩 세고, 알맞은 ", "hi": "숫자 카드", "post": "를 눌러요."},
    "과학": {"q": "물에 둥둥 뜨는 것은? 💧", "pre": "가볍고 물에 뜨는 것을 생각하며, 알맞은 ", "hi": "답 카드", "post": "를 눌러요."},
    "사회": {"q": "한글을 만드신 임금님은? 👑", "pre": "옛날 이야기를 떠올리며, 알맞은 ", "hi": "답 카드", "post": "를 눌러요."},
    "생활": {"q": "횡단보도에서 바른 행동은? 🚸", "pre": "안전을 먼저 생각하며, 알맞은 ", "hi": "행동 카드", "post": "를 눌러요."},
}

GAME_REWARDS = {"국어": 3, "영어": 1, "수학": 4, "과학": 0, "사회": 2, "생활": 4}

# 학습결과 SUBJECTS 프리셋
RESULT_SUBJECTS = {
    "국어": {"cleared": 5, "correct": 5, "score": "+150", "time": "2:40", "streak": 5, "ai": "글의 속뜻까지 잘 파악했어요! 낱말과 문장을 꼼꼼히 읽는 습관이 멋져요. 다음 단계도 잘 해낼 거예요! 🐾"},
    "영어": {"cleared": 3, "correct": 4, "score": "+90", "time": "2:10", "streak": 3, "ai": "영어 문장과 문법을 척척 풀었어요! 단어의 쓰임을 잘 이해했네요. 다음 단계도 잘 해낼 거예요! 🐾"},
    "수학": {"cleared": 5, "correct": 5, "score": "+160", "time": "3:05", "streak": 5, "ai": "계산과 도형 문제를 아주 정확하게 풀었어요! 차근차근 따지는 방법이 완벽했어요. 다음 단계도 도전해봐요! 🐾"},
    "과학": {"cleared": 2, "correct": 4, "score": "+95", "time": "2:20", "streak": 3, "ai": "관찰하고 탐구를 참 잘했어요! 원리를 꼼꼼히 살피는 눈이 멋져요. 다음엔 더 깊은 탐구에 도전해봐요! 🐾"},
    "사회": {"cleared": 1, "correct": 4, "score": "+80", "time": "2:05", "streak": 3, "ai": "우리 지역과 사회를 잘 이해하고 있네요! 지도와 공공기관 문제를 멋지게 풀었어요. 다음 단계로 가볼까요? 🐾"},
    "생활": {"cleared": 1, "correct": 4, "score": "+110", "time": "2:35", "streak": 4, "ai": "안전 규칙을 잘 지켰어요! 멈추고, 살피고, 건너기 — 참 잘 기억했어요. 다음 단계도 안전하게! 🐾"},
}
RESULT_LEVELS = ["기초 익히기", "기초 다지기", "조금 더 어렵게", "도전 문제", "마스터 챌린지"]
RESULT_TODAY_DONE = ["국어", "영어", "수학"]

# 프로필 꾸미기 반 랭킹
RANK_OTHERS = [
    {"name": "윤서준", "score": 2485}, {"name": "이도아", "score": 2410}, {"name": "박시우", "score": 2295},
    {"name": "최유나", "score": 2240}, {"name": "정민재", "score": 2185}, {"name": "강예린", "score": 2120},
    {"name": "한지호", "score": 2060}, {"name": "오세아", "score": 1990},
]
RANK_CLASS_SIZE = 24
RANK_MY_SCORE = 2360

# AI 선생님(학생) 질문-답 사전
STUDENT_AI_ANSWERS = {
    "그림 찾기가 어려워요": "고양이는 귀가 뾰족하고 수염이 길어요. 강아지는 귀가 아래로 처진 경우가 많지! 헷갈릴 땐 \"귀 모양\"부터 보면 훨씬 쉬워져요. 다음엔 100% 맞힐 수 있을 거야! 💪",
    "받침이 자꾸 헷갈려요": "받침은 글자 아래에 오는 소리예요. 예를 들어 \"곰\"은 ㄱ+ㅗ+ㅁ, 마지막 ㅁ이 받침이야. 소리를 천천히 나눠서 말해보면 어떤 받침인지 들려요! 🎵",
    "오늘 뭐 배우면 좋아?": "어제 숫자 놀이터를 조금 어려워했으니까, 오늘은 숫자 놀이터 2단계를 추천해! 그리고 아직 시작 안 한 안전 지킴이도 재미있을 거야. 같이 해볼까? 🚀",
    "나 칭찬해줘!": "하은이는 요즘 꾸준히 학습하고 있어! 정말 대단해 👏 끌어놓기 놀이도 점점 늘고 있어. 꾸준함이 최고의 재능이야. 오늘도 최고! 🌟",
}
STUDENT_AI_DEFAULT = "좋은 질문이야! 천천히 같이 알아보자. 😊"

# 학부모 상담 AI 답변 세트 (자녀 이름 기준)
PARENT_AI_ANSWERS = {
    "하은": {
        "intro": "안녕하세요, 김서연 학부모님. 하은이의 학습 데이터를 함께 살펴보는 AI 학습 상담사예요. 이번 주 하은이는 한글·그림 찾기에서 강점을 보였고, 숫자 놀이에서는 조금 더 연습이 필요해 보여요.",
        "answers": {
            "이번 주 우리 아이 어땠나요?": "이번 주 하은이는 총 14회 학습했고 평균 정답률은 89%로 지난주보다 4%p 올랐어요. 특히 한글 낱말 찾기(96%)와 그림 찾기(92%)에서 꾸준함이 돋보였어요. 다만 숫자 놀이터는 72%로, 덧셈·뺄셈 개념에서 조금 헷갈려 하는 모습이 보였습니다.",
            "숫자를 어려워하는데 어떻게 도울까요?": "하은이는 드래그 조작은 능숙한데 수 개념에서 머뭇거리는 편이에요. 집에서 사탕이나 블록으로 \"3개에 2개를 더하면?\"처럼 눈으로 보고 세는 활동을 권해요. 하루 5문제씩, 난이도를 한 단계 낮춰 성공 경험을 쌓게 해주시면 자신감이 붙어요.",
            "집에서 뭘 도와주면 좋을까요?": "세 가지를 추천드려요. ① 헷갈린 낱말 5개를 소리 내어 함께 읽기, ② 큰 물건으로 더하기·빼기 놀이, ③ 학습 후 \"오늘 뭐가 제일 재미있었어?\"라고 물어 성취를 언어로 표현하게 하기. 짧고 즐겁게, 칭찬 위주로 해주시는 게 가장 효과적이에요.",
            "학습 시간은 얼마가 적당한가요?": "7세 어린이는 한 번에 10~15분, 하루 20분 내외가 적당해요. 하은이는 평균 풀이 시간이 12초로 집중력이 좋은 편이니, 짧게 자주 하는 리듬이 잘 맞아요. 피곤해하면 바로 멈추고 다음 날 이어가는 것이 습관 형성에 더 좋습니다.",
        },
    },
    "도윤": {
        "intro": "안녕하세요, 김서연 학부모님. 이번엔 도윤이의 학습 상담을 도와드릴게요. 도윤이는 그림 찾기와 끌어놓기 놀이를 특히 좋아하고, 한글 낱말은 이제 시작하는 단계예요.",
        "answers": {
            "이번 주 우리 아이 어땠나요?": "이번 주 도윤이는 9회 학습했고 평균 정답률은 76%예요. 그림 찾기(88%)와 끌어놓기(84%)를 즐거워했어요. 한글 낱말은 이제 막 시작한 단계라 62% 정도인데, 5세임을 감안하면 아주 잘 따라오고 있어요.",
            "숫자를 어려워하는데 어떻게 도울까요?": "도윤이는 아직 5세라 수를 그림·사물과 연결하는 단계예요. 계단을 오르며 \"하나, 둘, 셋\" 세기, 간식 개수 세기처럼 일상 속 놀이로 접하게 해주세요. 정답보다 세는 즐거움에 초점을 맞추면 좋아요.",
            "집에서 뭘 도와주면 좋을까요?": "① 그림책을 함께 보며 사물 이름 말하기, ② 좋아하는 그림 찾기 놀이를 하루 3~4문제, ③ 잘했을 때 바로 안아주고 칭찬하기. 도윤이는 짧고 놀이 같은 활동에서 가장 잘 집중해요.",
            "학습 시간은 얼마가 적당한가요?": "5세는 한 번에 8~10분, 하루 10~15분이면 충분해요. 도윤이는 오래 앉아있기보다 놀이처럼 짧게 여러 번 하는 게 잘 맞아요. 재미없어하면 바로 멈추는 것이 중요해요.",
        },
    },
}

# 학부모 리포트 CHILDREN 프리셋 (자녀 이름 기준)
PARENT_REPORT = {
    "하은": {
        "grade": "A", "percentile": "12%",
        "strengths": [{"name": "국어", "pct": "96%"}, {"name": "과학", "pct": "92%"}, {"name": "생활", "pct": "88%"}],
        "weaknesses": [{"name": "수학", "pct": "72%"}, {"name": "영어", "pct": "78%"}, {"name": "사회", "pct": "75%"}],
        "strength_note": "국어·과학에서 꾸준히 높은 정답률을 유지하고 있어요. 새로운 도전 문제도 잘 소화해요.",
        "weakness_note": "수학에서 개념 오답이 반복돼요. 사과 세기처럼 눈으로 보는 활동이 도움돼요.",
        "bars": [{"label": "3주전", "v": 80}, {"label": "2주전", "v": 83}, {"label": "지난주", "v": 85}, {"label": "이번주", "v": 89}],
        "trend_delta": "+4%p 상승",
        "ai_comment": "하은이는 스스로 학습하는 습관이 잘 잡혀 있어요. 수학 놀이만 조금 더 함께해 주시면 균형 잡힌 성장이 기대돼요.",
        "kpis": [
            {"icon": "ph-fill ph-calendar-check", "value": "14회", "label": "학습 횟수"},
            {"icon": "ph-fill ph-target", "value": "89%", "label": "평균 정답률"},
            {"icon": "ph-fill ph-timer", "value": "12초", "label": "평균 풀이 시간"},
            {"icon": "ph-fill ph-medal", "value": "3개", "label": "새 배지"},
        ],
        "trend_base": [80, 82, 83, 85, 87, 89],
        "class_base": [79, 80, 80, 81, 82, 82],
        "subj_last": {"all": 89, "국어": 96, "영어": 78, "수학": 72, "과학": 92, "사회": 75, "생활": 88},
        "class_last": {"all": 82, "국어": 88, "영어": 74, "수학": 69, "과학": 85, "사회": 72, "생활": 83},
    },
    "도윤": {
        "grade": "B+", "percentile": "28%",
        "strengths": [{"name": "생활", "pct": "90%"}, {"name": "국어", "pct": "84%"}, {"name": "과학", "pct": "82%"}],
        "weaknesses": [{"name": "수학", "pct": "64%"}, {"name": "끌어놓기", "pct": "70%"}, {"name": "영어", "pct": "73%"}],
        "strength_note": "생활·국어 그림 문제에 흥미가 높아요. 좋아하는 주제부터 시작하면 몰입도가 좋아요.",
        "weakness_note": "드래그 조작에서 목표 근처 실패가 잦아요. 큰 카드 모드로 연습하면 나아져요.",
        "bars": [{"label": "3주전", "v": 70}, {"label": "2주전", "v": 72}, {"label": "지난주", "v": 74}, {"label": "이번주", "v": 78}],
        "trend_delta": "+4%p 상승",
        "ai_comment": "도윤이는 집중 시간이 조금씩 늘고 있어요. 짧고 재미있는 놀이를 자주 반복하는 것이 효과적이에요.",
        "kpis": [
            {"icon": "ph-fill ph-calendar-check", "value": "9회", "label": "학습 횟수"},
            {"icon": "ph-fill ph-target", "value": "76%", "label": "평균 정답률"},
            {"icon": "ph-fill ph-timer", "value": "18초", "label": "평균 풀이 시간"},
            {"icon": "ph-fill ph-medal", "value": "1개", "label": "새 배지"},
        ],
        "trend_base": [68, 70, 71, 72, 74, 78],
        "class_base": [79, 80, 80, 81, 82, 82],
        "subj_last": {"all": 78, "국어": 84, "영어": 73, "수학": 64, "과학": 82, "사회": 72, "생활": 90},
        "class_last": {"all": 82, "국어": 88, "영어": 74, "수학": 69, "과학": 85, "사회": 72, "생활": 83},
    },
}

# 학부모 주간 요약 (학부모.dc.html / 학부모 도윤.dc.html)
PARENT_SUMMARY = {
    "하은": {
        "status": "좋음",
        "banner_title": "하은이가 이번 주에 꾸준히 잘 하고 있어요!",
        "banner_body": "캡챠 게임으로 국어·과학을 즐겁게 풀었어요. 수학 게임은 조금 더 함께하면 좋아요.",
        "kpis": [
            {"value": "14회", "label": "이번 주 학습 횟수", "delta": "+3회"},
            {"value": "89%", "label": "평균 정답률", "delta": "+4%p"},
            {"value": "12초", "label": "평균 풀이 시간", "delta": "-2초"},
            {"value": "3개", "label": "이번 주 새 배지", "delta": "+2개"},
        ],
    },
    "도윤": {
        "status": "학습 뜸함",
        "banner_title": "도윤이가 요즘 학습이 조금 뜸해요",
        "banner_body": "며칠 쉬어간 것 같아요. 오늘 5분만 함께 시작해볼까요? 짧은 놀이부터 추천해요.",
        "kpis": [
            {"value": "6회", "label": "이번 주 학습 횟수", "delta": "-2회"},
            {"value": "84%", "label": "평균 정답률", "delta": "+4%p"},
            {"value": "18초", "label": "평균 풀이 시간", "delta": "-2초"},
            {"value": "1개", "label": "이번 주 새 배지", "delta": "+1개"},
        ],
    },
}

PARENT_SUMMARY_COMMON = {
    "period_label": "6월 넷째 주 (6.22~6.28)",
    "strengths": [
        {"name": "한글 낱말 찾기", "pct": "96%"},
        {"name": "그림 찾기 퀴즈", "pct": "92%"},
        {"name": "안전 지킴이", "pct": "88%"},
    ],
    "weaknesses": [
        {"name": "숫자 놀이터", "pct": "72%"},
        {"name": "끌어놓기 (드래그)", "pct": "78%"},
        {"name": "받침 낱말 완성", "pct": "75%"},
    ],
    "reasons": [
        {"tag": "조작 어려움", "icon": "ph-fill ph-hand-tap", "body": "개념은 이해했지만 정답 위치 근처에서 두 번 놓쳐, 터치·드래그 조작에 살짝 어려움이 있었어요."},
        {"tag": "개념 혼동", "icon": "ph-fill ph-lightbulb", "body": "드래그 조작은 원활했지만 덧셈·뺄셈 개념에서 헷갈린 것으로 보여요. 함께 세어보면 좋아요."},
        {"tag": "선택지 혼동", "icon": "ph-fill ph-arrows-left-right", "body": "비슷한 낱말 그림 사이에서 여러 번 오갔어요. 헷갈리는 낱말을 함께 읽어보면 도움이 돼요."},
    ],
    "recommendations": [
        {"icon": "ph-fill ph-plus-minus", "text": "숫자 놀이터를 하루 5문제씩, 난이도는 조금 낮춰서"},
        {"icon": "ph-fill ph-hand-grabbing", "text": "큰 카드로 드래그 연습 — 목표 칸을 크게 설정했어요"},
        {"icon": "ph-fill ph-book-open-text", "text": "헷갈린 낱말 5개를 소리 내어 함께 읽어보기"},
    ],
}

# 선생님 대시보드 (선생님.dc.html)
TEACHER_DASHBOARD = {
    "class_name": "1-2반",
    "kpis": {
        "total_students": 22,
        "today_done": 18,
        "today_done_pct": "82%",
        "avg_accuracy": 90,
        "avg_accuracy_delta": "+3%p",
        "need_help": 3,
    },
    "assignment": {"title": "숫자 놀이터 배정", "done": 16, "total": 22},
    "participation_delta": "+9%",
    "bar_data": [
        {"day": "월", "n": 16}, {"day": "화", "n": 19}, {"day": "수", "n": 14},
        {"day": "목", "n": 20}, {"day": "금", "n": 18, "today": True}, {"day": "토", "n": 9}, {"day": "일", "n": 6},
    ],
    "game_bars": [
        {"label": "한글 낱말 찾기", "pct": 94, "color": "#FF5A6E"},
        {"label": "그림 찾기 퀴즈", "pct": 92, "color": "#2E7BFF"},
        {"label": "안전 지킴이", "pct": 88, "color": "#8B6BFF"},
        {"label": "끌어놓기 놀이", "pct": 79, "color": "#17B08C"},
        {"label": "숫자 놀이터", "pct": 71, "color": "#FF922E"},
    ],
    "attention": [
        {"name": "박도윤", "note": "숫자 놀이터 정답률 62% · 개념 오답 추정", "tag": "숫자 도움"},
        {"name": "최서아", "note": "3일 연속 학습 안 함 · 참여 독려 필요", "tag": "참여 저조"},
        {"name": "김하람", "note": "드래그 near_miss 잦음 · 조작 어려움 추정", "tag": "조작 도움"},
    ],
    "todos": [
        {"title": "오늘의 그림 찾기 배정", "icon": "ph-fill ph-image", "done": True},
        {"title": "주의 학생 3명 개별 문제 배정", "icon": "ph-fill ph-target", "done": False},
        {"title": "가정 통신문 발송 (숫자 놀이)", "icon": "ph-fill ph-envelope-simple", "done": False},
    ],
    "ai_summary": "이번 주 반 전체는 낱말·그림에 강해요. 숫자 놀이터에서 개념 오답이 늘어, 함께 세어보는 활동을 추천해요.",
}

# 선생님 학습분석 (선생님 학습분석.dc.html)
TEACHER_ANALYTICS = {
    "week": {"kAcc": "89", "kAccDelta": "+3%p", "kActive": "18 / 22명", "kActiveSub": "이번 주 학습 학생", "kSolved": "1,240", "kSolvedSub": "이번 주 푼 문제", "kHelp": "3",
             "trendSub": "요일별 반 평균", "axis": ["월", "화", "수", "목", "금", "토", "일"], "accPct": [83, 85, 84, 88, 89, 91, 92]},
    "month": {"kAcc": "87", "kAccDelta": "+4%p", "kActive": "22 / 22명", "kActiveSub": "이번 달 학습 학생", "kSolved": "5,180", "kSolvedSub": "이번 달 푼 문제", "kHelp": "4",
              "trendSub": "주차별 반 평균", "axis": ["1주", "2주", "3주", "4주", "5주"], "accPct": [80, 83, 85, 87, 89]},
    "term": {"kAcc": "86", "kAccDelta": "+9%p", "kActive": "22 / 22명", "kActiveSub": "학기 중 학습 학생", "kSolved": "38,600", "kSolvedSub": "학기 중 푼 문제", "kHelp": "5",
             "trendSub": "월별 반 평균", "axis": ["3월", "4월", "5월", "6월", "7월"], "accPct": [78, 82, 85, 88, 90]},
}
TEACHER_ANALYTICS_SUBJ_LAST = {"국어": 94, "영어": 82, "수학": 71, "과학": 90, "사회": 85, "생활": 88}
TEACHER_ANALYTICS_SUBJECTS = [
    {"name": "한글 낱말", "icon": "ph-fill ph-book-open", "pct": 94, "delta": 3, "total": 320},
    {"name": "그림 찾기", "icon": "ph-fill ph-image", "pct": 92, "delta": 5, "total": 280},
    {"name": "숫자 놀이터", "icon": "ph-fill ph-plus-minus", "pct": 72, "delta": -4, "total": 300},
    {"name": "끌어놓기", "icon": "ph-fill ph-hand-grabbing", "pct": 78, "delta": 2, "total": 240},
    {"name": "안전 지킴이", "icon": "ph-fill ph-shield-check", "pct": 88, "delta": 6, "total": 200},
]
TEACHER_ANALYTICS_REASONS = [
    {"label": "개념 오답 추정", "pct": "41%", "color": "#FF5A6E"},
    {"label": "조작 실수 추정", "pct": "27%", "color": "#2E7BFF"},
    {"label": "선택지 혼동 추정", "pct": "20%", "color": "#8B6BFF"},
    {"label": "UI 문제 후보", "pct": "12%", "color": "#FF922E"},
]
TEACHER_ANALYTICS_ATTENTION = [
    {"name": "박도현", "note": "숫자 놀이터 정답률 58%", "tag": "개념 보강"},
    {"name": "이서아", "note": "최근 5일 학습 없음", "tag": "학습 뜸함"},
    {"name": "김준우", "note": "끌어놓기 조작 실수 잦음", "tag": "조작 연습"},
]
TEACHER_ANALYTICS_STUDENTS = [
    {"name": "강하은", "acc": 96, "sessions": "14회", "weak": "숫자 놀이터", "trend": "상승"},
    {"name": "박도현", "acc": 58, "sessions": "6회", "weak": "숫자 놀이터", "trend": "하락"},
    {"name": "이서아", "acc": 74, "sessions": "3회", "weak": "끌어놓기", "trend": "하락"},
    {"name": "정민지", "acc": 91, "sessions": "12회", "weak": "안전 지킴이", "trend": "유지"},
    {"name": "최유준", "acc": 88, "sessions": "11회", "weak": "끌어놓기", "trend": "상승"},
    {"name": "김준우", "acc": 69, "sessions": "7회", "weak": "끌어놓기", "trend": "하락"},
]

# 우리반 학생 8명 (우리반.dc.html)
MY_CLASS_STUDENTS = [
    {"login": "student01", "name": "김하은", "age": 7, "code": "CAT-4823", "today": "done", "acc": 96, "streak": 12, "status": "좋음", "solved": 86},
    {"login": "student02", "name": "박도윤", "age": 5, "code": "CAT-5119", "today": "done", "acc": 62, "streak": 3, "status": "도움 필요", "solved": 54},
    {"login": "student03", "name": "최서아", "age": 6, "code": "CAT-6042", "today": "none", "acc": 81, "streak": 0, "status": "학습 뜸함", "solved": 22},
    {"login": "student04", "name": "김하람", "age": 7, "code": "CAT-6188", "today": "done", "acc": 78, "streak": 5, "status": "도움 필요", "solved": 61},
    {"login": "student05", "name": "이준서", "age": 8, "code": "CAT-6205", "today": "done", "acc": 93, "streak": 8, "status": "좋음", "solved": 74},
    {"login": "student06", "name": "정유나", "age": 7, "code": "CAT-6317", "today": "done", "acc": 88, "streak": 6, "status": "좋음", "solved": 69},
    {"login": "student07", "name": "강시우", "age": 6, "code": "CAT-6402", "today": "none", "acc": 74, "streak": 1, "status": "좋음", "solved": 40},
    {"login": "student08", "name": "윤아린", "age": 7, "code": "CAT-6588", "today": "done", "acc": 91, "streak": 9, "status": "좋음", "solved": 80},
]

# 학생 코드 → 화면 표시용 이름(교사/기관 화면은 성 포함 표기)
CODE_FULL_NAME = {s["code"]: s["name"] for s in MY_CLASS_STUDENTS}

# 우리반 상세 AI 코멘트 (학생 코드 기준)
MY_CLASS_COMMENTS = {
    "CAT-5119": "개념 이해는 좋으나 숫자 놀이터에서 덧셈 개념 혼동이 반복돼요. 사과 세기 활동을 추천해요.",
    "CAT-6042": "최근 3일 학습 기록이 없어요. 짧은 그림 찾기부터 다시 시작하도록 독려해 주세요.",
    "CAT-6188": "드래그 시 목표 칸 근처에서 자주 놓쳐요. 큰 카드 모드로 조작 연습이 도움돼요.",
}
MY_CLASS_COMMENT_DEFAULT = "전반적으로 안정적으로 학습하고 있어요. 새로운 도전 문제를 배정해 보세요."

# 우리반 '학생 코드로 연동' 데모 디렉토리
CLASS_DIRECTORY = [
    {"code": "CAT-7001", "name": "한지우", "age": 7},
    {"code": "CAT-7002", "name": "오서준", "age": 6},
    {"code": "CAT-7003", "name": "배하윤", "age": 8},
    {"code": "CAT-7004", "name": "신도현", "age": 7},
]

# 전체학생조회 ROSTER 24명 + 담당 교사
ROSTER = [
    {"name": "강하은", "g": 1, "c": 2, "acc": 96, "sessions": "14회", "weak": "숫자 놀이터", "status": "좋음"},
    {"name": "박도현", "g": 1, "c": 2, "acc": 58, "sessions": "6회", "weak": "숫자 놀이터", "status": "도움 필요"},
    {"name": "이서아", "g": 1, "c": 2, "acc": 74, "sessions": "3회", "weak": "끌어놓기", "status": "학습 뜸함"},
    {"name": "정민지", "g": 1, "c": 2, "acc": 91, "sessions": "12회", "weak": "안전 지킴이", "status": "좋음"},
    {"name": "최유준", "g": 1, "c": 2, "acc": 88, "sessions": "11회", "weak": "끌어놓기", "status": "좋음"},
    {"name": "김준우", "g": 1, "c": 2, "acc": 69, "sessions": "7회", "weak": "끌어놓기", "status": "도움 필요"},
    {"name": "윤서연", "g": 1, "c": 3, "acc": 93, "sessions": "13회", "weak": "한글 낱말", "status": "좋음"},
    {"name": "장민석", "g": 1, "c": 3, "acc": 81, "sessions": "9회", "weak": "숫자 놀이터", "status": "좋음"},
    {"name": "한지호", "g": 1, "c": 3, "acc": 64, "sessions": "4회", "weak": "그림 찾기", "status": "학습 뜸함"},
    {"name": "오수빈", "g": 1, "c": 3, "acc": 87, "sessions": "10회", "weak": "안전 지킴이", "status": "좋음"},
    {"name": "배주은", "g": 2, "c": 1, "acc": 90, "sessions": "12회", "weak": "끌어놓기", "status": "좋음"},
    {"name": "신재원", "g": 2, "c": 1, "acc": 72, "sessions": "6회", "weak": "숫자 놀이터", "status": "도움 필요"},
    {"name": "문가온", "g": 2, "c": 1, "acc": 95, "sessions": "15회", "weak": "그림 찾기", "status": "좋음"},
    {"name": "조은채", "g": 2, "c": 1, "acc": 83, "sessions": "8회", "weak": "한글 낱말", "status": "좋음"},
    {"name": "임도윤", "g": 2, "c": 3, "acc": 78, "sessions": "7회", "weak": "끌어놓기", "status": "좋음"},
    {"name": "권시우", "g": 2, "c": 3, "acc": 61, "sessions": "3회", "weak": "숫자 놀이터", "status": "학습 뜸함"},
    {"name": "남하율", "g": 2, "c": 3, "acc": 89, "sessions": "11회", "weak": "안전 지킴이", "status": "좋음"},
    {"name": "서지안", "g": 3, "c": 1, "acc": 94, "sessions": "13회", "weak": "그림 찾기", "status": "좋음"},
    {"name": "홍예준", "g": 3, "c": 1, "acc": 76, "sessions": "6회", "weak": "끌어놓기", "status": "도움 필요"},
    {"name": "고나윤", "g": 3, "c": 1, "acc": 92, "sessions": "12회", "weak": "한글 낱말", "status": "좋음"},
    {"name": "백주호", "g": 3, "c": 2, "acc": 85, "sessions": "10회", "weak": "숫자 놀이터", "status": "좋음"},
    {"name": "유채원", "g": 3, "c": 2, "acc": 67, "sessions": "5회", "weak": "그림 찾기", "status": "학습 뜸함"},
    {"name": "전시윤", "g": 3, "c": 2, "acc": 88, "sessions": "11회", "weak": "안전 지킴이", "status": "좋음"},
    {"name": "노아린", "g": 3, "c": 2, "acc": 90, "sessions": "12회", "weak": "끌어놓기", "status": "좋음"},
]
ROSTER_TEACHERS = {"1-2": "이수진", "1-3": "최유나", "2-1": "박민호", "2-3": "강도현", "3-1": "정하늘", "3-2": "김보람"}

# 가정안내: 학생별 보호자 연결 정보 (학생 이름 기준)
FAMILY_PARENTS = {
    "김하은": {"parent": "김민정 학부모", "linked": True},
    "박도윤": {"parent": "박정호 학부모", "linked": True},
    "최서아": {"parent": "최은영 학부모", "linked": False},
    "김하람": {"parent": "김성우 학부모", "linked": True},
    "이준서": {"parent": "이지훈 학부모", "linked": True},
    "정유나": {"parent": "정혜수 학부모", "linked": False},
    "강시우": {"parent": "강태현 학부모", "linked": True},
    "윤아린": {"parent": "윤소라 학부모", "linked": True},
}

# 선생님관리 6명
ORG_TEACHERS = [
    {"name": "이수진", "cls": "1-2반", "role": "담임", "email": "sujin.lee@haetsal.kr", "code": "T-4821", "years": 8, "status": "active"},
    {"name": "박민호", "cls": "2-1반", "role": "담임", "email": "minho.park@haetsal.kr", "code": "T-5093", "years": 5, "status": "active"},
    {"name": "최유나", "cls": "1-3반", "role": "담임", "email": "yuna.choi@haetsal.kr", "code": "T-6270", "years": 3, "status": "active"},
    {"name": "정하늘", "cls": "3-2반", "role": "담임", "email": "haneul.jung@haetsal.kr", "code": "T-3388", "years": 11, "status": "active"},
    {"name": "김서연", "cls": "1-2반", "role": "교과", "email": "seoyeon.kim@haetsal.kr", "code": "T-7145", "years": 2, "status": "pending"},
    {"name": "오지훈", "cls": "2-1반", "role": "보조", "email": "jihoon.oh@haetsal.kr", "code": "T-8802", "years": 1, "status": "active"},
]

# 학급학생관리: 학급 9개 카드
ORG_CLASSES = [
    {"key": "1-2", "name": "1-2반", "teacher": "이수진", "count": 22, "acc": 90, "risk": "낮음"},
    {"key": "1-3", "name": "1-3반", "teacher": "최유나", "count": 25, "acc": 84, "risk": "주의"},
    {"key": "2-1", "name": "2-1반", "teacher": "박민호", "count": 24, "acc": 92, "risk": "낮음"},
    {"key": "2-2", "name": "2-2반", "teacher": "한지원", "count": 23, "acc": 88, "risk": "낮음"},
    {"key": "3-1", "name": "3-1반", "teacher": "오세훈", "count": 26, "acc": 79, "risk": "주의"},
    {"key": "3-2", "name": "3-2반", "teacher": "정하늘", "count": 27, "acc": 95, "risk": "낮음"},
    {"key": "4-1", "name": "4-1반", "teacher": "김도현", "count": 28, "acc": 91, "risk": "낮음"},
    {"key": "5-2", "name": "5-2반", "teacher": "서다은", "count": 29, "acc": 86, "risk": "낮음"},
    {"key": "6-1", "name": "6-1반", "teacher": "장민석", "count": 30, "acc": 93, "risk": "낮음"},
]

# 학급학생관리 roster 8명의 표시용 메타 (학생 코드 기준)
ORG_ROSTER_META = {
    "CAT-4823": {"cls": "1-2반", "link": True, "acc": 96, "risk": "낮음"},
    "CAT-5119": {"cls": "1-2반", "link": True, "acc": 62, "risk": "주의"},
    "CAT-6042": {"cls": "1-3반", "link": False, "acc": 81, "risk": "주의"},
    "CAT-6188": {"cls": "1-2반", "link": True, "acc": 78, "risk": "낮음"},
    "CAT-6205": {"cls": "3-2반", "link": True, "acc": 93, "risk": "낮음"},
    "CAT-6317": {"cls": "2-1반", "link": False, "acc": 88, "risk": "낮음"},
    "CAT-6402": {"cls": "1-3반", "link": True, "acc": 74, "risk": "낮음"},
    "CAT-6588": {"cls": "2-1반", "link": True, "acc": 91, "risk": "낮음"},
}

# 기관 대시보드 (기관.dc.html)
ORG_DASHBOARD = {
    "week": {
        "subtitle": "햇살초등학교 · 2026년 6월 4주차 · 실시간 집계",
        "periodLabel": "이번 주", "lineSub": "요일별로 추가 확인·잠시 제한이 권장된 비율",
        "kStudents": "248", "kTeachers": "16", "kTeachersSub": "교사 / 12 학급",
        "kApi": "3,912", "kApiSub": "오늘 API 요청", "kPass": "94.2", "kAvg": "11.4", "kFail": "8.1",
        "block": [97, 95, 98, 93, 96, 99, 97], "pass": [86, 88, 84, 90, 88, 93, 91],
        "axis": ["월", "화", "수", "목", "금", "토", "일"],
        "dLow": 82, "dReview": 12, "dElevated": 6,
        "r": [38, 29, 21, 12],
        "ageA": ["91%", "14%", "83%"], "ageB": ["88%", "24%", "1.6회"], "ageC": ["93%", "18%", "9.8초"],
        "apiCallLabel": "오늘 호출", "apiCallValue": "3,912",
    },
    "month": {
        "subtitle": "햇살초등학교 · 2026년 6월 · 실시간 집계",
        "periodLabel": "이번 달", "lineSub": "주차별로 추가 확인·잠시 제한이 권장된 비율",
        "kStudents": "251", "kTeachers": "16", "kTeachersSub": "교사 / 12 학급",
        "kApi": "86,540", "kApiSub": "이번 달 API 요청", "kPass": "93.1", "kAvg": "11.8", "kFail": "8.6",
        "block": [95, 96, 97, 98, 99], "pass": [85, 87, 89, 90, 92],
        "axis": ["1주", "2주", "3주", "4주", "5주"],
        "dLow": 79, "dReview": 14, "dElevated": 7,
        "r": [41, 27, 20, 12],
        "ageA": ["90%", "15%", "82%"], "ageB": ["87%", "25%", "1.7회"], "ageC": ["92%", "19%", "10.1초"],
        "apiCallLabel": "이번 달 호출", "apiCallValue": "86,540",
    },
    "year": {
        "subtitle": "햇살초등학교 · 2026년 · 실시간 집계",
        "periodLabel": "올해", "lineSub": "월별로 추가 확인·잠시 제한이 권장된 비율",
        "kStudents": "263", "kTeachers": "18", "kTeachersSub": "교사 / 13 학급",
        "kApi": "1.02M", "kApiSub": "올해 API 요청", "kPass": "92.4", "kAvg": "12.2", "kFail": "9.0",
        "block": [92, 93, 94, 95, 96, 95, 97, 97, 98, 98, 99, 99], "pass": [82, 84, 85, 86, 87, 88, 89, 90, 90, 91, 92, 93],
        "axis": ["1월", "2월", "3월", "4월", "5월", "6월", "7월", "8월", "9월", "10월", "11월", "12월"],
        "dLow": 76, "dReview": 16, "dElevated": 8,
        "r": [40, 28, 19, 13],
        "ageA": ["89%", "16%", "81%"], "ageB": ["86%", "26%", "1.8회"], "ageC": ["91%", "20%", "10.4초"],
        "apiCallLabel": "올해 호출", "apiCallValue": "1,024,880",
    },
}
ORG_DASHBOARD_GRADES = [
    {"name": "초등학교 1학년", "count": "42명", "acc": "86%", "wrong": "22%", "time": "13.2초", "color": "#FFB43C"},
    {"name": "초등학교 2학년", "count": "40명", "acc": "89%", "wrong": "19%", "time": "12.4초", "color": "#FF6DA6"},
    {"name": "초등학교 3학년", "count": "44명", "acc": "91%", "wrong": "16%", "time": "11.6초", "color": "#2E7BFF"},
    {"name": "초등학교 4학년", "count": "41명", "acc": "93%", "wrong": "13%", "time": "10.8초", "color": "#17B08C"},
    {"name": "초등학교 5학년", "count": "39명", "acc": "94%", "wrong": "11%", "time": "10.2초", "color": "#8B6BFF"},
    {"name": "초등학교 6학년", "count": "42명", "acc": "95%", "wrong": "9%", "time": "9.6초", "color": "#0EA5B5"},
]
ORG_DASHBOARD_BARS = [
    {"label": "1학년", "pass": 88, "fail": 7, "block": 5},
    {"label": "2학년", "pass": 90, "fail": 6, "block": 4},
    {"label": "3학년", "pass": 91, "fail": 5, "block": 4},
    {"label": "4학년", "pass": 92, "fail": 5, "block": 3},
    {"label": "5학년", "pass": 93, "fail": 4, "block": 3},
    {"label": "6학년", "pass": 94, "fail": 4, "block": 2},
]

# 기관 학습분석 (학습분석.dc.html)
ORG_ANALYTICS = {
    "week": {"kAcc": "90.4", "kAccDelta": "+2.1%p", "kActive": "214", "kActiveSub": "이번 주 학습 학생", "kSolved": "12,840", "kSolvedSub": "이번 주 푼 문제", "kHelp": "9",
             "trendSub": "요일별 기관 평균", "axis": ["월", "화", "수", "목", "금", "토", "일"], "accPct": [85, 87, 86, 90, 89, 92, 93]},
    "month": {"kAcc": "89.6", "kAccDelta": "+3.4%p", "kActive": "238", "kActiveSub": "이번 달 학습 학생", "kSolved": "52,190", "kSolvedSub": "이번 달 푼 문제", "kHelp": "12",
              "trendSub": "주차별 기관 평균", "axis": ["1주", "2주", "3주", "4주", "5주"], "accPct": [83, 85, 87, 89, 91]},
    "year": {"kAcc": "88.9", "kAccDelta": "+7.2%p", "kActive": "259", "kActiveSub": "올해 학습 학생", "kSolved": "612K", "kSolvedSub": "올해 푼 문제", "kHelp": "18",
             "trendSub": "월별 기관 평균", "axis": ["1월", "2월", "3월", "4월", "5월", "6월", "7월", "8월", "9월", "10월", "11월", "12월"],
             "accPct": [80, 82, 81, 84, 85, 86, 87, 88, 89, 90, 91, 92]},
}
ORG_ANALYTICS_SUBJ_LAST = {"국어": 94, "영어": 82, "수학": 71, "과학": 90, "사회": 85, "생활": 88}
ORG_ANALYTICS_SUBJECTS = [
    {"name": "국어", "icon": "ph-fill ph-book-open", "pct": 94, "delta": 5, "total": 3200},
    {"name": "영어", "icon": "ph-fill ph-translate", "pct": 82, "delta": 2, "total": 2600},
    {"name": "수학", "icon": "ph-fill ph-plus-minus", "pct": 71, "delta": -4, "total": 2400},
    {"name": "과학", "icon": "ph-fill ph-flask", "pct": 90, "delta": 3, "total": 2900},
    {"name": "사회", "icon": "ph-fill ph-scroll", "pct": 85, "delta": 1, "total": 2100},
    {"name": "생활", "icon": "ph-fill ph-house-line", "pct": 88, "delta": 6, "total": 2500},
]
ORG_ANALYTICS_GRADES = [
    {"label": "1학년", "pct": 88, "delta": 4, "students": 62},
    {"label": "2학년", "pct": 90, "delta": 2, "students": 58},
    {"label": "3학년", "pct": 89, "delta": 5, "students": 55},
    {"label": "4학년", "pct": 91, "delta": 1, "students": 44},
    {"label": "5학년", "pct": 93, "delta": 2, "students": 39},
]
ORG_ANALYTICS_CLASSES = [
    {"name": "1-2반", "teacher": "이수진", "acc": 90, "sessions": "118회", "weak": "수학", "trend": "상승"},
    {"name": "2-1반", "teacher": "박민호", "acc": 92, "sessions": "126회", "weak": "영어", "trend": "상승"},
    {"name": "1-3반", "teacher": "최유나", "acc": 84, "sessions": "98회", "weak": "수학", "trend": "하락"},
    {"name": "3-2반", "teacher": "정하늘", "acc": 95, "sessions": "131회", "weak": "사회", "trend": "유지"},
    {"name": "2-3반", "teacher": "강도현", "acc": 81, "sessions": "88회", "weak": "수학", "trend": "하락"},
]

# AI 모델 6종 (AI모델.dc.html)
MODEL_VERSIONS = [
    {"category": "대화·설명 AI", "name": "Claude Sonnet", "provider": "Anthropic · KakaoCloud AIaaS", "version": "2026.06-r3", "status": "정상", "description": "AI 선생님 대화, 학습 코멘트, 학부모 상담 답변을 생성해요.", "updated_on": "오늘"},
    {"category": "문제·힌트 생성", "name": "Claude Haiku", "provider": "Anthropic · KakaoCloud", "version": "2026.05-r7", "status": "정상", "description": "낱말·숫자 문제와 쉬운 힌트 문장을 자동으로 만들어요.", "updated_on": "3일 전"},
    {"category": "이미지 인식", "name": "Vision OCR", "provider": "KakaoCloud Vision", "version": "v3.2.1", "status": "정상", "description": "그림 찾기 정답 이미지 태깅과 손글씨 인식을 담당해요.", "updated_on": "어제"},
    {"category": "음성 안내 (TTS)", "name": "Kakao TTS", "provider": "KakaoCloud Speech", "version": "v2.4.0", "status": "정상", "description": "문제와 힌트를 저학년도 알아듣게 읽어줘요.", "updated_on": "1주 전"},
    {"category": "CAPTCHA 검증", "name": "CatChap Guard", "provider": "자체 모델 · On-prem", "version": "v1.8.2", "status": "정상", "description": "사람과 봇을 구분하고 부정 사용을 탐지해요.", "updated_on": "2일 전"},
    {"category": "학습 추천", "name": "Recsys Engine", "provider": "KakaoCloud ML", "version": "v0.9.4-beta", "status": "베타", "description": "다음 학습 놀이와 난이도를 아이마다 개인화해 추천해요.", "updated_on": "오늘"},
]
MODEL_CHANGELOG = [
    {"model": "Claude Sonnet", "version": "2026.06-r3", "note": "한국어 저학년 말투와 존댓말 톤을 개선했어요.", "when": "오늘 09:12", "dot": "#2E7BFF"},
    {"model": "Recsys Engine", "version": "v0.9.4", "note": "놀이 추천 정확도를 높이는 베타 업데이트를 적용했어요.", "when": "오늘 08:40", "dot": "#E0475E"},
    {"model": "Vision OCR", "version": "v3.2.1", "note": "손글씨 숫자 인식률이 약 4%p 향상됐어요.", "when": "어제", "dot": "#17B08C"},
    {"model": "CatChap Guard", "version": "v1.8.2", "note": "반복 오답을 악용하는 자동 클릭 패턴 탐지를 추가했어요.", "when": "2일 전", "dot": "#FF5A4D"},
]

# 기관 마이페이지: 요금제/사용량/청구
BILLING_PLANS = [
    {"key": "Basic", "name": "Basic", "monthly": 99000, "yearly": 950000, "seats": 100, "teacher_seats": 15, "api_quota": 20000,
     "features": ["학생 최대 100석", "CAPTCHA API 전체 기능", "연령별 대시보드", "상담 AI", "학습 리포트"]},
    {"key": "Pro", "name": "Pro", "monthly": 290000, "yearly": 2784000, "seats": 300, "teacher_seats": 25, "api_quota": 50000,
     "features": ["학생 최대 300석", "CAPTCHA API 전체 기능", "연령별 대시보드", "상담 AI", "학습 리포트"]},
    {"key": "Enterprise", "name": "Enterprise", "monthly": 0, "yearly": 0, "seats": 1000, "teacher_seats": 100, "api_quota": 500000,
     "features": ["학생 최대 1,000석", "CAPTCHA API 전체 기능", "연령별 대시보드", "상담 AI", "학습 리포트"]},
]
BILLING_INVOICES = [
    {"invoice_no": "INV-2026-0622", "billed_on": "2026. 6. 22", "description": "Pro 요금제 월 구독", "amount": 290000},
    {"invoice_no": "INV-2026-0522", "billed_on": "2026. 5. 22", "description": "Pro 요금제 월 구독", "amount": 290000},
    {"invoice_no": "INV-2026-0422", "billed_on": "2026. 4. 22", "description": "Pro 요금제 월 구독", "amount": 290000},
    {"invoice_no": "INV-2026-0322", "billed_on": "2026. 3. 22", "description": "학생 좌석 추가 (50석)", "amount": 150000},
]
BILLING_USAGE = {
    "api": {"label": "CAPTCHA API 호출", "used": 34000, "quota": 50000},
    "teacher_seats": {"used": 18, "quota": 25},
    "next_billing_date": "2026년 7월 22일",
}

# 기관 관리자 3명
ORG_ADMINS = [
    {"name": "김서연", "email": "admin@catchap.dev", "role": "최고 관리자"},
    {"name": "박지훈", "email": "jihoon.p@haetsal.es.kr", "role": "결제 관리자"},
    {"name": "이수민", "email": "sumin.lee@haetsal.es.kr", "role": "조회 전용"},
]

# InstitutionPicker 기관 디렉토리 8건
INSTITUTIONS = [
    {"name": "햇살초등학교", "type": "초등학교", "sido": "서울특별시", "sigungu": "강남구", "dong": "역삼동", "road": "서울 강남구 테헤란로 123"},
    {"name": "대치샘유치원", "type": "유치원", "sido": "서울특별시", "sigungu": "강남구", "dong": "대치동", "road": "서울 강남구 삼성로 456"},
    {"name": "광진 새싹초등학교", "type": "초등학교", "sido": "서울특별시", "sigungu": "광진구", "dong": "화양동", "road": "서울 광진구 능동로 120"},
    {"name": "상계 푸른숲 어린이집", "type": "어린이집", "sido": "서울특별시", "sigungu": "노원구", "dong": "상계동", "road": "서울 노원구 동일로 789"},
    {"name": "별빛초등학교", "type": "초등학교", "sido": "경기도", "sigungu": "성남시 분당구", "dong": "정자동", "road": "경기 성남시 분당구 불정로 55"},
    {"name": "광교 무지개유치원", "type": "유치원", "sido": "경기도", "sigungu": "수원시 영통구", "dong": "이의동", "road": "경기 수원시 영통구 광교로 22"},
    {"name": "센텀 푸른솔초등학교", "type": "초등학교", "sido": "부산광역시", "sigungu": "해운대구", "dong": "우동", "road": "부산 해운대구 센텀로 30"},
    {"name": "해운대 바다어린이집", "type": "어린이집", "sido": "부산광역시", "sigungu": "해운대구", "dong": "좌동", "road": "부산 해운대구 좌동순환로 40"},
]

# 학생 알림 (알림.dc.html) — 위에서부터 최신
STUDENT_NOTIFICATIONS = [
    {"title": "퀴즈 완료!", "message": "그림 찾기 퀴즈를 정답률 86%로 끝냈어요. 참 잘했어요!", "category": "진도", "type": "progress", "unread": True},
    {"title": "새 배지 획득 🏅", "message": "\"매의 눈\" 배지를 얻었어요. 배지함에서 확인해 보세요.", "category": "배지", "type": "badge", "unread": True},
    {"title": "오늘의 추천 문제", "message": "하은이에게 딱 맞는 숫자 놀이터 5문제를 준비했어요! 지금 도전해 볼까요?", "category": "추천문제", "type": "recommend", "unread": True},
    {"title": "AI 선생님 냥냥이", "message": "\"오늘 숫자 놀이터도 같이 해볼까? 5문제만 도전!\"", "category": "AI", "type": "ai", "unread": True},
    {"title": "연속 학습 리마인드 🔥", "message": "오늘도 학습하면 연속 기록이 쭉 이어져요!", "category": "진도", "type": "progress", "unread": False},
    {"title": "끌어놓기 놀이 완료", "message": "정답률 100%! 드래그 마스터 배지에 한 걸음 가까워졌어요.", "category": "진도", "type": "progress", "unread": False},
    {"title": "새 추천 문제 도착", "message": "받침이 조금 헷갈렸죠? 한글 낱말 3문제를 추천해 드려요.", "category": "추천문제", "type": "recommend", "unread": False},
    {"title": "AI 선생님 냥냥이", "message": "\"받침이 조금 헷갈렸구나. 천천히 소리 내어 읽어보자!\"", "category": "AI", "type": "ai", "unread": False},
    {"title": "주간 리포트가 도착했어요", "message": "이번 주 학습 요약을 나의 기록에서 확인할 수 있어요.", "category": "진도", "type": "report", "unread": False},
]

# 학부모 알림 (학부모알림.dc.html) — child: 자녀 이름
PARENT_NOTIFICATIONS = [
    {"title": "이수진 선생님 메시지", "message": "\"하은이가 오늘 숫자 놀이터를 끝까지 잘 해냈어요. 집에서도 칭찬 많이 해주세요!\"", "category": "선생님", "type": "teacher", "child": "하은", "unread": True},
    {"title": "주간 리포트 도착", "message": "하은이의 6월 넷째 주 학습 요약이 준비됐어요. 지금 확인해 보세요.", "category": "리포트", "type": "report", "child": "하은", "unread": True},
    {"title": "새 배지 획득 🏅", "message": "도윤이가 \"첫 걸음\" 배지를 얻었어요!", "category": "배지", "type": "badge", "child": "도윤", "unread": True},
    {"title": "박민호 선생님 메시지", "message": "\"도윤이가 그림 찾기를 참 좋아해요. 오늘도 스스로 3판이나 했답니다.\"", "category": "선생님", "type": "teacher", "child": "도윤", "unread": False},
    {"title": "상담 AI 답변 준비 완료", "message": "어제 남기신 질문에 대한 상담 AI 답변이 준비됐어요.", "category": "AI", "type": "ai", "child": "하은", "unread": False},
    {"title": "학습 리마인드", "message": "하은이가 3일 연속 학습 중이에요. 오늘도 함께 응원해 주세요!", "category": "일반", "type": "remind", "child": "하은", "unread": False},
    {"title": "월간 리포트 안내", "message": "6월 월간 리포트를 곧 보내드릴 예정이에요.", "category": "리포트", "type": "report", "child": "하은", "unread": False},
]

# 오늘의퀴즈 (오늘의퀴즈.dc.html)
DAILY_QUIZ = [
    {"subject": "국어", "topic": "그림 보고 낱말 찾기", "status": "done", "reward": 10},
    {"subject": "영어", "topic": "단어와 문장", "status": "progress", "reward": 10},
    {"subject": "수학", "topic": "더하기·빼기 놀이", "status": "todo", "reward": 15},
    {"subject": "과학", "topic": "관찰하고 골라요", "status": "todo", "reward": 15},
    {"subject": "사회", "topic": "옛날 이야기 속으로", "status": "todo", "reward": 20},
    {"subject": "생활", "topic": "안전하게 생활해요", "status": "todo", "reward": 15},
]
DAILY_QUIZ_WEEK = [
    {"label": "월", "done": True}, {"label": "화", "done": True}, {"label": "수", "done": True},
    {"label": "목", "done": True}, {"label": "금", "done": False, "today": True},
    {"label": "토", "done": False}, {"label": "일", "done": False},
]

# 오답노트 ITEMS 6건 — cat: word|num|img|safe
WRONG_ITEMS = [
    {"cat": "img", "subject": "과학", "question": "고양이는 모두 몇 마리일까요?", "wrong": "강아지도 골랐어요", "answer": "고양이 3마리만", "tip": "고양이는 귀가 뾰족하고 수염이 길어요. 귀 모양부터 살펴보면 쉬워요!", "days_ago": 0},
    {"cat": "num", "subject": "수학", "question": "7 + 5 = ?", "wrong": "11", "answer": "12", "tip": "7에서 3을 더하면 10, 남은 2를 더하면 12! 10을 먼저 만들어 보세요.", "days_ago": 0},
    {"cat": "word", "subject": "국어", "question": "그림에 알맞은 받침은? (고ㅇ이)", "wrong": "ㅁ", "answer": "ㅇ (고양이)", "tip": "\"고양이\"를 천천히 소리 내보면 \"양\"에서 ㅇ 받침이 들려요.", "days_ago": 1},
    {"cat": "word", "subject": "국어", "question": "\"바ㄷ\" 에 알맞은 받침은?", "wrong": "ㅅ", "answer": "ㄷ (받다)", "tip": "끝소리가 \"ㄷ\"으로 나는지 \"ㅅ\"으로 나는지 입 모양을 확인해요.", "days_ago": 1},
    {"cat": "safe", "subject": "생활", "question": "횡단보도에서 바른 행동은?", "wrong": "빨간불에 뛰기", "answer": "초록불에 손들고 건너기", "tip": "초록불에도 좌우를 살피고 손을 들어 운전자에게 알려요.", "days_ago": 2},
    {"cat": "img", "subject": "과학", "question": "생선을 모두 골라요", "wrong": "문어를 골랐어요", "answer": "생선 2마리", "tip": "생선은 지느러미와 비늘이 있어요. 문어는 다리가 많답니다!", "days_ago": 3},
]
WRONG_TAGS = {
    "word": {"label": "낱말·한글", "icon": "ph-fill ph-text-aa", "color": "#FF5A6E", "bg": "#FFE3E9"},
    "num": {"label": "수·연산", "icon": "ph-fill ph-plus-minus", "color": "#FF922E", "bg": "#FFEDE0"},
    "img": {"label": "이미지 선택", "icon": "ph-fill ph-image", "color": "#2E7BFF", "bg": "#E6F0FF"},
    "safe": {"label": "생활 안전", "icon": "ph-fill ph-shield-check", "color": "#8B6BFF", "bg": "#EDE6FF"},
    "soc": {"label": "사회·문화", "icon": "ph-fill ph-scroll", "color": "#17B08C", "bg": "#DFF6EE"},
    "eng": {"label": "영어·어휘", "icon": "ph-fill ph-translate", "color": "#E0489E", "bg": "#FCE4F1"},
}

# 취약문제추천 REC 6건
RECOMMENDATIONS = [
    {"title": "두 수 모아 더하기", "subject": "수학", "chapter": 2, "priority": "우선", "reason": "최근 더하기 문제 3개 중 2개를 틀렸어요."},
    {"title": "동물 친구 관찰하기", "subject": "과학", "chapter": 1, "priority": "우선", "reason": "정답을 골랐다가 자주 바꿨어요. 한 번 더 연습!"},
    {"title": "받침 있는 낱말 찾기", "subject": "국어", "chapter": 2, "priority": "보통", "reason": "비슷한 낱말에서 살짝 헷갈렸어요."},
    {"title": "알파벳 소리 맞히기", "subject": "영어", "chapter": 2, "priority": "보통", "reason": "파닉스 소리에서 헷갈린 적이 있어요."},
    {"title": "위인 이야기 떠올리기", "subject": "사회", "chapter": 2, "priority": "낮음", "reason": "대체로 잘했지만 한 문제만 다시 볼까요?"},
    {"title": "안전한 행동 고르기", "subject": "생활", "chapter": 1, "priority": "낮음", "reason": "상황을 보고 바른 행동을 한 번 더 골라봐요."},
]

# 나의기록 (나의기록.dc.html)
RECORDS_WEEKS = [
    {"label": "3주 전", "v": 62}, {"label": "2주 전", "v": 82}, {"label": "지난주", "v": 75}, {"label": "이번주", "v": 100},
]
RECORDS_CAL_LEARNED = [1, 2, 3, 5, 6, 8, 9, 10, 12, 13, 15, 16, 17, 19, 20, 22, 23, 24, 26, 27, 29, 30]
RECORDS_CAL = {"month": 7, "year": 2026, "today": 2, "blanks": 2, "days": 31}
RECORDS_MASTERY = [
    {"name": "끌어놓기 놀이", "icon": "ph-fill ph-hand-grabbing", "color": "#17B08C", "bg": "#DFF6ED", "pct": 95, "solved": 40, "delta": 3},
    {"name": "한글 낱말 찾기", "icon": "ph-fill ph-text-aa", "color": "#FF5A6E", "bg": "#FFE3E9", "pct": 88, "solved": 50, "delta": 2},
    {"name": "숫자 놀이터", "icon": "ph-fill ph-plus-minus", "color": "#FF922E", "bg": "#FFEDE0", "pct": 76, "solved": 45, "delta": -4},
    {"name": "그림 찾기 퀴즈", "icon": "ph-fill ph-image", "color": "#2E7BFF", "bg": "#E6F0FF", "pct": 64, "solved": 38, "delta": 6},
    {"name": "안전 지킴이", "icon": "ph-fill ph-shield-check", "color": "#8B6BFF", "bg": "#EDE6FF", "pct": 32, "solved": 25, "delta": 12},
]
RECORDS_ACTIVITIES = [
    {"title": "그림 찾기 퀴즈", "sub": "고양이만 골라요 · 8문제", "icon": "ph-fill ph-image", "color": "#2E7BFF", "bg": "#E6F0FF", "result": "정답률 86%", "time": "방금 전"},
    {"title": "끌어놓기 놀이", "sub": "카드 옮기기 · 6문제", "icon": "ph-fill ph-hand-grabbing", "color": "#17B08C", "bg": "#DFF6ED", "result": "정답률 100%", "time": "오늘 오후 3:10"},
    {"title": "숫자 놀이터", "sub": "더하기·빼기 · 10문제", "icon": "ph-fill ph-plus-minus", "color": "#FF922E", "bg": "#FFEDE0", "result": "정답률 72%", "time": "어제"},
    {"title": "한글 낱말 찾기", "sub": "받침 완성 · 10문제", "icon": "ph-fill ph-text-aa", "color": "#FF5A6E", "bg": "#FFE3E9", "result": "정답률 90%", "time": "2일 전"},
]

# 학습 홈 (학습 홈.dc.html)
HOME_SUBJECT_CARDS = [
    {"subject": "국어", "desc": "낱말·문장·글의 속뜻을 익히는 오늘의 국어 한 판", "done": 5, "total": 5, "state": "done"},
    {"subject": "영어", "desc": "단어·문장·문법으로 배우는 영어 한 판", "done": 3, "total": 5, "state": "progress"},
    {"subject": "수학", "desc": "수·연산·도형·측정을 배우는 수학 한 판", "done": 5, "total": 5, "state": "done"},
    {"subject": "과학", "desc": "그림을 관찰하고 탐구하는 과학 한 판", "done": 0, "total": 5, "state": "todo"},
    {"subject": "사회", "desc": "지도·지역·공공기관을 알아가는 사회 한 판", "done": 0, "total": 5, "state": "todo"},
    {"subject": "생활", "desc": "생활 속 안전과 지혜를 배우는 생활 한 판", "done": 0, "total": 5, "state": "todo"},
]
HOME_GROWTH = {
    "streak_days": 12,
    "week_solved": 86,
    "accuracy": 92,
    "time_delta": "+18%",
    "week_total": "5h 43m",
    "week_bars": [
        {"day": "월", "pct": 45, "time": "40m"}, {"day": "화", "pct": 65, "time": "58m"},
        {"day": "수", "pct": 40, "time": "36m"}, {"day": "목", "pct": 80, "time": "1h 12m"},
        {"day": "금", "pct": 100, "time": "1h 30m", "today": True},
        {"day": "토", "pct": 30, "time": "27m"}, {"day": "일", "pct": 22, "time": "20m"},
    ],
}
HOME_AI_COMMENT = "그림 찾기가 조금 어려웠구나! 천천히 다시 해보면 금방 늘어요."
HOME_MASCOT_MESSAGE = "오늘도 같이 배워볼까?"
HOME_CLASS_RANK_NOTE = "우리 반 상위 30% 구간이에요. 친구 이름·점수는 보이지 않아요 🙂"

# ==================================================================
# 하드코딩 제거 배치 — 프론트 JSX 리터럴을 API 응답 필드로 이관한 값들
# (실집계가 없을 때의 fallback / DB(stat_blobs)에서 수정 가능한 문구)
# ==================================================================

# 나의기록 상단 통계 4종 (나의기록.dc.html) — 시도 없으면 D 유지
RECORDS_STATS = {
    "streak_days": 12,
    "total_hours": 8,
    "total_minutes": 20,
    "total_solved": 342,
    "avg_accuracy": 89,
}

# 배지 히어로 쇼케이스 문구 (배지 이름 기준 — 없으면 배지 name/desc 사용)
BADGE_HERO = {
    "매의 눈": {
        "title": "매의 눈 🦅",
        "desc": "그림 찾기 퀴즈에서 정답률 85%를 넘겼어요. 정말 날카로운 눈이네요!",
    },
}
BADGE_NEXT_CHIP = "거의 다 왔어요!"

# 취약문제추천 '이번 주 분석 요약' — **…** 구간은 프론트에서 <b>로 렌더
RECO_SUMMARY = (
    "**수학 · 더하기**에서 오답이 가장 많았고, **과학 · 그림 관찰**에서 선택을 자주 바꿨어요. "
    "아래 5문제를 풀면 약한 부분이 쑥 올라가요!"
)

# AI 선생님 첫 인사 — {n}: 학생 이름, {recent}: 최근 학습 코멘트
AI_TEACHER_GREETING = [
    "안녕 {n}아! 나는 AI 선생님 냥냥이야 🐱 {recent} 궁금한 게 있으면 뭐든지 물어봐.",
    "아래 버튼을 눌러서 이야기를 시작해도 좋아!",
]
AI_TEACHER_GREETING_RECENT = "오늘 {game} 정말 잘했더라!"
AI_TEACHER_GREETING_DEFAULT = "오늘도 만나서 반가워!"

# 프로필꾸미기 주간 목표 (this-week 학습일 실집계 없으면 D)
PROFILE_WEEK_GOAL = {
    "done": 4,
    "total": 5,
    "hint_one": "하루만 더 하면 이번 주 목표를 채워요! 🎯",
    "hint_many": "{n}일만 더 하면 이번 주 목표를 채워요! 🎯",
    "hint_done": "이번 주 목표를 모두 채웠어요! 🎉",
}

# 마이페이지 '로그인된 기기' 표시 문구 (세션 위치 테이블 없음 — D)
LOGIN_DEVICE_NOTE = "이 기기 · 서울 · 방금 활동"

# 교사 학습분석 AI 요약/사이드 인사이트 (학습분석.dc.html)
TEACHER_ANALYTICS_AI = {
    "strength": "한글 낱말·그림 찾기 정답률이 높아요. 이미지 선택형에서 특히 강해요.",
    "warning": "숫자 놀이터에서 개념 오답이 늘고 있어요. 3명은 최근 학습이 뜸해요.",
    "recommend": "숫자 연산 난이도를 한 단계 낮추고, 사과 세기 같은 시각 활동을 배정해 보세요.",
}
TEACHER_ANALYTICS_INSIGHT = "한글 정답률이 지난주 대비 +5%p 올랐어요."

# 기관 학습분석 AI 요약 / 오답 원인 분포 (기관 학습분석.dc.html)
ORG_ANALYTICS_AI = {
    "strength": "국어·과학 정답률이 안정적으로 높아요. 이미지 선택형 문제 성취가 특히 우수해요.",
    "warning": "수학에서 개념 오답이 늘고 있어요. 1-3반·2-3반에 집중 지도가 필요해요.",
    "recommend": "저학년에 숫자 연산형 난이도를 낮추고, 사과 세기 같은 시각 활동을 배정해 보세요.",
}
ORG_ANALYTICS_REASONS = [
    {"label": "개념 오답 추정", "pct": "38%", "color": "#FF5A6E"},
    {"label": "조작 실수 추정", "pct": "29%", "color": "#2E7BFF"},
    {"label": "선택지 혼동 추정", "pct": "21%", "color": "#8B6BFF"},
    {"label": "UI 문제 후보", "pct": "12%", "color": "#FF922E"},
]

# 기관 사이드바 위젯 (OrgLayout) — 실집계 없으면 D
ORG_SIDEBAR = {
    "pro": {"pct": 68, "sub": "이번 달 API 68% 사용"},
    "semester": {"done": 9, "total": 12, "pct": 75, "sub": "2026-2학기 담임 배정 · 12개 반 중 9개 완료"},
    "insight": {"sub": "국어 정답률이 지난주 대비 +6%p 상승했어요."},
}
ORG_SIDEBAR_SEMESTER_TPL = "2026-2학기 담임 배정 · {total}개 반 중 {done}개 완료"
ORG_SIDEBAR_INSIGHT_TPL = "{subject} 정답률이 지난주 대비 {delta}%p {dir}했어요."

# 기관 보안정책 — 보호자 동의 완료율 (parent_student_links 실집계 없으면 D)
ORG_CONSENT_RATE = "98.6%"

# 결제수단 만료일 (payment_methods에 만료 컬럼 없음 — 카드 last4 기준 D)
BILLING_CARD_EXP = {"4821": "2027 / 08", "7702": "2026 / 11"}

# 기관 세금계산서 담당자 이메일 (organizations에 컬럼 없음 — D)
ORG_TAX_EMAIL = "account@haetsal.es.kr"

# AI 모델 레지스트리 버전 표기 (기관 AI모델 화면)
MODEL_REGISTRY_VERSION = "v2026.07.02"
