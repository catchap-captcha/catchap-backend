# 미디어를 Object Storage 로 — 무엇을 왜 바꿨나

작성 2026-07-31 · 브랜치 `feat/media-object-storage` (기반 `origin/jy` `93e4ed9`)
푸시 대상 `th-after`

---

## 1. 왜 바꾸나

지금 강의 영상·썸네일·자료·문항 이미지가 **백엔드 서버의 로컬 디스크**에 있습니다.
도커 볼륨 `catchap-backend_lecture_media` 이고, **실측 3.3GB · 3,462개**입니다.

```
mp4     19개    강의 영상 (가장 큰 것 487MB · 402MB · 376MB …)
png  2,677개    썸네일 · 문항 이미지
jpg    766개
```

서버가 한 대이고 컨테이너가 하나일 때는 아무 문제가 없습니다.

```
지금:  [백엔드 컨테이너 1개] ──▶ /app/media  (그 서버의 디스크)
       올림 → 그 디스크에 저장 → 같은 컨테이너가 읽음 → 정상
```

**쿠버네티스로 옮겨 파드를 2개로 늘리면 깨집니다.**

```
K8s:   [파드 A · 워커1] ──▶ 워커1 디스크
       [파드 B · 워커2] ──▶ 워커2 디스크     ← 서로 다른 디스크

       강사가 업로드 → LB가 A로 보냄 → A 디스크에 저장
       학생이 재생  → LB가 B로 보냄 → B에는 그 파일이 없음 → 404
```

요청이 **어느 파드로 가느냐에 따라 되기도 하고 안 되기도** 합니다. 재현이 어려운 장애입니다.
게다가 파드는 죽으면 새로 뜨는데 그때 디스크 내용이 사라집니다.

**→ 파일을 파드 밖(버킷)에 두면 파드가 몇 개든, 죽었다 살아나든 같은 파일을 봅니다.**
이것이 "무상태화(stateless)"이고, K8s로 옮기는 실제 이유입니다.

---

## 2. 무엇을 만들었나 (1단계 — 토대)

### `app/services/media_storage.py` (신규)

저장소를 **인터페이스로 추상화**하고 구현을 두 개 뒀습니다.

```
MediaStorage (인터페이스)
  ├─ LocalMediaStorage   서버 디스크 — 지금까지의 동작
  └─ ObjectMediaStorage  버킷 (S3 호환 API · SigV4)

save / save_path / stat / open_range / delete
```

### `app/core/config.py` (설정 7개 추가)

```python
MEDIA_STORAGE_BACKEND = "local"    # local | object     ★기본값 local
MEDIA_BUCKET          = ""
MEDIA_KEY_PREFIX      = "media"
MEDIA_S3_ENDPOINT     = "https://objectstorage.kr-central-2.kakaocloud.com"
MEDIA_S3_REGION       = "kr-central-2"
MEDIA_S3_ACCESS_KEY   = ""
MEDIA_S3_SECRET_KEY   = ""
```

---

## 3. 왜 이렇게 만들었나 — 설계 판단 4가지

### ★① 한 번에 갈아엎지 않고 추상화 계층을 먼저 뒀다

`MEDIA_STORAGE_BACKEND=local` 이면 **지금과 100% 같은 동작**입니다. 그래서

- 개발·테스트는 버킷 없이 계속 돌아갑니다(팀원이 로컬에서 작업할 때 버킷 자격증명이 필요 없음)
- 이전 중 문제가 생기면 **환경변수 하나만 되돌리면** 됩니다. 이미지 재빌드가 필요 없습니다

**되돌릴 방법이 없는 변경은 만들지 않는다** — 이게 이 설계의 첫 번째 원칙입니다.

### ★★② 서명 URL 을 쓰지 않고 백엔드가 중계한다 — 보안 기능이 걸려 있어서

가장 중요한 판단입니다. 영상을 버킷에 두면 보통 **서명 URL(presigned)** 을 발급해
브라우저가 버킷에서 직접 받게 합니다. 백엔드 부하가 0이라 그게 정석입니다.

**그런데 그러면 동시 시청 차단이 무력화됩니다.**

```python
# lectures.py:1187  GET /lectures/{id}/stream — 지금 코드
if progress.session_id != session_id:
    raise HTTPException(403, "다른 곳에서 재생이 시작되었어요.")
return FileResponse(path)
```

