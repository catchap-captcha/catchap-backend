/*!
 * CatChap Widget — 임베드형 캡차/교육 위젯 (self-contained, 의존성 없음)
 *
 * 사용법 (외부 사이트):
 *   <div class="catchap"
 *        data-site-key="ck_captcha_xxx"
 *        data-api="https://api.catchap.io/api/v1"></div>
 *   <script src="https://api.catchap.io/api/v1/widget/catchap-widget.js" defer></script>
 *
 * 통과하면 위젯 안에 <input type="hidden" name="catchap-token"> 가 채워진다.
 * 폼 제출 시 이 토큰을 서버로 보내고, 서버가 /captcha/v1/validate 로 최종 확인한다.
 */
(function () {
  'use strict';
  // ── 디자인 토큰 (1단계 도입) — 코드 전체에 흩어져 있던 하드코딩 스타일의 단일 근거.
  // 기존 브랜드 톤을 유지하며 체계화: 코랄(#FF5A4D)=주 액션, 잉크 브라운(#3A3226)=본문.
  // 대비(크림 #FFFAF4 배경 기준): ink 11.9:1 · inkSoft 6.6:1 · inkFaint 4.6:1 — 본문/보조 AA 이상.
  var T = {
    color: {
      brand: '#FF5A4D',        // 브랜드 코랄 — 주 버튼·선택 강조
      brandSoft: '#FFF0EE',    // 선택 배경(코랄 틴트)
      brandLine: '#FFB8A8',    // 코랄 보조 라인(드롭 링 등)
      ok: '#17B08C', okSoft: '#E1F5EC',        // 정답
      err: '#D14559', errSoft: '#FFEDEF',      // 오답(아동용 — 채도 낮춘 로즈)
      ink: '#3A3226',          // 본문 잉크(웜 브라운)
      inkSoft: '#6B6157',      // 보조 텍스트(뜻·라벨)
      inkFaint: '#8A8070',     // 힌트·자리표시
      inkMute: '#B0A79B',      // 상태 표시·비활성
      line: '#F0E4D8',         // 카드 테두리(웜 샌드)
      lineSoft: '#E3D6C6',     // 점선 존 테두리
      cream: '#FFFAF4',        // 놀이 영역 배경
      card: '#fff',
      // 발자국 트레일 젤리 — 브랜드 코랄에 로즈 살짝 섞은 투톤(위 밝은 하이라이트/아래 진한 그늘).
      // 반투명(≈0.5~0.62)이라 글자 위를 덮어도 읽히되 1단계(0.30)보다 또렷하다.
      pawLite: 'rgba(255,138,110,0.52)', // 젤리 윗면 하이라이트(밝은 살구코랄)
      pawBase: 'rgba(232,86,74,0.60)',   // 젤리 본색(코랄)
      pawDeep: 'rgba(196,63,74,0.62)',   // 젤리 아랫면 그늘(로즈)
    },
    font: { xl: '21px', lg: '17px', md: '15px', sm: '13px', xs: '12px' },
    radius: { lg: '16px', md: '12px', sm: '10px', pill: '30px' },
    shadow: { card: '0 10px 30px -20px rgba(120,90,70,.4)', pop: '0 10px 24px -14px rgba(120,90,70,0.35)' },
    tap: '44px', // 아동 손가락 최소 터치타깃(WCAG 2.5.5 · 플랫폼 HIG 44pt)
  };
  var C = T.color.brand, OK = T.color.ok; // 기존 사용처(전 렌더러) 호환 별칭

  function css(el, o) { for (var k in o) el.style[k] = o[k]; }
  function h(tag, cls) { var e = document.createElement(tag); if (cls) e.className = cls; return e; }

  // 포인터 → SVG viewBox(0..1) 정규화 — preserveAspectRatio 'meet' 레터박스 보정.
  // 클라이언트 rect로 그냥 나누면 svg가 정사각이 아닐 때(가로가 넓은 게임 화면) 가이드/
  // 존과 좌표계가 어긋나 채점·히트판정이 깨진다(넓은 화면에서 따라쓰기 전멸·캐릭터 못 잡음).
  function svgNorm(svg, e) {
    var r = svg.getBoundingClientRect();
    var scale = Math.min(r.width, r.height) / 100; // viewBox 0 0 100 100 기준
    var offX = (r.width - scale * 100) / 2, offY = (r.height - scale * 100) / 2;
    var x = (e.clientX - r.left - offX) / (scale * 100);
    var y = (e.clientY - r.top - offY) / (scale * 100);
    return [Math.max(0, Math.min(1, x)), Math.max(0, Math.min(1, y))];
  }

  // 움직이는 존(생활 place의 자동차 등) 애니메이션 — 문서에 1회만 주입
  function ensureKeyframes() {
    if (document.getElementById('catchap-kf')) return;
    var st = document.createElement('style'); st.id = 'catchap-kf';
    st.textContent = '@keyframes ccMove{from{margin-left:-2.5%}to{margin-left:2.5%}}'
      // 발자국 트레일 — 나타난 뒤 서서히 사라지며 살짝 작아진다(걷고 지나간 자국)
      + '@keyframes ccPawFade{0%{opacity:1}100%{opacity:0;transform:scale(.72)}}'
      // 오디오 재생중 이퀄라이저 막대 — 위아래로 출렁
      + '@keyframes ccWave{0%,100%{transform:scaleY(.32)}50%{transform:scaleY(1)}}'
      // 카드 뒤집기 — Y축 회전(memory/puzzle 스냅)
      + '@keyframes ccPop{0%{transform:scale(.6);opacity:.2}60%{transform:scale(1.08)}100%{transform:scale(1);opacity:1}}';
    document.head.appendChild(st);
  }

  // ── 효과음 (WebAudio 합성 — 오디오 에셋 불필요, 전 과목 공통) ──
  // 답 제출·버튼 클릭은 항상 사용자 제스처 뒤라 자동재생 정책에 걸리지 않는다.
  var sfxCtx = null;
  function sfxContext() {
    try {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      if (!sfxCtx) sfxCtx = new AC();
      if (sfxCtx.state === 'suspended') sfxCtx.resume();
      return sfxCtx;
    } catch (e) { return null; }
  }
  function sfxNote(ctx, freq, at, dur, type, peak) {
    var o = ctx.createOscillator(), g = ctx.createGain();
    o.type = type; o.frequency.value = freq;
    g.gain.setValueAtTime(0.0001, at);
    g.gain.exponentialRampToValueAtTime(peak, at + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, at + dur);
    o.connect(g); g.connect(ctx.destination);
    o.start(at); o.stop(at + dur + 0.05);
  }
  function playSfx(kind) {
    var ctx = sfxContext();
    if (!ctx) return;
    try {
      var t = ctx.currentTime + 0.01;
      if (kind === 'correct') { // 밝은 상행 차임
        sfxNote(ctx, 659.25, t, 0.14, 'triangle', 0.17);
        sfxNote(ctx, 1046.5, t + 0.11, 0.24, 'triangle', 0.17);
      } else if (kind === 'wrong') { // 부드러운 하행(아동용 — 거슬리는 버저 금지)
        sfxNote(ctx, 392.0, t, 0.16, 'sine', 0.14);
        sfxNote(ctx, 311.13, t + 0.13, 0.24, 'sine', 0.12);
      } else if (kind === 'finish') { // 세션 완료 팡파르
        sfxNote(ctx, 523.25, t, 0.12, 'triangle', 0.15);
        sfxNote(ctx, 659.25, t + 0.1, 0.12, 'triangle', 0.15);
        sfxNote(ctx, 783.99, t + 0.2, 0.12, 'triangle', 0.15);
        sfxNote(ctx, 1046.5, t + 0.3, 0.34, 'triangle', 0.17);
      }
    } catch (e) {}
  }

  function api(base, path, key, body, auth) {
    var headers = { 'Content-Type': 'application/json', 'X-Site-Key': key };
    // 인앱(1st-party) 학생 토큰 — 서버가 채점 결과를 학생 학습기록(코인·진도·퀴즈)에 적립
    if (auth) headers['Authorization'] = 'Bearer ' + auth;
    return fetch(base.replace(/\/$/, '') + path, {
      method: 'POST',
      headers: headers,
      body: body ? JSON.stringify(body) : undefined,
    }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, status: r.status, data: d }; }); });
  }

  function mount(box) {
    ensureKeyframes();
    var key = box.getAttribute('data-site-key');
    var base = box.getAttribute('data-api') || '/api/v1';
    // data-subject: 교육형 키로 과목별 챌린지를 요청할 때(우리 앱 과목별 게임화면 등)
    var subject = box.getAttribute('data-subject') || '';
    // 인앱 소비(1st-party) 전용 속성 — 외부 임베드는 전부 생략 가능
    var authStatic = box.getAttribute('data-auth') || '';      // 학생 access token(고정) → 적립
    var authFn = typeof box.catchapAuth === 'function' ? box.catchapAuth : null; // 매 요청 호출(만료 자동 갱신)
    var day = box.getAttribute('data-day') || '';              // 커리큘럼 일차
    var chapter = box.getAttribute('data-chapter') || '';      // 전체학습 주간 챕터
    var stage = box.getAttribute('data-stage') || '';          // 챕터 단계(1~5)
    var replay = box.getAttribute('data-replay') === '1';      // 복습(코인·퀴즈 상태 미반영)
    var bankMode = box.getAttribute('data-bank') === '1';      // 전체학습 문제은행(안 푼>틀린>맞춘, 무보상)
    var sessionTotal = parseInt(box.getAttribute('data-total') || '0', 10) || 0; // 세션 문항 수
    // 효과음: 기본 켜짐(외부 임베드). 인앱은 게임 화면이 학생 설정에 따라 직접 재생하므로
    // data-sfx="0"으로 꺼서 이중 재생을 막는다.
    var sfxOn = box.getAttribute('data-sfx') !== '0';
    function sfx(kind) { if (sfxOn) playSfx(kind); }
    if (!key) { box.textContent = 'CatChap: data-site-key 가 필요합니다.'; return; }

    // 요청 직전에 항상 유효한 토큰을 얻는다 — 콜백 실패 시 고정 토큰으로 폴백(익명 강등 방지 최선).
    // 콜백이 영영 안 끝나면(pending) 위젯 전체가 굳으므로 4초 타임아웃으로 폴백한다.
    function getAuth() {
      if (!authFn) return Promise.resolve(authStatic);
      try {
        var got = Promise.resolve(authFn()).then(
          function (t) { return t || authStatic; },
          function () { return authStatic; }
        );
        var timeout = new Promise(function (resolve) {
          setTimeout(function () { resolve(authStatic); }, 4000);
        });
        return Promise.race([got, timeout]);
      } catch (e) { return Promise.resolve(authStatic); }
    }

    // data-size="full" → 컨테이너 꽉 채움(앱 게임 화면용), 기본은 420px 컴팩트(외부 임베드용)
    var full = box.getAttribute('data-size') === 'full';
    css(box, {
      display: full ? 'flex' : 'block', flexDirection: full ? 'column' : '',
      maxWidth: full ? '100%' : '420px', width: '100%',
      border: full ? 'none' : '1px solid #F0E4D8', borderRadius: '16px',
      padding: full ? '30px 26px 20px' : '18px', fontFamily: "'Pretendard','Malgun Gothic',sans-serif",
      background: full ? 'transparent' : '#fff',
      boxShadow: full ? 'none' : '0 10px 30px -20px rgba(120,90,70,.4)', boxSizing: 'border-box',
    });
    box.__full = full;

    var head = h('div'); css(head, { display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' });
    var logo = h('span'); logo.textContent = '🐱'; css(logo, { fontSize: '20px' });
    var brand = h('span'); brand.textContent = 'CatChap'; css(brand, { fontWeight: '800', color: C, fontSize: '15px' });
    var spacer = h('span'); css(spacer, { flex: '1' });
    var status = h('span'); css(status, { fontSize: '12px', fontWeight: '700', color: '#B0A79B' });
    head.appendChild(logo); head.appendChild(brand); head.appendChild(spacer); head.appendChild(status);

    var body = h('div');
    // 풀 사이즈: 문항은 세로 중앙, 액션 풋터는 카드 하단에 붙도록 본문이 남는 높이를 차지
    if (full) css(body, { flex: '1', display: 'flex', flexDirection: 'column', justifyContent: 'center' });
    var hidden = h('input'); hidden.type = 'hidden'; hidden.name = 'catchap-token';
    box.innerHTML = ''; box.appendChild(head); box.appendChild(body); box.appendChild(hidden);
    var product = 'captcha', renderedAt = 0, retries = 0, solvedCount = 0;
    var redoCount = 0; // 문항당 '다시 고르기/그리기' 횟수 — 행동데이터(retry_count)에 합산
    var answeredCount = 0, sessionDone = false; // 교육형 세션 진행 — 서버 session 응답 우선
    var grading = false;  // verify in-flight — 이 동안 다음 문제/제출 클릭을 무시(데드락 방지)
    var renderSeq = 0;    // 문항 세대 — 이전 문항의 늦은 verify 응답이 새 문항을 못 건드리게

    // ── 포인터 궤적 캡처 — 아이/어른의 움직임 차이(속도·경로·멈춤)가 행동 판정 모델의 재료.
    // 위젯 영역 기준 0~1 정규화 좌표를 [t,x,y]로 샘플링(16ms 스로틀, 최대 1500점).
    var trace = [], traceStart = 0, traceLastT = 0, TRACE_MAX = 1500, inputType = '';
    function traceReset() { trace = []; traceStart = Date.now(); traceLastT = -1; inputType = ''; }
    function tracePoint(e, force) {
      if (e && e.pointerType) inputType = e.pointerType; // mouse|touch|pen — 기기 축(소급 불가)
      if (!traceStart || trace.length >= TRACE_MAX) return;
      var r = box.getBoundingClientRect();
      if (!r.width || !r.height) return;
      var x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
      if (x < 0 || x > 1 || y < 0 || y > 1) return;
      var t = Date.now() - traceStart;
      if (!force && traceLastT >= 0 && t - traceLastT < 16) return;
      traceLastT = t;
      trace.push([t, Math.round(x * 1e4) / 1e4, Math.round(y * 1e4) / 1e4]);
    }
    box.addEventListener('pointermove', function (e) { tracePoint(e, false); });
    box.addEventListener('pointerdown', function (e) { tracePoint(e, true); onActivity(); });
    box.addEventListener('pointerup', function (e) { tracePoint(e, true); onActivity(); });
    box.addEventListener('keydown', function () { onActivity(); });

    // ── 캣찹 발자국 트레일 — 포인터(마우스·손가락)가 걸어간 길에 고양이 발자국을 남긴다.
    // 순수 시각 레이어: pointer-events:none 오버레이에만 그려 조작·채점·verify 계약과 무관하고,
    // data-paws="0"으로 끌 수 있다(기본 켬). prefers-reduced-motion이면 만들지 않는다(접근성).
    // 일정 보폭(PAW_STEP)마다 좌우 발을 번갈아, 진행 방향으로 회전시켜 '걷는' 자국을 만들고
    // 각 발자국은 ccPawFade로 서서히 사라진 뒤 DOM에서 제거된다(노드 상한 = 성능 보호).
    var PAW_STEP = 34;    // 발자국 간격(px) — 연속 도배가 아니라 걷는 보폭
    var PAW_JUMP = 120;   // 이보다 큰 이동은 걸음이 아니라 점프 — 자국 없이 기준점만 갱신
    var PAW_MAX = 24;     // 동시 표시 상한
    var PAW_LIFE = 1100;  // 수명(ms) — ccPawFade 길이와 일치
    var pawsOn = box.getAttribute('data-paws') !== '0'
      && !(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    var pawLayer = null, pawLast = null, pawSide = 0, pawAlive = [];
    // 고양이 발자국 SVG("toe beans"/젤리 스타일) — 발끝이 위(-y)라 진행 방향으로 회전시킨다.
    // 젤리 콘셉트: (1) 발가락 젤리 4개 = 통통한 둥근 젤리, 부채꼴 아치로(안쪽 2개가 조금 앞으로)
    // (2) 메인 젤리(掌球) = 크고 둥근 하트/콩팥형 · (3) 발가락과 掌球 살짝 분리 · (4) 발톱 없음.
    // 좌우 발 scaleX(-1) 미러. 입체감: 세로 그라디언트(위 밝은 하이라이트→아래 진한 그늘)로
    // 젤리 본색을 칠하고, 각 젤리 윗부분에 반투명 하이라이트 타원을 얹어 촉촉한 광택을 준다.
    var PG = 'ccPawG' + Math.random().toString(36).slice(2, 8); // 그라디언트 id 충돌 방지(다중 인스턴스)
    var PAW_SVG = '<svg width="17" height="17" viewBox="0 0 24 24"'
      + ' style="display:block;overflow:visible;animation:ccPawFade ' + (PAW_LIFE / 1000) + 's ease-out forwards">'
      + '<defs><linearGradient id="' + PG + '" x1="0" y1="0" x2="0" y2="1">'
      + '<stop offset="0" stop-color="' + T.color.pawLite + '"/>'
      + '<stop offset="0.55" stop-color="' + T.color.pawBase + '"/>'
      + '<stop offset="1" stop-color="' + T.color.pawDeep + '"/></linearGradient></defs>'
      + '<g fill="url(#' + PG + ')">'
      // 발가락 젤리 4개 — 통통한 둥근 타원. 바깥 2개는 조금 아래·벌어짐
      + '<ellipse cx="5.5" cy="10.2" rx="2.5" ry="2.9" transform="rotate(-18 5.5 10.2)"/>'
      + '<ellipse cx="9.7" cy="7.2" rx="2.6" ry="3.1" transform="rotate(-7 9.7 7.2)"/>'
      + '<ellipse cx="14.3" cy="7.2" rx="2.6" ry="3.1" transform="rotate(7 14.3 7.2)"/>'
      + '<ellipse cx="18.5" cy="10.2" rx="2.5" ry="2.9" transform="rotate(18 18.5 10.2)"/>'
      // 메인 젤리(掌球) — 둥글고 통통한 하트/콩팥형
      + '<path d="M12,12.4C9.2,12.4 6.1,13.7 6.1,16.5C6.1,18.5 8.0,19.7 9.6,19.7'
      + 'C11.0,19.7 11.4,18.9 12,18.9C12.6,18.9 13.0,19.7 14.4,19.7'
      + 'C16.0,19.7 17.9,18.5 17.9,16.5C17.9,13.7 14.8,12.4 12,12.4Z"/></g>'
      // 젤리 광택 — 각 젤리 윗부분에 반투명 흰 하이라이트 타원(촉촉한 반사)
      + '<g fill="rgba(255,255,255,0.42)">'
      + '<ellipse cx="5.2" cy="9.1" rx="1.1" ry="1.4" transform="rotate(-18 5.2 9.1)"/>'
      + '<ellipse cx="9.4" cy="5.9" rx="1.2" ry="1.5" transform="rotate(-7 9.4 5.9)"/>'
      + '<ellipse cx="14.0" cy="5.9" rx="1.2" ry="1.5" transform="rotate(7 14 5.9)"/>'
      + '<ellipse cx="18.2" cy="9.1" rx="1.1" ry="1.4" transform="rotate(18 18.2 9.1)"/>'
      + '<ellipse cx="10.6" cy="14.0" rx="2.6" ry="1.7"/></g></svg>';
    function pawSpawn(x, y, ang, mirror) {
      if (!pawLayer) {
        if (getComputedStyle(box).position === 'static') box.style.position = 'relative';
        pawLayer = h('div');
        css(pawLayer, { position: 'absolute', top: '0', left: '0', right: '0', bottom: '0',
          overflow: 'hidden', pointerEvents: 'none', zIndex: '20' }); // 문항 위 · idle 게이트(30) 아래
        box.appendChild(pawLayer);
      }
      var p = h('div', 'cc-paw');
      p.innerHTML = PAW_SVG;
      css(p, { position: 'absolute', left: x + 'px', top: y + 'px', pointerEvents: 'none',
        transform: 'translate(-50%,-50%) rotate(' + Math.round(ang) + 'deg)' + (mirror ? ' scaleX(-1)' : '') });
      pawLayer.appendChild(p);
      pawAlive.push(p);
      if (pawAlive.length > PAW_MAX) { var old = pawAlive.shift(); if (old.parentNode) old.parentNode.removeChild(old); }
      setTimeout(function () {
        var i = pawAlive.indexOf(p);
        if (i !== -1) pawAlive.splice(i, 1);
        if (p.parentNode) p.parentNode.removeChild(p);
      }, PAW_LIFE + 60);
    }
    function pawStep(e) {
      if (!pawsOn) return;
      var r = box.getBoundingClientRect();
      if (!r.width) return;
      var x = e.clientX - r.left, y = e.clientY - r.top;
      if (x < 0 || y < 0 || x > r.width || y > r.height) { pawLast = null; return; }
      if (!pawLast) { pawLast = [x, y]; return; }
      var dx = x - pawLast[0], dy = y - pawLast[1];
      var dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < PAW_STEP) return;
      if (dist > PAW_JUMP) { pawLast = [x, y]; return; }
      pawSide = 1 - pawSide;
      var off = pawSide ? 5 : -5; // 좌우 발 교대 — 진행 방향의 수직으로 어긋나게(걷는 자국)
      pawSpawn(x - dy / dist * off, y + dx / dist * off,
        Math.atan2(dy, dx) * 180 / Math.PI + 90, pawSide === 0);
      pawLast = [x, y];
    }
    box.addEventListener('pointermove', pawStep);

    // ── 방치 감지 — 학습시간은 '화면 보며 실제로 푸는 활성 시간'만 센다(승인 2026-07-13).
    // 탭 이탈(hidden)이면 즉시 타이머 정지, 화면 켠 채 오래 무입력이면 정지 + '아직 있니?'
    // 게이트로 재확인 후 이어간다. 방치 구간은 solve_time_ms에서 빠져 수학처럼 오래 푸는
    // 문항은 그대로 세되(활동 중이므로), 자리 비운 시간은 학습시간을 부풀리지 않는다.
    var IDLE_MS = 3 * 60 * 1000;  // 무입력 이 시간 지나면 방치로 보고 게이트
    var activeMs = 0, lastResume = 0, tPaused = true, idleTimer = null, idleGate = null;
    function elapsedActive() { return activeMs + (tPaused ? 0 : Math.max(0, Date.now() - lastResume)); }
    function timerStart() { activeMs = 0; lastResume = Date.now(); tPaused = false; armIdle(); }
    function timerPause() { if (!tPaused) { activeMs += Math.max(0, Date.now() - lastResume); tPaused = true; } }
    function timerResume() { if (tPaused) { lastResume = Date.now(); tPaused = false; } }
    function armIdle() { if (idleTimer) clearTimeout(idleTimer); idleTimer = (answered || grading) ? null : setTimeout(onIdle, IDLE_MS); }
    function onActivity() { if (!idleGate && !answered && !grading) armIdle(); }
    function onIdle() {
      if (answered || grading || idleGate) return;
      timerPause();  // 방치 구간 제외
      try { stopSounds(); } catch (e) {}
      showIdleGate();
    }
    function showIdleGate() {
      idleGate = h('div');
      css(idleGate, { position: 'absolute', top: '0', left: '0', right: '0', bottom: '0',
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
        gap: '12px', textAlign: 'center', padding: '20px', zIndex: '30',
        background: 'rgba(255,250,244,0.97)', borderRadius: '16px' });
      var t = h('div'); t.textContent = '🐱 냥? 잠깐 쉬고 있었어?';
      css(t, { fontWeight: '800', fontSize: '18px', color: '#3A3226' });
      var s = h('div'); s.textContent = '괜찮아! 다시 할 준비되면 아래 버튼을 콕 눌러줘 🐾';
      css(s, { fontSize: '13px', color: '#8A8070' });
      var b = h('button'); b.textContent = '좋아, 계속할래! ✏️'; css(b, btnStyle(C, '#fff'));
      b.onclick = hideIdleGate;
      idleGate.appendChild(t); idleGate.appendChild(s); idleGate.appendChild(b);
      if (getComputedStyle(box).position === 'static') box.style.position = 'relative';
      box.appendChild(idleGate);
    }
    function hideIdleGate() {
      if (idleGate) { try { idleGate.remove(); } catch (e) { if (idleGate.parentNode) idleGate.parentNode.removeChild(idleGate); } idleGate = null; }
      timerResume(); armIdle();
    }
    // document/window 리스너는 mount당 추가 — 위젯이 DOM에서 떨어지면(리마운트) 스테일
    // 핸들러가 죽은 타이머를 건드리지 않게 box.isConnected로 무력화한다(누수 완화).
    document.addEventListener('visibilitychange', function () {
      if (!box.isConnected) return;
      if (document.hidden) timerPause();
      else if (!idleGate) { timerResume(); armIdle(); }
    });
    window.addEventListener('blur', function () { if (box.isConnected) timerPause(); });
    window.addEventListener('focus', function () { if (box.isConnected && !idleGate && !document.hidden) { timerResume(); armIdle(); } });

    function fail(msg) { body.innerHTML = ''; var p = h('div'); p.textContent = msg || '문제를 불러오지 못했어요.'; css(p, { color: '#C25', fontSize: '13px' }); body.appendChild(p); refreshBtn(); if (footerOn) footerReset(); }
    function refreshBtn() {
      var b = h('button'); b.textContent = '다시 시도'; css(b, btnStyle('#eee', '#555'));
      b.onclick = load; body.appendChild(b);
    }
    function btnStyle(bg, col) {
      return { marginTop: '10px', width: '100%', border: 'none', borderRadius: T.radius.md,
        padding: '11px', minHeight: T.tap, fontWeight: '800', fontSize: '14px',
        fontFamily: 'inherit', cursor: 'pointer', background: bg, color: col };
    }

    function solved(verdict) {
      hidden.value = verdict;
      status.textContent = '✓ 확인됨'; status.style.color = OK;
      body.innerHTML = '';
      var ok = h('div');
      css(ok, { display: 'flex', alignItems: 'center', gap: '8px', padding: '16px', background: '#E1F5EC',
        borderRadius: '12px', color: OK, fontWeight: '800' });
      ok.textContent = '✅ 사람인 것이 확인됐어요!';
      body.appendChild(ok);
      box.dispatchEvent(new CustomEvent('catchap:success', { detail: { token: verdict }, bubbles: true }));
    }

    var lastOptions = [], lastType = '', answered = false;
    var hintSlot = null; // 공통 셸이 프롬프트·맥락 아래(보기 위)에 미리 놓는 힌트 자리 —
    // 각 렌더러의 hintLine(d.hint) 호출이 여기 채워져 '문제→힌트→보기' 순서로 읽힌다.

    // ── 연습장(scratchpad) — 계산·필기용 별도 캔버스(수학 등). 문제 조작과 완전 분리:
    // 캔버스 포인터 이벤트는 stopPropagation으로 box의 trace/발자국/제출과 섞이지 않는다.
    // 순수 부가 기능 — 안 열어도 문제풀이 정상. 획을 behavior.scratch로 전송(백엔드가 향후 저장·집계).
    var SCR_COLORS = [{ name: '검정', hex: '#2A2A2A' }, { name: '빨강', hex: '#E0475E' }, { name: '파랑', hex: '#2E7BFF' }];
    var SCR_WIDTHS = [{ name: '가는', w: 2 }, { name: '보통', w: 4 }, { name: '굵은', w: 7 }];
    var SCR_ERASE_R = 15; // 지우개 반경(px) — 이 안에 획 점이 있으면 그 획 제거
    var scr = {
      built: false, open: false, wrap: null, toggle: null, panel: null, canvas: null, ctx: null,
      strokes: [], cur: null, drawing: false, last: null, dpr: 1,
      color: '#2A2A2A', width: 4, mode: 'pen',
      firstAt: 0, lastAt: 0, drawnDist: 0, drawnCount: 0,
      colorBtns: [], widthBtns: [], eraserBtn: null,
    };

    // ── 풀 사이즈(교육형) 액션 풋터 — '다시 고르기 · 다음 문제 →'를 카드 우하단에 고정.
    //    보기 클릭은 '선택'만 하고, 다음 문제 버튼이 제출(채점)→한 번 더 누르면 다음 문제로.
    //    (버튼이 답 카드에서 멀어지지 않게 문제 영역 안에 둔다)
    var footer = null, redoBtn = null, nextBtn = null, dkBtn = null, footerOn = false;
    var pendingSubmit = null, pendingRedo = null;
    var curToken = null; // 현재 문항 토큰 — '잘 모르겠어요'가 render 밖(footer)에서 제출에 쓴다
    var onAnswered = null; // 렌더러별 답변 후 콜백 — 보기 근거(rationale) 공개 등
    function setBtnOn(b, on) {
      b.disabled = !on;
      css(b, { opacity: on ? '1' : '0.45', cursor: on ? 'pointer' : 'not-allowed' });
    }
    function ensureFooter() {
      if (footer) return;
      footer = h('div');
      css(footer, { display: 'flex', justifyContent: 'flex-end', gap: '10px', marginTop: '16px' });
      redoBtn = h('button'); redoBtn.textContent = '다시 고르기';
      css(redoBtn, { border: '2px solid ' + T.color.line, borderRadius: T.radius.md, padding: '11px 20px',
        minHeight: T.tap, fontWeight: '800', fontSize: '14px', background: T.color.card,
        color: T.color.inkFaint, fontFamily: 'inherit' });
      nextBtn = h('button'); nextBtn.textContent = '다음 문제 →';
      css(nextBtn, { border: 'none', borderRadius: T.radius.md, padding: '11px 24px',
        minHeight: T.tap, fontWeight: '800', fontSize: '14px', background: C, color: '#fff', fontFamily: 'inherit' });
      redoBtn.onclick = function () { if (!answered && !grading && pendingRedo) { pendingRedo(); redoCount += 1; } };
      // '잘 모르겠어요' — 찍기 강요 대신 정직하게 오답 처리(운 좋은 정답 방지). 서버가 오답으로
      // 채점하고 정답·해설을 내려주면 eduFeedback이 공부 자료로 보여준다.
      dkBtn = h('button'); dkBtn.textContent = '아직 잘 모르겠어요 🤔';
      css(dkBtn, { border: '2px solid ' + T.color.line, borderRadius: T.radius.md, padding: '11px 18px',
        minHeight: T.tap, fontWeight: '800', fontSize: T.font.sm, background: T.color.card,
        color: T.color.inkMute, fontFamily: 'inherit', marginRight: 'auto' });
      dkBtn.onclick = function () {
        if (answered || grading || !curToken) return;
        verify(curToken, null);
      };
      nextBtn.onclick = function () {
        if (grading) return; // 채점 응답 대기 중 더블클릭 → load() 유출로 위젯이 굳는 것 방지
        if (answered) {
          if (sessionDone) {
            // 세션 완료 — 진행은 소비자(게임 화면)가 결정 (결과 화면 이동 등)
            sfx('finish');
            box.dispatchEvent(new CustomEvent('catchap:finished', { bubbles: true }));
            return;
          }
          load();
        } else if (pendingSubmit) pendingSubmit();
      };
      footer.appendChild(dkBtn); footer.appendChild(redoBtn); footer.appendChild(nextBtn);
      box.insertBefore(footer, hidden);
    }
    function footerReset() {
      pendingSubmit = null; pendingRedo = null;
      if (footer) { setBtnOn(redoBtn, false); setBtnOn(nextBtn, false); setBtnOn(dkBtn, false); }
    }
    function footerState(canRedo, canNext) {
      if (footer) { setBtnOn(redoBtn, canRedo && !answered); setBtnOn(nextBtn, canNext); }
    }

    function eduFeedback(res) {
      var fb = h('div');
      var okAns = res.success;
      sfx(okAns ? 'correct' : 'wrong');
      var msg;
      if (lastType === 'drag_drop') {
        msg = okAns ? '정확히 쏙 넣었어요! 🎯' : '조금 빗나갔어요. 다시 한 번 해볼까요?';
      } else if (lastType === 'trace_path') {
        msg = okAns ? '선을 참 잘 따라 그렸어요! ✍️' : '점선을 따라 천천히 다시 그려볼까요?';
      } else if (lastType === 'dictation' || lastType === 'type_in' || lastType === 'input') {
        // 입력형 — 서버가 내려준 정답을 보여준다(input은 정답 목록 → 첫 번째)
        var ansStr = typeof res.answer === 'string' ? res.answer
          : (Array.isArray(res.answer) && res.answer.length ? String(res.answer[0]) : '');
        msg = okAns
          ? '정답이에요! 참 잘했어요 🎉'
          : (ansStr ? '아쉬워요! 정답은 "' + ansStr + '"' : '아쉬워요! 다시 한 번 생각해봐요.');
      } else if (lastType === 'crossword') {
        msg = okAns ? '십자말을 완성했어요! 🎉' : '아쉬워요! 낱말을 다시 살펴볼까요?';
      } else if (lastType === 'drag_pick') {
        // 정답 카드 라벨 매핑 (res.answer = {item, zone})
        var okItem = res.answer && res.answer.item;
        var okOpt = null;
        for (var di = 0; di < lastOptions.length; di++) { if (lastOptions[di].id === okItem) okOpt = lastOptions[di]; }
        msg = okAns
          ? '정확히 쏙 넣었어요! 🎯'
          : (okOpt ? '아쉬워요! 정답은 "' + okOpt.text + '"' : '조금 빗나갔어요. 다시 한 번 해볼까요?');
      } else {
        // res.answer: 정답 id(단일) 또는 id 배열(multi) — 서버가 채점 후에만 내려준다(오답 시 없음)
        var ansIds = res.answer === undefined || res.answer === null ? [] : [].concat(res.answer);
        var texts = [];
        for (var i = 0; i < lastOptions.length; i++) {
          if (ansIds.indexOf(lastOptions[i].id) !== -1) texts.push(lastOptions[i].text || lastOptions[i].emoji || '');
        }
        var ansText = texts.join(', ');
        msg = okAns
          ? '정답이에요! 참 잘했어요 🎉'
          : (ansText ? '아쉬워요! 정답은 "' + ansText + '"' : '아쉬워요! 다시 한 번 생각해봐요.');
      }
      css(fb, { display: 'flex', alignItems: 'center', gap: '8px', marginTop: '12px', padding: '13px 15px',
        borderRadius: T.radius.md, fontWeight: '800', fontSize: '14px',
        background: okAns ? T.color.okSoft : T.color.errSoft, color: okAns ? OK : T.color.err });
      fb.textContent = msg;
      body.appendChild(fb);
      // 오답·'잘 모르겠어요' 시 해설을 보여준다(공부 자료) — 서버가 explain(없으면 hint)을 내려준다
      if (!okAns && res.explain) {
        var exp = h('div');
        css(exp, { marginTop: '10px', padding: '12px 14px', borderRadius: '12px', fontSize: '13px',
          lineHeight: '1.6', background: '#FFF8EE', border: '1px solid #F3E4CC', color: '#6B5E48' });
        exp.textContent = '🐾 이렇게 풀어요! ' + res.explain;
        body.appendChild(exp);
      }
      if (dkBtn) setBtnOn(dkBtn, false); // 답한 뒤엔 '잘 모르겠어요' 비활성
      if (footerOn) { // 풋터의 다음 문제 버튼이 진행 담당
        footerState(false, true);
        nextBtn.textContent = sessionDone ? '결과 보기 →' : '다음 문제 →';
        return;
      }
      var next = h('button');
      next.textContent = sessionDone ? '결과 보기 →' : '다음 문제 →';
      css(next, btnStyle(C, '#fff'));
      next.onclick = sessionDone
        ? function () { sfx('finish'); box.dispatchEvent(new CustomEvent('catchap:finished', { bubbles: true })); }
        : load;
      body.appendChild(next);
    }

    // 힌트(💡 지시/도움말) — 공통 셸이 미리 놓은 hintSlot(프롬프트·맥락 아래, 보기 위)에
    // 채운다. slot이 없으면(방어) body에 붙인다. 위계상 프롬프트보다 작고 부드러운 보조 텍스트.
    function hintLine(text) {
      if (!text) return;
      var hint = h('div'); hint.textContent = '💡 ' + text;
      css(hint, { fontSize: T.font.sm, lineHeight: '1.5', color: T.color.inkSoft, fontWeight: '600',
        textAlign: footerOn ? 'center' : 'left', maxWidth: '480px',
        margin: footerOn ? '0 auto 16px' : '0 0 12px' });
      if (hintSlot) { hintSlot.textContent = ''; hintSlot.appendChild(hint); }
      else body.appendChild(hint);
    }

    // ── 끌어다 놓기 (drag_drop) — 아이템을 목표에 드래그, 드롭 좌표를 서버가 채점
    function renderDrag(d, token) {
      var area = h('div');
      css(area, { position: 'relative', width: '100%', height: '260px', background: T.color.cream,
        border: '2px dashed ' + T.color.line, borderRadius: '14px', overflow: 'hidden', touchAction: 'none' });
      var ring = h('div');
      css(ring, { position: 'absolute', width: '90px', height: '90px', border: '3px dashed ' + T.color.brandLine,
        borderRadius: '50%', transform: 'translate(-50%,-50%)', pointerEvents: 'none',
        left: (d.zone.cx * 100) + '%', top: (d.zone.cy * 100) + '%' });
      var target = h('div'); target.textContent = d.target;
      css(target, { position: 'absolute', fontSize: '44px', transform: 'translate(-50%,-50%)', pointerEvents: 'none',
        left: (d.zone.cx * 100) + '%', top: (d.zone.cy * 100) + '%' });
      var item = h('div'); item.textContent = d.item;
      css(item, { position: 'absolute', fontSize: '40px', transform: 'translate(-50%,-50%)',
        left: (d.start.x * 100) + '%', top: (d.start.y * 100) + '%',
        cursor: 'grab', userSelect: 'none', touchAction: 'none' });
      area.appendChild(ring); area.appendChild(target); area.appendChild(item);
      body.appendChild(area);
      hintLine(d.hint);
      var dropAt = null; // 풋터 모드: 놓은 위치를 기억해 두고 다음 문제 버튼이 제출
      function itemHome() { item.style.left = (d.start.x * 100) + '%'; item.style.top = (d.start.y * 100) + '%'; }
      makeDnd().dragFree(item, {
        area: area,
        disabled: function () { return answered; },
        onMove: function (p) { item.style.left = (p.x * 100) + '%'; item.style.top = (p.y * 100) + '%'; },
        onCancel: itemHome, // 제스처 취소(스크롤 등) — 제출 없이 원위치
        onDrop: function (p) {
          if (answered) return;
          var ans = { x: Math.round(p.x * 1000) / 1000, y: Math.round(p.y * 1000) / 1000 };
          if (footerOn) { dropAt = ans; footerState(true, true); }
          else verify(token, ans);
        },
      });
      if (footerOn) {
        pendingRedo = function () {
          dropAt = null;
          item.style.left = (d.start.x * 100) + '%'; item.style.top = (d.start.y * 100) + '%';
          footerState(false, false);
        };
        pendingSubmit = function () { if (dropAt) verify(token, dropAt); };
      }
    }

    // ── 따라 그리기 (trace_path) — 점선 글자/도형 위에 손으로 긋기, 궤적을 서버가 채점
    function renderTrace(d, token) {
      var NS = 'http://www.w3.org/2000/svg';
      function pl(points, color, width, dash) {
        var el = document.createElementNS(NS, 'polyline');
        el.setAttribute('points', points.map(function (p) { return (p[0] * 100) + ',' + (p[1] * 100); }).join(' '));
        el.setAttribute('fill', 'none'); el.setAttribute('stroke', color); el.setAttribute('stroke-width', width);
        el.setAttribute('stroke-linecap', 'round'); el.setAttribute('stroke-linejoin', 'round');
        if (dash) el.setAttribute('stroke-dasharray', dash);
        return el;
      }
      var svg = document.createElementNS(NS, 'svg');
      svg.setAttribute('viewBox', '0 0 100 100');
      svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
      css(svg, { width: '100%', height: '260px', display: 'block', background: T.color.cream,
        border: '2px dashed ' + T.color.line, borderRadius: '14px', touchAction: 'none', cursor: 'crosshair' });
      // 원본 가이드 5단계(guideStyle): dotted(점선)→faint(흐린 실선)→arrow(점선+시작점·방향)→
      // partial(앞부분만 점선)→blank(가이드 없음, 위에 예시 글자만).
      var gs = d.guideStyle || 'dotted';
      var GUIDE = '#C9B7A2'; // 가이드 선색(웜 그레이) — 사용자 코랄 획과 대비되게 중립 유지
      if (gs === 'blank' && d.showExample && d.glyph) {
        var ex = h('div'); ex.textContent = d.glyph;
        css(ex, { textAlign: 'center', fontSize: '46px', fontWeight: '800', color: T.color.ink,
          border: '2px solid ' + T.color.line, borderRadius: T.radius.md, width: '72px', margin: '0 auto 10px', background: T.color.card });
        body.appendChild(ex);
      }
      // 둥근 점 대시(dot) — 기존 '1.5 5.5' 대시보다 또렷하고 아이 친화적
      if (gs === 'dotted') svg.appendChild(pl(d.path, GUIDE, '4.5', '0.1 6.5'));
      else if (gs === 'faint') { var f = pl(d.path, '#DBCFC0', '5.5'); f.setAttribute('opacity', '0.6'); svg.appendChild(f); }
      else if (gs === 'arrow') {
        svg.appendChild(pl(d.path, GUIDE, '4.5', '0.1 6.5'));
      } else if (gs === 'partial') {
        svg.appendChild(pl(d.path.slice(0, Math.max(2, Math.ceil(d.path.length / 2))), GUIDE, '4.5', '0.1 6.5'));
      } // blank: 가이드 없음
      // 시작점 마커 — blank 외 전 가이드에 '여기서 출발' 표시(초록 점 + 펄스 링 + 방향 화살촉).
      // 순수 가이드(표시용)라 d.path·채점 좌표 불변.
      if (gs !== 'blank' && d.path && d.path.length) {
        var sp0 = d.path[0];
        var pulse = document.createElementNS(NS, 'circle');
        pulse.setAttribute('cx', sp0[0] * 100); pulse.setAttribute('cy', sp0[1] * 100);
        pulse.setAttribute('r', '5'); pulse.setAttribute('fill', 'none');
        pulse.setAttribute('stroke', OK); pulse.setAttribute('stroke-width', '1.4'); pulse.setAttribute('opacity', '0.55');
        var reduceTr = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (!reduceTr) {
          var an = document.createElementNS(NS, 'animate');
          an.setAttribute('attributeName', 'r'); an.setAttribute('values', '3.5;7;3.5');
          an.setAttribute('dur', '1.4s'); an.setAttribute('repeatCount', 'indefinite');
          pulse.appendChild(an);
          var an2 = document.createElementNS(NS, 'animate');
          an2.setAttribute('attributeName', 'opacity'); an2.setAttribute('values', '0.7;0.1;0.7');
          an2.setAttribute('dur', '1.4s'); an2.setAttribute('repeatCount', 'indefinite');
          pulse.appendChild(an2);
        }
        svg.appendChild(pulse);
        var sdot = document.createElementNS(NS, 'circle');
        sdot.setAttribute('cx', sp0[0] * 100); sdot.setAttribute('cy', sp0[1] * 100);
        sdot.setAttribute('r', '3.4'); sdot.setAttribute('fill', OK);
        sdot.setAttribute('stroke', '#fff'); sdot.setAttribute('stroke-width', '1');
        svg.appendChild(sdot);
        if (d.path.length > 1) { // 시작 방향 화살촉 — 어느 쪽으로 그을지
          var p0 = d.path[0], p1 = d.path[1];
          var ang = Math.atan2(p1[1] - p0[1], p1[0] - p0[0]);
          var ax = p0[0] * 100 + Math.cos(ang) * 9, ay = p0[1] * 100 + Math.sin(ang) * 9;
          var arw2 = document.createElementNS(NS, 'path');
          var w1 = ang + 2.6, w2 = ang - 2.6;
          arw2.setAttribute('d', 'M' + ax + ',' + ay + ' L' + (ax + Math.cos(w1) * 4.5) + ',' + (ay + Math.sin(w1) * 4.5)
            + ' M' + ax + ',' + ay + ' L' + (ax + Math.cos(w2) * 4.5) + ',' + (ay + Math.sin(w2) * 4.5));
          arw2.setAttribute('stroke', OK); arw2.setAttribute('stroke-width', '1.8'); arw2.setAttribute('fill', 'none');
          arw2.setAttribute('stroke-linecap', 'round'); arw2.setAttribute('stroke-linejoin', 'round');
          svg.appendChild(arw2);
        }
      }
      // 사용자 획 — 원본처럼 여러 획 지원: 획 사이를 잇는 선이 그려지지 않게 path(M...)로 렌더
      var user = document.createElementNS(NS, 'path');
      user.setAttribute('fill', 'none'); user.setAttribute('stroke', C); user.setAttribute('stroke-width', '3.5');
      user.setAttribute('stroke-linecap', 'round'); user.setAttribute('stroke-linejoin', 'round');
      svg.appendChild(user);
      body.appendChild(svg);

      var redo = null, submit = null;
      if (!footerOn) { // 풋터 모드에선 다시 그리기/제출을 공용 풋터 버튼이 담당
        var row = h('div'); css(row, { display: 'flex', gap: '8px' });
        redo = h('button'); redo.textContent = '다시 그리기'; css(redo, btnStyle('#eee', '#555'));
        submit = h('button'); submit.textContent = '다 그렸어요!'; css(submit, btnStyle(C, '#fff'));
        submit.disabled = true; submit.style.opacity = '0.5';
        row.appendChild(redo); row.appendChild(submit);
        body.appendChild(row);
      }
      hintLine(d.hint);

      var drawing = false, strokes = [];
      function allPts() { return strokes.reduce(function (a, s) { return a.concat(s); }, []); }
      function norm(e) { return svgNorm(svg, e); } // viewBox 좌표 — 가이드·서버 채점과 동일 좌표계
      function draw() {
        user.setAttribute('d', strokes.map(function (s) {
          return s.length ? 'M' + s.map(function (p) { return (p[0] * 100) + ',' + (p[1] * 100); }).join(' L') : '';
        }).join(' '));
        var n = allPts().length, enough = n >= 8;
        if (footerOn) { footerState(n > 0, enough); return; }
        submit.disabled = !enough; submit.style.opacity = enough ? '1' : '0.5';
      }
      svg.addEventListener('pointerdown', function (e) {
        if (answered) return;
        drawing = true; svg.setPointerCapture(e.pointerId);
        strokes.push([norm(e)]); draw(); e.preventDefault(); // 원본처럼 획을 여러 번 나눠 그릴 수 있다
      });
      svg.addEventListener('pointermove', function (e) {
        if (!drawing || answered || allPts().length >= 600) return;
        var s = strokes[strokes.length - 1];
        var p = norm(e), last = s[s.length - 1];
        if (last && Math.abs(p[0] - last[0]) < 0.005 && Math.abs(p[1] - last[1]) < 0.005) return;
        s.push(p); draw();
      });
      svg.addEventListener('pointerup', function () { drawing = false; });
      function doRedo() { if (answered) return; strokes = []; draw(); }
      function doSubmit() {
        var pts = allPts();
        if (answered || pts.length < 8) return;
        verify(token, pts.map(function (p) { return [Math.round(p[0] * 1e4) / 1e4, Math.round(p[1] * 1e4) / 1e4]; }));
      }
      if (footerOn) { pendingRedo = doRedo; pendingSubmit = doSubmit; }
      else { redo.onclick = function () { if (answered) return; doRedo(); redoCount += 1; }; submit.onclick = doSubmit; }
    }

    function renderRoute(d, token) {
      var NS = 'http://www.w3.org/2000/svg';
      function mk(tag, attrs) { var e = document.createElementNS(NS, tag); for (var k in attrs) e.setAttribute(k, attrs[k]); return e; }
      var svg = mk('svg', { viewBox: '0 0 100 100', preserveAspectRatio: 'xMidYMid meet' });
      css(svg, { width: '100%', maxWidth: '440px', margin: '0 auto', height: '300px', display: 'block',
        background: '#F2F8F1', border: '2px solid #DCEBD8', borderRadius: '14px', touchAction: 'none', cursor: 'crosshair' });
      // 위험존(빨강) → 도착(초록) → 시작(캐릭터)
      function zone(z, fill, stroke, dashed) {
        var rc = mk('rect', { x: z.x * 100, y: z.y * 100, width: z.w * 100, height: z.h * 100, rx: 3, fill: fill, stroke: stroke, 'stroke-width': 1.4 });
        if (dashed) rc.setAttribute('stroke-dasharray', '3 2');
        svg.appendChild(rc);
        if (z.emoji) { var t = mk('text', { x: (z.x + z.w / 2) * 100, y: (z.y + z.h / 2) * 100 + 3, 'text-anchor': 'middle', 'font-size': '10' }); t.textContent = z.emoji; svg.appendChild(t); }
        if (z.label) { var l = mk('text', { x: (z.x + z.w / 2) * 100, y: (z.y + z.h) * 100 + 4.5, 'text-anchor': 'middle', 'font-size': '4', 'font-weight': '700', fill: '#6B7B66' }); l.textContent = z.label; svg.appendChild(l); }
      }
      // 위험존 — 대비 강화(빗금 테두리로 '지나가면 안 됨' 신호)
      (d.dangers || []).forEach(function (z) { zone(z, 'rgba(226,87,76,0.18)', '#E2574C', true); });
      // 도착존 — 초록 + 펄스 링으로 목표 강조
      if (d.dest) {
        zone(d.dest, 'rgba(23,176,140,0.18)', OK);
        var dcx = (d.dest.x + d.dest.w / 2) * 100, dcy = (d.dest.y + d.dest.h / 2) * 100;
        var dr = mk('circle', { cx: dcx, cy: dcy, r: 3, fill: 'none', stroke: OK, 'stroke-width': '1.4', opacity: '0.7' });
        if (!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches)) {
          var da = mk('animate', { attributeName: 'r', values: '3;9;3', dur: '1.6s', repeatCount: 'indefinite' });
          var da2 = mk('animate', { attributeName: 'opacity', values: '0.7;0.05;0.7', dur: '1.6s', repeatCount: 'indefinite' });
          dr.appendChild(da); dr.appendChild(da2);
        }
        svg.appendChild(dr);
      }
      var user = mk('polyline', { fill: 'none', stroke: C, 'stroke-width': '3.5', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' });
      svg.appendChild(user);
      // 출발 표시 — 캐릭터 뒤에 펄스 링(여기서 잡아 끌어). 위치는 시작점 고정(원점 안내).
      var scx = d.start.x * 100, scy = d.start.y * 100;
      var sring = mk('circle', { cx: scx, cy: scy, r: 6, fill: 'none', stroke: C, 'stroke-width': '1.6', opacity: '0.6' });
      if (!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches)) {
        sring.appendChild(mk('animate', { attributeName: 'r', values: '5;9;5', dur: '1.5s', repeatCount: 'indefinite' }));
        sring.appendChild(mk('animate', { attributeName: 'opacity', values: '0.7;0.1;0.7', dur: '1.5s', repeatCount: 'indefinite' }));
      }
      svg.appendChild(sring);
      // 원본 방식: 캐릭터 토큰을 '끌어서' 이동 — 경로는 캐릭터가 지나간 궤적으로 기록된다.
      var st = mk('text', { x: d.start.x * 100, y: d.start.y * 100 + 3, 'text-anchor': 'middle', 'font-size': '11', cursor: 'grab' });
      st.textContent = d.character || '🧒'; svg.appendChild(st);
      body.appendChild(svg);
      if (!footerOn) { var row = h('div'); css(row, { display: 'flex', gap: '8px' });
        var redo = h('button'); redo.textContent = '다시 하기'; css(redo, btnStyle('#eee', '#555'));
        var submit = h('button'); submit.textContent = '도착했어요!'; css(submit, btnStyle(C, '#fff')); submit.disabled = true; submit.style.opacity = '0.5';
        row.appendChild(redo); row.appendChild(submit); body.appendChild(row);
        var refs = { redo: redo, submit: submit };
      }
      if (d.hint) hintLine(d.hint);
      var drawing = false, pts = [];
      function normp(e) { return svgNorm(svg, e); } // viewBox 좌표 — 캐릭터/존과 동일 좌표계(레터박스 보정)
      function moveChar(p) { st.setAttribute('x', p[0] * 100); st.setAttribute('y', p[1] * 100 + 3); }
      function draw() {
        user.setAttribute('points', pts.map(function (p) { return (p[0] * 100) + ',' + (p[1] * 100); }).join(' '));
        var enough = pts.length >= 8;
        if (footerOn) { footerState(pts.length > 0, enough); return; }
        refs.submit.disabled = !enough; refs.submit.style.opacity = enough ? '1' : '0.5';
      }
      function nearChar(p) {
        var cx = parseFloat(st.getAttribute('x')) / 100, cy = (parseFloat(st.getAttribute('y')) - 3) / 100;
        return Math.abs(p[0] - cx) < 0.12 && Math.abs(p[1] - cy) < 0.12;
      }
      svg.addEventListener('pointerdown', function (e) {
        if (answered) return;
        var p = normp(e);
        if (!nearChar(p)) return; // 캐릭터를 잡아야 출발(원본과 동일 — 빈 곳 드래그 무효)
        drawing = true; svg.setPointerCapture(e.pointerId);
        if (!pts.length) pts = [[d.start.x, d.start.y]]; // 경로는 항상 시작점부터
        draw(); e.preventDefault();
      });
      svg.addEventListener('pointermove', function (e) {
        if (!drawing || answered || pts.length >= 600) return;
        var p = normp(e), last = pts[pts.length - 1];
        if (last && Math.abs(p[0] - last[0]) < 0.005 && Math.abs(p[1] - last[1]) < 0.005) return;
        pts.push(p); moveChar(p); draw();
      });
      svg.addEventListener('pointerup', function () { drawing = false; });
      function doRedo() { if (answered) return; pts = []; moveChar([d.start.x, d.start.y]); draw(); }
      function doSubmit() { if (answered || pts.length < 8) return; verify(token, pts.map(function (p) { return [Math.round(p[0] * 1e4) / 1e4, Math.round(p[1] * 1e4) / 1e4]; })); }
      if (footerOn) { pendingRedo = doRedo; pendingSubmit = doSubmit; }
      else { refs.redo.onclick = function () { if (answered) return; doRedo(); redoCount += 1; }; refs.submit.onclick = doSubmit; }
    }

    // ── 카드 드래그(drag_pick) — 원본(과학·수학): 카드 여러 장 중 알맞은 것을 타겟에 끌어놓기.
    //    제출 {item: 카드id, x, y} → 서버가 아이템 일치 + 드롭 존 거리로 채점.
    function renderDragPick(d, token) {
      var area = h('div');
      css(area, { position: 'relative', width: '100%', height: '300px', background: T.color.cream,
        border: '2px dashed ' + T.color.line, borderRadius: '14px', overflow: 'hidden', touchAction: 'none' });
      var ring = h('div');
      // 링 크기 = 서버 채점 반경(정규화 0.14)과 동일한 타원 — 보이는 링 안 = 정답 존
      css(ring, { position: 'absolute', width: (d.zone.r * 2 * 100) + '%', height: (d.zone.r * 2 * 100) + '%',
        border: '3px dashed ' + T.color.brandLine, borderRadius: '50%', transform: 'translate(-50%,-50%)',
        pointerEvents: 'none', left: (d.zone.cx * 100) + '%', top: (d.zone.cy * 100) + '%' });
      var tgt = h('div'); tgt.textContent = (d.target && d.target.e) || '🎯';
      css(tgt, { position: 'absolute', fontSize: '44px', transform: 'translate(-50%,-50%)', pointerEvents: 'none',
        left: (d.zone.cx * 100) + '%', top: (d.zone.cy * 100) + '%' });
      area.appendChild(ring); area.appendChild(tgt);
      if (d.target && d.target.label) {
        var tl = h('div'); tl.textContent = d.target.label;
        css(tl, { position: 'absolute', fontSize: '11px', fontWeight: '700', color: '#B7A68F',
          transform: 'translate(-50%,0)', left: (d.zone.cx * 100) + '%',
          top: 'calc(' + (d.zone.cy * 100) + '% + 50px)', pointerEvents: 'none' });
        area.appendChild(tl);
      }
      var dropAt = null, els = {}, pickDnd = makeDnd(); // 공용 드래그 엔진(dragFree) 1회 생성
      // 카드들을 아래쪽에 가로로 배치 — 각자 드래그 가능
      var n = d.items.length;
      d.items.forEach(function (it, i) {
        var card = h('div');
        var em = h('span'); em.textContent = it.e || '';
        css(em, { display: 'block', fontSize: '34px', lineHeight: '1' });
        card.appendChild(em);
        if (it.label) {
          var lb = h('span'); lb.textContent = it.label;
          css(lb, { display: 'block', fontSize: '11px', fontWeight: '700', color: '#6B6157', marginTop: '2px' });
          card.appendChild(lb);
        }
        var sx = (i + 1) / (n + 1), sy = 0.82;
        css(card, { position: 'absolute', textAlign: 'center', transform: 'translate(-50%,-50%)',
          left: (sx * 100) + '%', top: (sy * 100) + '%', cursor: 'grab', userSelect: 'none', touchAction: 'none',
          padding: '8px 10px', minWidth: '44px', minHeight: '44px', boxSizing: 'border-box',
          background: T.color.card, border: '2px solid ' + T.color.line, borderRadius: T.radius.md,
          boxShadow: '0 3px 8px -4px rgba(120,90,70,.5)' });
        function cardHome() { card.style.left = (sx * 100) + '%'; card.style.top = (sy * 100) + '%'; }
        pickDnd.dragFree(card, {
          area: area,
          disabled: function () { return answered; },
          onStart: function () { card.style.zIndex = '5'; },
          onMove: function (p) { card.style.left = (p.x * 100) + '%'; card.style.top = (p.y * 100) + '%'; },
          onCancel: function () { card.style.zIndex = '1'; cardHome(); }, // 취소 = 무판정 원위치
          onDrop: function (p) {
            card.style.zIndex = '1';
            if (answered) return;
            // 원본과 동일: 존 밖 릴리즈 = 무판정, 카드 원위치(손 미끄러짐이 오답이 되지 않게)
            var dx = p.x - d.zone.cx, dy = p.y - d.zone.cy;
            if (Math.sqrt(dx * dx + dy * dy) > d.zone.r) { cardHome(); return; }
            dropAt = { item: it.id, x: Math.round(p.x * 1000) / 1000, y: Math.round(p.y * 1000) / 1000 };
            if (footerOn) footerState(true, true);
            else verify(token, dropAt);
          },
        });
        els[it.id] = { el: card, sx: sx, sy: sy };
        area.appendChild(card);
      });
      body.appendChild(area);
      if (d.hint) hintLine(d.hint);
      if (footerOn) {
        pendingRedo = function () {
          dropAt = null;
          d.items.forEach(function (it) { var s = els[it.id];
            s.el.style.left = (s.sx * 100) + '%'; s.el.style.top = (s.sy * 100) + '%'; });
          footerState(false, false);
        };
        pendingSubmit = function () { if (dropAt) verify(token, dropAt); };
      }
    }

    // ── 공용 드래그&드롭 — 원본 위젯들의 주 상호작용(칩/카드/조각을 상자·슬롯으로 끌어다
    //    놓기)을 복원한다. 원본도 "드래그가 어려우면 탭으로도 담긴다"는 탭 폴백을 내장했으므로
    //    여기서도 유지한다: 거의 안 움직이고 뗀 경우(<6px)는 onTap을, 존 안에서 뗀 경우는
    //    onDrop(zoneId)을 부른다. 존 밖 드롭은 무판정(원본과 동일 — 카드가 제자리로).
    function makeDnd() {
      var zones = []; // {el, id, hi}
      function addZone(el, id, hi) { zones.push({ el: el, id: id, hi: hi || null }); }
      function zoneAt(x, y) {
        // 겹칠 경우 나중에 등록된(=더 안쪽) 존이 이기도록 역순 탐색
        for (var i = zones.length - 1; i >= 0; i--) {
          var r = zones[i].el.getBoundingClientRect();
          if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return zones[i];
        }
        return null;
      }
      function drag(handle, opts) {
        // opts: { onDrop:function(zoneId){}, onTap:function(){}, disabled:function(){} }
        var ghost = null, active = false, sx = 0, sy = 0, ox = 0, oy = 0, moved = false, hiZone = null;
        var prevOpacity = '';
        handle.style.touchAction = 'none'; handle.style.cursor = 'grab'; handle.style.userSelect = 'none';
        function clearHi() { if (hiZone && hiZone.hi) hiZone.hi(false); hiZone = null; }
        handle.addEventListener('pointerdown', function (e) {
          if (opts.disabled && opts.disabled()) return;
          active = true; moved = false; sx = e.clientX; sy = e.clientY;
          try { handle.setPointerCapture(e.pointerId); } catch (er) {}
          // 드래그 도중 핸들이 DOM에서 제거되면(문항 전환 등) 핸들로는 pointerup이 안 와서
          // 고스트가 화면에 남는다 — window 레벨에서도 종료를 받아 정리한다.
          window.addEventListener('pointerup', end);
          window.addEventListener('pointercancel', end);
          e.preventDefault();
        });
        handle.addEventListener('pointermove', function (e) {
          if (!active) return;
          var dx = e.clientX - sx, dy = e.clientY - sy;
          if (!moved && Math.abs(dx) < 6 && Math.abs(dy) < 6) return;
          if (!moved) {
            moved = true;
            var r = handle.getBoundingClientRect(); ox = r.left; oy = r.top;
            ghost = handle.cloneNode(true);
            css(ghost, { position: 'fixed', left: r.left + 'px', top: r.top + 'px',
              width: r.width + 'px', height: r.height + 'px', margin: '0', zIndex: '99999',
              pointerEvents: 'none', opacity: '0.92', boxShadow: '0 10px 24px rgba(80,50,30,.35)',
              transform: 'scale(1.06)', boxSizing: 'border-box' });
            document.body.appendChild(ghost);
            prevOpacity = handle.style.opacity; // 원래 값 보존(퍼즐 사용조각 0.25 등)
            handle.style.opacity = '0.3';
          }
          ghost.style.left = (ox + dx) + 'px'; ghost.style.top = (oy + dy) + 'px';
          var z = zoneAt(e.clientX, e.clientY);
          if (z !== hiZone) { clearHi(); hiZone = z; if (z && z.hi) z.hi(true); }
        });
        function end(e) {
          if (!active) return; active = false;
          window.removeEventListener('pointerup', end);
          window.removeEventListener('pointercancel', end);
          if (moved) handle.style.opacity = prevOpacity;
          if (ghost && ghost.parentNode) ghost.parentNode.removeChild(ghost);
          ghost = null;
          var z = moved ? zoneAt(e.clientX, e.clientY) : null;
          clearHi();
          if (e && e.type === 'pointercancel') return; // 취소된 제스처는 탭/드롭 아님(오제출 방지)
          if (!moved) { if (opts.onTap) opts.onTap(); return; } // 탭 폴백
          if (z && opts.onDrop) opts.onDrop(z.id);               // 존 밖 = 무판정
        }
        handle.addEventListener('pointerup', end);
        handle.addEventListener('pointercancel', end);
      }
      // 자유 드래그 — 존 판정 없이 '놓은 좌표'가 답인 유형(drag_drop·drag_pick)용 공용 엔진.
      // forest 캡차의 검증된 포인터 패턴(drag-rotate.js)과 동일: pointerdown/move/up +
      // setPointerCapture(마우스·터치·펜 통합, HTML5 DnD 금지), pointercancel은 제출이 아니라
      // 원위치 복원(onCancel) — 스크롤 제스처에 뺏긴 드래그가 오답이 되지 않게 한다.
      // 좌표는 opts.area 기준 0~1 정규화(클램프) — 기존 렌더러·서버 채점과 동일 계약.
      function dragFree(handle, opts) {
        // opts: { area, onStart(), onMove(p), onDrop(p), onCancel(), disabled:function(){} }
        var active = false;
        handle.style.touchAction = 'none'; handle.style.cursor = 'grab'; handle.style.userSelect = 'none';
        function norm(e) {
          var r = opts.area.getBoundingClientRect();
          return { x: Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)),
                   y: Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)) };
        }
        handle.addEventListener('pointerdown', function (e) {
          if (opts.disabled && opts.disabled()) return;
          active = true;
          try { handle.setPointerCapture(e.pointerId); } catch (er) {}
          handle.style.cursor = 'grabbing';
          if (opts.onStart) opts.onStart();
          e.preventDefault();
        });
        handle.addEventListener('pointermove', function (e) {
          if (!active) return;
          if (opts.onMove) opts.onMove(norm(e));
        });
        function end(e) {
          if (!active) return; active = false;
          handle.style.cursor = 'grab';
          if (e.type === 'pointercancel') { if (opts.onCancel) opts.onCancel(); return; }
          if (opts.onDrop) opts.onDrop(norm(e));
        }
        handle.addEventListener('pointerup', end);
        handle.addEventListener('pointercancel', end);
      }
      return { addZone: addZone, drag: drag, dragFree: dragFree };
    }

    // ── 참조 지도(사회 방위 sort) — 원본 renderRefMap 이식: 기준 건물을 강조하고 북 나침반을
    //    보여줘, "학교를 기준으로 각 건물의 방향" 같은 문제를 화면 정보만으로 풀 수 있게 한다.
    function renderRefMap(ref) {
      var map = h('div');
      css(map, { position: 'relative', width: '100%', maxWidth: '340px', margin: '0 auto 14px',
        aspectRatio: '1 / 0.95', background: '#F3EEE4', border: '2px solid #E3D6C6', borderRadius: '12px', overflow: 'hidden' });
      if (ref.compass !== false) {
        var cp = h('div'); cp.textContent = '북 ↑';
        css(cp, { position: 'absolute', top: '3px', left: '50%', transform: 'translateX(-50%)',
          fontSize: '11px', fontWeight: '800', color: '#B7A68F', zIndex: '2' });
        map.appendChild(cp);
      }
      (ref.zones || []).forEach(function (z) {
        var base = z.id === ref.highlight;
        var zn = h('div');
        css(zn, { position: 'absolute', left: z.x + '%', top: z.y + '%', width: z.w + '%', height: z.h + '%',
          border: '2px solid ' + (base ? C : '#D9CBB8'), background: base ? '#FFF0EE' : '#fff',
          borderRadius: '8px', display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: '10.5px', fontWeight: base ? '800' : '700', color: base ? C : '#6B6157',
          textAlign: 'center', boxSizing: 'border-box', padding: '1px' });
        zn.textContent = z.label + (base ? ' (기준)' : '');
        map.appendChild(zn);
      });
      body.appendChild(map);
    }

    // ── 공용 오디오 버튼 — 사운드 웨이브 이퀄라이저(재생중 출렁) + 재생중/완료 라벨.
    //    반환: { el, playing(on), setLabel(t) }. reduced-motion이면 막대를 정지 상태로 둔다.
    function audioBtn(label, opts) {
      opts = opts || {};
      var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      var el = h('button');
      css(el, { display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '10px',
        margin: opts.compact ? '0 auto 12px' : '0 auto 16px',
        padding: opts.compact ? '11px 22px' : '14px 30px', minHeight: T.tap,
        fontSize: opts.compact ? T.font.md : T.font.lg, fontWeight: '800',
        border: 'none', borderRadius: T.radius.pill, background: C, color: '#fff',
        cursor: 'pointer', fontFamily: 'inherit', boxShadow: T.shadow.pop });
      var eq = h('span'); css(eq, { display: 'inline-flex', alignItems: 'center', gap: '3px', height: '20px' });
      var bars = [];
      [0, 1, 2, 3].forEach(function (i) {
        var bar = h('span');
        css(bar, { display: 'inline-block', width: '4px', height: '20px', borderRadius: '2px',
          background: '#fff', transformOrigin: 'center', transform: 'scaleY(0.32)' });
        bar.style.animation = 'ccWave 0.85s ease-in-out infinite';
        bar.style.animationDelay = (i * 0.13) + 's';
        bar.style.animationPlayState = 'paused';
        bars.push(bar); eq.appendChild(bar);
      });
      var lab = h('span'); lab.textContent = label;
      el.appendChild(eq); el.appendChild(lab);
      function playing(on) {
        bars.forEach(function (b) {
          b.style.animationPlayState = (on && !reduce) ? 'running' : 'paused';
          if (!on) b.style.transform = 'scaleY(0.32)';
          else if (reduce) b.style.transform = 'scaleY(0.7)'; // 모션 최소화 시 정적 표시
        });
      }
      return { el: el, playing: playing, setLabel: function (t) { lab.textContent = t; } };
    }

    // ── 입력형(dictation/type_in) — 원본(국어): 받아쓰기는 TTS로 듣고, 높임말은 밑줄
    //    낱말을 보고 타이핑. 제출 문자열 → 서버 trim 정확 일치.
    function renderTyping(d, token) {
      if (d.type === 'dictation') {
        var canSpeak = typeof window.speechSynthesis !== 'undefined';
        var played = false;
        var ab = audioBtn('듣기');
        if (!canSpeak) { ab.el.style.background = '#D8CBBB'; ab.el.style.cursor = 'not-allowed'; ab.el.style.boxShadow = 'none'; }
        var sp = ab.el;
        sp.onclick = function () {
          if (!canSpeak) return;
          try {
            window.speechSynthesis.cancel();
            var u = new SpeechSynthesisUtterance(d.tts);
            u.lang = 'ko-KR'; u.rate = 0.9;
            u.onstart = function () { ab.playing(true); ab.setLabel('듣는 중…'); };
            u.onend = function () { ab.playing(false); ab.setLabel('다시 듣기'); };
            u.onerror = function () { ab.playing(false); ab.setLabel('다시 듣기'); };
            window.speechSynthesis.speak(u);
            played = true; ab.setLabel('듣는 중…');
          } catch (e) {}
        };
        body.appendChild(sp);
        if (!canSpeak) {
          var warn = h('div'); warn.textContent = '이 브라우저에서는 음성 듣기를 지원하지 않아요.';
          css(warn, { textAlign: 'center', fontSize: '12px', color: '#C25', marginBottom: '10px' });
          body.appendChild(warn);
        }
      } else if (d.type === 'type_in') {
        // type_in(높임말): 원문 문장 + 밑줄 강조 낱말 (textContent — 뱅크 문자열 그대로)
        var sent = h('div');
        sent.appendChild(document.createTextNode(d.before || ''));
        var hi = h('span'); hi.textContent = d.highlight || '';
        css(hi, { color: T.color.err, borderBottom: '3px solid ' + T.color.err, fontWeight: '800' });
        sent.appendChild(hi);
        sent.appendChild(document.createTextNode(d.after || ''));
        css(sent, { fontSize: '17px', fontWeight: '700', color: T.color.ink, lineHeight: '1.8',
          background: T.color.card, border: '2px solid ' + T.color.line, borderRadius: '14px',
          padding: '14px 18px', maxWidth: '440px', margin: '0 auto 16px' });
        body.appendChild(sent);
      }
      // d.type === 'input'(수학 직접입력): 프롬프트/도형은 render() 공통부가 이미 표시 — 입력창만.
      var input = h('input');
      input.type = 'text';
      input.autocomplete = 'off';
      input.placeholder = d.type === 'dictation' ? '들은 문장을 그대로 입력해요'
        : d.type === 'input' ? '답을 입력해요' : '알맞은 표현을 입력해요';
      css(input, { display: 'block', width: '100%', maxWidth: '420px', margin: '0 auto', boxSizing: 'border-box',
        fontFamily: 'inherit', fontSize: '16px', fontWeight: '600', padding: '14px 16px', minHeight: T.tap,
        borderRadius: T.radius.md, border: '2px solid ' + T.color.line, color: T.color.ink, outline: 'none' });
      // 포커스 시 코랄 테두리(입력 위치 강조)
      input.addEventListener('focus', function () { input.style.borderColor = C; });
      input.addEventListener('blur', function () { input.style.borderColor = T.color.line; });
      input.addEventListener('input', function () {
        if (footerOn) { var v = input.value.trim(); footerState(v.length > 0, v.length > 0); }
      });
      input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && input.value.trim()) {
          if (footerOn) { if (pendingSubmit) pendingSubmit(); }
          else verify(token, input.value);
        }
      });
      body.appendChild(input);
      if (d.hint) hintLine(d.hint);
      function subT() { var v = input.value; if (v.trim()) verify(token, v); }
      if (footerOn) {
        pendingRedo = function () { input.value = ''; footerState(false, false); };
        pendingSubmit = subT;
      } else {
        var tb = h('button'); tb.textContent = '확인'; css(tb, btnStyle(C, '#fff'));
        tb.onclick = subT; body.appendChild(tb);
      }
    }

    // ── 문장부호(punct) — 원본(국어): 어절 사이 자리(동그라미)를 모두 탭. 제출 [gap...]
    function renderPunct(d, token) {
      var picked = {};
      var gapEls = {};
      var line = h('div');
      css(line, { display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'center',
        gap: '6px', maxWidth: '480px', margin: '0 auto 8px' });
      (d.tokens || []).forEach(function (w, i) {
        var word = h('span'); word.textContent = w;
        css(word, { fontSize: '19px', fontWeight: '700', color: T.color.ink });
        line.appendChild(word);
        if ((d.gaps || []).indexOf(i) !== -1) {
          var g = h('button'); g.textContent = '_';
          // 44px 원형 — 어절 사이 탭 타깃(WCAG 2.5.5, 기존 28px는 아동 손가락에 작음)
          css(g, { width: '44px', height: '44px', margin: '0 2px', borderRadius: '50%',
            border: '2px solid ' + T.color.line, background: T.color.card, color: T.color.inkFaint,
            fontSize: '18px', fontWeight: '800', cursor: 'pointer', lineHeight: '1', padding: '0',
            flexShrink: '0', touchAction: 'manipulation', transition: 'border-color .12s, background .12s' });
          g.onclick = function () {
            if (answered) return;
            picked[i] = !picked[i];
            g.textContent = picked[i] ? '✓' : '_';
            g.style.borderColor = picked[i] ? C : T.color.line;
            g.style.background = picked[i] ? T.color.brandSoft : T.color.card;
            g.style.color = picked[i] ? C : T.color.inkFaint;
            if (footerOn) {
              var pn = Object.keys(picked).filter(function (k) { return picked[k]; }).length;
              footerState(pn > 0, pn > 0);
            }
          };
          gapEls[i] = g;
          line.appendChild(g);
        }
      });
      body.appendChild(line);
      if (d.hint) hintLine(d.hint);
      function subG() {
        var ans = Object.keys(picked).filter(function (k) { return picked[k]; });
        if (ans.length) verify(token, ans);
      }
      if (footerOn) {
        pendingRedo = function () {
          picked = {};
          Object.keys(gapEls).forEach(function (k) { var g = gapEls[k];
            g.textContent = '_'; g.style.borderColor = T.color.line; g.style.background = T.color.card; g.style.color = T.color.inkFaint; });
          footerState(false, false);
        };
        pendingSubmit = subG;
      } else {
        var gb = h('button'); gb.textContent = '확인'; css(gb, btnStyle(C, '#fff'));
        gb.onclick = subG; body.appendChild(gb);
      }
    }

    // ── 십자말(crossword) — 원본(국어): 격자 셀 탭으로 낱말 선택 → 음절 타일로 채우기.
    //    낱말 정답은 서버에만 있어 즉시 판정 대신 전부 채우면 제출({w0:"낱말",...} match 채점).
    function renderCrossword(d, token) {
      // 셀 46px — 아동 터치타깃(44px 이상). 큰 격자(size)는 화면 폭에 맞춰 축소해 넘침 방지.
      var CELL = 46;
      if (d.size && d.size * (CELL + 4) > 360) CELL = Math.max(38, Math.floor(360 / d.size) - 4);
      var wordCells = [], cellOwner = {}, startNo = {};
      (d.words || []).forEach(function (w, wi) {
        var cells = [];
        for (var k = 0; k < w.len; k++) {
          var r = w.dir === 'down' ? w.row + k : w.row;
          var c = w.dir === 'across' ? w.col + k : w.col;
          var key = r + ',' + c;
          cells.push(key);
          (cellOwner[key] = cellOwner[key] || []).push(wi);
        }
        wordCells.push(cells);
        startNo[w.row + ',' + w.col] = w.no;
      });
      var filled = {}, lockedCells = {}, active = null;
      if (d.reveal) { Object.keys(d.reveal).forEach(function (k) { filled[k] = d.reveal[k]; lockedCells[k] = true; }); }
      var bank = (d.tiles || []).map(function (t, i) { return { id: i, letter: t }; });
      var cellEls = {}, clue = null, tray = null;

      var grid = h('div');
      css(grid, { display: 'grid',
        gridTemplateColumns: 'repeat(' + d.size + ',' + CELL + 'px)',
        gridTemplateRows: 'repeat(' + d.size + ',' + CELL + 'px)',
        gap: '4px', justifyContent: 'center', margin: '0 auto 14px' });
      for (var gi = 0; gi < d.size * d.size; gi++) {
        (function (gi) {
          var r = Math.floor(gi / d.size), c = gi % d.size, key = r + ',' + c;
          if (!cellOwner[key]) { var sp = h('div'); css(sp, { width: CELL + 'px', height: CELL + 'px' }); grid.appendChild(sp); return; }
          var cell = h('button');
          css(cell, { position: 'relative', width: CELL + 'px', height: CELL + 'px', fontFamily: 'inherit',
            fontSize: '19px', fontWeight: '800', borderRadius: T.radius.sm, border: '2px solid ' + T.color.line,
            background: T.color.card, color: T.color.ink, cursor: 'pointer', padding: '0',
            touchAction: 'manipulation', transition: 'border-color .12s, background .12s' });
          if (startNo[key] !== undefined) {
            var no = h('span'); no.textContent = startNo[key];
            css(no, { position: 'absolute', top: '2px', left: '4px', fontSize: '10px', fontWeight: '700', color: T.color.inkMute });
            cell.appendChild(no);
          }
          var ch = h('span'); ch.textContent = filled[key] || '';
          css(ch, { display: 'block' });
          cell.appendChild(ch);
          cell.onclick = function () {
            if (answered) return;
            var owners = cellOwner[key] || [];
            if (!owners.length) return;
            active = owners[0];
            paint();
          };
          cellEls[key] = { el: cell, ch: ch };
          grid.appendChild(cell);
        })(gi);
      }
      body.appendChild(grid);

      clue = h('div');
      css(clue, { maxWidth: '380px', margin: '0 auto 12px', textAlign: 'left', background: '#F5ECF1',
        border: '2px solid #A65B8C', borderRadius: '14px', padding: '10px 14px', display: 'none' });
      body.appendChild(clue);

      tray = h('div');
      css(tray, { display: 'flex', flexWrap: 'wrap', gap: '8px', justifyContent: 'center', maxWidth: '400px', margin: '0 auto 12px' });
      body.appendChild(tray);

      var clearBtn = h('button'); clearBtn.textContent = '지우기';
      css(clearBtn, { display: 'block', margin: '0 auto', padding: '8px 20px', borderRadius: '20px',
        border: '2px solid #F0E4D8', background: '#fff', color: '#8A8070', fontWeight: '800', fontSize: '13px', cursor: 'pointer' });
      clearBtn.onclick = function () {
        if (answered || active == null) return;
        wordCells[active].forEach(function (key) {
          if (lockedCells[key] || !(key in filled)) return;
          // 다른 낱말이 완성 상태로 쓰는 셀이라도 서버 채점 전이라 완성 여부를 모름 → 전부 반환
          bank.push({ id: bank.length ? bank[bank.length - 1].id + 1 : 0, letter: filled[key] });
          delete filled[key];
        });
        paint();
      };
      body.appendChild(clearBtn);
      if (d.hint) hintLine(d.hint);

      function assembled() {
        var out = {};
        for (var wi = 0; wi < wordCells.length; wi++) {
          var s = '';
          for (var k = 0; k < wordCells[wi].length; k++) {
            var key = wordCells[wi][k];
            if (!(key in filled)) return null; // 미완성
            s += filled[key];
          }
          out['w' + wi] = s;
        }
        return out;
      }
      function paint() {
        Object.keys(cellEls).forEach(function (key) {
          var e = cellEls[key];
          e.ch.textContent = filled[key] || '';
          var isActive = active != null && (cellOwner[key] || []).indexOf(active) !== -1;
          e.el.style.borderColor = lockedCells[key] ? '#8E7CC3' : isActive ? '#A65B8C' : '#F0E4D8';
          e.el.style.background = lockedCells[key] ? '#EDE9F7' : isActive ? '#F5ECF1' : '#fff';
        });
        if (active != null && d.words[active]) {
          var w = d.words[active];
          clue.style.display = 'block';
          clue.textContent = '';
          var cl1 = h('div'); cl1.textContent = w.no + '번 ' + (w.dir === 'across' ? '가로' : '세로');
          css(cl1, { fontSize: '12px', fontWeight: '800', color: '#A65B8C', marginBottom: '3px' });
          clue.appendChild(cl1);
          if (w.cho) {
            var cl2 = h('div'); cl2.textContent = w.cho;
            css(cl2, { fontSize: '14px', fontWeight: '800', color: '#3A3226', letterSpacing: '2px', marginBottom: '3px' });
            clue.appendChild(cl2);
          }
          var cl3 = h('div'); cl3.textContent = w.hint;
          css(cl3, { fontSize: '13.5px', fontWeight: '700', color: '#3A3226' });
          clue.appendChild(cl3);
        } else { clue.style.display = 'none'; }
        tray.innerHTML = '';
        bank.forEach(function (t) {
          var tb = h('button'); tb.textContent = t.letter;
          css(tb, { fontFamily: 'inherit', fontSize: '18px', fontWeight: '800', padding: '0',
            minWidth: '44px', minHeight: '44px', borderRadius: T.radius.md, border: '2px solid ' + T.color.line,
            background: T.color.card, color: T.color.ink, cursor: 'pointer', touchAction: 'manipulation' });
          tb.onclick = function () {
            if (answered || active == null) return;
            var next = null;
            for (var k = 0; k < wordCells[active].length; k++) {
              if (!(wordCells[active][k] in filled)) { next = wordCells[active][k]; break; }
            }
            if (next == null) return; // 이 낱말은 다 참
            filled[next] = t.letter;
            bank = bank.filter(function (x) { return x.id !== t.id; });
            paint();
          };
          tray.appendChild(tb);
        });
        var done = assembled();
        if (footerOn) footerState(Object.keys(filled).length > Object.keys(lockedCells).length, !!done);
      }
      // 첫 낱말 자동 선택
      if (d.words && d.words.length) active = 0;
      paint();
      function subCw() { var a = assembled(); if (a) verify(token, a); }
      if (footerOn) {
        pendingRedo = function () {
          Object.keys(filled).forEach(function (key) {
            if (lockedCells[key]) return;
            bank.push({ id: bank.length ? bank[bank.length - 1].id + 1 : 0, letter: filled[key] });
            delete filled[key];
          });
          paint();
        };
        pendingSubmit = subCw;
      } else {
        var cwb = h('button'); cwb.textContent = '확인'; css(cwb, btnStyle(C, '#fff'));
        cwb.onclick = subCw; body.appendChild(cwb);
      }
    }

    // ── 스와이프(swipe) — 원본(국어 사실·의견): 카드를 좌(의견)/우(사실)로 넘겨 분류.
    //    버튼 탭도 지원. 제출 '사실'|'의견' → 서버 등호 채점.
    function renderSwipe(d, token) {
      var chosen = null;
      var wrap = h('div'); css(wrap, { position: 'relative', maxWidth: '420px', margin: '0 auto 14px', touchAction: 'pan-y' });
      var lab = h('div');
      var labL = h('span'); labL.textContent = '← ' + (d.leftLabel || '의견');
      css(labL, { color: '#A65B8C', fontWeight: '800' });
      var labR = h('span'); labR.textContent = (d.rightLabel || '사실') + ' →';
      css(labR, { float: 'right', color: '#3E7CA6', fontWeight: '800' });
      lab.appendChild(labL); lab.appendChild(labR);
      css(lab, { fontSize: '13px', marginBottom: '8px' });
      wrap.appendChild(lab);
      var card = h('div'); card.textContent = d.card;
      css(card, { background: T.color.card, border: '2px solid ' + T.color.line, borderRadius: '18px', padding: '26px 20px',
        fontSize: '16.5px', fontWeight: '700', color: T.color.ink, lineHeight: '1.7', textAlign: 'center',
        cursor: 'grab', userSelect: 'none', touchAction: 'none', transition: 'transform 0.15s',
        boxShadow: T.shadow.pop });
      wrap.appendChild(card);
      body.appendChild(wrap);
      var row = h('div'); css(row, { display: 'flex', gap: '10px', justifyContent: 'center', maxWidth: '420px', margin: '0 auto' });
      function mkBtn(label, color, bg) {
        var b = h('button'); b.textContent = label;
        css(b, { flex: '1', maxWidth: '160px', padding: '12px', borderRadius: '13px', border: '2px solid ' + color,
          background: bg, color: color, fontWeight: '800', fontSize: '15px', cursor: 'pointer' });
        return b;
      }
      var leftBtn = mkBtn(d.leftLabel || '의견', '#A65B8C', '#F5ECF1');
      var rightBtn = mkBtn(d.rightLabel || '사실', '#3E7CA6', '#E8F1F7');
      row.appendChild(leftBtn); row.appendChild(rightBtn);
      body.appendChild(row);
      if (d.hint) hintLine(d.hint);
      function choose(side) {
        if (answered) return;
        chosen = side;
        var isL = side === (d.leftLabel || '의견');
        card.style.transform = 'translateX(' + (isL ? -46 : 46) + 'px) rotate(' + (isL ? -4 : 4) + 'deg)';
        card.style.borderColor = isL ? '#A65B8C' : '#3E7CA6';
        leftBtn.style.opacity = isL ? '1' : '0.45';
        rightBtn.style.opacity = isL ? '0.45' : '1';
        if (footerOn) footerState(true, true);
        else verify(token, chosen);
      }
      leftBtn.onclick = function () { choose(d.leftLabel || '의견'); };
      rightBtn.onclick = function () { choose(d.rightLabel || '사실'); };
      // 스와이프 제스처
      var startX = null, dragging = false;
      card.addEventListener('pointerdown', function (e) {
        if (answered) return;
        dragging = true; startX = e.clientX; card.setPointerCapture(e.pointerId);
        card.style.transition = 'none'; e.preventDefault();
      });
      card.addEventListener('pointermove', function (e) {
        if (!dragging) return;
        var dx = e.clientX - startX;
        card.style.transform = 'translateX(' + dx + 'px) rotate(' + (dx / 18) + 'deg)';
      });
      card.addEventListener('pointerup', function (e) {
        if (!dragging) return;
        dragging = false; card.style.transition = 'transform 0.15s';
        var dx = e.clientX - startX;
        if (dx > 90) choose(d.rightLabel || '사실');
        else if (dx < -90) choose(d.leftLabel || '의견');
        else card.style.transform = 'none';
      });
      if (footerOn) {
        pendingRedo = function () {
          chosen = null; card.style.transform = 'none'; card.style.borderColor = T.color.line;
          leftBtn.style.opacity = '1'; rightBtn.style.opacity = '1';
          footerState(false, false);
        };
        pendingSubmit = function () { if (chosen != null) verify(token, chosen); };
      }
    }

    // ── 장면 클릭(position) — 원본(과학·수학): 장면 SVG의 부위(data-region)를 탭.
    //    제출 regionId → 서버 등호 채점. scene_svg는 서버 뱅크의 신뢰된 마크업.
    function renderPosition(d, token) {
      var sel = null;
      var holder = h('div');
      css(holder, { maxWidth: '440px', margin: '0 auto 10px', textAlign: 'center' });
      holder.innerHTML = d.scene_svg || '';
      var svgEl = holder.querySelector('svg');
      if (svgEl) { svgEl.style.maxWidth = '100%'; svgEl.style.height = 'auto'; }
      var regions = holder.querySelectorAll('[data-region]');
      Array.prototype.forEach.call(regions, function (g) {
        g.style.cursor = 'pointer';
        g.addEventListener('click', function () {
          if (answered) return;
          sel = g.getAttribute('data-region');
          Array.prototype.forEach.call(regions, function (g2) {
            g2.style.opacity = g2 === g ? '1' : '0.55';
            g2.style.outline = 'none';
          });
          g.style.opacity = '1';
          if (footerOn) footerState(true, true);
          else verify(token, sel);
        });
      });
      body.appendChild(holder);
      if (d.hint) hintLine(d.hint);
      if (footerOn) {
        pendingRedo = function () {
          sel = null;
          Array.prototype.forEach.call(regions, function (g2) { g2.style.opacity = '1'; });
          footerState(false, false);
        };
        pendingSubmit = function () { if (sel != null) verify(token, sel); };
      }
    }

    // ── 연속 듣기(영어 02 sequence 원본) — 오디오 여러 개를 순서대로 듣고, 들린 순서대로
    //    그림을 탭해 슬롯에 배치. [optionId,...] 순서 제출 → 서버 sequence 채점.
    function renderListenSeq(d, token) {
      var seq = [], need = d.slotCount || (d.audios || []).length;
      var auds = (d.audios || []).map(function (a) {
        var el = h('audio'); el.src = base + '/captcha/v1/audio/' + a; el.preload = 'auto';
        body.appendChild(el); return el;
      });
      var playing = false;
      var ab = audioBtn('순서대로 듣기', { compact: !footerOn });
      function playAll() {
        if (playing || !auds.length) return;
        playing = true; var i = 0;
        ab.playing(true); ab.setLabel('듣는 중…');
        function done() { playing = false; ab.playing(false); ab.setLabel('다시 듣기'); }
        function next() {
          if (i >= auds.length) { done(); return; }
          var a = auds[i++];
          try {
            a.currentTime = 0;
            a.onended = function () { setTimeout(next, 500); };
            a.onerror = function () { setTimeout(next, 200); }; // 로드 실패 시 다음으로(잠김 방지)
            var pr = a.play();
            // play()는 거부를 프로미스로 반환 — 자동재생 차단/실패 시 잠금 해제(버튼 재시도 가능)
            if (pr && pr.catch) pr.catch(function () { done(); });
          } catch (e) { done(); }
        }
        next();
      }
      var playBtn = ab.el;
      playBtn.onclick = playAll;
      body.appendChild(playBtn);
      setTimeout(playAll, 200);
      var slotWrap = h('div');
      css(slotWrap, { display: 'flex', flexWrap: 'wrap', gap: '8px', justifyContent: 'center', minHeight: '30px',
        padding: '12px', marginBottom: '12px', maxWidth: '440px', margin: '0 auto 12px',
        border: '2px dashed ' + T.color.lineSoft, borderRadius: '14px', background: T.color.cream, alignItems: 'center' });
      var tray = h('div'); css(tray, { display: 'flex', flexWrap: 'wrap', gap: '10px', justifyContent: 'center' });
      var byId = {}; (d.options || []).forEach(function (o) { byId[o.id] = o; });
      function paint() {
        slotWrap.textContent = '';
        if (!seq.length) {
          var ph = h('span'); ph.textContent = '들린 순서대로 그림을 눌러요';
          css(ph, { color: '#B7A68F', fontSize: '13px', fontWeight: '700' }); slotWrap.appendChild(ph);
        }
        seq.forEach(function (id, i) {
          var o = byId[id]; if (!o) return;
          var s = h('button');
          var badge = h('span'); badge.textContent = (i + 1);
          css(badge, { display: 'inline-block', minWidth: '18px', height: '18px', borderRadius: '9px', background: C,
            color: '#fff', fontSize: '11px', fontWeight: '800', lineHeight: '18px', textAlign: 'center', marginRight: '6px' });
          s.appendChild(badge); s.appendChild(document.createTextNode(o.emoji || o.text));
          css(s, { padding: '8px 12px', border: '2px solid ' + C, borderRadius: '12px', background: '#FFF0EE',
            cursor: 'pointer', fontSize: '24px' });
          s.onclick = function () { if (answered) return; seq.splice(i, 1); paint(); };
          slotWrap.appendChild(s);
        });
        tray.textContent = '';
        (d.options || []).forEach(function (o) {
          if (seq.indexOf(o.id) !== -1) return;
          var e = h('button'); e.textContent = o.emoji || o.text;
          if (d.showLabel && o.text) { e.textContent = ''; var em2 = h('div'); em2.textContent = o.emoji; css(em2, { fontSize: '30px' });
            var tx2 = h('div'); tx2.textContent = o.text; css(tx2, { fontSize: '12px', fontWeight: '700' }); e.appendChild(em2); e.appendChild(tx2); }
          css(e, { padding: '12px 16px', border: '2px solid #F0E4D8', borderRadius: '12px', background: '#fff',
            cursor: 'pointer', fontSize: '30px' });
          e.onclick = function () { if (answered || seq.length >= need) return; seq.push(o.id); paint(); };
          tray.appendChild(e);
        });
        if (footerOn) footerState(seq.length > 0, seq.length === need);
      }
      body.appendChild(slotWrap); body.appendChild(tray);
      paint();
      function sub() { if (seq.length === need) verify(token, seq.slice()); }
      if (footerOn) { pendingRedo = function () { seq = []; paint(); }; pendingSubmit = sub; }
      else { var b2 = h('button'); b2.textContent = '확인'; css(b2, btnStyle(C, '#fff')); b2.onclick = sub; body.appendChild(b2); }
      if (d.hint) hintLine(d.hint);
    }

    // ── 카드 뒤집기 기억 게임(영어 07 원본) — 미리보기 후 뒤집힌 카드 2장씩 열어 짝 확인.
    //    짝 판정은 서버 /pair(토큰 미소비 오라클, 원본 /match와 동일), 전부 맞추면
    //    {그림카드:단어카드} 매핑을 최종 제출한다. timeLimitMs가 있으면 제한시간 초과 시 실패.
    function renderMemory(d, token) {
      var cards = d.cards || [];
      var mySeq = renderSeq; // 스테일 /pair 응답이 다음 문항을 못 건드리게(문항 세대 고정)
      var mapping = {}, open = [], lock = true, done = false, timer = null, timeLeft = 0;
      var matched = {}; // cardId -> true
      var cols = cards.length <= 4 ? 2 : (cards.length <= 6 ? 3 : 4);
      var grid = h('div');
      css(grid, { display: 'grid', gridTemplateColumns: 'repeat(' + cols + ',minmax(64px,88px))', gap: '10px',
        justifyContent: 'center', margin: '0 auto 12px' });
      var info = h('div');
      css(info, { textAlign: 'center', fontSize: '13px', fontWeight: '800', color: '#8A8070', marginBottom: '10px' });
      body.appendChild(info); body.appendChild(grid);
      var els = {};
      var reduceM = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      function face(el, c, up, animate) {
        el.textContent = up ? c.face : '❓';
        el.style.background = up ? '#FFF9F1' : '#FFE9E2';
        el.style.borderColor = matched[c.id] ? OK : (up ? C : '#F0C9BC');
        el.style.fontSize = c.kind === 'image' ? '30px' : '15px';
        if (!up) el.style.fontSize = '24px';
        // 뒤집기 팝 애니메이션 — 열 때(up) 부드럽게 튕겨 나타남(reduced-motion 존중)
        if (animate && !reduceM) { el.style.animation = 'none'; void el.offsetWidth; el.style.animation = 'ccPop 0.28s ease-out'; }
      }
      cards.forEach(function (c) {
        var el = h('button');
        css(el, { aspectRatio: '3 / 4', border: '2px solid #F0C9BC', borderRadius: T.radius.md, cursor: 'pointer',
          fontWeight: '800', color: T.color.ink, padding: '2px', overflowWrap: 'anywhere',
          touchAction: 'manipulation', transition: 'border-color .15s, background .15s' });
        el.onclick = function () {
          if (answered || lock || done || matched[c.id]) return;
          if (open.some(function (o) { return o.id === c.id; })) return;
          face(el, c, true, true); open.push(c);
          if (open.length < 2) return;
          lock = true;
          var a = open[0], b = open[1];
          getAuth().then(function (auth) {
            return api(base, '/captcha/v1/pair', key, { challenge_token: token, a: a.id, b: b.id }, auth);
          }).then(function (r) {
            // 문항이 이미 넘어갔거나(타임아웃 제출 등) 끝났으면 늦은 응답 무시
            if (mySeq !== renderSeq || done || answered) return;
            var m = r && r.data && r.data.match;
            if (m) {
              matched[a.id] = matched[b.id] = true;
              mapping[r.data.left] = r.data.right;
              face(els[a.id], a, true); face(els[b.id], b, true);
              open = []; lock = false;
              var doneAll = Object.keys(matched).length === cards.length;
              if (doneAll) { done = true; if (timer) clearInterval(timer);
                if (footerOn) footerState(true, true); else verify(token, mapping); }
            } else {
              setTimeout(function () {
                face(els[a.id], a, false); face(els[b.id], b, false);
                open = []; lock = false;
              }, 700);
            }
          }).catch(function () { open = []; lock = false; });
        };
        els[c.id] = el; grid.appendChild(el);
      });
      // 미리보기: previewMs 동안 전부 공개 후 뒤집기 (원본 5초 외우기)
      cards.forEach(function (c) { face(els[c.id], c, true); });
      var pv = Math.max(1000, d.previewMs || 5000);
      info.textContent = '👀 위치를 외워요!';
      setTimeout(function () {
        if (answered) return;
        cards.forEach(function (c) { if (!matched[c.id]) face(els[c.id], c, false); });
        lock = false;
        if (d.timeLimitMs) {
          timeLeft = Math.round(d.timeLimitMs / 1000);
          info.textContent = '⏱️ ' + timeLeft + '초';
          timer = setInterval(function () {
            timeLeft -= 1;
            info.textContent = '⏱️ ' + timeLeft + '초';
            if (timeLeft <= 0) { clearInterval(timer); if (!done && !answered) { done = true; verify(token, mapping); } }
          }, 1000);
        } else {
          info.textContent = '카드 두 장을 열어 짝을 찾아요';
        }
      }, pv);
      function subM() { if (Object.keys(matched).length === cards.length) verify(token, mapping); }
      if (footerOn) {
        pendingRedo = function () { /* 진행 중 보드 초기화 */
          if (done) return;
          open = []; mapping = {}; matched = {};
          cards.forEach(function (c) { face(els[c.id], c, false); });
          lock = false; footerState(false, false);
        };
        pendingSubmit = subM;
      }
      if (d.hint) hintLine(d.hint);
    }

    // 문항 전환 시 소리 정지 — <audio>는 DOM에서 떼어내도 계속 재생되고 TTS도 이어진다.
    function stopSounds() {
      [].forEach.call(body.querySelectorAll('audio'), function (a) { try { a.pause(); a.onended = null; } catch (e) {} });
      try { if (window.speechSynthesis) window.speechSynthesis.cancel(); } catch (e) {}
    }

    // ── 연습장 구현 ─────────────────────────────────────────────────────
    function scrPointerXY(e) {
      var r = scr.canvas.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    }
    function scrPt(p) { return [Date.now() - traceStart, Math.round(p.x * 10) / 10, Math.round(p.y * 10) / 10]; }
    function scrPaper() {
      var ctx = scr.ctx, w = scr.canvas.clientWidth, h = scr.canvas.clientHeight;
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, w, h);
      ctx.strokeStyle = '#F0EAE0'; ctx.lineWidth = 1; // 연한 모눈(웜 톤)
      var g = 26, x, y;
      for (x = g; x < w; x += g) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke(); }
      for (y = g; y < h; y += g) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); }
    }
    function scrSeg(a, b, color, width) {
      var ctx = scr.ctx;
      ctx.strokeStyle = color; ctx.lineWidth = width; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    }
    function scrDot(p, color, width) {
      var ctx = scr.ctx; ctx.fillStyle = color;
      ctx.beginPath(); ctx.arc(p.x, p.y, Math.max(1, width / 2), 0, Math.PI * 2); ctx.fill();
    }
    function scrStrokePts(s) { return s.points.map(function (pt) { return { x: pt[1], y: pt[2] }; }); }
    function scrRedraw() {
      if (!scr.ctx) return;
      scrPaper();
      scr.strokes.forEach(function (s) {
        var pts = scrStrokePts(s);
        if (pts.length === 1) { scrDot(pts[0], s.color, s.width); return; }
        for (var i = 1; i < pts.length; i++) scrSeg(pts[i - 1], pts[i], s.color, s.width);
      });
    }
    function scrResize() {
      if (!scr.canvas || !scr.ctx) return;
      var rect = scr.canvas.getBoundingClientRect();
      if (!rect.width) return; // 패널이 닫혀 폭 0이면 건너뜀(열 때 다시 호출)
      scr.dpr = window.devicePixelRatio || 1;
      scr.canvas.width = Math.max(1, Math.round(rect.width * scr.dpr));
      scr.canvas.height = Math.max(1, Math.round(rect.height * scr.dpr));
      scr.ctx.setTransform(scr.dpr, 0, 0, scr.dpr, 0, 0);
      scrRedraw();
    }
    function scrEraseAt(p) {
      var before = scr.strokes.length;
      scr.strokes = scr.strokes.filter(function (s) {
        return !s.points.some(function (pt) {
          var dx = pt[1] - p.x, dy = pt[2] - p.y; return (dx * dx + dy * dy) <= (SCR_ERASE_R * SCR_ERASE_R);
        });
      });
      if (scr.strokes.length !== before) scrRedraw();
    }
    function scrFinish() {
      if (!scr.drawing) return;
      scr.drawing = false;
      if (scr.mode !== 'eraser' && scr.cur && scr.cur.points.length) scr.strokes.push(scr.cur);
      scr.cur = null;
    }
    function scrSync() { // 툴바 선택 상태 시각 반영
      scr.colorBtns.forEach(function (cb) {
        var on = scr.mode === 'pen' && scr.color === cb.hex;
        cb.el.style.outline = on ? '3px solid ' + cb.hex : 'none';
        cb.el.style.outlineOffset = '2px';
        cb.el.style.transform = on ? 'scale(1.08)' : 'scale(1)';
      });
      scr.widthBtns.forEach(function (wb) {
        var on = scr.width === wb.w;
        wb.el.style.borderColor = on ? C : T.color.line;
        wb.el.style.background = on ? T.color.brandSoft : T.color.card;
      });
      if (scr.eraserBtn) {
        var on = scr.mode === 'eraser';
        scr.eraserBtn.style.borderColor = on ? C : T.color.line;
        scr.eraserBtn.style.background = on ? T.color.brandSoft : T.color.card;
        scr.eraserBtn.style.color = on ? C : T.color.inkSoft;
      }
      scr.canvas.style.cursor = scr.mode === 'eraser' ? 'cell' : 'crosshair';
    }
    function scrClearAll() { scr.strokes = []; scr.cur = null; scrRedraw(); }
    function scrToggle() {
      scr.open = !scr.open;
      scr.panel.style.display = scr.open ? 'block' : 'none';
      scr.toggle.textContent = scr.open ? '✍️ 연습장 닫기' : '✍️ 연습장 열기';
      scr.toggle.style.borderColor = scr.open ? C : T.color.line;
      scr.toggle.style.color = scr.open ? C : T.color.inkSoft;
      if (scr.open) setTimeout(scrResize, 30); // 펼친 뒤 실제 폭으로 캔버스 재설정
    }
    function scrToolBtn(label) {
      var b = h('button'); b.textContent = label;
      css(b, { display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: '4px',
        minWidth: T.tap, minHeight: T.tap, padding: '0 12px', border: '2px solid ' + T.color.line,
        borderRadius: T.radius.sm, background: T.color.card, color: T.color.inkSoft, fontWeight: '800',
        fontSize: T.font.sm, fontFamily: 'inherit', cursor: 'pointer', touchAction: 'manipulation' });
      return b;
    }
    function ensureScratch() {
      if (scr.built) return;
      scr.built = true;
      scr.wrap = h('div'); css(scr.wrap, { margin: '14px 0 0' });
      scr.toggle = h('button');
      css(scr.toggle, { display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px',
        margin: '0 auto', padding: '11px 20px', minHeight: T.tap, border: '2px solid ' + T.color.line,
        borderRadius: T.radius.pill, background: T.color.card, color: T.color.inkSoft, fontWeight: '800',
        fontSize: T.font.sm, fontFamily: 'inherit', cursor: 'pointer', touchAction: 'manipulation' });
      scr.toggle.textContent = '✍️ 연습장 열기';
      scr.toggle.onclick = scrToggle;
      scr.wrap.appendChild(scr.toggle);

      scr.panel = h('div');
      css(scr.panel, { display: 'none', marginTop: '10px', border: '2px solid ' + T.color.line,
        borderRadius: T.radius.md, background: T.color.card, overflow: 'hidden', boxShadow: T.shadow.card });
      var bar = h('div');
      css(bar, { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '7px', padding: '9px 10px',
        borderBottom: '1px solid ' + T.color.line, background: '#FFFBF6' });
      // 펜 3색
      SCR_COLORS.forEach(function (c) {
        var b = h('button'); b.title = c.name; b.setAttribute('aria-label', c.name + ' 펜');
        css(b, { width: T.tap, height: T.tap, minWidth: T.tap, borderRadius: '50%', border: '2px solid #fff',
          background: c.hex, cursor: 'pointer', padding: '0', boxShadow: '0 1px 3px rgba(0,0,0,.25)',
          touchAction: 'manipulation' });
        b.onclick = function () { scr.mode = 'pen'; scr.color = c.hex; scrSync(); };
        scr.colorBtns.push({ el: b, hex: c.hex });
        bar.appendChild(b);
      });
      var sep = h('span'); css(sep, { width: '1px', height: '26px', background: T.color.line, margin: '0 2px' }); bar.appendChild(sep);
      // 굵기 3단계 — 점 크기로 표현
      SCR_WIDTHS.forEach(function (wd) {
        var b = h('button'); b.title = wd.name + ' 선'; b.setAttribute('aria-label', wd.name + ' 선');
        css(b, { minWidth: T.tap, minHeight: T.tap, border: '2px solid ' + T.color.line, borderRadius: T.radius.sm,
          background: T.color.card, cursor: 'pointer', padding: '0', display: 'inline-flex',
          alignItems: 'center', justifyContent: 'center', touchAction: 'manipulation' });
        var dot = h('span'); css(dot, { display: 'block', width: (wd.w + 4) + 'px', height: (wd.w + 4) + 'px',
          borderRadius: '50%', background: T.color.ink }); b.appendChild(dot);
        b.onclick = function () { scr.width = wd.w; if (scr.mode === 'eraser') scr.mode = 'pen'; scrSync(); };
        scr.widthBtns.push({ el: b, w: wd.w });
        bar.appendChild(b);
      });
      var sep2 = h('span'); css(sep2, { width: '1px', height: '26px', background: T.color.line, margin: '0 2px' }); bar.appendChild(sep2);
      // 지우개(획 지움) · 전체 지우기
      scr.eraserBtn = scrToolBtn('🧽 지우개');
      scr.eraserBtn.onclick = function () { scr.mode = scr.mode === 'eraser' ? 'pen' : 'eraser'; scrSync(); };
      bar.appendChild(scr.eraserBtn);
      var clr = scrToolBtn('🗑 전체 지우기');
      css(clr, { marginLeft: 'auto', color: T.color.inkFaint });
      clr.onclick = scrClearAll;
      bar.appendChild(clr);

      scr.canvas = h('canvas');
      css(scr.canvas, { display: 'block', width: '100%', height: '240px', minHeight: '200px',
        touchAction: 'none', cursor: 'crosshair', background: '#fff' });
      scr.ctx = scr.canvas.getContext('2d');
      // 캔버스 포인터 이벤트 — stopPropagation으로 box(trace/발자국/idle) 오염 방지. 활동은 수동 통지.
      scr.canvas.addEventListener('pointerdown', function (e) {
        e.preventDefault(); e.stopPropagation();
        try { scr.canvas.setPointerCapture(e.pointerId); } catch (er) {}
        onActivity();
        var p = scrPointerXY(e);
        scr.drawing = true; scr.last = p;
        if (scr.mode === 'eraser') { scrEraseAt(p); return; }
        scr.cur = { color: scr.color, width: scr.width, points: [scrPt(p)] };
        scr.drawnCount += 1;
        var now = Date.now(); if (!scr.firstAt) scr.firstAt = now; scr.lastAt = now;
        scrDot(p, scr.color, scr.width);
      });
      scr.canvas.addEventListener('pointermove', function (e) {
        e.stopPropagation(); // 캔버스 위 hover도 box로 안 번지게(발자국·trace가 캔버스에 안 찍힘)
        if (!scr.drawing) return;
        e.preventDefault();
        var p = scrPointerXY(e);
        if (scr.mode === 'eraser') { scrEraseAt(p); scr.last = p; return; }
        var dx = p.x - scr.last.x, dy = p.y - scr.last.y, d = Math.sqrt(dx * dx + dy * dy);
        if (d < 1.2) return;
        scrSeg(scr.last, p, scr.color, scr.width);
        scr.cur.points.push(scrPt(p)); scr.drawnDist += d; scr.last = p; scr.lastAt = Date.now();
      });
      ['pointerup', 'pointercancel', 'lostpointercapture'].forEach(function (n) {
        scr.canvas.addEventListener(n, function (e) { if (e && e.stopPropagation) e.stopPropagation(); scrFinish(); });
      });
      scr.panel.appendChild(bar); scr.panel.appendChild(scr.canvas);
      scr.wrap.appendChild(scr.panel);
      // 문제 본문(body) 아래, 진행 풋터 위에 배치 (풋터가 있으면 그 앞).
      box.insertBefore(scr.wrap, footer || hidden);
      scrSync();
      // 창 크기 변화 대응 — 리마운트 시 죽은 핸들러가 안 돌게 isConnected 가드.
      window.addEventListener('resize', function () { if (box.isConnected && scr.open) scrResize(); });
    }
    function scrReset() { // 새 문항 — 연습장 데이터 초기화(열림 상태·펜 선택은 유지)
      scr.strokes = []; scr.cur = null; scr.drawing = false;
      scr.firstAt = 0; scr.lastAt = 0; scr.drawnDist = 0; scr.drawnCount = 0;
      if (scr.ctx) scrRedraw();
    }
    // verify에 실을 연습장 획 데이터 — 안 썼으면 null(payload에 미포함).
    function scratchData() {
      if (!scr.built || (scr.drawnCount === 0 && scr.strokes.length === 0)) return null;
      return {
        strokes: scr.strokes.map(function (s) { return { color: s.color, width: s.width, points: s.points }; }),
        strokeCount: scr.drawnCount,          // 그은 획 수(누적 — 지운 것도 포함, 필기 노력 지표)
        distancePx: Math.round(scr.drawnDist), // 총 필기 거리(누적)
        firstWriteMs: scr.firstAt ? Math.max(0, scr.firstAt - traceStart) : 0, // 첫 필기까지(문항 표시 기준)
        drawMs: (scr.firstAt && scr.lastAt) ? Math.max(0, scr.lastAt - scr.firstAt) : 0, // 첫~마지막 필기 시간
      };
    }

    function render(d) {
      stopSounds();
      body.innerHTML = '';
      // retries는 리셋하지 않는다 — 캡차 오답 재발급을 건너 누적돼야 행동데이터
      // retry_count가 실제 재시도 횟수를 반영한다(통과 시 위젯 세션 종료로 자연 소멸).
      renderedAt = Date.now(); redoCount = 0; traceReset();
      if (idleGate) hideIdleGate();  // 새 문항 — 이전 게이트 정리
      lastType = d.type; answered = false; grading = false; renderSeq += 1;
      timerStart();  // 활성 시간(방치 제외) 계측 — answered 리셋 뒤라야 idle 타이머가 켜진다
      lastOptions = []; // 이전 문항 보기가 새 문항 피드백(정답 텍스트 매칭)에 누출되지 않게
      onAnswered = null;
      footerOn = full && product === 'edu';
      curToken = d.challenge_token; // '잘 모르겠어요'가 참조 (오늘의 퀴즈·전체학습 등 학습 세션)
      if (footerOn) {
        ensureFooter(); footerReset();
        redoBtn.textContent = d.type === 'trace_path' ? '다시 그리기'
          : (d.type === 'dictation' || d.type === 'type_in' || d.type === 'input') ? '다시 쓰기' : '다시 고르기';
        nextBtn.textContent = '다음 문제 →';
        setBtnOn(dkBtn, true); // 문항 푸는 동안 '잘 모르겠어요' 활성 (답하면 eduFeedback이 끈다)
      }
      if (footer) footer.style.display = footerOn ? 'flex' : 'none';
      // 연습장 — 앱(full) 문항에만 노출. 매 문항 데이터 초기화(열림·펜 선택은 유지).
      if (full) { ensureScratch(); scrReset(); scr.wrap.style.display = ''; if (scr.open) setTimeout(scrResize, 30); }
      else if (scr.wrap) scr.wrap.style.display = 'none';
      var token = d.challenge_token;
      var prompt = h('div');
      if (/「[^」]+」/.test(d.prompt || '')) {
        // 원본(국어 속담): 괄호 대신 표현 자체를 색+밑줄로 하이라이트
        String(d.prompt).split(/(「[^」]+」)/).forEach(function (sg) {
          if (!sg) return;
          if (sg.charAt(0) === '「') {
            var hs = h('span'); hs.textContent = sg.slice(1, -1);
            css(hs, { color: C, borderBottom: '3px solid #FFCDC4', fontWeight: '800' });
            prompt.appendChild(hs);
          } else prompt.appendChild(document.createTextNode(sg));
        });
      } else {
        prompt.textContent = d.prompt;
      }
      // 타이포 위계(3단계 정비): 프롬프트=L1(최상위). full 22px, 읽기 줄길이 위해 maxWidth 제한.
      css(prompt, footerOn
        ? { fontWeight: '800', fontSize: '22px', lineHeight: '1.4', color: T.color.ink,
            marginBottom: '18px', textAlign: 'center', maxWidth: '30ch', marginLeft: 'auto', marginRight: 'auto' }
        : { fontWeight: '800', fontSize: T.font.md, lineHeight: '1.5', color: T.color.ink, marginBottom: '12px' });
      body.appendChild(prompt);

      // 공통 셸 수직 리듬: 맥락 블록(figure/image+meaning/scenario)은 하단 16px로 통일.
      // figure(문제 위 도형 그림) — 서버 뱅크의 신뢰된 SVG 마크업. 모든 유형 공통.
      if (d.figure) {
        var fig = h('div');
        css(fig, { textAlign: 'center', margin: '0 auto 16px', maxWidth: '100%', overflowX: 'auto' });
        fig.innerHTML = d.figure;
        var fsvg = fig.querySelector('svg');
        if (fsvg) { fsvg.style.maxWidth = '100%'; fsvg.style.height = 'auto'; }
        body.appendChild(fig);
      }
      // 그림 힌트(이모지)+한국어 뜻 — 한 단위로 묶어 배치(그림은 뜻과 가깝게, 아래로 16px).
      var imgHintEl = null;
      if (d.image) {
        var gim = h('div'); gim.textContent = d.image;
        // L2 그림 힌트: 46px(기존 52는 프롬프트 대비 과함). 뜻이 있으면 바로 아래 붙게 tight.
        css(gim, { textAlign: 'center', fontSize: '46px', lineHeight: '1.1', margin: d.meaning ? '0 0 6px' : '0 0 16px' });
        body.appendChild(gim);
        imgHintEl = gim;
      }
      if (d.meaning) {
        // L3 해석(보조): 프롬프트보다 작고 부드러운 색. '뜻' 라벨은 더 옅게.
        var gmn = h('div');
        var mlab = h('span'); mlab.textContent = '뜻 '; css(mlab, { color: T.color.inkMute, fontWeight: '700' });
        gmn.appendChild(mlab); gmn.appendChild(document.createTextNode(d.meaning));
        css(gmn, { textAlign: 'center', fontSize: T.font.md, fontWeight: '700', color: T.color.inkSoft, margin: '0 0 16px' });
        body.appendChild(gmn);
      }
      // 상황 지문(생활 5단계 시나리오) — 문제 맥락 박스(프롬프트 바로 아래, 따뜻한 배경).
      if (d.scenario) {
        var scn = h('div'); scn.textContent = d.scenario;
        css(scn, { textAlign: 'center', fontSize: T.font.md, fontWeight: '700', color: T.color.ink, lineHeight: '1.6',
          background: '#FFF6EA', border: '1.5px solid #F0E0C8', borderRadius: T.radius.md,
          padding: '13px 18px', maxWidth: '460px', margin: '0 auto 16px' });
        body.appendChild(scn);
      }
      // 힌트 자리 — 프롬프트·맥락 아래, 보기/캔버스 위. 각 렌더러의 hintLine(d.hint)이 채운다.
      hintSlot = h('div'); body.appendChild(hintSlot);

      // 조작형 공용: 표준 보기 셀 버튼. img(지도기호·CPR 사진 등)가 있으면 그림+라벨로.
      function imgUrl(rel) { return base + '/captcha/v1/img/' + String(rel).replace(/^assets\//, ''); }
      function optCell(text, img) {
        var b = h('button');
        if (img) {
          var im = h('img'); im.src = imgUrl(img); im.alt = '';
          css(im, { display: 'block', width: '56px', height: '56px', objectFit: 'contain', margin: '0 auto 4px', pointerEvents: 'none' });
          b.appendChild(im);
          if (text) { var tx = h('span'); tx.textContent = text; css(tx, { display: 'block', fontSize: '12px' }); b.appendChild(tx); }
        } else {
          b.textContent = text;
        }
        css(b, { textAlign: 'center', padding: '12px 14px', border: '2px solid ' + T.color.line,
          borderRadius: T.radius.md, background: T.color.card, cursor: 'pointer', fontSize: T.font.md,
          fontWeight: '700', color: T.color.ink, lineHeight: '1.3', fontFamily: 'inherit',
          minHeight: T.tap, minWidth: T.tap, touchAction: 'manipulation',
          transition: 'border-color .15s, background .15s' });
        return b;
      }
      var PAIRC = ['#FF7A59', '#2E7BFF', '#17B08C', '#8B6BFF', '#FF922E', '#E0489E'];
      // 보기 내용 채우기 — svg(그림) 문항은 서버 뱅크의 신뢰된 SVG 마크업을 렌더(+라벨).
      function setOpt(el, o) {
        if (o.svg) {
          el.innerHTML = '<span class="cc-svg" style="display:block">' + o.svg + '</span>'
            + (o.text ? '<span style="display:block;font-size:12px;margin-top:4px;color:#6B6157">' + o.text + '</span>' : '');
          var g = el.querySelector('svg'); if (g) { g.style.width = '84px'; g.style.height = 'auto'; g.style.maxWidth = '100%'; }
        } else {
          el.textContent = (o.emoji ? o.emoji + '  ' : '') + o.text;
        }
      }

      if (d.type === 'drag_drop') {
        renderDrag(d, token);
      } else if (d.type === 'trace_path') {
        renderTrace(d, token);
      } else if (d.type === 'route') {
        renderRoute(d, token);
      } else if (d.type === 'image_select') {
        var picked = {};
        var cellEls = [];
        var grid = h('div');
        css(grid, { display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: '10px' });
        if (footerOn) css(grid, { maxWidth: '420px', margin: '0 auto', width: '100%' });
        d.cells.forEach(function (c) {
          var cell = h('button'); cell.textContent = c.emoji;
          css(cell, { fontSize: '32px', padding: '16px 0', minHeight: T.tap, border: '2px solid ' + T.color.line,
            borderRadius: T.radius.md, background: T.color.card, cursor: 'pointer', touchAction: 'manipulation',
            transition: 'border-color .12s, background .12s, transform .08s' });
          cell.onclick = function () {
            if (answered) return;
            picked[c.id] = !picked[c.id];
            cell.style.borderColor = picked[c.id] ? C : T.color.line;
            cell.style.background = picked[c.id] ? T.color.brandSoft : T.color.card;
            cell.style.transform = picked[c.id] ? 'scale(0.96)' : 'scale(1)';
            if (footerOn) {
              var n = Object.keys(picked).filter(function (k) { return picked[k]; }).length;
              footerState(n > 0, n > 0);
            }
          };
          cellEls.push(cell);
          grid.appendChild(cell);
        });
        body.appendChild(grid);
        function submitCells() {
          var ans = Object.keys(picked).filter(function (k) { return picked[k]; });
          if (!ans.length) return; // 빈 선택 제출 방지(오클릭 한 번에 챌린지 소모 방지)
          verify(token, ans);
        }
        if (footerOn) {
          pendingRedo = function () {
            picked = {};
            cellEls.forEach(function (cell) { cell.style.borderColor = T.color.line; cell.style.background = T.color.card; cell.style.transform = 'scale(1)'; });
            footerState(false, false);
          };
          pendingSubmit = submitCells;
        } else {
          var submit = h('button'); submit.textContent = '확인'; css(submit, btnStyle(C, '#fff'));
          submit.onclick = submitCells;
          body.appendChild(submit);
        }
      } else if (d.type === 'multi') {
        // 복수선택(교육형) — 보기 토글 후 확인 제출, 서버가 집합 비교로 채점.
        // 원본 pick(담기 상자)·touch(폰 화면) 프레이밍 복원: boxLabel이 있으면 상자로
        // 끌어 담기(+탭 토글 폴백), screenTitle이 있으면 폰 화면 프레임 안에 보기 배치.
        lastOptions = d.options || [];
        var mPicked = {};
        var mBtns = [];
        var mDnd = d.boxLabel ? makeDnd() : null;
        var mBoxChips = null;
        function mPaintBox() {
          if (!mBoxChips) return;
          mBoxChips.innerHTML = '';
          lastOptions.forEach(function (o) {
            if (!mPicked[o.id]) return;
            var chip = h('div'); chip.textContent = (o.emoji ? o.emoji + ' ' : '') + o.text;
            css(chip, { fontSize: T.font.sm, fontWeight: '700', padding: '7px 11px', margin: '3px',
              minHeight: '34px', display: 'flex', alignItems: 'center',
              background: T.color.card, border: '1.5px solid ' + C, borderRadius: T.radius.sm, cursor: 'pointer', color: T.color.ink });
            chip.onclick = function () { if (answered) return; mToggle(o, false); };
            mBoxChips.appendChild(chip);
          });
        }
        function mToggle(o, on) {
          if (answered) return;
          mPicked[o.id] = on == null ? !mPicked[o.id] : on;
          var mb = mBtns[lastOptions.indexOf(o)];
          if (mb) { mb.style.borderColor = mPicked[o.id] ? C : T.color.line; mb.style.background = mPicked[o.id] ? T.color.brandSoft : T.color.card;
            if (mBoxChips) mb.style.display = mPicked[o.id] ? 'none' : ''; }
          mPaintBox();
          if (footerOn) {
            var mn = Object.keys(mPicked).filter(function (k) { return mPicked[k]; }).length;
            footerState(mn > 0, mn > 0);
          }
        }
        var mFrame = null; // 폰 화면 프레임(touch 원본 연출)
        if (d.screenTitle) {
          mFrame = h('div');
          css(mFrame, { maxWidth: '360px', margin: '0 auto 14px', border: '3px solid #3A3226',
            borderRadius: '22px', overflow: 'hidden', background: '#fff' });
          var mBar = h('div'); mBar.textContent = d.screenTitle;
          css(mBar, { background: '#3A3226', color: '#fff', fontSize: '13px', fontWeight: '800',
            textAlign: 'center', padding: '8px 10px' });
          mFrame.appendChild(mBar);
          body.appendChild(mFrame);
        }
        var mOpts = h('div');
        css(mOpts, mFrame
          ? { display: 'grid', gap: '8px', padding: '12px' }
          : (footerOn
            ? { display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: '12px' }
            : { display: 'grid', gap: '8px' }));
        lastOptions.forEach(function (o) {
          var mb = h('button');
          setOpt(mb, o);
          css(mb, mFrame && d.screenStyle === 'chat'
            ? { textAlign: 'left', padding: '10px 14px', border: '2px solid #F0E4D8', borderRadius: '4px 16px 16px 16px',
                background: '#F6F1E9', cursor: 'pointer', fontSize: '14px', fontWeight: '700', color: '#3A3226', maxWidth: '85%' }
            : (footerOn && !mFrame
              ? { textAlign: 'center', padding: '16px 24px', minWidth: '110px', minHeight: T.tap,
                  border: '2px solid ' + T.color.line, borderRadius: '14px', background: T.color.card,
                  cursor: 'pointer', fontSize: '16px', fontWeight: '700', color: T.color.ink,
                  fontFamily: 'inherit', transition: 'border-color .15s, background .15s' }
              : { textAlign: 'left', padding: '13px 15px', minHeight: T.tap, border: '2px solid ' + T.color.line,
                  borderRadius: T.radius.md, background: T.color.card, cursor: 'pointer', fontSize: T.font.md,
                  fontWeight: '700', color: T.color.ink, fontFamily: 'inherit',
                  transition: 'border-color .15s, background .15s' }));
          if (mDnd) {
            mDnd.drag(mb, { disabled: function () { return answered; },
              onDrop: function () { mToggle(o, true); },
              onTap: function () { mToggle(o); } });
          } else {
            mb.onclick = function () { mToggle(o); };
          }
          mBtns.push(mb);
          mOpts.appendChild(mb);
        });
        (mFrame || body).appendChild(mOpts);
        if (d.boxLabel) {
          // 담기 상자 — 원본 pick: 보기 칩을 상자로 끌어다 담는다(칩 탭으로 회수)
          var mBox = h('div');
          css(mBox, { border: '2px dashed #E0D3C4', borderRadius: '12px', padding: '8px', minHeight: '64px',
            textAlign: 'center', maxWidth: '420px', margin: '12px auto 0', cursor: 'pointer' });
          var mLab = h('div'); mLab.textContent = d.boxLabel;
          css(mLab, { fontSize: '13px', fontWeight: '800', color: '#8A8070', marginBottom: '4px' });
          mBoxChips = h('div'); css(mBoxChips, { display: 'flex', flexWrap: 'wrap', justifyContent: 'center' });
          mBox.appendChild(mLab); mBox.appendChild(mBoxChips);
          mDnd.addZone(mBox, '__box__', function (on) { mBox.style.borderColor = on ? C : '#E0D3C4'; mBox.style.background = on ? '#FFF6F3' : ''; });
          body.appendChild(mBox);
          if (d.boxHint) { var mbh = h('div'); mbh.textContent = '💡 ' + d.boxHint; css(mbh, { textAlign: 'center', fontSize: '12px', color: '#8A8070', marginTop: '6px' }); body.appendChild(mbh); }
        }
        function submitMulti() {
          var mAns = Object.keys(mPicked).filter(function (k) { return mPicked[k]; });
          if (!mAns.length) return; // 아무것도 안 고르고 제출 방지
          verify(token, mAns);
        }
        if (footerOn) {
          pendingRedo = function () {
            mPicked = {};
            mBtns.forEach(function (mb) { mb.style.borderColor = T.color.line; mb.style.background = T.color.card; mb.style.display = ''; });
            mPaintBox();
            footerState(false, false);
          };
          pendingSubmit = submitMulti;
        } else {
          var mSubmit = h('button'); mSubmit.textContent = '확인'; css(mSubmit, btnStyle(C, '#fff'));
          mSubmit.onclick = submitMulti;
          body.appendChild(mSubmit);
        }
        if (d.hint) hintLine(d.hint);
      } else if (d.type === 'connect') {
        // 연결(원본 시각화): 왼쪽 항목을 오른쪽으로 드래그(또는 탭-탭)해 짝짓고, 확정된
        // 짝은 색 테두리 + 두 카드를 잇는 색 선(SVG)으로 표시한다. 제출 {leftId:rightId}
        var cPairs = {}, cSel = null, cL = {}, cR = {};
        var cOuter = h('div'); css(cOuter, { position: 'relative', maxWidth: '480px', margin: '0 auto', width: '100%' });
        var cWrap = h('div'); css(cWrap, { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px', width: '100%' });
        var cSvg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        cSvg.setAttribute('style', 'position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:1;');
        var colL = h('div'), colR = h('div'); css(colL, { display: 'grid', gap: '8px' }); css(colR, { display: 'grid', gap: '8px' });
        var NSc = 'http://www.w3.org/2000/svg';
        function cLines() {
          while (cSvg.firstChild) cSvg.removeChild(cSvg.firstChild);
          var ob = cOuter.getBoundingClientRect();
          if (!ob.width) return;
          d.left.forEach(function (l, i) {
            var rid = cPairs[l.id]; if (rid == null || !cR[rid]) return;
            var a = cL[l.id].getBoundingClientRect(), b = cR[rid].getBoundingClientRect();
            var col = PAIRC[i % 6];
            var x1 = a.right - ob.left, y1 = a.top + a.height / 2 - ob.top;
            var x2 = b.left - ob.left, y2 = b.top + b.height / 2 - ob.top;
            // 굵은 선 + 양끝 색점(연결 지점 명확화) — 시각 피드백 강화
            var ln = document.createElementNS(NSc, 'line');
            ln.setAttribute('x1', x1); ln.setAttribute('y1', y1);
            ln.setAttribute('x2', x2); ln.setAttribute('y2', y2);
            ln.setAttribute('stroke', col); ln.setAttribute('stroke-width', '4'); ln.setAttribute('stroke-linecap', 'round');
            cSvg.appendChild(ln);
            [[x1, y1], [x2, y2]].forEach(function (pt) {
              var dot = document.createElementNS(NSc, 'circle');
              dot.setAttribute('cx', pt[0]); dot.setAttribute('cy', pt[1]); dot.setAttribute('r', '5');
              dot.setAttribute('fill', col); dot.setAttribute('stroke', '#fff'); dot.setAttribute('stroke-width', '2');
              cSvg.appendChild(dot);
            });
          });
        }
        function cPaint() {
          d.left.forEach(function (l, i) { var e = cL[l.id]; var on = cPairs[l.id] != null;
            e.style.borderColor = cSel === l.id ? C : (on ? PAIRC[i % 6] : T.color.line);
            e.style.borderWidth = (cSel === l.id || on) ? '2.5px' : '2px';
            e.style.background = on ? '#FFF6F3' : T.color.card; });
          d.right.forEach(function (r) { var e = cR[r.id]; var ow = Object.keys(cPairs).filter(function (k) { return cPairs[k] === r.id; })[0];
            var ci = ow ? d.left.map(function (l) { return l.id; }).indexOf(ow) : -1;
            e.style.borderColor = ci >= 0 ? PAIRC[ci % 6] : T.color.line;
            e.style.borderWidth = ci >= 0 ? '2.5px' : '2px';
            e.style.background = ci >= 0 ? '#FFF6F3' : T.color.card; });
          cLines();
          var done = d.left.length && Object.keys(cPairs).length === d.left.length;
          if (footerOn) footerState(Object.keys(cPairs).length > 0 || cSel != null, done);
        }
        var cDnd = makeDnd();
        function cLink(leftId, rightId) { if (answered) return;
          Object.keys(cPairs).forEach(function (k) { if (cPairs[k] === rightId) delete cPairs[k]; });
          cPairs[leftId] = rightId; cSel = null; cPaint(); }
        d.left.forEach(function (l) { var e = optCell(l.text, l.img); cL[l.id] = e; colL.appendChild(e);
          // 원본(사회): 왼쪽에서 오른쪽으로 끌어다 연결. 탭 폴백(왼쪽 탭→오른쪽 탭)도 유지.
          cDnd.drag(e, { disabled: function () { return answered; },
            onDrop: function (rid) { cLink(l.id, rid); },
            onTap: function () { if (answered) return; cSel = cSel === l.id ? null : l.id; cPaint(); } }); });
        d.right.forEach(function (r) { var e = optCell(r.text); cR[r.id] = e; colR.appendChild(e);
          e.onclick = function () { if (answered || cSel == null) return; cLink(cSel, r.id); };
          cDnd.addZone(e, r.id, function (on) { if (Object.keys(cPairs).some(function (k) { return cPairs[k] === r.id; })) return;
            e.style.borderColor = on ? C : T.color.line; e.style.background = on ? '#FFF6F3' : T.color.card; }); });
        cWrap.appendChild(colL); cWrap.appendChild(colR);
        cOuter.appendChild(cWrap); cOuter.appendChild(cSvg); body.appendChild(cOuter);
        function subC() { if (Object.keys(cPairs).length === d.left.length) verify(token, cPairs); }
        if (footerOn) { pendingRedo = function () { cPairs = {}; cSel = null; cPaint(); }; pendingSubmit = subC; }
        else { var cbtn = h('button'); cbtn.textContent = '확인'; css(cbtn, btnStyle(C, '#fff')); cbtn.onclick = subC; body.appendChild(cbtn); }
        if (d.hint) hintLine(d.hint);
      } else if (d.type === 'sort') {
        // 분류(원본 복원): 칩을 바구니로 끌어다 놓기. 탭 폴백(칩 탭→바구니 탭)도 유지. 제출 {itemId:binId}
        var sMap = {}, sSel = null, sIt = {}, sBinEls = {};
        var sDnd = makeDnd();
        // 참조 지도(사회 방위 등) — 원본 renderRefMap: 기준 건물 강조 + 나침반. 없으면 생략.
        if (d.mapRef) renderRefMap(d.mapRef);
        var itRow = h('div'); css(itRow, { display: 'flex', flexWrap: 'wrap', gap: '10px', justifyContent: 'center', marginBottom: '16px' });
        // width:100% — full 모드 body(flex column)에서 margin auto만 주면 stretch가 풀려
        // 내용 너비로 쭈그러든다(바구니가 좁게 나오던 버그). 100%로 채우고 maxWidth로 센터.
        var binRow = h('div'); css(binRow, { display: 'grid', gridTemplateColumns: 'repeat(' + Math.min(d.bins.length, 3) + ',1fr)',
          gap: '12px', width: '100%', maxWidth: '480px', margin: '0 auto', boxSizing: 'border-box' });
        function sPaint() {
          d.items.forEach(function (it) { var e = sIt[it.id]; var bin = sMap[it.id];
            e.style.display = bin ? 'none' : ''; e.style.borderColor = sSel === it.id ? C : T.color.line; e.style.background = sSel === it.id ? T.color.brandSoft : T.color.card; });
          d.bins.forEach(function (b) { var box2 = sBinEls[b.id]; box2.chips.innerHTML = '';
            d.items.filter(function (it) { return sMap[it.id] === b.id; }).forEach(function (it) {
              var chip = h('div'); chip.textContent = it.text; css(chip, { fontSize: T.font.sm, fontWeight: '700', padding: '7px 11px', margin: '3px', minHeight: '34px', display: 'flex', alignItems: 'center', background: T.color.card, border: '1.5px solid ' + C, borderRadius: T.radius.sm, cursor: 'pointer', color: T.color.ink });
              chip.onclick = function () { if (answered) return; delete sMap[it.id]; sPaint(); }; box2.chips.appendChild(chip); }); });
          // placeCount: 방해 항목이 있는 분류 — 정답 개수만 담으면 제출 가능(남는 건 트레이에)
          var done = Object.keys(sMap).length === (d.placeCount || d.items.length);
          if (footerOn) footerState(Object.keys(sMap).length > 0 || sSel != null, done);
        }
        function sDrop(itemId, binId) { if (answered) return; sMap[itemId] = binId; sSel = null; sPaint(); }
        d.items.forEach(function (it) {
          var e = optCell(it.text); sIt[it.id] = e; itRow.appendChild(e);
          sDnd.drag(e, { disabled: function () { return answered; },
            onDrop: function (binId) { sDrop(it.id, binId); },
            onTap: function () { if (answered) return; sSel = sSel === it.id ? null : it.id; sPaint(); } });
        });
        d.bins.forEach(function (b) { var box2 = h('div'); css(box2, { border: '2.5px dashed ' + T.color.lineSoft, borderRadius: T.radius.md, padding: '10px', minHeight: '88px', textAlign: 'center', cursor: 'pointer', boxSizing: 'border-box', transition: 'border-color .15s, background .15s' });
          var lab = h('div'); lab.textContent = b.label; css(lab, { fontSize: T.font.sm, fontWeight: '800', color: T.color.inkFaint, marginBottom: '6px' });
          var chips = h('div'); css(chips, { display: 'flex', flexWrap: 'wrap', justifyContent: 'center' });
          box2.appendChild(lab); box2.appendChild(chips); box2.chips = chips;
          box2.onclick = function () { if (answered || sSel == null) return; sMap[sSel] = b.id; sSel = null; sPaint(); };
          sDnd.addZone(box2, b.id, function (on) { box2.style.borderColor = on ? C : T.color.lineSoft; box2.style.background = on ? '#FFF6F3' : ''; });
          sBinEls[b.id] = box2; binRow.appendChild(box2); });
        body.appendChild(itRow); body.appendChild(binRow);
        function subS() { if (Object.keys(sMap).length === (d.placeCount || d.items.length)) verify(token, sMap); }
        if (footerOn) { pendingRedo = function () { sMap = {}; sSel = null; sPaint(); }; pendingSubmit = subS; }
        else { var sbtn = h('button'); sbtn.textContent = '확인'; css(sbtn, btnStyle(C, '#fff')); sbtn.onclick = subS; body.appendChild(sbtn); }
        if (d.hint) hintLine(d.hint);
      } else if (d.type === 'order') {
        // 순서(원본 복원): 아래 카드를 위 칸으로 끌어다 놓거나 탭하면 순서대로 '배치'된다. 채운
        // 칸을 다시 누르면 그 카드를 빼고 뒤를 당긴다. 슬롯 수(need)만큼 채우면 제출.
        // need = slotCount(정답 길이). 방해 카드가 섞인 문항은 need < 카드수라, 남은 카드는
        // 트레이에 두고 슬롯만 채우면 된다(원본 슬롯 방식 — 방해카드 강제 배치 정답불가 버그 수정).
        var oSeq = [], oDnd = makeDnd(), need = d.slotCount || d.cards.length;
        function byId(id) { return d.cards.filter(function (c) { return c.id === id; })[0]; }
        var slotWrap = h('div');
        // width:100% — flex-column body에서 margin auto만 주면 stretch가 풀려 좁아지는 문제 방지
        css(slotWrap, { display: 'flex', flexWrap: 'wrap', gap: '8px', justifyContent: 'center',
          minHeight: '30px', padding: '14px 12px', marginBottom: '14px', width: '100%', maxWidth: '480px',
          marginLeft: 'auto', marginRight: 'auto', boxSizing: 'border-box', border: '2px dashed ' + T.color.lineSoft,
          borderRadius: '14px', background: T.color.cream, alignItems: 'center', transition: 'border-color .15s, background .15s' });
        var trayWrap = h('div');
        css(trayWrap, { display: 'flex', flexWrap: 'wrap', gap: '10px', justifyContent: 'center', maxWidth: '480px', margin: '0 auto' });
        function oPaint() {
          slotWrap.textContent = '';
          if (oSeq.length === 0) {
            var ph = h('span'); ph.textContent = '아래 카드를 여기로 끌어다(또는 눌러) 순서대로 배치해요';
            css(ph, { color: T.color.inkFaint, fontSize: T.font.sm, fontWeight: '700' });
            slotWrap.appendChild(ph);
          }
          oSeq.forEach(function (id, i) {
            var c = byId(id); if (!c) return;
            var s = h('button');
            var badge = h('span'); badge.textContent = (i + 1);
            css(badge, { display: 'inline-block', minWidth: '18px', height: '18px', borderRadius: '9px',
              background: C, color: '#fff', fontSize: '11px', fontWeight: '800', lineHeight: '18px',
              textAlign: 'center', marginRight: '6px' });
            s.appendChild(badge);
            if (c.img) { var sim = h('img'); sim.src = imgUrl(c.img); css(sim, { display: 'block', width: '52px', height: '52px', objectFit: 'contain', margin: '4px auto 2px', pointerEvents: 'none' }); s.appendChild(sim); }
            s.appendChild(document.createTextNode(c.text));
            css(s, { padding: '10px 14px', minHeight: T.tap, border: '2px solid ' + C, borderRadius: T.radius.md,
              background: T.color.brandSoft, cursor: 'pointer', fontSize: T.font.md, fontWeight: '700', color: T.color.ink,
              touchAction: 'manipulation' });
            s.onclick = function () { if (answered) return; oSeq.splice(i, 1); oPaint(); };
            slotWrap.appendChild(s);
          });
          trayWrap.textContent = '';
          d.cards.forEach(function (c) {
            if (oSeq.indexOf(c.id) !== -1) return; // 이미 배치된 카드는 트레이에서 숨김
            var e = h('button');
            if (c.img) { var tim = h('img'); tim.src = imgUrl(c.img); css(tim, { display: 'block', width: '52px', height: '52px', objectFit: 'contain', margin: '0 auto 4px', pointerEvents: 'none' }); e.appendChild(tim); e.appendChild(document.createTextNode(c.text)); }
            else e.textContent = c.text;
            css(e, { padding: '12px 16px', minHeight: T.tap, border: '2px solid ' + T.color.line, borderRadius: T.radius.md,
              background: T.color.card, cursor: 'pointer', fontSize: T.font.md, fontWeight: '700', color: T.color.ink,
              touchAction: 'manipulation' });
            function oPush() { if (answered || oSeq.length >= need) return; oSeq.push(c.id); oPaint(); }
            oDnd.drag(e, { disabled: function () { return answered || oSeq.length >= need; },
              onDrop: function () { oPush(); }, onTap: oPush });
            trayWrap.appendChild(e);
          });
          var done = oSeq.length === need;
          if (footerOn) footerState(oSeq.length > 0, done);
        }
        oDnd.addZone(slotWrap, '__slot__', function (on) { slotWrap.style.borderColor = on ? C : T.color.lineSoft; slotWrap.style.background = on ? '#FFF3EC' : T.color.cream; });
        body.appendChild(slotWrap); body.appendChild(trayWrap);
        oPaint();
        function subO() { if (oSeq.length === need) verify(token, oSeq.slice()); }
        if (footerOn) { pendingRedo = function () { oSeq = []; oPaint(); }; pendingSubmit = subO; }
        else { var obtn = h('button'); obtn.textContent = '확인'; css(obtn, btnStyle(C, '#fff')); obtn.onclick = subO; body.appendChild(obtn); }
        if (d.hint) hintLine(d.hint);
      } else if (d.type === 'place') {
        // 위치(원본 복원): 핀을 지도/장면 위 알맞은 존으로 끌어다 놓기. 탭(존 탭) 폴백 유지.
        // 제출 zoneId. d.reference가 있으면 기준 건물을 강조(방위 문제에서 기준을 눈으로 찾게).
        var pSel = null, pEls = {}, pDnd = makeDnd();
        var board = h('div'); css(board, { position: 'relative', width: '100%', maxWidth: '460px', margin: '0 auto',
          aspectRatio: '1 / 0.94', background: '#F7F1E8', border: '2px solid #EADFCE', borderRadius: T.radius.lg, overflow: 'hidden' });
        if (d.compass) { ['북 N|top:5px;left:50%;transform:translateX(-50%)', '남 S|bottom:5px;left:50%;transform:translateX(-50%)',
          '동 E|right:7px;top:50%;transform:translateY(-50%)', '서 W|left:7px;top:50%;transform:translateY(-50%)'].forEach(function (s) {
          var parts = s.split('|'); var lab = h('div'); lab.textContent = parts[0]; lab.setAttribute('style', 'position:absolute;font-size:11px;font-weight:800;color:' + T.color.inkMute + ';z-index:1;pointer-events:none;' + parts[1]); board.appendChild(lab); }); }
        var PREF = '#8B6BFF', PREFBG = '#F1ECFF'; // 기준 건물 강조(브랜드 코랄과 구분되는 보라 — 의도적 3색)
        function pPaint() { d.zones.forEach(function (z2) { var base = z2.id === d.reference;
          var sel = z2.id === pSel;
          pEls[z2.id].style.borderColor = sel ? C : (base ? PREF : T.color.lineSoft);
          pEls[z2.id].style.borderWidth = (sel || base) ? '2.5px' : '2px';
          pEls[z2.id].style.background = sel ? T.color.brandSoft : (base ? PREFBG : T.color.card); }); }
        d.zones.forEach(function (z) { var e = h('button');
          e.textContent = (z.emoji ? z.emoji + ' ' : '') + z.label + (z.id === d.reference ? ' (기준)' : '');
          // 존을 살짝 안쪽으로(gap) 그려 붙어 보이지 않게 — margin 대신 inset 계산으로 격자 정합 유지
          e.setAttribute('style', 'position:absolute;left:calc(' + z.x + '% + 3px);top:calc(' + z.y + '% + 3px);'
            + 'width:calc(' + z.w + '% - 6px);height:calc(' + z.h + '% - 6px);'
            + 'border:2px solid ' + T.color.lineSoft + ';border-radius:' + T.radius.md + ';background:' + T.color.card
            + ';cursor:pointer;font-size:13px;font-weight:700;color:' + T.color.ink + ';padding:2px;line-height:1.25;'
            + 'box-shadow:0 2px 5px -3px rgba(120,90,70,.5);box-sizing:border-box;transition:border-color .15s, background .15s;'
            + (z.moving ? 'animation:ccMove 2.2s ease-in-out infinite alternate;' : ''));
          e.onclick = function () { if (answered) return; pSel = z.id; pPaint(); if (footerOn) footerState(true, true); };
          pDnd.addZone(e, z.id, function (on) { if (z.id === pSel) return;
            e.style.background = on ? '#FFF6F3' : (z.id === d.reference ? PREFBG : T.color.card); });
          pEls[z.id] = e; board.appendChild(e); });
        pPaint();
        // 끌어다 놓을 토큰 — 원본 캐릭터(🧒 등)가 있으면 그걸, 없으면 핀. start 위치(없으면 하단 중앙).
        var pin = h('div'); pin.textContent = d.character || '📍';
        var pinX = d.start && d.start.x != null ? d.start.x : 50, pinY = d.start && d.start.y != null ? d.start.y : 92;
        css(pin, { position: 'absolute', left: pinX + '%', top: pinY + '%', transform: 'translate(-50%,-70%)',
          fontSize: '30px', lineHeight: '1', zIndex: '3', filter: 'drop-shadow(0 2px 2px rgba(0,0,0,.25))' });
        pDnd.drag(pin, { disabled: function () { return answered; },
          onDrop: function (zoneId) { if (answered) return; pSel = zoneId; var z = d.zones.filter(function (z2) { return z2.id === zoneId; })[0];
            if (z) { pin.style.left = (z.x + z.w / 2) + '%'; pin.style.top = (z.y + z.h / 2) + '%'; }
            pPaint(); if (footerOn) footerState(true, true); } });
        board.appendChild(pin);
        body.appendChild(board);
        if (d.arrow) { var arw = h('div'); arw.textContent = '👉 ' + d.arrow;
          css(arw, { textAlign: 'center', fontSize: '13px', fontWeight: '700', color: '#8A8070', margin: '8px 0 0' });
          body.appendChild(arw); }
        function subP() { if (pSel != null) verify(token, pSel); }
        if (footerOn) { pendingRedo = function () { pSel = null; pPaint();
          pin.style.left = pinX + '%'; pin.style.top = pinY + '%'; // 핀/캐릭터 원위치
          footerState(false, false); }; pendingSubmit = subP; }
        else { var pbtn = h('button'); pbtn.textContent = '확인'; css(pbtn, btnStyle(C, '#fff')); pbtn.onclick = subP; body.appendChild(pbtn); }
        if (d.hint) hintLine(d.hint);
      } else if (d.type === 'puzzle') {
        // 국기 완성 — 조각(국기 크롭)을 그리드 슬롯에 배치. 제출 {slotId:pieceId}, 서버 match 채점.
        var GW = 180, GH = 120, cw = GW / d.cols, chh = GH / d.rows;
        function crop(el, col, row, flag) {
          // 각 조각은 자기 국기(flag)에서 크롭 — 방해 조각은 다른 나라 국기라 정답과 안 겹친다.
          el.style.backgroundImage = "url('" + base + '/captcha/v1/flag/' + (flag || d.flag) + "')";
          el.style.backgroundSize = GW + 'px ' + GH + 'px';
          el.style.backgroundPosition = '-' + (col * cw) + 'px -' + (row * chh) + 'px';
          el.style.backgroundRepeat = 'no-repeat';
        }
        // 완성 미리보기(원본 preview) — preview===false(마지막 단계)만 숨긴다.
        if (d.preview !== false) {
          var prev = h('div');
          css(prev, { width: (GW * 0.5) + 'px', height: (GH * 0.5) + 'px', margin: '0 auto 10px',
            border: '1px solid #E0D3C4', borderRadius: '3px', opacity: '0.85' });
          crop(prev, 0, 0, d.flag); prev.style.backgroundSize = (GW * 0.5) + 'px ' + (GH * 0.5) + 'px';
          var pcap = h('div'); pcap.textContent = '완성 그림'; css(pcap, { textAlign: 'center', fontSize: '11px', color: '#B7A68F', margin: '0 0 8px', fontWeight: '700' });
          body.appendChild(prev); body.appendChild(pcap);
        }
        var placed = {}, zSel = null, pieceEls = {}, slotEls = {}, pzDnd = makeDnd();
        // 2단계 등 원본 prefilled(미리 놓인 조각) — 국기 맥락을 유지. 없으면 무시.
        if (d.prefilled) { for (var pk in d.prefilled) placed[pk] = d.prefilled[pk]; }
        function pzAssign(slotId, pieceId) { if (answered) return;
          if (d.prefilled && d.prefilled[slotId] != null) return; // 미리 채워진 칸은 고정
          Object.keys(placed).forEach(function (k) { if (placed[k] === pieceId && !(d.prefilled && d.prefilled[k] != null)) delete placed[k]; });
          placed[slotId] = pieceId; zSel = null; pzPaint(); }
        var gridBox = h('div');
        css(gridBox, { display: 'grid', gridTemplateColumns: 'repeat(' + d.cols + ',' + cw + 'px)', gridTemplateRows: 'repeat(' + d.rows + ',' + chh + 'px)',
          width: GW + 'px', margin: '0 auto 16px', border: '2px solid #C9B79E', borderRadius: '4px', overflow: 'hidden', background: '#faf6ef' });
        d.slots.forEach(function (sl) {
          var slot = h('div'); css(slot, { border: '1px dashed #D8C8B4', cursor: 'pointer', backgroundRepeat: 'no-repeat' });
          slot.onclick = function () { if (answered || zSel == null) return; pzAssign(sl.id, zSel); };
          pzDnd.addZone(slot, sl.id, function (on) { if (placed[sl.id]) return; slot.style.borderColor = on ? C : '#D8C8B4'; slot.style.borderStyle = on ? 'solid' : 'dashed'; });
          slotEls[sl.id] = slot; gridBox.appendChild(slot);
        });
        body.appendChild(gridBox);
        var tray = h('div'); css(tray, { display: 'flex', flexWrap: 'wrap', gap: '8px', justifyContent: 'center' });
        d.pieces.forEach(function (p) {
          var pc = h('button'); css(pc, { width: cw + 'px', height: chh + 'px', border: '2px solid #F0E4D8', borderRadius: '6px', cursor: 'pointer', padding: '0' });
          crop(pc, p.col, p.row, p.flag);
          pzDnd.drag(pc, { disabled: function () { return answered; },
            onDrop: function (slotId) { pzAssign(slotId, p.id); },
            onTap: function () { if (answered) return; zSel = zSel === p.id ? null : p.id; pzPaint(); } });
          pieceEls[p.id] = pc; tray.appendChild(pc);
        });
        body.appendChild(tray);
        var pzReduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        var pzWas = {}; // 직전 배치 상태 — 새로 채워진 슬롯만 스냅 애니메이션
        function pzPaint() {
          d.slots.forEach(function (sl) { var el = slotEls[sl.id]; var pid = placed[sl.id];
            if (pid) { var pc = d.pieces.filter(function (x) { return x.id === pid; })[0]; crop(el, pc.col, pc.row, pc.flag); el.style.borderColor = C; el.style.borderStyle = 'solid';
              if (pzWas[sl.id] !== pid && !pzReduce) { el.style.animation = 'none'; void el.offsetWidth; el.style.animation = 'ccPop 0.26s ease-out'; } }
            else { el.style.backgroundImage = 'none'; el.style.borderColor = '#D8C8B4'; el.style.borderStyle = 'dashed'; }
            pzWas[sl.id] = pid; });
          d.pieces.forEach(function (p) { var el = pieceEls[p.id]; var used = Object.keys(placed).some(function (k) { return placed[k] === p.id; });
            el.style.opacity = used ? '0.25' : '1'; el.style.borderColor = zSel === p.id ? C : '#F0E4D8'; });
          var done = Object.keys(placed).length === d.slots.length;
          if (footerOn) footerState(Object.keys(placed).length > 0 || zSel != null, done);
        }
        function subPz() { if (Object.keys(placed).length === d.slots.length) verify(token, placed); }
        function pzReset() { placed = {}; if (d.prefilled) { for (var pk in d.prefilled) placed[pk] = d.prefilled[pk]; } zSel = null; pzPaint(); }
        pzPaint(); // 초기 렌더 — prefilled(미리 놓인 조각)를 화면에 표시
        if (footerOn) { pendingRedo = pzReset; pendingSubmit = subPz; }
        else { var pzb = h('button'); pzb.textContent = '확인'; css(pzb, btnStyle(C, '#fff')); pzb.onclick = subPz; body.appendChild(pzb); }
        if (d.hint) hintLine(d.hint);
      } else if (d.type === 'drag_pick') {
        // 원본 카드 드래그(과학·수학) — 피드백에 카드 라벨을 쓰도록 lastOptions 매핑
        lastOptions = (d.items || []).map(function (it) { return { id: it.id, text: it.label || it.e }; });
        renderDragPick(d, token);
      } else if (d.type === 'memory') {
        lastOptions = [];
        renderMemory(d, token);
      } else if (d.type === 'listen_seq') {
        lastOptions = d.options || [];
        renderListenSeq(d, token);
      } else if (d.type === 'dictation' || d.type === 'type_in' || d.type === 'input') {
        renderTyping(d, token);
      } else if (d.type === 'punct') {
        renderPunct(d, token);
      } else if (d.type === 'crossword') {
        renderCrossword(d, token);
      } else if (d.type === 'swipe') {
        lastOptions = [{ id: d.leftLabel || '의견', text: d.leftLabel || '의견' },
                       { id: d.rightLabel || '사실', text: d.rightLabel || '사실' }];
        renderSwipe(d, token);
      } else if (d.type === 'position') {
        lastOptions = (d.regions || []).map(function (r) { return { id: r.id, text: r.name }; });
        renderPosition(d, token);
      } else {
        // single / arithmetic / listen — 보기 중 하나 선택
        // 풋터 모드: 클릭은 '선택'만(테두리 강조), 제출은 풋터의 다음 문제 버튼이 담당
        lastOptions = d.options || [];
        // 듣기(listen): 🔊 오디오 재생 버튼 — 파일은 불투명 이름이라 정답(단어) 노출 없음
        if (d.type === 'listen' && d.audio) {
          var au = h('audio'); au.src = base + '/captcha/v1/audio/' + d.audio; au.preload = 'auto';
          var lab0 = '다시 듣기';
          var lab = audioBtn(lab0, { compact: !footerOn });
          var playBtn = lab.el;
          // 재생 종료·일시정지 시 라벨을 유휴 상태로 되돌리되, 남은 재생횟수 힌트는 보존
          // (원본은 '(N번 남음)'이 다음 클릭까지 유지됐다 — ended가 이를 지우지 않게 restLabel 사용).
          function restLabel() {
            if (!d.plays) return lab0;
            return playsLeft <= 0 ? '다 들었어요' : '다시 듣기 (' + playsLeft + '번 남음)';
          }
          au.addEventListener('play', function () { lab.playing(true); lab.setLabel('듣는 중…'); });
          au.addEventListener('ended', function () { lab.playing(false); lab.setLabel(restLabel()); });
          au.addEventListener('pause', function () { lab.playing(false); lab.setLabel(restLabel()); });
          // 원본 maxAudioPlays — 어려운 단계(4~5)는 재생 횟수 제한(자동재생 포함)
          var playsLeft = d.plays ? d.plays : Infinity;
          function playCount() {
            playsLeft -= 1;
            if (d.plays) {
              if (playsLeft <= 0) { playBtn.disabled = true; playBtn.style.opacity = '0.5'; playBtn.style.cursor = 'default'; lab.setLabel('다 들었어요'); }
              else lab.setLabel('다시 듣기 (' + playsLeft + '번 남음)');
            }
          }
          // 재생이 '실제로 시작됐을 때만' 횟수를 차감 — 자동재생이 차단되는 환경에서
          // 헛차감으로 남은 기회가 줄어들지 않게 한다(모바일 등).
          function tryPlay() {
            if (playsLeft <= 0) return;
            try {
              au.currentTime = 0;
              var pr = au.play();
              if (pr && pr.then) pr.then(playCount).catch(function () {});
              else playCount();
            } catch (e) {}
          }
          playBtn.onclick = tryPlay;
          body.appendChild(au);
          body.appendChild(playBtn);
          setTimeout(tryPlay, 150); // 자동재생 시도(막히면 버튼으로)
        }
        var chosen = null;
        var optBtns = [];
        // 국어 중심생각 — 원본 2단계: 지문을 먼저 읽고 "생각 정리 완료"를 눌러야 보기가 열린다.
        if (d.paragraph) {
          var para = h('div'); para.textContent = d.paragraph;
          css(para, { fontSize: footerOn ? '17px' : '14px', fontWeight: '600', color: '#3A3226', lineHeight: '1.7',
            background: '#FFF9F0', border: '1.5px solid #F0E4D8', borderRadius: '12px',
            padding: '14px 18px', maxWidth: '520px', margin: '0 auto 14px', textAlign: 'left' });
          body.appendChild(para);
        }
        // 영문법(빈칸) — 원본: 보기 카드를 문장 속 ___ 빈칸에 끌어다 넣기. 문장을 보여줘야
        // 풀 수 있으므로 표시하고, 빈칸을 드롭 존으로 만든다(탭 선택도 유지).
        var gDnd = null, gGap = null;
        if (d.sentence) {
          gDnd = makeDnd();
          var sent = h('div');
          css(sent, { fontSize: footerOn ? '21px' : '16px', fontWeight: '700', color: '#3A3226',
            textAlign: 'center', margin: '0 auto 18px', lineHeight: '1.8', maxWidth: '460px' });
          var parts = String(d.sentence).split('___');
          sent.appendChild(document.createTextNode(parts[0] || ''));
          gGap = h('span'); gGap.textContent = '____';
          css(gGap, { display: 'inline-block', minWidth: '64px', padding: '2px 12px', margin: '0 4px',
            borderBottom: '3px solid ' + C, color: '#B7A68F', fontWeight: '800', textAlign: 'center' });
          sent.appendChild(gGap);
          sent.appendChild(document.createTextNode(parts.slice(1).join('___') || ''));
          body.appendChild(sent);
          gDnd.addZone(gGap, '__gap__', function (on) { gGap.style.background = on ? '#FFF0EE' : ''; });
        } else if (imgHintEl) {
          // 원본(영어 01 낱말그림): 단어 칩을 그림 위로 끌어다 놓는다. 탭 폴백 유지.
          gDnd = makeDnd();
          css(imgHintEl, { border: '2px dashed #E3D6C6', borderRadius: '16px', padding: '10px 24px',
            maxWidth: '200px', margin: '0 auto 12px' });
          gDnd.addZone(imgHintEl, '__img__', function (on) {
            imgHintEl.style.borderColor = on ? C : T.color.lineSoft; imgHintEl.style.background = on ? '#FFF6F3' : ''; });
        }
        function chooseOpt(o, b) {
          if (answered) return;
          chosen = o.id;
          if (gGap) { gGap.textContent = (o.emoji ? o.emoji + ' ' : '') + o.text; gGap.style.color = C; }
          optBtns.forEach(function (x) { x.style.borderColor = T.color.line; x.style.background = T.color.card; });
          b.style.borderColor = C; b.style.background = T.color.brandSoft;
          if (footerOn) footerState(true, true);
          else verify(token, o.id);
        }
        var opts = h('div');
        css(opts, footerOn
          ? { display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: '12px' }
          : { display: 'grid', gap: '8px' });
        d.options.forEach(function (o) {
          var b = h('button');
          if (d.type === 'listen' && o.emoji && o.text) {
            // 원본(듣기 1~3단계): 그림 카드 아래 단어 라벨 — 소리·그림·단어를 함께 학습
            var lem = h('div'); lem.textContent = o.emoji; css(lem, { fontSize: '34px', lineHeight: '1.2' });
            var ltx = h('div'); ltx.textContent = o.text; css(ltx, { fontSize: '13px', fontWeight: '800', marginTop: '2px' });
            b.appendChild(lem); b.appendChild(ltx);
          } else {
            setOpt(b, o);
          }
          css(b, footerOn
            ? { textAlign: 'center', padding: '16px 24px', minWidth: '110px', minHeight: T.tap,
                border: '2px solid ' + T.color.line, borderRadius: '14px', background: T.color.card,
                cursor: 'pointer', fontSize: '16px', fontWeight: '700', color: T.color.ink,
                fontFamily: 'inherit', transition: 'border-color .15s, background .15s' }
            : { textAlign: 'left', padding: '13px 15px', minHeight: T.tap, border: '2px solid ' + T.color.line,
                borderRadius: T.radius.md, background: T.color.card, cursor: 'pointer', fontSize: T.font.md,
                fontWeight: '700', color: T.color.ink, fontFamily: 'inherit',
                transition: 'border-color .15s, background .15s' });
          if (gDnd) {
            // 빈칸에 끌어다 넣기 + 탭 폴백
            gDnd.drag(b, { disabled: function () { return answered; },
              onDrop: function () { chooseOpt(o, b); }, onTap: function () { chooseOpt(o, b); } });
          } else {
            b.onclick = function () { chooseOpt(o, b); };
          }
          optBtns.push(b);
          opts.appendChild(b);
        });
        body.appendChild(opts);
        // 이 분기는 지문(d.paragraph)·문장(d.sentence)을 셸 힌트 자리 뒤에 붙이므로, 힌트가
        // 그 맥락 위로 떠 보인다. 힌트 자리를 보기 바로 위로 옮겨 '지문/문장 → 힌트 → 보기'
        // 순서를 맞춘다(가독·교육 흐름). 힌트가 없으면 빈 slot이라 무해.
        if (hintSlot && (d.paragraph || d.sentence)) body.insertBefore(hintSlot, opts);
        // 중심생각 2단계 — 보기 숨김 + "생각 정리 완료" 버튼으로 공개 (원본 흐름)
        if (d.readFirst) {
          opts.style.display = 'none';
          var readyBtn = h('button'); readyBtn.textContent = '생각 정리 완료 ✔';
          css(readyBtn, { display: 'block', margin: '0 auto 14px', padding: '12px 26px', fontSize: '15px',
            fontWeight: '800', border: 'none', borderRadius: '24px', background: C, color: '#fff', cursor: 'pointer' });
          readyBtn.onclick = function () { readyBtn.remove(); opts.style.display = ''; };
          body.insertBefore(readyBtn, opts);
        }
        // 답변 후 보기별 근거(rationale) 공개 — 원본은 정답·오답 이유를 모두 보여준다
        if (lastOptions.some(function (o) { return o.rationale; })) {
          onAnswered = function () {
            lastOptions.forEach(function (o, i2) {
              if (!o.rationale || !optBtns[i2]) return;
              var rt = h('div'); rt.textContent = o.rationale;
              css(rt, { fontSize: '12px', fontWeight: '600', color: '#8A8070', marginTop: '6px', lineHeight: '1.5' });
              optBtns[i2].appendChild(rt);
            });
          };
        }
        if (footerOn) {
          pendingRedo = function () {
            chosen = null;
            optBtns.forEach(function (x) { x.style.borderColor = T.color.line; x.style.background = T.color.card; });
            if (gGap) { gGap.textContent = '____'; gGap.style.color = T.color.inkMute; }
            footerState(false, false);
          };
          pendingSubmit = function () { if (chosen != null) verify(token, chosen); };
        }
        if (d.hint) hintLine(d.hint);
      }
    }

    function verify(token, answer, correctText) {
      if (answered || grading) return; // 채점 후/채점 중 재제출 방지
      answered = true;
      grading = true;
      var mySeq = renderSeq; // 응답 도착 시 문항이 이미 바뀌었으면 무시(스테일 응답 가드)
      status.textContent = '확인 중…';
      var rect = box.getBoundingClientRect();
      timerPause();  // 제출 순간 활성 시간 확정(이후 대기·피드백 시간은 미포함)
      var behavior = {
        solve_time_ms: elapsedActive(),
        retry_count: retries + redoCount, // 캡차 재시도 + 다시 고르기/그리기 횟수
        input_type: inputType || 'unknown', // mouse|touch|pen
        trace: trace.slice(),
        box: { w: Math.round(rect.width), h: Math.round(rect.height) },
      };
      // 연습장 필기 데이터 — 실제로 쓴 경우에만 실어 보낸다(안 쓰면 payload 미포함).
      var scData = scratchData();
      if (scData) behavior.scratch = scData;
      getAuth().then(function (a) {
        return api(base, '/captcha/v1/verify', key, { challenge_token: token, answer: answer, behavior: behavior }, a);
      }).then(function (r) {
        if (mySeq !== renderSeq) return; // 이전 문항의 늦은 응답 — 새 문항을 건드리지 않음
        grading = false;
        if (product === 'edu') {
          if (!r.ok) {
            // 레이트리밋(429)·만료(400)·중복(409) — 채점되지 않았으므로
            // 오답 피드백·세션 카운트로 오처리하지 않고 에러+새 문항 재시도로 복구한다.
            answered = false;
            fail(r.data && r.data.detail ? r.data.detail : '확인에 실패했어요. 다시 시도해 주세요.');
            return;
          }
          // 교육형: 통과 게이트가 아니라 학습 피드백 + 행동데이터 수집. 계속 다음 문제로.
          solvedCount += r.data && r.data.success ? 1 : 0;
          // 정답 여부와 무관하게 진행 토큰 채움(임베드 폼이 학습 완료를 알 수 있게)
          hidden.value = 'edu:' + solvedCount;
          // 세션 완료는 '이 화면에서 푼 수' 기준(재입장 시 항상 새 세션) —
          // 코인·퀴즈 완료 적립은 서버가 일 단위로 따로 판정한다(session 응답).
          answeredCount += 1;
          var sess = r.data && r.data.session;
          sessionDone = sessionTotal > 0 && answeredCount >= sessionTotal;
          [].forEach.call(body.querySelectorAll('button'), function (b) { b.disabled = true; });
          eduFeedback(r.data || {}, correctText);
          if (onAnswered) { try { onAnswered(r.data || {}); } catch (e) {} } // 렌더러별 답변 후 표시(근거 공개 등)
          // 소비자(게임 화면)가 진행 통계·완료 이동을 처리할 수 있게 알림
          box.dispatchEvent(new CustomEvent('catchap:answer', {
            bubbles: true,
            detail: { correct: !!(r.data && r.data.success), session: sess || null },
          }));
        } else if (r.ok && r.data.success) {
          solved(r.data.verdict_token);
        } else {
          retries += 1; status.textContent = '다시 해볼까요'; status.style.color = C; load();
        }
      }).catch(function () { if (mySeq === renderSeq) { grading = false; fail('네트워크 오류'); } });
    }

    function load() {
      status.textContent = '불러오는 중…'; status.style.color = '#B0A79B';
      stopSounds();
      body.innerHTML = '';
      var qs = [];
      if (subject) qs.push('subject=' + encodeURIComponent(subject));
      if (day) qs.push('day=' + encodeURIComponent(day));
      if (chapter) qs.push('chapter=' + encodeURIComponent(chapter));
      if (stage) qs.push('stage=' + encodeURIComponent(stage));
      if (replay) qs.push('replay=true');
      if (bankMode) qs.push('bank=true');
      var path = '/captcha/v1/challenge' + (qs.length ? '?' + qs.join('&') : '');
      getAuth().then(function (a) {
        return api(base, path, key, null, a);
      }).then(function (r) {
        if (!r.ok) { fail(r.data && r.data.detail ? r.data.detail : '요청 실패'); return; }
        product = r.data.product;
        status.textContent = product === 'edu' ? ('교육 · ' + (r.data.subject || '')) : '캡차';
        render(r.data);
      }).catch(function () { fail('네트워크 오류'); });
    }

    load();
  }

  function init() {
    var boxes = document.querySelectorAll('.catchap,[data-catchap]');
    for (var i = 0; i < boxes.length; i++) if (!boxes[i].__catchap) { boxes[i].__catchap = 1; mount(boxes[i]); }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
  window.CatChap = { init: init, mount: mount };
})();
