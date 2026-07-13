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
  var C = '#FF5A4D', OK = '#17B08C';

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
    st.textContent = '@keyframes ccMove{from{margin-left:-2.5%}to{margin-left:2.5%}}';
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
    box.addEventListener('pointerdown', function (e) { tracePoint(e, true); });
    box.addEventListener('pointerup', function (e) { tracePoint(e, true); });

    function fail(msg) { body.innerHTML = ''; var p = h('div'); p.textContent = msg || '문제를 불러오지 못했어요.'; css(p, { color: '#C25', fontSize: '13px' }); body.appendChild(p); refreshBtn(); if (footerOn) footerReset(); }
    function refreshBtn() {
      var b = h('button'); b.textContent = '다시 시도'; css(b, btnStyle('#eee', '#555'));
      b.onclick = load; body.appendChild(b);
    }
    function btnStyle(bg, col) {
      return { marginTop: '10px', width: '100%', border: 'none', borderRadius: '10px', padding: '11px',
        fontWeight: '800', fontSize: '14px', cursor: 'pointer', background: bg, color: col };
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

    // ── 풀 사이즈(교육형) 액션 풋터 — '다시 고르기 · 다음 문제 →'를 카드 우하단에 고정.
    //    보기 클릭은 '선택'만 하고, 다음 문제 버튼이 제출(채점)→한 번 더 누르면 다음 문제로.
    //    (버튼이 답 카드에서 멀어지지 않게 문제 영역 안에 둔다)
    var footer = null, redoBtn = null, nextBtn = null, footerOn = false;
    var pendingSubmit = null, pendingRedo = null;
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
      css(redoBtn, { border: '2px solid #F0E4D8', borderRadius: '12px', padding: '11px 20px',
        fontWeight: '800', fontSize: '14px', background: '#fff', color: '#8A8070', fontFamily: 'inherit' });
      nextBtn = h('button'); nextBtn.textContent = '다음 문제 →';
      css(nextBtn, { border: 'none', borderRadius: '12px', padding: '11px 24px',
        fontWeight: '800', fontSize: '14px', background: C, color: '#fff', fontFamily: 'inherit' });
      redoBtn.onclick = function () { if (!answered && !grading && pendingRedo) { pendingRedo(); redoCount += 1; } };
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
      footer.appendChild(redoBtn); footer.appendChild(nextBtn);
      box.insertBefore(footer, hidden);
    }
    function footerReset() {
      pendingSubmit = null; pendingRedo = null;
      if (footer) { setBtnOn(redoBtn, false); setBtnOn(nextBtn, false); }
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
        borderRadius: '12px', fontWeight: '800', fontSize: '14px',
        background: okAns ? '#E1F5EC' : '#FFEDEF', color: okAns ? OK : '#D14559' });
      fb.textContent = msg;
      body.appendChild(fb);
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

    function hintLine(text) {
      if (!text) return;
      var hint = h('div'); hint.textContent = '💡 ' + text;
      css(hint, { marginTop: '10px', fontSize: '12px', color: '#8A8070' });
      body.appendChild(hint);
    }

    // ── 끌어다 놓기 (drag_drop) — 아이템을 목표에 드래그, 드롭 좌표를 서버가 채점
    function renderDrag(d, token) {
      var area = h('div');
      css(area, { position: 'relative', width: '100%', height: '260px', background: '#FFFAF4',
        border: '2px dashed #F0E4D8', borderRadius: '14px', overflow: 'hidden', touchAction: 'none' });
      var ring = h('div');
      css(ring, { position: 'absolute', width: '86px', height: '86px', border: '3px dashed #FFB8A8',
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
      var dragging = false;
      function norm(e) {
        var r = area.getBoundingClientRect();
        return { x: Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)),
                 y: Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)) };
      }
      item.addEventListener('pointerdown', function (e) {
        if (answered) return;
        dragging = true; item.setPointerCapture(e.pointerId);
        item.style.cursor = 'grabbing'; e.preventDefault();
      });
      item.addEventListener('pointermove', function (e) {
        if (!dragging) return;
        var p = norm(e);
        item.style.left = (p.x * 100) + '%'; item.style.top = (p.y * 100) + '%';
      });
      var dropAt = null; // 풋터 모드: 놓은 위치를 기억해 두고 다음 문제 버튼이 제출
      item.addEventListener('pointerup', function (e) {
        if (!dragging || answered) return;
        dragging = false; item.style.cursor = 'grab';
        var p = norm(e);
        var ans = { x: Math.round(p.x * 1000) / 1000, y: Math.round(p.y * 1000) / 1000 };
        if (footerOn) { dropAt = ans; footerState(true, true); }
        else verify(token, ans);
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
      css(svg, { width: '100%', height: '260px', display: 'block', background: '#FFFAF4',
        border: '2px dashed #F0E4D8', borderRadius: '14px', touchAction: 'none', cursor: 'crosshair' });
      // 원본 가이드 5단계(guideStyle): dotted(점선)→faint(흐린 실선)→arrow(점선+시작점·방향)→
      // partial(앞부분만 점선)→blank(가이드 없음, 위에 예시 글자만).
      var gs = d.guideStyle || 'dotted';
      if (gs === 'blank' && d.showExample && d.glyph) {
        var ex = h('div'); ex.textContent = d.glyph;
        css(ex, { textAlign: 'center', fontSize: '46px', fontWeight: '800', color: '#3A3226',
          border: '2px solid #F0E4D8', borderRadius: '12px', width: '72px', margin: '0 auto 10px', background: '#fff' });
        body.appendChild(ex);
      }
      if (gs === 'dotted') svg.appendChild(pl(d.path, '#D9CDBE', '4', '1.5 5.5'));
      else if (gs === 'faint') { var f = pl(d.path, '#E2D8CB', '5'); f.setAttribute('opacity', '0.55'); svg.appendChild(f); }
      else if (gs === 'arrow') {
        svg.appendChild(pl(d.path, '#D9CDBE', '4', '1.5 5.5'));
        var sdot = document.createElementNS(NS, 'circle');
        sdot.setAttribute('cx', d.path[0][0] * 100); sdot.setAttribute('cy', d.path[0][1] * 100);
        sdot.setAttribute('r', '3'); sdot.setAttribute('fill', C);
        svg.appendChild(sdot);
        if (d.path.length > 1) { // 시작 방향 화살촉
          var p0 = d.path[0], p1 = d.path[1];
          var ang = Math.atan2(p1[1] - p0[1], p1[0] - p0[0]);
          var ax = p0[0] * 100 + Math.cos(ang) * 8, ay = p0[1] * 100 + Math.sin(ang) * 8;
          var arw2 = document.createElementNS(NS, 'path');
          var w1 = ang + 2.6, w2 = ang - 2.6;
          arw2.setAttribute('d', 'M' + ax + ',' + ay + ' L' + (ax + Math.cos(w1) * 4) + ',' + (ay + Math.sin(w1) * 4)
            + ' M' + ax + ',' + ay + ' L' + (ax + Math.cos(w2) * 4) + ',' + (ay + Math.sin(w2) * 4));
          arw2.setAttribute('stroke', C); arw2.setAttribute('stroke-width', '1.6'); arw2.setAttribute('fill', 'none');
          arw2.setAttribute('stroke-linecap', 'round');
          svg.appendChild(arw2);
        }
      } else if (gs === 'partial') {
        svg.appendChild(pl(d.path.slice(0, Math.max(2, Math.ceil(d.path.length / 2))), '#D9CDBE', '4', '1.5 5.5'));
      } // blank: 가이드 없음
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
      function zone(z, fill, stroke) {
        svg.appendChild(mk('rect', { x: z.x * 100, y: z.y * 100, width: z.w * 100, height: z.h * 100, rx: 3, fill: fill, stroke: stroke, 'stroke-width': 1 }));
        if (z.emoji) { var t = mk('text', { x: (z.x + z.w / 2) * 100, y: (z.y + z.h / 2) * 100 + 3, 'text-anchor': 'middle', 'font-size': '9' }); t.textContent = z.emoji; svg.appendChild(t); }
        if (z.label) { var l = mk('text', { x: (z.x + z.w / 2) * 100, y: (z.y + z.h) * 100 + 4, 'text-anchor': 'middle', 'font-size': '4', fill: '#6B7B66' }); l.textContent = z.label; svg.appendChild(l); }
      }
      (d.dangers || []).forEach(function (z) { zone(z, 'rgba(226,87,76,0.14)', '#E2574C'); });
      if (d.dest) zone(d.dest, 'rgba(23,176,140,0.16)', '#17B08C');
      var user = mk('polyline', { fill: 'none', stroke: C, 'stroke-width': '3', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' });
      svg.appendChild(user);
      // 원본 방식: 캐릭터 토큰을 '끌어서' 이동 — 경로는 캐릭터가 지나간 궤적으로 기록된다.
      var st = mk('text', { x: d.start.x * 100, y: d.start.y * 100 + 3, 'text-anchor': 'middle', 'font-size': '9', cursor: 'grab' });
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
      css(area, { position: 'relative', width: '100%', height: '300px', background: '#FFFAF4',
        border: '2px dashed #F0E4D8', borderRadius: '14px', overflow: 'hidden', touchAction: 'none' });
      var ring = h('div');
      // 링 크기 = 서버 채점 반경(정규화 0.14)과 동일한 타원 — 보이는 링 안 = 정답 존
      css(ring, { position: 'absolute', width: (d.zone.r * 2 * 100) + '%', height: (d.zone.r * 2 * 100) + '%',
        border: '3px dashed #FFB8A8', borderRadius: '50%', transform: 'translate(-50%,-50%)',
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
      var dropAt = null, els = {};
      function norm(e) {
        var r = area.getBoundingClientRect();
        return { x: Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)),
                 y: Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)) };
      }
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
          padding: '6px 8px', background: '#fff', border: '2px solid #F0E4D8', borderRadius: '12px' });
        var dragging = false;
        card.addEventListener('pointerdown', function (e) {
          if (answered) return;
          dragging = true; card.setPointerCapture(e.pointerId);
          card.style.cursor = 'grabbing'; card.style.zIndex = '5'; e.preventDefault();
        });
        card.addEventListener('pointermove', function (e) {
          if (!dragging) return;
          var p = norm(e);
          card.style.left = (p.x * 100) + '%'; card.style.top = (p.y * 100) + '%';
        });
        card.addEventListener('pointerup', function (e) {
          if (!dragging || answered) return;
          dragging = false; card.style.cursor = 'grab'; card.style.zIndex = '1';
          var p = norm(e);
          // 원본과 동일: 존 밖 릴리즈 = 무판정, 카드 원위치(손 미끄러짐이 오답이 되지 않게)
          var dx = p.x - d.zone.cx, dy = p.y - d.zone.cy;
          if (Math.sqrt(dx * dx + dy * dy) > d.zone.r) {
            card.style.left = (sx * 100) + '%'; card.style.top = (sy * 100) + '%';
            return;
          }
          dropAt = { item: it.id, x: Math.round(p.x * 1000) / 1000, y: Math.round(p.y * 1000) / 1000 };
          if (footerOn) footerState(true, true);
          else verify(token, dropAt);
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
      return { addZone: addZone, drag: drag };
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

    // ── 입력형(dictation/type_in) — 원본(국어): 받아쓰기는 TTS로 듣고, 높임말은 밑줄
    //    낱말을 보고 타이핑. 제출 문자열 → 서버 trim 정확 일치.
    function renderTyping(d, token) {
      if (d.type === 'dictation') {
        var canSpeak = typeof window.speechSynthesis !== 'undefined';
        var played = false;
        var sp = h('button'); sp.textContent = '🔊 듣기';
        css(sp, { display: 'block', margin: '0 auto 16px', padding: '13px 26px', fontSize: '17px', fontWeight: '800',
          border: 'none', borderRadius: '30px', background: canSpeak ? C : '#D8CBBB', color: '#fff',
          cursor: canSpeak ? 'pointer' : 'not-allowed' });
        sp.onclick = function () {
          if (!canSpeak) return;
          try {
            window.speechSynthesis.cancel();
            var u = new SpeechSynthesisUtterance(d.tts);
            u.lang = 'ko-KR'; u.rate = 0.9;
            window.speechSynthesis.speak(u);
            played = true; sp.textContent = '🔊 다시 듣기';
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
        css(hi, { color: '#E2574C', borderBottom: '3px solid #E2574C', fontWeight: '800' });
        sent.appendChild(hi);
        sent.appendChild(document.createTextNode(d.after || ''));
        css(sent, { fontSize: '17px', fontWeight: '700', color: '#3A3226', lineHeight: '1.8',
          background: '#fff', border: '2px solid #F0E4D8', borderRadius: '14px',
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
        fontFamily: 'inherit', fontSize: '16px', fontWeight: '600', padding: '13px 15px',
        borderRadius: '13px', border: '2px solid #F0E4D8', color: '#3A3226', outline: 'none' });
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
        gap: '4px', maxWidth: '480px', margin: '0 auto 8px' });
      (d.tokens || []).forEach(function (w, i) {
        var word = h('span'); word.textContent = w;
        css(word, { fontSize: '17px', fontWeight: '700', color: '#3A3226' });
        line.appendChild(word);
        if ((d.gaps || []).indexOf(i) !== -1) {
          var g = h('button'); g.textContent = '_';
          css(g, { width: '28px', height: '28px', margin: '0 3px', borderRadius: '50%',
            border: '2px solid #F0E4D8', background: '#fff', color: '#B7A68F',
            fontSize: '15px', fontWeight: '800', cursor: 'pointer', lineHeight: '1', padding: '0' });
          g.onclick = function () {
            if (answered) return;
            picked[i] = !picked[i];
            g.textContent = picked[i] ? '✓' : '_';
            g.style.borderColor = picked[i] ? C : '#F0E4D8';
            g.style.background = picked[i] ? '#FFF0EE' : '#fff';
            g.style.color = picked[i] ? C : '#B7A68F';
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
            g.textContent = '_'; g.style.borderColor = '#F0E4D8'; g.style.background = '#fff'; g.style.color = '#B7A68F'; });
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
      var CELL = 40;
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
            fontSize: '17px', fontWeight: '800', borderRadius: '8px', border: '2px solid #F0E4D8',
            background: '#fff', color: '#3A3226', cursor: 'pointer', padding: '0' });
          if (startNo[key] !== undefined) {
            var no = h('span'); no.textContent = startNo[key];
            css(no, { position: 'absolute', top: '1px', left: '4px', fontSize: '9px', fontWeight: '700', color: '#B7A68F' });
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
          css(tb, { fontFamily: 'inherit', fontSize: '16px', fontWeight: '800', padding: '8px 14px',
            borderRadius: '11px', border: '2px solid #F0E4D8', background: '#fff', color: '#3A3226', cursor: 'pointer' });
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
      css(card, { background: '#fff', border: '2px solid #F0E4D8', borderRadius: '18px', padding: '26px 20px',
        fontSize: '16.5px', fontWeight: '700', color: '#3A3226', lineHeight: '1.7', textAlign: 'center',
        cursor: 'grab', userSelect: 'none', touchAction: 'none', transition: 'transform 0.15s',
        boxShadow: '0 10px 24px -14px rgba(120,90,70,0.35)' });
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
          chosen = null; card.style.transform = 'none'; card.style.borderColor = '#F0E4D8';
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
      function playAll() {
        if (playing || !auds.length) return;
        playing = true; var i = 0;
        function next() {
          if (i >= auds.length) { playing = false; return; }
          var a = auds[i++];
          try {
            a.currentTime = 0;
            a.onended = function () { setTimeout(next, 500); };
            a.onerror = function () { setTimeout(next, 200); }; // 로드 실패 시 다음으로(잠김 방지)
            var pr = a.play();
            // play()는 거부를 프로미스로 반환 — 자동재생 차단/실패 시 잠금 해제(버튼 재시도 가능)
            if (pr && pr.catch) pr.catch(function () { playing = false; });
          } catch (e) { playing = false; }
        }
        next();
      }
      var playBtn = h('button'); playBtn.textContent = '🔊 순서대로 듣기';
      css(playBtn, { display: 'block', margin: '0 auto 16px', padding: footerOn ? '14px 28px' : '10px 20px',
        fontSize: footerOn ? '18px' : '15px', fontWeight: '800', border: 'none', borderRadius: '30px',
        background: C, color: '#fff', cursor: 'pointer' });
      playBtn.onclick = playAll;
      body.appendChild(playBtn);
      setTimeout(playAll, 200);
      var slotWrap = h('div');
      css(slotWrap, { display: 'flex', flexWrap: 'wrap', gap: '8px', justifyContent: 'center', minHeight: '30px',
        padding: '12px', marginBottom: '12px', maxWidth: '440px', margin: '0 auto 12px',
        border: '2px dashed #E3D6C6', borderRadius: '14px', background: '#FFFAF4', alignItems: 'center' });
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
      function face(el, c, up) {
        el.textContent = up ? c.face : '❓';
        el.style.background = up ? '#FFF9F1' : '#FFE9E2';
        el.style.borderColor = matched[c.id] ? OK : (up ? C : '#F0C9BC');
        el.style.fontSize = c.kind === 'image' ? '30px' : '15px';
        if (!up) el.style.fontSize = '24px';
      }
      cards.forEach(function (c) {
        var el = h('button');
        css(el, { aspectRatio: '3 / 4', border: '2px solid #F0C9BC', borderRadius: '12px', cursor: 'pointer',
          fontWeight: '800', color: '#3A3226', padding: '2px', overflowWrap: 'anywhere' });
        el.onclick = function () {
          if (answered || lock || done || matched[c.id]) return;
          if (open.some(function (o) { return o.id === c.id; })) return;
          face(el, c, true); open.push(c);
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

    function render(d) {
      stopSounds();
      body.innerHTML = '';
      // retries는 리셋하지 않는다 — 캡차 오답 재발급을 건너 누적돼야 행동데이터
      // retry_count가 실제 재시도 횟수를 반영한다(통과 시 위젯 세션 종료로 자연 소멸).
      renderedAt = Date.now(); redoCount = 0; traceReset();
      lastType = d.type; answered = false; grading = false; renderSeq += 1;
      lastOptions = []; // 이전 문항 보기가 새 문항 피드백(정답 텍스트 매칭)에 누출되지 않게
      onAnswered = null;
      footerOn = full && product === 'edu';
      if (footerOn) {
        ensureFooter(); footerReset();
        redoBtn.textContent = d.type === 'trace_path' ? '다시 그리기'
          : (d.type === 'dictation' || d.type === 'type_in' || d.type === 'input') ? '다시 쓰기' : '다시 고르기';
        nextBtn.textContent = '다음 문제 →';
      }
      if (footer) footer.style.display = footerOn ? 'flex' : 'none';
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
      css(prompt, footerOn
        ? { fontWeight: '800', fontSize: '21px', color: '#3A3226', marginBottom: '20px', textAlign: 'center' }
        : { fontWeight: '800', fontSize: '15px', color: '#3A3226', marginBottom: '12px' });
      body.appendChild(prompt);

      // figure(문제 위 도형 그림) — 서버 뱅크의 신뢰된 SVG 마크업. 모든 유형 공통.
      if (d.figure) {
        var fig = h('div');
        css(fig, { textAlign: 'center', margin: '0 auto 18px', maxWidth: '100%', overflowX: 'auto' });
        fig.innerHTML = d.figure;
        var fsvg = fig.querySelector('svg');
        if (fsvg) { fsvg.style.maxWidth = '100%'; fsvg.style.height = 'auto'; }
        body.appendChild(fig);
      }
      // 원본 그림 힌트(이모지)·한국어 뜻 — 영어 어순(08)·영문법(09) 등 모든 유형 공통.
      var imgHintEl = null;
      if (d.image) {
        var gim = h('div'); gim.textContent = d.image;
        css(gim, { textAlign: 'center', fontSize: '52px', lineHeight: '1.1', margin: '0 0 10px' });
        body.appendChild(gim);
        imgHintEl = gim;
      }
      if (d.meaning) {
        var gmn = h('div'); gmn.textContent = '뜻: ' + d.meaning;
        css(gmn, { textAlign: 'center', fontSize: '15px', fontWeight: '700', color: '#6B6157', margin: '0 0 14px' });
        body.appendChild(gmn);
      }
      // 상황 지문(생활 5단계 시나리오) — 프롬프트 아래 상황 설명 박스.
      if (d.scenario) {
        var scn = h('div'); scn.textContent = d.scenario;
        css(scn, { textAlign: 'center', fontSize: '16px', fontWeight: '700', color: '#3A3226',
          background: '#FFF6EA', border: '1.5px solid #F0E0C8', borderRadius: '12px',
          padding: '12px 16px', maxWidth: '460px', margin: '0 auto 16px' });
        body.appendChild(scn);
      }

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
        css(b, { textAlign: 'center', padding: '12px 14px', border: '2px solid #F0E4D8', borderRadius: '12px',
          background: '#fff', cursor: 'pointer', fontSize: '15px', fontWeight: '700', color: '#3A3226', lineHeight: '1.3' });
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
        css(grid, { display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: '8px' });
        if (footerOn) css(grid, { maxWidth: '420px', margin: '0 auto', width: '100%' });
        d.cells.forEach(function (c) {
          var cell = h('button'); cell.textContent = c.emoji;
          css(cell, { fontSize: '30px', padding: '14px 0', border: '2px solid #F0E4D8', borderRadius: '12px',
            background: '#fff', cursor: 'pointer' });
          cell.onclick = function () {
            if (answered) return;
            picked[c.id] = !picked[c.id];
            cell.style.borderColor = picked[c.id] ? C : '#F0E4D8';
            cell.style.background = picked[c.id] ? '#FFF0EE' : '#fff';
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
            cellEls.forEach(function (cell) { cell.style.borderColor = '#F0E4D8'; cell.style.background = '#fff'; });
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
            css(chip, { fontSize: '13px', fontWeight: '700', padding: '5px 9px', margin: '3px',
              background: '#fff', border: '1px solid ' + C, borderRadius: '8px', cursor: 'pointer', color: '#3A3226' });
            chip.onclick = function () { if (answered) return; mToggle(o, false); };
            mBoxChips.appendChild(chip);
          });
        }
        function mToggle(o, on) {
          if (answered) return;
          mPicked[o.id] = on == null ? !mPicked[o.id] : on;
          var mb = mBtns[lastOptions.indexOf(o)];
          if (mb) { mb.style.borderColor = mPicked[o.id] ? C : '#F0E4D8'; mb.style.background = mPicked[o.id] ? '#FFF0EE' : '#fff';
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
              ? { textAlign: 'center', padding: '16px 24px', minWidth: '110px', border: '2px solid #F0E4D8',
                  borderRadius: '14px', background: '#fff', cursor: 'pointer', fontSize: '16px', fontWeight: '700', color: '#3A3226' }
              : { textAlign: 'left', padding: '13px 15px', border: '2px solid #F0E4D8', borderRadius: '12px',
                  background: '#fff', cursor: 'pointer', fontSize: '15px', fontWeight: '700', color: '#3A3226' }));
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
            mBtns.forEach(function (mb) { mb.style.borderColor = '#F0E4D8'; mb.style.background = '#fff'; mb.style.display = ''; });
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
        function cLines() {
          while (cSvg.firstChild) cSvg.removeChild(cSvg.firstChild);
          var ob = cOuter.getBoundingClientRect();
          if (!ob.width) return;
          d.left.forEach(function (l, i) {
            var rid = cPairs[l.id]; if (rid == null || !cR[rid]) return;
            var a = cL[l.id].getBoundingClientRect(), b = cR[rid].getBoundingClientRect();
            var ln = document.createElementNS('http://www.w3.org/2000/svg', 'line');
            ln.setAttribute('x1', a.right - ob.left); ln.setAttribute('y1', a.top + a.height / 2 - ob.top);
            ln.setAttribute('x2', b.left - ob.left); ln.setAttribute('y2', b.top + b.height / 2 - ob.top);
            ln.setAttribute('stroke', PAIRC[i % 6]); ln.setAttribute('stroke-width', '3'); ln.setAttribute('stroke-linecap', 'round');
            cSvg.appendChild(ln);
          });
        }
        function cPaint() {
          d.left.forEach(function (l, i) { var e = cL[l.id]; var on = cPairs[l.id] != null;
            e.style.borderColor = cSel === l.id ? C : (on ? PAIRC[i % 6] : '#F0E4D8'); e.style.background = on ? '#FFF6F3' : '#fff'; });
          d.right.forEach(function (r) { var e = cR[r.id]; var ow = Object.keys(cPairs).filter(function (k) { return cPairs[k] === r.id; })[0];
            var ci = ow ? d.left.map(function (l) { return l.id; }).indexOf(ow) : -1;
            e.style.borderColor = ci >= 0 ? PAIRC[ci % 6] : '#F0E4D8'; e.style.background = ci >= 0 ? '#FFF6F3' : '#fff'; });
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
            e.style.borderColor = on ? C : '#F0E4D8'; e.style.background = on ? '#FFF6F3' : '#fff'; }); });
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
        var itRow = h('div'); css(itRow, { display: 'flex', flexWrap: 'wrap', gap: '8px', justifyContent: 'center', marginBottom: '12px' });
        var binRow = h('div'); css(binRow, { display: 'grid', gridTemplateColumns: 'repeat(' + Math.min(d.bins.length, 3) + ',1fr)', gap: '10px', maxWidth: '480px', margin: '0 auto' });
        function sPaint() {
          d.items.forEach(function (it) { var e = sIt[it.id]; var bin = sMap[it.id];
            e.style.display = bin ? 'none' : ''; e.style.borderColor = sSel === it.id ? C : '#F0E4D8'; e.style.background = sSel === it.id ? '#FFF0EE' : '#fff'; });
          d.bins.forEach(function (b) { var box2 = sBinEls[b.id]; box2.chips.innerHTML = '';
            d.items.filter(function (it) { return sMap[it.id] === b.id; }).forEach(function (it) {
              var chip = h('div'); chip.textContent = it.text; css(chip, { fontSize: '13px', fontWeight: '700', padding: '5px 9px', margin: '3px', background: '#fff', border: '1px solid ' + C, borderRadius: '8px', cursor: 'pointer', color: '#3A3226' });
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
        d.bins.forEach(function (b) { var box2 = h('div'); css(box2, { border: '2px dashed #E0D3C4', borderRadius: '12px', padding: '8px', minHeight: '70px', textAlign: 'center', cursor: 'pointer' });
          var lab = h('div'); lab.textContent = b.label; css(lab, { fontSize: '13px', fontWeight: '800', color: '#8A8070', marginBottom: '4px' });
          var chips = h('div'); css(chips, { display: 'flex', flexWrap: 'wrap', justifyContent: 'center' });
          box2.appendChild(lab); box2.appendChild(chips); box2.chips = chips;
          box2.onclick = function () { if (answered || sSel == null) return; sMap[sSel] = b.id; sSel = null; sPaint(); };
          sDnd.addZone(box2, b.id, function (on) { box2.style.borderColor = on ? C : '#E0D3C4'; box2.style.background = on ? '#FFF6F3' : ''; });
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
        css(slotWrap, { display: 'flex', flexWrap: 'wrap', gap: '8px', justifyContent: 'center',
          minHeight: '30px', padding: '14px 12px', marginBottom: '14px', maxWidth: '480px',
          marginLeft: 'auto', marginRight: 'auto', border: '2px dashed #E3D6C6', borderRadius: '14px',
          background: '#FFFAF4', alignItems: 'center' });
        var trayWrap = h('div');
        css(trayWrap, { display: 'flex', flexWrap: 'wrap', gap: '8px', justifyContent: 'center', maxWidth: '480px', margin: '0 auto' });
        function oPaint() {
          slotWrap.textContent = '';
          if (oSeq.length === 0) {
            var ph = h('span'); ph.textContent = '아래 카드를 여기로 끌어다(또는 눌러) 순서대로 배치해요';
            css(ph, { color: '#B7A68F', fontSize: '13px', fontWeight: '700' });
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
            css(s, { padding: '10px 14px', border: '2px solid ' + C, borderRadius: '12px',
              background: '#FFF0EE', cursor: 'pointer', fontSize: '15px', fontWeight: '700', color: '#3A3226' });
            s.onclick = function () { if (answered) return; oSeq.splice(i, 1); oPaint(); };
            slotWrap.appendChild(s);
          });
          trayWrap.textContent = '';
          d.cards.forEach(function (c) {
            if (oSeq.indexOf(c.id) !== -1) return; // 이미 배치된 카드는 트레이에서 숨김
            var e = h('button');
            if (c.img) { var tim = h('img'); tim.src = imgUrl(c.img); css(tim, { display: 'block', width: '52px', height: '52px', objectFit: 'contain', margin: '0 auto 4px', pointerEvents: 'none' }); e.appendChild(tim); e.appendChild(document.createTextNode(c.text)); }
            else e.textContent = c.text;
            css(e, { padding: '12px 16px', border: '2px solid #F0E4D8', borderRadius: '12px',
              background: '#fff', cursor: 'pointer', fontSize: '15px', fontWeight: '700', color: '#3A3226' });
            function oPush() { if (answered || oSeq.length >= need) return; oSeq.push(c.id); oPaint(); }
            oDnd.drag(e, { disabled: function () { return answered || oSeq.length >= need; },
              onDrop: function () { oPush(); }, onTap: oPush });
            trayWrap.appendChild(e);
          });
          var done = oSeq.length === need;
          if (footerOn) footerState(oSeq.length > 0, done);
        }
        oDnd.addZone(slotWrap, '__slot__', function (on) { slotWrap.style.borderColor = on ? C : '#E3D6C6'; slotWrap.style.background = on ? '#FFF3EC' : '#FFFAF4'; });
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
        var board = h('div'); css(board, { position: 'relative', width: '100%', maxWidth: '440px', margin: '0 auto',
          aspectRatio: '1 / 0.9', background: '#F7F1E8', border: '2px solid #EADFce', borderRadius: '14px', overflow: 'hidden' });
        if (d.compass) { ['북 N|top:4px;left:50%;transform:translateX(-50%)', '남 S|bottom:4px;left:50%;transform:translateX(-50%)',
          '동 E|right:6px;top:50%;transform:translateY(-50%)', '서 W|left:6px;top:50%;transform:translateY(-50%)'].forEach(function (s) {
          var parts = s.split('|'); var lab = h('div'); lab.textContent = parts[0]; lab.setAttribute('style', 'position:absolute;font-size:11px;font-weight:800;color:#B7A68F;z-index:1;' + parts[1]); board.appendChild(lab); }); }
        function pPaint() { d.zones.forEach(function (z2) { var base = z2.id === d.reference;
          var sel = z2.id === pSel;
          pEls[z2.id].style.borderColor = sel ? C : (base ? '#8B6BFF' : '#E3D6C6');
          pEls[z2.id].style.background = sel ? '#FFF0EE' : (base ? '#F1ECFF' : '#fff'); }); }
        d.zones.forEach(function (z) { var e = h('button');
          e.textContent = (z.emoji ? z.emoji + ' ' : '') + z.label + (z.id === d.reference ? ' (기준)' : '');
          e.setAttribute('style', 'position:absolute;left:' + z.x + '%;top:' + z.y + '%;width:' + z.w + '%;height:' + z.h + '%;'
            + 'border:2px solid #E3D6C6;border-radius:10px;background:#fff;cursor:pointer;font-size:12px;font-weight:700;color:#3A3226;padding:2px;'
            + (z.moving ? 'animation:ccMove 2.2s ease-in-out infinite alternate;' : ''));
          e.onclick = function () { if (answered) return; pSel = z.id; pPaint(); if (footerOn) footerState(true, true); };
          pDnd.addZone(e, z.id, function (on) { if (z.id === pSel) return;
            e.style.background = on ? '#FFF6F3' : (z.id === d.reference ? '#F1ECFF' : '#fff'); });
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
        function pzPaint() {
          d.slots.forEach(function (sl) { var el = slotEls[sl.id]; var pid = placed[sl.id];
            if (pid) { var pc = d.pieces.filter(function (x) { return x.id === pid; })[0]; crop(el, pc.col, pc.row, pc.flag); el.style.borderColor = C; el.style.borderStyle = 'solid'; }
            else { el.style.backgroundImage = 'none'; el.style.borderColor = '#D8C8B4'; el.style.borderStyle = 'dashed'; } });
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
          var playBtn = h('button'); playBtn.textContent = '🔊 다시 듣기';
          css(playBtn, footerOn
            ? { display: 'block', margin: '0 auto 18px', padding: '14px 28px', fontSize: '18px', fontWeight: '800',
                border: 'none', borderRadius: '30px', background: C, color: '#fff', cursor: 'pointer' }
            : { display: 'block', margin: '0 auto 12px', padding: '10px 20px', fontSize: '15px', fontWeight: '800',
                border: 'none', borderRadius: '24px', background: C, color: '#fff', cursor: 'pointer' });
          // 원본 maxAudioPlays — 어려운 단계(4~5)는 재생 횟수 제한(자동재생 포함)
          var playsLeft = d.plays ? d.plays : Infinity;
          function playCount() {
            playsLeft -= 1;
            if (d.plays) {
              playBtn.textContent = playsLeft > 0 ? ('🔊 다시 듣기 (' + playsLeft + '번 남음)') : '🔊 다 들었어요';
              if (playsLeft <= 0) { playBtn.disabled = true; playBtn.style.opacity = '0.5'; playBtn.style.cursor = 'default'; }
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
            imgHintEl.style.borderColor = on ? C : '#E3D6C6'; imgHintEl.style.background = on ? '#FFF6F3' : ''; });
        }
        function chooseOpt(o, b) {
          if (answered) return;
          chosen = o.id;
          if (gGap) { gGap.textContent = (o.emoji ? o.emoji + ' ' : '') + o.text; gGap.style.color = C; }
          optBtns.forEach(function (x) { x.style.borderColor = '#F0E4D8'; x.style.background = '#fff'; });
          b.style.borderColor = C; b.style.background = '#FFF0EE';
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
            ? { textAlign: 'center', padding: '16px 24px', minWidth: '110px', border: '2px solid #F0E4D8',
                borderRadius: '14px', background: '#fff', cursor: 'pointer', fontSize: '16px', fontWeight: '700', color: '#3A3226' }
            : { textAlign: 'left', padding: '13px 15px', border: '2px solid #F0E4D8', borderRadius: '12px',
                background: '#fff', cursor: 'pointer', fontSize: '15px', fontWeight: '700', color: '#3A3226' });
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
            optBtns.forEach(function (x) { x.style.borderColor = '#F0E4D8'; x.style.background = '#fff'; });
            if (gGap) { gGap.textContent = '____'; gGap.style.color = '#B7A68F'; }
            footerState(false, false);
          };
          pendingSubmit = function () { if (chosen != null) verify(token, chosen); };
        }
        if (d.hint) { var hint = h('div'); hint.textContent = '💡 ' + d.hint; css(hint, { marginTop: '10px', fontSize: '12px', color: '#8A8070', textAlign: footerOn ? 'center' : 'left' }); body.appendChild(hint); }
      }
    }

    function verify(token, answer, correctText) {
      if (answered || grading) return; // 채점 후/채점 중 재제출 방지
      answered = true;
      grading = true;
      var mySeq = renderSeq; // 응답 도착 시 문항이 이미 바뀌었으면 무시(스테일 응답 가드)
      status.textContent = '확인 중…';
      var rect = box.getBoundingClientRect();
      var behavior = {
        solve_time_ms: Date.now() - renderedAt,
        retry_count: retries + redoCount, // 캡차 재시도 + 다시 고르기/그리기 횟수
        input_type: inputType || 'unknown', // mouse|touch|pen
        trace: trace.slice(),
        box: { w: Math.round(rect.width), h: Math.round(rect.height) },
      };
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