주석에 의도가 명시돼 있습니다.

> 토큰의 session_id가 현재 progress.session_id와 다르면 403 — takeover로 세션이 교체된
> 순간 **이전 기기의 스트림 URL이 즉시 죽는다**(동시 차단이 '진도 인정'만이 아니라
> **영상 바이트 전달에도** 걸린다).

즉 **매 Range 요청마다 세션을 다시 확인**하는 것이 기능입니다. 서명 URL 을 한 번 내주면
그 뒤로는 백엔드를 안 거치므로, 세션이 교체돼도 이전 기기가 계속 재생합니다.
만료를 짧게 줘도 `<video>` 태그가 URL 을 스스로 갱신하지 않아 재생이 끊깁니다.

**→ 백엔드가 중계합니다.** 버킷→백엔드는 같은 VPC 내부라 외부 대역폭이 늘지 않고,
백엔드→브라우저는 지금과 동일합니다. 늘어나는 것은 백엔드 CPU/메모리인데,
**파일을 통째로 메모리에 올리지 않고 1MiB 씩 조각으로 흘려보내므로** 상주 메모리는 일정합니다.

### ★③ Range 를 직접 처리해야 한다

지금은 `FileResponse(path)` 한 줄로 끝납니다 — starlette 가 로컬 파일의 Range 를 알아서
처리해 주기 때문입니다. **저장소가 버킷이 되면 starlette 는 그 파일을 모릅니다.**
그래서 `media_response()` 를 만들어 직접 처리합니다.

```
Range: bytes=5000-      →  206 + Content-Range: bytes 5000-10239/10240
Range 없음               →  200 + Content-Length
항상                     →  Accept-Ranges: bytes   (브라우저가 탐색 가능 여부를 이걸로 판단)
```

이게 없으면 **영상 탐색(seek)이 안 되고**, 브라우저가 전체를 받을 때까지 재생이 시작되지 않습니다.

★**범위가 파일 크기를 벗어나면 416 대신 200(전체)** 을 보냅니다. 엄밀히는 416이 맞지만
브라우저가 벗어난 Range 를 보내는 경우가 있어 **재생이 끊기는 쪽이 더 나쁩니다.**

### ★④ 키 검증을 저장소 계층에서 한 번 더 한다

현재 코드의 좋은 설계를 그대로 이어받았습니다 — **파일 경로를 DB에 저장하지 않고
`{id}{확장자}` 로만 유도**합니다(경로 조작 원천 차단). 저장소 계층에서도 상위 경로 이동·
절대 경로·허용되지 않는 문자를 막습니다. **나중에 누가 사용자 입력을 키에 넣더라도 여기서 걸립니다.**

★설정이 빠졌을 때 **로컬로 조용히 떨어지지 않고 예외를 냅니다.** 설정 누락이
"파일이 없습니다"로 둔갑하면 원인을 못 찾습니다 — 가짜 성공을 만들지 않습니다.

---

## 4. 검증 — 실제로 돌려 확인한 것 (43건 전부 통과)

### 저장소 계층 28건

```
저장·조회·삭제   save 반환 바이트 · stat 크기 · 없는 키 None · 하위 디렉터리 자동 생성
Range 읽기      0-9 · 중간 구간 · 마지막 10바이트 · 전체 · 없는 키 → MediaNotFound
삭제 멱등        두 번 삭제해도 예외 없음
★키 검증        ../etc/passwd · /abs/path · a/../../b · 빈 문자열 · a/b;rm -rf · 한글.png  전부 거부
★Range 파서     10가지 형태 (0- · 0-49 · -10 · 끝 초과 · 역전 · 형식오류 · 1바이트 · -0)
```

### HTTP 응답 15건 (실제 FastAPI 앱에 태워 요청)

```
전체 응답    200 · 본문 전체 일치 · Accept-Ranges · Content-Length
부분 응답    206 · 본문이 그 구간 · Content-Range 정확 · Content-Length 정확
            bytes=5000- · bytes=-256 · 범위 초과 → 200 전체
다운로드     한글 파일명 → RFC 5987 인코딩 · 헤더가 ASCII 로 안전
없는 파일    404
```

---

## 5. 2단계 — 호출부 전환 (완료)

경로 함수 4개를 **키 함수로 이름까지 바꿨습니다.**

