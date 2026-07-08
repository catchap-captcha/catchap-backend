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
        redoBtn.textContent = d.type === 'trace_path' ? '다시 그리기' : '다시 고르기';
        nextBtn.textContent = '다음 문제 →';
      }
      if (footer) footer.style.display = footerOn ? 'flex' : 'none';
      var token = d.challenge_token;
      var prompt = h('div'); prompt.textContent = d.prompt;
      css(prompt, footerOn
        ? { fontWeight: '800', fontSize: '21px', color: '#3A3226', marginBottom: '20px', textAlign: 'center' }
        : { fontWeight: '800', fontSize: '15px', color: '#3A3226', marginBottom: '12px' });
      body.appendChild(prompt);

      if (d.type === 'drag_drop') {
        renderDrag(d, token);
      } else if (d.type === 'trace_path') {
        renderTrace(d, token);
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
          mb.textContent = (o.emoji ? o.emoji + '  ' : '') + o.text;
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
      } else {
        // single / arithmetic — 보기 중 하나 선택
        // 풋터 모드: 클릭은 '선택'만(테두리 강조), 제출은 풋터의 다음 문제 버튼이 담당
        lastOptions = d.options || [];
        var chosen = null;
        var optBtns = [];
        var opts = h('div');
        css(opts, footerOn
          ? { display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: '12px' }
          : { display: 'grid', gap: '8px' });
        d.options.forEach(function (o) {
          var b = h('button');
          b.textContent = (o.emoji ? o.emoji + '  ' : '') + o.text;
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
