"""임베드 위젯 서빙 — 위젯 JS + 데모 페이지 (외부 사이트가 <script src>로 로드).

위젯/데모는 어떤 출처에서도 로드되도록 CORS 허용 헤더를 붙인다.
"""

import html
import re
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response

router = APIRouter(prefix="/widget", tags=["widget"])

_STATIC = Path(__file__).resolve().parents[3] / "static"  # app/static
_CORS = {"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=300"}

# 데모 페이지 CSP — 위젯 로드(같은 오리진 스크립트)에 필요한 최소.
# 입력값은 아래에서 이스케이프/화이트리스트로 검증하고, CSP는 방어심층.
_DEMO_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "base-uri 'none'; form-action 'self'"
)
# api 경로 화이트리스트: 슬래시로 시작하는 안전한 경로 문자만 (스킴/호스트/따옴표 차단)
_API_RE = re.compile(r"^/[A-Za-z0-9_/\-]{1,64}$")


@lru_cache(maxsize=1)
def _widget_js_source() -> str:
    """위젯 JS는 배포 단위로만 바뀌므로 프로세스당 1회만 디스크에서 읽는다."""
    return (_STATIC / "catchap-widget.js").read_text(encoding="utf-8")


@router.get("/catchap-widget.js")
def widget_js():
    return Response(
        content=_widget_js_source(), media_type="application/javascript", headers=_CORS
    )


@router.get("/demo", response_class=HTMLResponse)
def widget_demo(site_key: str = "", api: str = "/api/v1"):
    """임베드 예시 페이지 — ?site_key=... 로 발급받은 키를 넣으면 실제 위젯이 뜬다.

    site_key/api 쿼리는 HTML·<script src>에 삽입되므로 XSS 방지를 위해:
      - api 는 안전한 경로 화이트리스트만 허용(불일치 시 기본값 /api/v1)
      - 모든 값은 html.escape(quote=True)로 이스케이프
    """
    if not _API_RE.match(api):
        api = "/api/v1"
    sk_raw = site_key or "여기에_발급받은_site_key"
    # 속성·요소 컨텍스트 모두에 안전하도록 따옴표까지 이스케이프
    sk = html.escape(sk_raw, quote=True)
    api = html.escape(api, quote=True)
    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CatChap 위젯 데모</title>
<style>
 body{{font-family:'Pretendard','Malgun Gothic',sans-serif;background:#FFF7F0;margin:0;padding:40px 16px;color:#3A3226}}
 .wrap{{max-width:520px;margin:0 auto}}
 h1{{font-size:22px}} p{{color:#7A7266;line-height:1.6;font-size:14px}}
 form{{background:#fff;border:1px solid #F0E4D8;border-radius:16px;padding:22px;margin-top:18px}}
 label{{font-weight:800;font-size:13px;display:block;margin-bottom:8px}}
 input[type=text]{{width:100%;box-sizing:border-box;padding:11px 13px;border:1.5px solid #E4E6EF;border-radius:10px;margin-bottom:16px;font-size:14px}}
 button.go{{width:100%;border:none;background:linear-gradient(135deg,#FF7A4D,#FF5A4D);color:#fff;font-weight:800;font-size:15px;padding:13px;border-radius:12px;cursor:pointer;margin-top:14px}}
 code{{background:#f4f0eb;padding:2px 6px;border-radius:5px;font-size:12px}}
 .note{{margin-top:12px;font-size:12px;color:#9a9187}}
</style></head><body><div class="wrap">
 <h1>🐱 CatChap 위젯 데모</h1>
 <p>아래는 외부 사이트에 이렇게 붙이면 되는 예시예요:</p>
 <p><code>&lt;div class="catchap" data-site-key="..." data-api="{api}"&gt;&lt;/div&gt;</code><br>
    <code>&lt;script src="{api}/widget/catchap-widget.js" defer&gt;&lt;/script&gt;</code></p>
 <form onsubmit="return check(event)">
   <label>회원가입 (예시 폼)</label>
   <input type="text" placeholder="이메일" required>
   <input type="text" placeholder="닉네임" required>
   <div class="catchap" data-site-key="{sk}" data-api="{api}"></div>
   <button class="go" type="submit">가입하기</button>
   <div class="note" id="note">위 캡차를 통과해야 가입돼요.</div>
 </form>
</div>
<script src="{api}/widget/catchap-widget.js" defer></script>
<script>
 function check(e){{
   e.preventDefault();
   var t=document.querySelector('input[name="catchap-token"]');
   var note=document.getElementById('note');
   if(!t||!t.value){{ note.textContent='⚠️ 먼저 캡차를 통과해 주세요.'; note.style.color='#C25'; return false; }}
   note.textContent='✅ 통과 토큰 확보! 실제로는 이 토큰을 서버로 보내 /captcha/v1/validate 로 최종 검증합니다.';
   note.style.color='#17B08C';
   return false;
 }}
 document.addEventListener('catchap:success',function(){{
   var n=document.getElementById('note'); n.textContent='✅ 캡차 통과! 이제 가입하기를 눌러도 돼요.'; n.style.color='#17B08C';
 }});
</script>
</body></html>"""
    return HTMLResponse(
        content=page,
        headers={"Access-Control-Allow-Origin": "*", "Content-Security-Policy": _DEMO_CSP},
    )