```
_video_path()          →  _video_key()           lectures/{id}{ext}
_thumbnail_path()      →  _thumbnail_key()       lectures/thumbnails/{id}{ext}
_material_path()       →  _material_key()        lectures/materials/{id}{ext}
_question_image_path() →  _question_image_key()  lectures/questions/{id}{ext}
_media_dir() · _thumbnails_dir() · _materials_dir() · _question_images_dir()  →  삭제
```

★**이름을 바꾼 것은 의도적입니다.** 이름을 그대로 두고 반환형만 `Path` → `str` 로 바꾸면
고치지 않은 호출부가 문자열을 `Path` 처럼 다뤄 **조용히 잘못 동작**합니다. 이름을 바꾸면
놓친 자리가 `NameError` 로 즉시 드러납니다. **실패는 시끄러워야 합니다.**

전환한 호출부 — `lectures.py` · `course_exam.py` · `lecture_service.py` 합계 **37곳**

```
서빙 7곳     FileResponse(path)        →  media_response(key, range_header=…)
업로드 5곳   os.replace(tmp, final)    →  storage.save_path(key, tmp)
삭제 12곳    path.unlink(missing_ok)   →  storage.delete(key)
존재확인 3곳  path.is_file()            →  storage.stat(key) is not None
복사 1곳     shutil.copyfile           →  local_file(src) + storage.save(dst)
STT 1곳      _video_path(lec)          →  with local_file(key) as p
그 외 8곳    경로 수집 → 키 수집
```

**스트리밍 엔드포인트 2개에 `Request` 를 추가**했습니다 — `Range` 헤더를 읽어야 하는데
기존 시그니처에 없었습니다.

### `local_file()` — 외부 라이브러리에 경로를 넘겨야 할 때

STT 워커 호출이 `open(path,'rb')` 로 영상을 읽습니다. 버킷 객체는 경로가 없으므로 임시로
내려받아 줍니다. **로컬 백엔드에서는 복사하지 않고 원본 경로를 그대로 줍니다** — 3.3GB 를
쓸데없이 두 번 쓰지 않기 위해서입니다.

---

## 6. 검증 — 실제로 돌려 확인한 것

### 저장소 계층 · HTTP 응답 (로컬) 43건 전부 통과

```
저장·조회·삭제 · Range 읽기(5) · 삭제 멱등 · 키 검증(6: ../etc/passwd · /abs · a;rm · 한글 …)
Range 파서 10가지 · 206 부분응답 · Content-Range · 한글 파일명 RFC 5987 · 404
```

### ★실제 버킷(`catchap-storage-prod-team1`) 18건 전부 통과

```
1MiB 저장·조회·Range(0-9 · 중간 · 마지막 · 전체 무결성 md5 일치) · 삭제 멱등
키 검증 6건 · ★설정 누락 시 조용히 로컬로 안 떨어지고 RuntimeError
```

**속도 실측** — 20MiB 기준

```
사외(이 PC)        업로드 12.1 MB/s · 다운로드 11.3 MB/s
VPC 안(K8s 워커)   다운로드 25.5 MB/s        ← 백엔드 파드가 뜰 위치. 2배 빠름
```

### ★기존 테스트 전체 394건 통과

```
394 passed · 1 failed
실패한 1건 = tests/test_forest_captcha.py::test_dbstore_shares_across_worker_instances
  → ★원본(origin/jy)에서도 똑같이 실패합니다. 제 변경과 무관한 기존 실패입니다
    (별도 워크트리를 만들어 대조 확인)
```

---

## 7. ★검증에서 잡힌 내 실수 2가지

테스트를 돌리지 않았으면 **둘 다 프로덕션에서 터졌을 것**입니다.

### ① `local` 백엔드가 파일 위치를 바꿔 버렸다

`LECTURE_MEDIA_DIR` 이 `/lectures` 로 끝난다고 가정하고 부모 디렉터리를 저장소 루트로
삼았습니다. 그러면 기존 파일이 전부 "없는 파일"이 됩니다 — **"local 이면 지금과 100% 같은
동작"이라는 설계 목표를 제가 깬 것**입니다.

→ 키의 첫 구간을 기존 설정 디렉터리에 대응시키도록 고쳤습니다.
   `lectures/materials/x` → `{LECTURE_MEDIA_DIR}/materials/x`. **배치가 한 바이트도 안 바뀝니다.**

### ② 저장소를 캐시해 미디어 경로가 얼어붙었다

