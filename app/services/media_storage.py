"""미디어 저장소 추상화 — 로컬 디스크와 오브젝트 스토리지를 같은 인터페이스로 다룬다.

왜 이게 있나(팀 학습용): 지금 강의 영상·썸네일·자료·문항 이미지가 **백엔드 서버의 로컬
디스크**(도커 볼륨 `lecture_media`, 실측 3.3GB)에 있다. 서버가 한 대일 때는 문제가 없지만,
쿠버네티스에서 파드를 2개로 늘리면 **파드마다 디스크가 달라진다** — A 파드로 업로드한 영상을
B 파드가 못 찾아 404가 난다. 요청이 어느 파드로 가느냐에 따라 되기도 하고 안 되기도 하는,
재현이 어려운 장애가 된다. 그래서 파일을 **카카오클라우드 Object Storage(버킷)** 로 옮긴다.
버킷은 파드 밖에 있으므로 파드가 몇 개든, 죽었다 살아나든 같은 파일을 본다.

왜 이렇게 만들었나 — **한 번에 갈아엎지 않고 추상화 계층을 먼저 둔다.**
`MEDIA_STORAGE_BACKEND=local` 이면 지금과 100% 같은 동작(로컬 디스크)이고, `object` 면 버킷을
쓴다. 덕분에 ① 개발·테스트는 버킷 없이 로컬로 계속 돌아가고 ② 이전 중 문제가 생기면 환경변수
하나로 즉시 되돌릴 수 있다(이미지 재빌드 불필요). 되돌릴 방법이 없는 변경은 만들지 않는다.

왜 서명 URL(presigned)로 브라우저가 버킷에서 직접 받게 하지 않았나 — **동시 시청 차단이 깨진다.**
`lectures.py`의 스트리밍 엔드포인트는 **매 Range 요청마다** `progress.session_id`를 확인해서,
다른 기기에서 재생이 시작되면(takeover) 이전 기기의 스트림을 즉시 끊는다. 서명 URL을 한 번
내주면 그 뒤로는 백엔드를 거치지 않으므로 세션이 교체돼도 계속 재생된다. 만료를 짧게 줘도
`<video>` 태그가 URL을 스스로 갱신하지 않아 재생이 끊긴다. 그래서 **백엔드가 중계**한다.
버킷→백엔드는 같은 VPC 안이라 외부 대역폭이 늘지 않고, 백엔드→브라우저는 지금과 동일하다.

공부 키워드: S3 호환 API(SigV4 서명), HTTP Range 요청(206 부분 응답 — 영상 탐색이 이걸로
동작한다), 스트리밍 응답(파일을 메모리에 통째로 올리지 않고 조각으로 흘려보내기).
"""

from __future__ import annotations

import os
import re
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO, Iterator, Protocol

from app.core.config import get_settings

# 한 번에 읽어 넘기는 조각 크기. 너무 작으면 왕복이 늘고, 너무 크면 메모리를 물고 있는다.
_CHUNK = 1024 * 1024  # 1MiB


class MediaNotFound(Exception):
    """요청한 키가 저장소에 없다 — 호출부가 404로 바꾼다."""


@dataclass(frozen=True)
class MediaStat:
    """파일 존재 확인 결과. size 는 Range 응답의 Content-Range 계산에 필요하다."""

    size: int


class MediaStorage(Protocol):
    """미디어 저장소 인터페이스.

    키(key)는 `lectures/{id}.mp4` 처럼 **슬래시로 구분한 상대 경로 문자열**이다.
    로컬 백엔드는 이것을 디렉터리 경로로, 오브젝트 백엔드는 객체 이름으로 쓴다.
    ★키는 항상 코드가 id+확장자로 만들어 낸다 — 사용자 입력이 키에 섞이면 경로 조작이 된다.
    """

    def save(self, key: str, src: BinaryIO) -> int: ...
    def save_path(self, key: str, src_path: Path) -> int: ...
    def stat(self, key: str) -> MediaStat | None: ...
    def open_range(self, key: str, start: int, end: int) -> Iterator[bytes]: ...
    def delete(self, key: str) -> None: ...


