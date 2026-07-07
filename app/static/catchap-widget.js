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

  function api(base, path, key, body) {
    return fetch(base.replace(/\/$/, '') + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Site-Key': key },
      body: body ? JSON.stringify(body) : undefined,
    }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, status: r.status, data: d }; }); });
  }

  function mount(box) {
    var key = box.getAttribute('data-site-key');
    var base = box.getAttribute('data-api') || '/api/v1';
    // data-subject: 교육형 키로 과목별 챌린지를 요청할 때(우리 앱 과목별 게임화면 등)
    var subject = box.getAttribute('data-subject') || '';
    if (!key) { box.textContent = 'CatChap: data-site-key 가 필요합니다.'; return; }

    // data-size="full" → 컨테이너 꽉 채움(앱 게임 화면용), 기본은 420px 컴팩트(외부 임베드용)
    var full = box.getAttribute('data-size') === 'full';
    css(box, {
      display: 'block', maxWidth: full ? '100%' : '420px', width: '100%',
      border: full ? 'none' : '1px solid #F0E4D8', borderRadius: '16px',
      padding: full ? '4px' : '18px', fontFamily: "'Pretendard','Malgun Gothic',sans-serif",
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
    var hidden = h('input'); hidden.type = 'hidden'; hidden.name = 'catchap-token';
    box.innerHTML = ''; box.appendChild(head); box.appendChild(body); box.appendChild(hidden);
    var product = 'captcha', renderedAt = 0, retries = 0, solvedCount = 0;

    // ── 포인터 궤적 캡처 — 아이/어른의 움직임 차이(속도·경로·멈춤)가 행동 판정 모델의 재료.
    // 위젯 영역 기준 0~1 정규화 좌표를 [t,x,y]로 샘플링(16ms 스로틀, 최대 1500점).
    var trace = [], traceStart = 0, traceLastT = 0, TRACE_MAX = 1500;
    function traceReset() { trace = []; traceStart = Date.now(); traceLastT = -1; }
    function tracePoint(e, force) {
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

    function fail(msg) { body.innerHTML = ''; var p = h('div'); p.textContent = msg || '문제를 불러오지 못했어요.'; css(p, { color: '#C25', fontSize: '13px' }); body.appendChild(p); refreshBtn(); }
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
      var next = h('button'); next.textContent = '다음 문제 →'; css(next, btnStyle(C, '#fff'));
      next.onclick = load; body.appendChild(next);
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
      item.addEventListener('pointerup', function (e) {
        if (!dragging || answered) return;
        dragging = false; item.style.cursor = 'grab';
        var p = norm(e);
        verify(token, { x: Math.round(p.x * 1000) / 1000, y: Math.round(p.y * 1000) / 1000 });
      });
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

      var row = h('div'); css(row, { display: 'flex', gap: '8px' });
      var redo = h('button'); redo.textContent = '다시 그리기'; css(redo, btnStyle('#eee', '#555'));
      var submit = h('button'); submit.textContent = '다 그렸어요!'; css(submit, btnStyle(C, '#fff'));
      submit.disabled = true; submit.style.opacity = '0.5';
      row.appendChild(redo); row.appendChild(submit);
      body.appendChild(row);
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
      redo.onclick = function () { if (answered) return; pts = []; draw(); };
      submit.onclick = function () {
        if (answered || pts.length < 8) return;
        verify(token, pts.map(function (p) { return [Math.round(p[0] * 1e4) / 1e4, Math.round(p[1] * 1e4) / 1e4]; }));
      };
    }

    function render(d) {
      body.innerHTML = '';
      renderedAt = Date.now(); retries = 0; traceReset();
      lastType = d.type; answered = false;
      var token = d.challenge_token;
      var prompt = h('div'); prompt.textContent = d.prompt;
      css(prompt, { fontWeight: '800', fontSize: '15px', color: '#3A3226', marginBottom: '12px' });
      body.appendChild(prompt);

      if (d.type === 'drag_drop') {
        renderDrag(d, token);
      } else if (d.type === 'trace_path') {
        renderTrace(d, token);
      } else if (d.type === 'image_select') {
        var picked = {};
        var grid = h('div');
        css(grid, { display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: '8px' });
        d.cells.forEach(function (c) {
          var cell = h('button'); cell.textContent = c.emoji;
          css(cell, { fontSize: '30px', padding: '14px 0', border: '2px solid #F0E4D8', borderRadius: '12px',
            background: '#fff', cursor: 'pointer' });
          cell.onclick = function () {
            picked[c.id] = !picked[c.id];
            cell.style.borderColor = picked[c.id] ? C : '#F0E4D8';
            cell.style.background = picked[c.id] ? '#FFF0EE' : '#fff';
          };
          grid.appendChild(cell);
        });
        body.appendChild(grid);
        var submit = h('button'); submit.textContent = '확인'; css(submit, btnStyle(C, '#fff'));
        submit.onclick = function () {
          var ans = Object.keys(picked).filter(function (k) { return picked[k]; });
          verify(token, ans);
        };
        body.appendChild(submit);
      } else if (d.type === 'multi') {
        // 복수선택(교육형) — 보기 토글 후 확인 제출, 서버가 집합 비교로 채점
        lastOptions = d.options || [];
        var mPicked = {};
        var mOpts = h('div'); css(mOpts, { display: 'grid', gap: '8px' });
        lastOptions.forEach(function (o) {
          var mb = h('button');
          mb.textContent = (o.emoji ? o.emoji + '  ' : '') + o.text;
          css(mb, { textAlign: 'left', padding: '13px 15px', border: '2px solid #F0E4D8', borderRadius: '12px',
            background: '#fff', cursor: 'pointer', fontSize: '15px', fontWeight: '700', color: '#3A3226' });
          mb.onclick = function () {
            mPicked[o.id] = !mPicked[o.id];
            mb.style.borderColor = mPicked[o.id] ? C : '#F0E4D8';
            mb.style.background = mPicked[o.id] ? '#FFF0EE' : '#fff';
          };
          mOpts.appendChild(mb);
        });
        body.appendChild(mOpts);
        var mSubmit = h('button'); mSubmit.textContent = '확인'; css(mSubmit, btnStyle(C, '#fff'));
        mSubmit.onclick = function () {
          var mAns = Object.keys(mPicked).filter(function (k) { return mPicked[k]; });
          if (!mAns.length) return; // 아무것도 안 고르고 제출 방지
          verify(token, mAns);
        };
        body.appendChild(mSubmit);
        if (d.hint) hintLine(d.hint);
      } else {
        // single / arithmetic — 보기 중 하나 선택
        lastOptions = d.options || [];
        var opts = h('div'); css(opts, { display: 'grid', gap: '8px' });
        d.options.forEach(function (o) {
          var b = h('button');
          b.textContent = (o.emoji ? o.emoji + '  ' : '') + o.text;
          css(b, { textAlign: 'left', padding: '13px 15px', border: '2px solid #F0E4D8', borderRadius: '12px',
            background: '#fff', cursor: 'pointer', fontSize: '15px', fontWeight: '700', color: '#3A3226' });
          b.onclick = function () { verify(token, o.id); };  // eslint-disable-line
          opts.appendChild(b);
        });
        body.appendChild(opts);
        if (d.hint) { var hint = h('div'); hint.textContent = '💡 ' + d.hint; css(hint, { marginTop: '10px', fontSize: '12px', color: '#8A8070' }); body.appendChild(hint); }
      }
    }

    function verify(token, answer, correctText) {
      if (answered) return; // 채점 후 재제출 방지 (드래그/그리기 영역은 리스너가 남아 있음)
      answered = true;
      status.textContent = '확인 중…';
      var rect = box.getBoundingClientRect();
      var behavior = {
        solve_time_ms: Date.now() - renderedAt,
        retry_count: retries,
        trace: trace.slice(),
        box: { w: Math.round(rect.width), h: Math.round(rect.height) },
      };
      api(base, '/captcha/v1/verify', key, { challenge_token: token, answer: answer, behavior: behavior }).then(function (r) {
        if (product === 'edu') {
          // 교육형: 통과 게이트가 아니라 학습 피드백 + 행동데이터 수집. 계속 다음 문제로.
          solvedCount += r.data && r.data.success ? 1 : 0;
          // 정답 여부와 무관하게 진행 토큰 채움(임베드 폼이 학습 완료를 알 수 있게)
          hidden.value = 'edu:' + solvedCount;
          [].forEach.call(body.querySelectorAll('button'), function (b) { b.disabled = true; });
          eduFeedback(r.data || {}, correctText);
        } else if (r.ok && r.data.success) {
          solved(r.data.verdict_token);
        } else {
          retries += 1; status.textContent = '다시 해볼까요'; status.style.color = C; load();
        }
      }).catch(function () { fail('네트워크 오류'); });
    }

    function load() {
      status.textContent = '불러오는 중…'; status.style.color = '#B0A79B';
      body.innerHTML = '';
      var path = '/captcha/v1/challenge' + (subject ? '?subject=' + encodeURIComponent(subject) : '');
      api(base, path, key).then(function (r) {
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
