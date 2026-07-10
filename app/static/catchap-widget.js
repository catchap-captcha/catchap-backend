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
        ? function () { box.dispatchEvent(new CustomEvent('catchap:finished', { bubbles: true })); }
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
      var user = pl([], C, '3.5');
      svg.appendChild(pl(d.path, '#D9CDBE', '4', '1.5 5.5'));
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

      var drawing = false, pts = [];
      function norm(e) {
        var r = svg.getBoundingClientRect();
        return [Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)),
                Math.max(0, Math.min(1, (e.clientY - r.top) / r.height))];
      }
      function draw() {
        user.setAttribute('points', pts.map(function (p) { return (p[0] * 100) + ',' + (p[1] * 100); }).join(' '));
        var enough = pts.length >= 8;
        if (footerOn) { footerState(pts.length > 0, enough); return; }
        submit.disabled = !enough; submit.style.opacity = enough ? '1' : '0.5';
      }
      svg.addEventListener('pointerdown', function (e) {
        if (answered) return;
        drawing = true; svg.setPointerCapture(e.pointerId); pts.push(norm(e)); draw(); e.preventDefault();
      });
      svg.addEventListener('pointermove', function (e) {
        if (!drawing || answered || pts.length >= 600) return;
        var p = norm(e), last = pts[pts.length - 1];
        if (last && Math.abs(p[0] - last[0]) < 0.005 && Math.abs(p[1] - last[1]) < 0.005) return;
        pts.push(p); draw();
      });
      svg.addEventListener('pointerup', function () { drawing = false; });
      function doRedo() { if (answered) return; pts = []; draw(); }
      function doSubmit() {
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
      var st = mk('text', { x: d.start.x * 100, y: d.start.y * 100 + 3, 'text-anchor': 'middle', 'font-size': '9' }); st.textContent = d.character || '🧒'; svg.appendChild(st);
      body.appendChild(svg);
      if (!footerOn) { var row = h('div'); css(row, { display: 'flex', gap: '8px' });
        var redo = h('button'); redo.textContent = '다시 그리기'; css(redo, btnStyle('#eee', '#555'));
        var submit = h('button'); submit.textContent = '도착했어요!'; css(submit, btnStyle(C, '#fff')); submit.disabled = true; submit.style.opacity = '0.5';
        row.appendChild(redo); row.appendChild(submit); body.appendChild(row);
        var refs = { redo: redo, submit: submit };
      }
      if (d.hint) hintLine(d.hint);
      var drawing = false, pts = [];
      function normp(e) { var r = svg.getBoundingClientRect(); return [Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)), Math.max(0, Math.min(1, (e.clientY - r.top) / r.height))]; }
      function draw() {
        user.setAttribute('points', pts.map(function (p) { return (p[0] * 100) + ',' + (p[1] * 100); }).join(' '));
        var enough = pts.length >= 8;
        if (footerOn) { footerState(pts.length > 0, enough); return; }
        refs.submit.disabled = !enough; refs.submit.style.opacity = enough ? '1' : '0.5';
      }
      svg.addEventListener('pointerdown', function (e) { if (answered) return; drawing = true; svg.setPointerCapture(e.pointerId); pts = [normp(e)]; draw(); e.preventDefault(); });
      svg.addEventListener('pointermove', function (e) { if (!drawing || answered || pts.length >= 600) return; var p = normp(e), last = pts[pts.length - 1]; if (last && Math.abs(p[0] - last[0]) < 0.005 && Math.abs(p[1] - last[1]) < 0.005) return; pts.push(p); draw(); });
      svg.addEventListener('pointerup', function () { drawing = false; });
      function doRedo() { if (answered) return; pts = []; draw(); }
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
      css(ring, { position: 'absolute', width: '92px', height: '92px', border: '3px dashed #FFB8A8',
        borderRadius: '50%', transform: 'translate(-50%,-50%)', pointerEvents: 'none',
        left: (d.zone.cx * 100) + '%', top: (d.zone.cy * 100) + '%' });
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

    function render(d) {
      body.innerHTML = '';
      // retries는 리셋하지 않는다 — 캡차 오답 재발급을 건너 누적돼야 행동데이터
      // retry_count가 실제 재시도 횟수를 반영한다(통과 시 위젯 세션 종료로 자연 소멸).
      renderedAt = Date.now(); redoCount = 0; traceReset();
      lastType = d.type; answered = false; grading = false; renderSeq += 1;
      lastOptions = []; // 이전 문항 보기가 새 문항 피드백(정답 텍스트 매칭)에 누출되지 않게
      footerOn = full && product === 'edu';
      if (footerOn) {
        ensureFooter(); footerReset();
        redoBtn.textContent = d.type === 'trace_path' ? '다시 그리기'
          : (d.type === 'dictation' || d.type === 'type_in' || d.type === 'input') ? '다시 쓰기' : '다시 고르기';
        nextBtn.textContent = '다음 문제 →';
      }
      if (footer) footer.style.display = footerOn ? 'flex' : 'none';
      var token = d.challenge_token;
      var prompt = h('div'); prompt.textContent = d.prompt;
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

      // 조작형 공용: 표준 보기 셀 버튼
      function optCell(text) {
        var b = h('button'); b.textContent = text;
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
        // 복수선택(교육형) — 보기 토글 후 확인 제출, 서버가 집합 비교로 채점
        lastOptions = d.options || [];
        var mPicked = {};
        var mBtns = [];
        var mOpts = h('div');
        css(mOpts, footerOn
          ? { display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: '12px' }
          : { display: 'grid', gap: '8px' });
        lastOptions.forEach(function (o) {
          var mb = h('button');
          setOpt(mb, o);
          css(mb, footerOn
            ? { textAlign: 'center', padding: '16px 24px', minWidth: '110px', border: '2px solid #F0E4D8',
                borderRadius: '14px', background: '#fff', cursor: 'pointer', fontSize: '16px', fontWeight: '700', color: '#3A3226' }
            : { textAlign: 'left', padding: '13px 15px', border: '2px solid #F0E4D8', borderRadius: '12px',
                background: '#fff', cursor: 'pointer', fontSize: '15px', fontWeight: '700', color: '#3A3226' });
          mb.onclick = function () {
            if (answered) return;
            mPicked[o.id] = !mPicked[o.id];
            mb.style.borderColor = mPicked[o.id] ? C : '#F0E4D8';
            mb.style.background = mPicked[o.id] ? '#FFF0EE' : '#fff';
            if (footerOn) {
              var mn = Object.keys(mPicked).filter(function (k) { return mPicked[k]; }).length;
              footerState(mn > 0, mn > 0);
            }
          };
          mBtns.push(mb);
          mOpts.appendChild(mb);
        });
        body.appendChild(mOpts);
        function submitMulti() {
          var mAns = Object.keys(mPicked).filter(function (k) { return mPicked[k]; });
          if (!mAns.length) return; // 아무것도 안 고르고 제출 방지
          verify(token, mAns);
        }
        if (footerOn) {
          pendingRedo = function () {
            mPicked = {};
            mBtns.forEach(function (mb) { mb.style.borderColor = '#F0E4D8'; mb.style.background = '#fff'; });
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
        // 연결: 왼쪽 항목 선택 → 오른쪽 항목 탭으로 짝짓기. 제출 {leftId:rightId}
        var cPairs = {}, cSel = null, cL = {}, cR = {};
        var cWrap = h('div'); css(cWrap, { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px', maxWidth: '480px', margin: '0 auto', width: '100%' });
        var colL = h('div'), colR = h('div'); css(colL, { display: 'grid', gap: '8px' }); css(colR, { display: 'grid', gap: '8px' });
        function cPaint() {
          d.left.forEach(function (l, i) { var e = cL[l.id]; var on = cPairs[l.id] != null;
            e.style.borderColor = cSel === l.id ? C : (on ? PAIRC[i % 6] : '#F0E4D8'); e.style.background = on ? '#FFF6F3' : '#fff'; });
          d.right.forEach(function (r) { var e = cR[r.id]; var ow = Object.keys(cPairs).filter(function (k) { return cPairs[k] === r.id; })[0];
            var ci = ow ? d.left.map(function (l) { return l.id; }).indexOf(ow) : -1;
            e.style.borderColor = ci >= 0 ? PAIRC[ci % 6] : '#F0E4D8'; e.style.background = ci >= 0 ? '#FFF6F3' : '#fff'; });
          var done = d.left.length && Object.keys(cPairs).length === d.left.length;
          if (footerOn) footerState(Object.keys(cPairs).length > 0 || cSel != null, done);
        }
        d.left.forEach(function (l) { var e = optCell(l.text); e.onclick = function () { if (answered) return; cSel = cSel === l.id ? null : l.id; cPaint(); }; cL[l.id] = e; colL.appendChild(e); });
        d.right.forEach(function (r) { var e = optCell(r.text); e.onclick = function () { if (answered || cSel == null) return;
          Object.keys(cPairs).forEach(function (k) { if (cPairs[k] === r.id) delete cPairs[k]; }); cPairs[cSel] = r.id; cSel = null; cPaint(); }; cR[r.id] = e; colR.appendChild(e); });
        cWrap.appendChild(colL); cWrap.appendChild(colR); body.appendChild(cWrap);
        function subC() { if (Object.keys(cPairs).length === d.left.length) verify(token, cPairs); }
        if (footerOn) { pendingRedo = function () { cPairs = {}; cSel = null; cPaint(); }; pendingSubmit = subC; }
        else { var cbtn = h('button'); cbtn.textContent = '확인'; css(cbtn, btnStyle(C, '#fff')); cbtn.onclick = subC; body.appendChild(cbtn); }
        if (d.hint) hintLine(d.hint);
      } else if (d.type === 'sort') {
        // 분류: 항목 선택 → 바구니 탭으로 담기. 제출 {itemId:binId}
        var sMap = {}, sSel = null, sIt = {}, sBinEls = {};
        var itRow = h('div'); css(itRow, { display: 'flex', flexWrap: 'wrap', gap: '8px', justifyContent: 'center', marginBottom: '12px' });
        var binRow = h('div'); css(binRow, { display: 'grid', gridTemplateColumns: 'repeat(' + Math.min(d.bins.length, 3) + ',1fr)', gap: '10px', maxWidth: '480px', margin: '0 auto' });
        function sPaint() {
          d.items.forEach(function (it) { var e = sIt[it.id]; var bin = sMap[it.id];
            e.style.display = bin ? 'none' : ''; e.style.borderColor = sSel === it.id ? C : '#F0E4D8'; e.style.background = sSel === it.id ? '#FFF0EE' : '#fff'; });
          d.bins.forEach(function (b) { var box2 = sBinEls[b.id]; box2.chips.innerHTML = '';
            d.items.filter(function (it) { return sMap[it.id] === b.id; }).forEach(function (it) {
              var chip = h('div'); chip.textContent = it.text; css(chip, { fontSize: '13px', fontWeight: '700', padding: '5px 9px', margin: '3px', background: '#fff', border: '1px solid ' + C, borderRadius: '8px', cursor: 'pointer', color: '#3A3226' });
              chip.onclick = function () { if (answered) return; delete sMap[it.id]; sPaint(); }; box2.chips.appendChild(chip); }); });
          var done = Object.keys(sMap).length === d.items.length;
          if (footerOn) footerState(Object.keys(sMap).length > 0 || sSel != null, done);
        }
        d.items.forEach(function (it) { var e = optCell(it.text); e.onclick = function () { if (answered) return; sSel = sSel === it.id ? null : it.id; sPaint(); }; sIt[it.id] = e; itRow.appendChild(e); });
        d.bins.forEach(function (b) { var box2 = h('div'); css(box2, { border: '2px dashed #E0D3C4', borderRadius: '12px', padding: '8px', minHeight: '70px', textAlign: 'center', cursor: 'pointer' });
          var lab = h('div'); lab.textContent = b.label; css(lab, { fontSize: '13px', fontWeight: '800', color: '#8A8070', marginBottom: '4px' });
          var chips = h('div'); css(chips, { display: 'flex', flexWrap: 'wrap', justifyContent: 'center' });
          box2.appendChild(lab); box2.appendChild(chips); box2.chips = chips;
          box2.onclick = function () { if (answered || sSel == null) return; sMap[sSel] = b.id; sSel = null; sPaint(); };
          sBinEls[b.id] = box2; binRow.appendChild(box2); });
        body.appendChild(itRow); body.appendChild(binRow);
        function subS() { if (Object.keys(sMap).length === d.items.length) verify(token, sMap); }
        if (footerOn) { pendingRedo = function () { sMap = {}; sSel = null; sPaint(); }; pendingSubmit = subS; }
        else { var sbtn = h('button'); sbtn.textContent = '확인'; css(sbtn, btnStyle(C, '#fff')); sbtn.onclick = subS; body.appendChild(sbtn); }
        if (d.hint) hintLine(d.hint);
      } else if (d.type === 'order') {
        // 순서(원본 방식): 아래 카드를 순서대로 누르면 위 칸에 하나씩 '배치'된다. 채운 칸을
        // 다시 누르면 그 카드를 빼고 뒤를 당긴다. 전부 채워야 제출([cardId,...] 칸 순서).
        var oSeq = [];
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
            var ph = h('span'); ph.textContent = '아래 카드를 순서대로 눌러 배치해요';
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
            s.appendChild(badge); s.appendChild(document.createTextNode(c.text));
            css(s, { padding: '10px 14px', border: '2px solid ' + C, borderRadius: '12px',
              background: '#FFF0EE', cursor: 'pointer', fontSize: '15px', fontWeight: '700', color: '#3A3226' });
            s.onclick = function () { if (answered) return; oSeq.splice(i, 1); oPaint(); };
            slotWrap.appendChild(s);
          });
          trayWrap.textContent = '';
          d.cards.forEach(function (c) {
            if (oSeq.indexOf(c.id) !== -1) return; // 이미 배치된 카드는 트레이에서 숨김
            var e = h('button'); e.textContent = c.text;
            css(e, { padding: '12px 16px', border: '2px solid #F0E4D8', borderRadius: '12px',
              background: '#fff', cursor: 'pointer', fontSize: '15px', fontWeight: '700', color: '#3A3226' });
            e.onclick = function () { if (answered) return; oSeq.push(c.id); oPaint(); };
            trayWrap.appendChild(e);
          });
          var done = oSeq.length === d.cards.length;
          if (footerOn) footerState(oSeq.length > 0, done);
        }
        body.appendChild(slotWrap); body.appendChild(trayWrap);
        oPaint();
        function subO() { if (oSeq.length === d.cards.length) verify(token, oSeq.slice()); }
        if (footerOn) { pendingRedo = function () { oSeq = []; oPaint(); }; pendingSubmit = subO; }
        else { var obtn = h('button'); obtn.textContent = '확인'; css(obtn, btnStyle(C, '#fff')); obtn.onclick = subO; body.appendChild(obtn); }
        if (d.hint) hintLine(d.hint);
      } else if (d.type === 'place') {
        // 위치: 지도/장면 위 존을 탭해 선택. 제출 zoneId
        var pSel = null, pEls = {};
        var board = h('div'); css(board, { position: 'relative', width: '100%', maxWidth: '440px', margin: '0 auto',
          aspectRatio: '1 / 0.9', background: '#F7F1E8', border: '2px solid #EADFce', borderRadius: '14px', overflow: 'hidden' });
        if (d.compass) { ['북 N|top:4px;left:50%;transform:translateX(-50%)', '남 S|bottom:4px;left:50%;transform:translateX(-50%)',
          '동 E|right:6px;top:50%;transform:translateY(-50%)', '서 W|left:6px;top:50%;transform:translateY(-50%)'].forEach(function (s) {
          var parts = s.split('|'); var lab = h('div'); lab.textContent = parts[0]; lab.setAttribute('style', 'position:absolute;font-size:11px;font-weight:800;color:#B7A68F;' + parts[1]); board.appendChild(lab); }); }
        d.zones.forEach(function (z) { var e = h('button'); e.textContent = z.label;
          e.setAttribute('style', 'position:absolute;left:' + z.x + '%;top:' + z.y + '%;width:' + z.w + '%;height:' + z.h + '%;'
            + 'border:2px solid #E3D6C6;border-radius:10px;background:#fff;cursor:pointer;font-size:12px;font-weight:700;color:#3A3226;padding:2px;');
          e.onclick = function () { if (answered) return; pSel = z.id; d.zones.forEach(function (z2) { pEls[z2.id].style.borderColor = z2.id === z.id ? C : '#E3D6C6'; pEls[z2.id].style.background = z2.id === z.id ? '#FFF0EE' : '#fff'; });
            if (footerOn) footerState(true, true); };
          pEls[z.id] = e; board.appendChild(e); });
        body.appendChild(board);
        function subP() { if (pSel != null) verify(token, pSel); }
        if (footerOn) { pendingRedo = function () { pSel = null; d.zones.forEach(function (z) { pEls[z.id].style.borderColor = '#E3D6C6'; pEls[z.id].style.background = '#fff'; }); footerState(false, false); }; pendingSubmit = subP; }
        else { var pbtn = h('button'); pbtn.textContent = '확인'; css(pbtn, btnStyle(C, '#fff')); pbtn.onclick = subP; body.appendChild(pbtn); }
        if (d.hint) hintLine(d.hint);
      } else if (d.type === 'puzzle') {
        // 국기 완성 — 조각(국기 크롭)을 그리드 슬롯에 배치. 제출 {slotId:pieceId}, 서버 match 채점.
        var GW = 180, GH = 120, cw = GW / d.cols, chh = GH / d.rows;
        var flagUrl = base + '/captcha/v1/flag/' + d.flag;
        function crop(el, col, row) {
          el.style.backgroundImage = "url('" + flagUrl + "')";
          el.style.backgroundSize = GW + 'px ' + GH + 'px';
          el.style.backgroundPosition = '-' + (col * cw) + 'px -' + (row * chh) + 'px';
          el.style.backgroundRepeat = 'no-repeat';
        }
        var placed = {}, zSel = null, pieceEls = {}, slotEls = {};
        var gridBox = h('div');
        css(gridBox, { display: 'grid', gridTemplateColumns: 'repeat(' + d.cols + ',' + cw + 'px)', gridTemplateRows: 'repeat(' + d.rows + ',' + chh + 'px)',
          width: GW + 'px', margin: '0 auto 16px', border: '2px solid #C9B79E', borderRadius: '4px', overflow: 'hidden', background: '#faf6ef' });
        d.slots.forEach(function (sl) {
          var slot = h('div'); css(slot, { border: '1px dashed #D8C8B4', cursor: 'pointer', backgroundRepeat: 'no-repeat' });
          slot.onclick = function () { if (answered || zSel == null) return; Object.keys(placed).forEach(function (k) { if (placed[k] === zSel) delete placed[k]; }); placed[sl.id] = zSel; zSel = null; pzPaint(); };
          slotEls[sl.id] = slot; gridBox.appendChild(slot);
        });
        body.appendChild(gridBox);
        var tray = h('div'); css(tray, { display: 'flex', flexWrap: 'wrap', gap: '8px', justifyContent: 'center' });
        d.pieces.forEach(function (p) {
          var pc = h('button'); css(pc, { width: cw + 'px', height: chh + 'px', border: '2px solid #F0E4D8', borderRadius: '6px', cursor: 'pointer', padding: '0' });
          crop(pc, p.col, p.row);
          pc.onclick = function () { if (answered) return; zSel = zSel === p.id ? null : p.id; pzPaint(); };
          pieceEls[p.id] = pc; tray.appendChild(pc);
        });
        body.appendChild(tray);
        function pzPaint() {
          d.slots.forEach(function (sl) { var el = slotEls[sl.id]; var pid = placed[sl.id];
            if (pid) { var pc = d.pieces.filter(function (x) { return x.id === pid; })[0]; crop(el, pc.col, pc.row); el.style.borderColor = C; el.style.borderStyle = 'solid'; }
            else { el.style.backgroundImage = 'none'; el.style.borderColor = '#D8C8B4'; el.style.borderStyle = 'dashed'; } });
          d.pieces.forEach(function (p) { var el = pieceEls[p.id]; var used = Object.keys(placed).some(function (k) { return placed[k] === p.id; });
            el.style.opacity = used ? '0.25' : '1'; el.style.borderColor = zSel === p.id ? C : '#F0E4D8'; });
          var done = Object.keys(placed).length === d.slots.length;
          if (footerOn) footerState(Object.keys(placed).length > 0 || zSel != null, done);
        }
        function subPz() { if (Object.keys(placed).length === d.slots.length) verify(token, placed); }
        if (footerOn) { pendingRedo = function () { placed = {}; zSel = null; pzPaint(); }; pendingSubmit = subPz; }
        else { var pzb = h('button'); pzb.textContent = '확인'; css(pzb, btnStyle(C, '#fff')); pzb.onclick = subPz; body.appendChild(pzb); }
        if (d.hint) hintLine(d.hint);
      } else if (d.type === 'drag_pick') {
        // 원본 카드 드래그(과학·수학) — 피드백에 카드 라벨을 쓰도록 lastOptions 매핑
        lastOptions = (d.items || []).map(function (it) { return { id: it.id, text: it.label || it.e }; });
        renderDragPick(d, token);
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
          playBtn.onclick = function () { try { au.currentTime = 0; au.play(); } catch (e) {} };
          body.appendChild(au);
          body.appendChild(playBtn);
          setTimeout(function () { try { au.play(); } catch (e) {} }, 150); // 자동재생 시도(막히면 버튼으로)
        }
        var chosen = null;
        var optBtns = [];
        var opts = h('div');
        css(opts, footerOn
          ? { display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: '12px' }
          : { display: 'grid', gap: '8px' });
        d.options.forEach(function (o) {
          var b = h('button');
          setOpt(b, o);
          css(b, footerOn
            ? { textAlign: 'center', padding: '16px 24px', minWidth: '110px', border: '2px solid #F0E4D8',
                borderRadius: '14px', background: '#fff', cursor: 'pointer', fontSize: '16px', fontWeight: '700', color: '#3A3226' }
            : { textAlign: 'left', padding: '13px 15px', border: '2px solid #F0E4D8', borderRadius: '12px',
                background: '#fff', cursor: 'pointer', fontSize: '15px', fontWeight: '700', color: '#3A3226' });
          b.onclick = function () {
            if (!footerOn) { verify(token, o.id); return; }
            if (answered) return;
            chosen = o.id;
            optBtns.forEach(function (x) { x.style.borderColor = '#F0E4D8'; x.style.background = '#fff'; });
            b.style.borderColor = C; b.style.background = '#FFF0EE';
            footerState(true, true);
          };
          optBtns.push(b);
          opts.appendChild(b);
        });
        body.appendChild(opts);
        if (footerOn) {
          pendingRedo = function () {
            chosen = null;
            optBtns.forEach(function (x) { x.style.borderColor = '#F0E4D8'; x.style.background = '#fff'; });
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