def _validate_key(key: str) -> str:
    """키에 상위 경로 이동이나 절대 경로가 섞이지 않았는지 확인한다.

    호출부가 id+확장자로만 키를 만들지만, 저장소 계층에서 한 번 더 막는다 —
    나중에 누가 사용자 입력을 키에 넣더라도 여기서 걸린다(방어적 이중화)."""
    if not key or key.startswith("/") or ".." in key.split("/"):
        raise ValueError(f"잘못된 미디어 키: {key!r}")
    if not re.fullmatch(r"[A-Za-z0-9._/\-]+", key):
        raise ValueError(f"미디어 키에 허용되지 않는 문자: {key!r}")
    return key


# ────────────────────────────────────────────────────────────────
# 로컬 디스크 — 지금까지의 동작. 개발·테스트 기본값이자 되돌리기 경로.
# ────────────────────────────────────────────────────────────────
class LocalMediaStorage:
    """키의 **첫 구간**을 기존 설정 디렉터리에 대응시킨다.

    ★왜 단순히 `root/key` 가 아닌가: 기존 파일들이 이미 `{LECTURE_MEDIA_DIR}/materials/…`
    처럼 놓여 있다. 여기서 배치를 바꾸면 **기존 3.3GB 가 전부 "없는 파일"이 된다.**
    `local` 모드는 지금과 한 바이트도 다르지 않아야 되돌리기가 성립하므로, 키
    `lectures/materials/x` 를 `{LECTURE_MEDIA_DIR}/materials/x` 로 그대로 매핑한다.
    (버킷에서는 접두사가 그대로 객체 이름이 되어 종류별로 정리된다.)

    ★디렉터리는 **호출할 때마다** 설정에서 읽는다. 캐시해 두면 테스트가 설정을 바꿔도
    첫 호출 값에 얼어붙는다(원래 코드의 `_media_dir()`도 매번 읽었다 — 그 동작을 지킨다).
    """

    def _roots(self) -> dict[str, Path]:
        s = get_settings()
        return {
            "lectures": Path(s.LECTURE_MEDIA_DIR),
            "captcha": Path(s.CAPTCHA_MEDIA_DIR),
        }

    def _path(self, key: str) -> Path:
        k = _validate_key(key)
        head, _, rest = k.partition("/")
        base = self._roots().get(head)
        if base is None or not rest:
            raise ValueError(f"알 수 없는 미디어 키 접두사: {key!r} (허용: lectures, captcha)")
        return base / rest

    def save(self, key: str, src: BinaryIO) -> int:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        with open(p, "wb") as f:
            while chunk := src.read(_CHUNK):
                total += len(chunk)
                f.write(chunk)
        return total

    def save_path(self, key: str, src_path: Path) -> int:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        # 같은 파일시스템이면 rename 이 원자적이라 반쯤 쓰인 파일이 보이지 않는다.
        shutil.move(str(src_path), str(p))
        return p.stat().st_size

    def stat(self, key: str) -> MediaStat | None:
        p = self._path(key)
        if not p.is_file():
            return None
        return MediaStat(size=p.stat().st_size)

    def open_range(self, key: str, start: int, end: int) -> Iterator[bytes]:
        p = self._path(key)
        if not p.is_file():
            raise MediaNotFound(key)
        remaining = end - start + 1
        with open(p, "rb") as f:
            f.seek(start)
            while remaining > 0:
                chunk = f.read(min(_CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


# ────────────────────────────────────────────────────────────────
# 오브젝트 스토리지 — 카카오클라우드 Object Storage (S3 호환 API)
# ────────────────────────────────────────────────────────────────
class ObjectMediaStorage:
    """S3 호환 API로 버킷에 저장한다.

    ★boto3 클라이언트는 스레드 안전하지 않은 부분이 있어 요청마다 만들지 않고 인스턴스가
    하나를 들고 쓴다(`get_media_storage()`가 lru_cache 로 싱글턴을 준다).
    """

    def __init__(self, *, bucket: str, prefix: str, endpoint: str, region: str,
                 access_key: str, secret_key: str) -> None:
        import boto3  # 지연 임포트 — local 백엔드만 쓰는 환경에서는 설치조차 필요 없다
        from botocore.config import Config

        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            # SigV4 명시 — 카카오클라우드 Object Storage 가 요구한다.
            config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}),
        )

    def _obj(self, key: str) -> str:
        k = _validate_key(key)
        return f"{self._prefix}/{k}" if self._prefix else k

    def save(self, key: str, src: BinaryIO) -> int:
        # upload_fileobj 는 큰 파일을 자동으로 멀티파트로 나눠 올린다(메모리 상주 없음).
        self._s3.upload_fileobj(src, self._bucket, self._obj(key))
        st = self.stat(key)
        return st.size if st else 0

    def save_path(self, key: str, src_path: Path) -> int:
        self._s3.upload_file(str(src_path), self._bucket, self._obj(key))
        size = src_path.stat().st_size
        # 업로드 성공 후에만 임시파일을 지운다 — 실패하면 남겨 두어 재시도할 수 있게.
        src_path.unlink(missing_ok=True)
        return size

    def stat(self, key: str) -> MediaStat | None:
        from botocore.exceptions import ClientError

        try:
            r = self._s3.head_object(Bucket=self._bucket, Key=self._obj(key))
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        return MediaStat(size=int(r["ContentLength"]))

    def open_range(self, key: str, start: int, end: int) -> Iterator[bytes]:
        from botocore.exceptions import ClientError

        try:
            r = self._s3.get_object(
                Bucket=self._bucket, Key=self._obj(key), Range=f"bytes={start}-{end}"
            )
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NoSuchBucket"):
                raise MediaNotFound(key) from e
            raise
        body = r["Body"]
        try:
            while chunk := body.read(_CHUNK):
                yield chunk
        finally:
            body.close()

    def delete(self, key: str) -> None:
        # S3 delete_object 는 없는 키에도 성공한다 — 로컬의 missing_ok=True 와 같은 성질.
        self._s3.delete_object(Bucket=self._bucket, Key=self._obj(key))