`get_media_storage()` 를 `lru_cache` 로 감쌌더니 첫 호출의 설정값에 고정됐습니다.
원래 코드의 `_media_dir()` 은 **매번** 설정을 읽었는데 그 동작을 바꿔 버린 것입니다.

→ 로컬 저장소는 디렉터리를 호출할 때마다 설정에서 읽도록 고쳤습니다.
   (boto3 클라이언트는 생성 비용이 있어 캐시를 유지합니다)

★두 실수 모두 **"기존과 똑같이 동작한다"고 말하기 전에 기존 테스트를 돌려서** 드러났습니다.

---

## 8. 캡차 자산도 전환 (완료) — ★캐시 동반

캡차 배경·조각도 로컬 디스크에 있어 K8s 에서 같은 문제가 생깁니다. 같은 저장소 계층으로 옮겼습니다.

### ★그냥 옮기면 느려진다 — 실측으로 확인

버킷에서 **작은 객체 하나 = 약 0.27초**(VPC 안).

```
11KB ~ 119KB 모두 0.26~0.30초     크기가 아니라 왕복 지연이 지배
  연결 0.002s · TLS 0.025s · 나머지 0.24s
```

자산 서빙은 **챌린지당 배경 1 + 조각 N 번**인 핫패스라(코드 주석에 명시),
캐시 없이 바꾸면 캡차 한 번에 2초쯤 그대로 붙습니다. 지금은 로컬이라 1ms 미만입니다.

### 그래서 메모리 캐시를 같이 넣었다

캡차 자산은 **유한하고(배경 766 + 조각 2,676) 불변**이라 캐시에 맞습니다.

```
_cached_asset_bytes   lru_cache(512)   평균 56KB × 512 ≈ 29MB
cached_asset_response(key, media_type, cache_control)

실측: 첫 읽기 615ms → 캐시 후 0ms
```

★**영상에는 쓰지 않습니다.** 수백 MB 를 메모리에 올리게 되고 Range 도 못 줍니다.
영상은 `media_response()` 로 조각 단위 중계합니다.

★`reset_media_storage_cache()` 가 자산 캐시도 비웁니다 — 백엔드를 바꿨는데 옛 바이트가
남아 있으면 *"바꿨는데 옛 파일이 나온다"* 는 재현 어려운 혼선이 됩니다.

### 이미지에 굽는 방식은 안 택했다

빠르지만 백엔드 이미지가 **362MB → 530MB** 로 커져 파드가 뜰 때마다 더 받아야 하고,
**캡차 뱅크를 갱신할 때마다 이미지 재빌드·재배포**가 필요합니다. 계속 늘려 갈 자산이라 부담입니다.

### 보안 성질 유지

```
종전   resolve() 후 루트 하위인지 검사
지금   저장소 계층 _validate_key(상위경로·절대경로·비허용문자) + 확장자 화이트리스트 이중
       SVG 금지 유지 — <img> 인라인 서빙이라 스크립트 삽입 위험
```

차단 6가지 시험 통과: `../../etc/passwd` · `/etc/passwd` · `.svg` · `.php` · 빈 값 · 중간 상위경로

### 바뀐 이름

```
safe_asset(relative) -> Path   →  safe_asset_key(relative) -> (키, Content-Type)
asset_path(cid, aid) -> Path   →  asset_key(cid, aid)      -> (키, Content-Type)
_final_dir()                   →  삭제
```

**검증 20건 통과** — 키 변환 3 · 차단 6 · 버킷 읽기·캐시 4 · HTTP 6 · 404

---

## 9. 아직 안 한 것

```
□ 프론트 영향 확인 (URL 은 안 바뀌어 없을 것으로 보이나 미확인)
□ ★S3 키 회전 — 발급된 키의 보안값 일부가 노출됨(회전 대기)
□ 실제 배포 — 아직 안 함. 기본값이 local 이라 배포해도 동작이 안 바뀐다
```

---

## 10. 되돌리기

```
MEDIA_STORAGE_BACKEND=local    ← 이 한 줄이면 원래 동작 (기본값)
```

2단계까지 마쳤지만 **기본값이 `local` 이라 배포해도 동작이 바뀌지 않습니다.**
`object` 로 바꾸는 순간부터 버킷을 씁니다. 이미지 재빌드 없이 환경변수만으로 오갑니다.