@lru_cache(maxsize=1)
def get_media_storage() -> MediaStorage:
    """설정에 따라 저장소 구현을 고른다.

    ★`object` 인데 필수 설정이 비어 있으면 **로컬로 조용히 떨어지지 않고 예외를 낸다.**
    설정 누락이 "파일이 없습니다"로 둔갑하면 원인을 못 찾는다 — 가짜 성공을 만들지 않는다."""
    s = get_settings()
    backend = (getattr(s, "MEDIA_STORAGE_BACKEND", "local") or "local").strip().lower()
    if backend == "local":
        return LocalMediaStorage()
    if backend == "object":
        missing = [
            n for n in ("MEDIA_BUCKET", "MEDIA_S3_ENDPOINT", "MEDIA_S3_ACCESS_KEY", "MEDIA_S3_SECRET_KEY")
            if not (getattr(s, n, "") or "").strip()
        ]
        if missing:
            raise RuntimeError(
                "MEDIA_STORAGE_BACKEND=object 인데 다음 설정이 비어 있습니다: " + ", ".join(missing)
            )
        return ObjectMediaStorage(
            bucket=s.MEDIA_BUCKET,
            prefix=s.MEDIA_KEY_PREFIX,
            endpoint=s.MEDIA_S3_ENDPOINT,
            region=s.MEDIA_S3_REGION,
            access_key=s.MEDIA_S3_ACCESS_KEY,
            secret_key=s.MEDIA_S3_SECRET_KEY,
        )
    raise RuntimeError(f"MEDIA_STORAGE_BACKEND 값이 올바르지 않습니다: {backend!r} (local|object)")


def reset_media_storage_cache() -> None:
    """테스트에서 설정을 바꾼 뒤 저장소를 다시 만들게 한다.

    ★자산 캐시도 같이 비운다 — 저장소가 바뀌었는데 옛 백엔드에서 읽은 바이트가 남아 있으면
    "바꿨는데 옛 파일이 나온다"는 재현 어려운 혼선이 생긴다."""
    get_media_storage.cache_clear()
    _cached_asset_bytes.cache_clear()


# ────────────────────────────────────────────────────────────────
# Range 요청 파싱 — 영상 탐색(seek)이 이걸로 동작한다
# ────────────────────────────────────────────────────────────────
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """`Range: bytes=start-end` 를 (start, end) 로. 없거나 형식이 틀리면 None(전체 응답).

    지원 형태 — `bytes=0-`(그 뒤 전부) · `bytes=0-1023`(구간) · `bytes=-500`(끝에서 500바이트).
    ★범위가 파일 크기를 벗어나면 None 을 돌려 전체를 보낸다. 416 을 내는 것이 더 엄격하지만,
    브라우저마다 벗어난 Range 를 보내는 경우가 있어 재생이 끊기는 것을 피한다."""
    if not header or size <= 0:
        return None
    m = _RANGE_RE.match(header.strip())
    if not m:
        return None
    raw_start, raw_end = m.group(1), m.group(2)
    if not raw_start and not raw_end:
        return None
    if not raw_start:  # bytes=-500 → 마지막 500바이트
        length = int(raw_end)
        if length <= 0:
            return None
        start = max(0, size - length)
        end = size - 1
    else:
        start = int(raw_start)
        end = int(raw_end) if raw_end else size - 1
    if start > end or start >= size:
        return None
    return start, min(end, size - 1)


# ────────────────────────────────────────────────────────────────
# 작고 불변인 자산 전용 — 메모리 캐시
# ────────────────────────────────────────────────────────────────
# 왜 필요한가(실측): 버킷에서 작은 객체 하나를 읽는 데 **약 0.27초**가 걸린다(VPC 안, 11KB~119KB
# 모두 비슷 — 크기가 아니라 왕복 지연이 지배한다). 드래그 캡차는 챌린지당 배경 1 + 조각 N개를
# 매번 읽는 핫패스라, 그냥 버킷으로 바꾸면 **한 번 푸는 데 2초쯤 그대로 느려진다**
# (지금은 로컬 디스크라 1ms도 안 걸린다).
#
# 캡차 자산은 캐시하기에 딱 맞다 — **유한하고(배경 766 + 조각 2,676) 내용이 변하지 않는다**
# (뱅크를 새로 만들면 새 파일이 생길 뿐 기존 파일은 그대로). 그래서 한 번 읽으면 계속 쓴다.
#
# ★영상에는 절대 쓰지 말 것. 수백 MB 를 메모리에 올리게 되고 Range 도 못 준다.
#   영상은 media_response() 로 조각 단위 중계한다.
_ASSET_CACHE_MAX = 512  # 평균 56KB × 512 ≈ 29MB


@lru_cache(maxsize=_ASSET_CACHE_MAX)
def _cached_asset_bytes(key: str) -> bytes:
    storage = get_media_storage()
    st = storage.stat(key)
    if st is None:
        raise MediaNotFound(key)
    return b"".join(storage.open_range(key, 0, st.size - 1))


def cached_asset_response(key: str, *, media_type: str, cache_control: str | None = None):
    """작고 불변인 자산을 메모리 캐시로 서빙한다 — 첫 요청만 저장소를 읽는다.

    ★`media_response()` 와 달리 Range 를 처리하지 않는다. 캡차 이미지처럼 통째로 받는
    자산 전용이다(브라우저도 <img> 로는 Range 를 안 쓴다)."""
    from fastapi import HTTPException, status
    from fastapi.responses import Response

    try:
        data = _cached_asset_bytes(key)
    except MediaNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="파일을 찾을 수 없습니다.")
    headers = {"Cache-Control": cache_control} if cache_control else {}
    return Response(content=data, media_type=media_type, headers=headers)


@contextmanager
def local_file(key: str, *, suffix: str = "") -> Iterator[Path]:
    """저장소의 객체를 **로컬 파일 경로로** 잠깐 빌려 준다. 블록을 나가면 정리한다.

    왜 필요한가: 외부 라이브러리는 대개 파일 경로를 받는다(우리 경우 STT 워커 호출이
    `open(path,'rb')` 로 영상을 읽는다). 버킷에 있는 객체는 경로가 없으므로 임시로 내려받아
    준다. **로컬 백엔드에서는 이미 파일이라 복사하지 않고 원본 경로를 그대로 준다** —
    3.3GB 짜리를 쓸데없이 두 번 쓰지 않기 위해서다.

    ★로컬 경로를 그대로 주므로 **호출부는 이 파일을 수정·삭제하면 안 된다.** 읽기 전용이다.
    """
    storage = get_media_storage()
    if isinstance(storage, LocalMediaStorage):
        p = storage._path(key)
        if not p.is_file():
            raise MediaNotFound(key)
        yield p
        return

    st = storage.stat(key)
    if st is None:
        raise MediaNotFound(key)
    import tempfile

    d = Path(tempfile.mkdtemp(prefix="catchap-media-"))
    tmp = d / f"obj{suffix}"
    try:
        with open(tmp, "wb") as f:
            for chunk in storage.open_range(key, 0, st.size - 1):
                f.write(chunk)
        yield tmp
    finally:
        tmp.unlink(missing_ok=True)
        try:
            d.rmdir()
        except OSError:
            pass


def media_response(
    key: str,
    *,
    media_type: str,
    range_header: str | None = None,
    filename: str | None = None,
    cache_control: str | None = None,
):
    """저장소의 파일을 HTTP 응답으로 — Range(206)를 직접 처리한다.

    지금까지는 `FileResponse(path)` 하나로 끝났다. starlette 가 로컬 파일의 Range 를
    알아서 처리해 줬기 때문이다. 저장소가 버킷이 되면 **starlette 가 그 파일을 모르므로**
    Range 를 우리가 해석해서 그 구간만 버킷에서 읽어 흘려보내야 한다. 이게 없으면
    영상 탐색(seek)이 안 되고, 브라우저가 전체를 받을 때까지 재생이 시작되지 않는다.

    `Accept-Ranges: bytes` 를 항상 붙인다 — 브라우저는 이 헤더를 보고 탐색이 가능한지 판단한다.
    ★파일 전체를 메모리에 올리지 않는다. 1MiB 씩 조각으로 읽어 그대로 내보낸다."""
    from fastapi import HTTPException, status
    from fastapi.responses import StreamingResponse

    storage = get_media_storage()
    st = storage.stat(key)
    if st is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="파일을 찾을 수 없습니다.")

    headers: dict[str, str] = {"Accept-Ranges": "bytes"}
    if filename:
        # 다운로드 파일명 — 한글 등 비ASCII 는 RFC 5987 형식으로만 보낸다(헤더 인코딩 오류 방지).
        from urllib.parse import quote

        headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"
    if cache_control:
        headers["Cache-Control"] = cache_control

    rng = parse_range(range_header, st.size)
    if rng is None:
        headers["Content-Length"] = str(st.size)
        return StreamingResponse(
            storage.open_range(key, 0, st.size - 1),
            media_type=media_type,
            headers=headers,
        )

    start, end = rng
    headers["Content-Range"] = f"bytes {start}-{end}/{st.size}"
    headers["Content-Length"] = str(end - start + 1)
    return StreamingResponse(
        storage.open_range(key, start, end),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        media_type=media_type,
        headers=headers,
    )
