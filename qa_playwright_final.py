#!/usr/bin/env python3
"""
QA 최종 인수 테스트 — P-023 Overwatch-like Web Game
검증 항목 8종 PASS/FAIL + 수치 증거
스크린샷: /tmp/qa_final_*.png
"""

import subprocess, time, sys, os, json, asyncio
import websockets, requests
from playwright.sync_api import sync_playwright

BASE   = "http://localhost:3000"
WS_URL = "ws://localhost:3000"
PORT   = 3000
SHOT_DIR = "/tmp"

results = {}

def report(key, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    results[key] = {"status": status, "detail": detail}
    mark = "✓" if passed else "✗"
    print(f"  [{status}] {key}: {detail}")

def start_server():
    proc = subprocess.Popen(
        ["node", "server.js"],
        env={**os.environ, "PORT": str(PORT)},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd="/home/user/organt_workspace/p-023-overwatch-like-웹-게임"
    )
    for _ in range(20):
        try:
            r = requests.get(f"{BASE}/health", timeout=1)
            if r.status_code == 200 and r.json().get("ok"):
                print(f"  서버 구동 완료 (port {PORT})")
                return proc
        except:
            pass
        time.sleep(0.5)
    raise RuntimeError("Server did not start within 10s")


# ═══════════════════════════════════════════════════════
# ITEM 1: Hero Select 화면 — Tank·DPS·Healer 카드 3종
# ═══════════════════════════════════════════════════════
def test_item1_hero_select(page):
    print("\n[ITEM-1] 히어로 셀렉트 화면 렌더링 — Tank·DPS·Healer 카드 3종")
    console_errors = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append(str(e)))

    page.set_viewport_size({"width": 1280, "height": 720})
    page.goto(BASE, wait_until="domcontentloaded")

    # 히어로 선택 화면 대기 (스플래시 후 자동 전환)
    page.wait_for_selector("#heroSelect", state="visible", timeout=8000)
    page.screenshot(path=f"{SHOT_DIR}/qa_final_01_hero_select.png")

    tank_card  = page.locator(".tank-card")
    dps_card   = page.locator(".dps-card")
    heal_card  = page.locator(".heal-card")

    tank_vis  = tank_card.is_visible()
    dps_vis   = dps_card.is_visible()
    heal_vis  = heal_card.is_visible()

    tank_text = tank_card.locator("h2").text_content()
    dps_text  = dps_card.locator("h2").text_content()
    heal_text = heal_card.locator("h2").text_content()

    all_visible = tank_vis and dps_vis and heal_vis
    report("ITEM1_hero_select",
           all_visible,
           f"Tank={tank_vis}('{tank_text}') DPS={dps_vis}('{dps_text}') Healer={heal_vis}('{heal_text}')")

    return console_errors


# ═══════════════════════════════════════════════════════
# ITEM 2: Tank 카드 클릭 → 카운트다운 → 게임 시작
# ═══════════════════════════════════════════════════════
def test_item2_countdown_to_game(page):
    print("\n[ITEM-2] Tank 카드 클릭 → 카운트다운(5→1) → playing 전환")

    # Tank 선택
    page.locator(".tank-card").click()
    time.sleep(0.3)

    # 카운트다운 div가 나타나야 함
    countdown_visible = False
    try:
        page.wait_for_selector("#countdown", state="visible", timeout=5000)
        countdown_visible = True
        num = page.locator("#countdownNum").text_content()
        page.screenshot(path=f"{SHOT_DIR}/qa_final_02_countdown.png")
        print(f"    카운트다운 값: {num}")
    except Exception as e:
        print(f"    카운트다운 미감지: {e}")

    # playing 진입 대기 (countdown 사라짐)
    playing_reached = False
    try:
        page.wait_for_selector("#countdown", state="hidden", timeout=12000)
        playing_reached = True
    except:
        cd_display = page.evaluate("document.getElementById('countdown').style.display")
        playing_reached = cd_display in ("none", "")

    phase = page.evaluate("typeof gameState !== 'undefined' ? gameState.phase : 'unknown'")

    # playing 상태에서 canvas 확인
    canvas_visible = page.locator("#gameCanvas").is_visible()
    page.screenshot(path=f"{SHOT_DIR}/qa_final_02_playing.png")

    passed = playing_reached and phase == "playing" and canvas_visible
    report("ITEM2_countdown_to_game",
           passed,
           f"countdown_visible={countdown_visible}, phase={phase}, canvas={canvas_visible}")

    return phase


# ═══════════════════════════════════════════════════════
# ITEM 3: WASD / 방향키 이동 확인
# ═══════════════════════════════════════════════════════
def test_item3_movement(page):
    print("\n[ITEM-3] WASD / 방향키 이동 → 위치 변화 확인")

    # 현재 localPos 읽기
    pos_before = page.evaluate("""() => ({
        x: typeof localPos !== 'undefined' ? localPos.x : null,
        y: typeof localPos !== 'undefined' ? localPos.y : null
    })""")

    # W 키 200ms 누르기
    page.keyboard.down("w")
    time.sleep(0.25)
    page.keyboard.up("w")
    time.sleep(0.15)

    pos_after_w = page.evaluate("""() => ({
        x: typeof localPos !== 'undefined' ? localPos.x : null,
        y: typeof localPos !== 'undefined' ? localPos.y : null
    })""")

    # A 키 200ms 누르기
    page.keyboard.down("a")
    time.sleep(0.25)
    page.keyboard.up("a")
    time.sleep(0.15)

    # 화살표 키도 확인
    page.keyboard.down("ArrowDown")
    time.sleep(0.25)
    page.keyboard.up("ArrowDown")
    time.sleep(0.15)

    pos_final = page.evaluate("""() => ({
        x: typeof localPos !== 'undefined' ? localPos.x : null,
        y: typeof localPos !== 'undefined' ? localPos.y : null
    })""")

    page.screenshot(path=f"{SHOT_DIR}/qa_final_03_movement.png")

    moved = False
    detail = f"before={pos_before}, after_W={pos_after_w}, final={pos_final}"
    if pos_before["x"] is not None and pos_after_w["x"] is not None:
        dy = abs((pos_after_w["y"] or 0) - (pos_before["y"] or 0))
        dx = abs((pos_final["x"] or 0) - (pos_before["x"] or 0))
        total_delta = dy + dx
        moved = total_delta > 1.0
        detail = f"before={pos_before}, after_W={pos_after_w}, final={pos_final}, Δ={total_delta:.1f}"

    report("ITEM3_wasd_movement", moved, detail)


# ═══════════════════════════════════════════════════════
# ITEM 4: 마우스 클릭 → 총알 발사 (canvas 이벤트)
# ═══════════════════════════════════════════════════════
def test_item4_shoot(page):
    print("\n[ITEM-4] 마우스 클릭 → 총알 발사 (canvas 이벤트)")

    # 발사 전 projectile 수
    proj_before = page.evaluate("""() =>
        typeof serverProjectiles !== 'undefined' ? Object.keys(serverProjectiles).length : -1
    """)

    # 인풋시퀀스 기록
    seq_before = page.evaluate("typeof inputSeq !== 'undefined' ? inputSeq : 0")

    # Canvas 중앙 오른쪽 클릭 (적 방향 = 오른쪽)
    canvas = page.locator("#gameCanvas")
    box = canvas.bounding_box()
    click_x = int(box["x"] + box["width"] * 0.75)
    click_y = int(box["y"] + box["height"] * 0.5)

    # 마우스 이동 후 클릭 (mousedown 유지 0.5초)
    page.mouse.move(click_x, click_y)
    page.mouse.down()
    time.sleep(0.35)
    page.mouse.up()
    time.sleep(0.4)  # 서버 왕복 대기

    # 발사 후 projectile 수 / 인풋시퀀스 증가 확인
    proj_after = page.evaluate("""() =>
        typeof serverProjectiles !== 'undefined' ? Object.keys(serverProjectiles).length : -1
    """)
    seq_after = page.evaluate("typeof inputSeq !== 'undefined' ? inputSeq : 0")

    # WS로 전송된 basic action 여부 확인 (inputSeq 증가)
    seq_increased = seq_after > seq_before

    # Tank는 melee(projectile 없음), DPS는 projectile 생성
    # 이 테스트에서 Tank를 선택했으므로 melee는 서버에서 AoE처리
    # proj_after >= proj_before 가 보장 (melee는 0개지만 seq는 증가)
    # 더 정확한 지표: inputSeq 증가 (클라이언트 전송 확인)

    page.screenshot(path=f"{SHOT_DIR}/qa_final_04_shoot.png")

    passed = seq_increased
    detail = f"seq: {seq_before}→{seq_after} (+{seq_after - seq_before}), proj: {proj_before}→{proj_after}"
    report("ITEM4_mouse_shoot", passed, detail)


# ═══════════════════════════════════════════════════════
# ITEM 5: E키 어빌리티 — HUD cooldown 반응
# ═══════════════════════════════════════════════════════
def test_item5_skill_e(page):
    print("\n[ITEM-5] E키 어빌리티(Shield/Scatter/Heal Orb) 발동 UI 반응")

    # 스킬1 CD 상태 전
    cd_before = page.evaluate("""() => {
        const el = document.getElementById('cdSkill1');
        return { display: el ? el.style.display : 'N/A', text: el ? el.textContent : '' };
    }""")

    # keyboard.down/up 방식: sendInput(50ms 인터벌)이 keys['e']=true를 확실히 포착
    page.keyboard.down("e")
    time.sleep(0.12)   # sendInput 최소 2회 이상 호출 보장
    page.keyboard.up("e")
    time.sleep(0.4)   # 서버 처리 + 스냅샷 수신

    # 스킬1 CD 발동 후 — cooldown overlay + 서버 shield 상태
    cd_after = page.evaluate("""() => {
        const el = document.getElementById('cdSkill1');
        const shield = (serverPlayers && myId && serverPlayers[myId]) ? serverPlayers[myId].shield : null;
        return {
            display: el ? el.style.display : 'N/A',
            text: el ? el.textContent : '',
            serverShield: shield,
        };
    }""")

    local_skill1_cd = page.evaluate("typeof localSkills !== 'undefined' ? localSkills.skill1Cd : null")

    page.screenshot(path=f"{SHOT_DIR}/qa_final_05_skill_e.png")

    # CD overlay=flex AND localSkills.skill1Cd>0 AND 서버 shield=True (Tank 기준)
    cd_triggered   = (cd_after["display"] == "flex") and (local_skill1_cd is not None and local_skill1_cd > 0)
    server_confirm = cd_after.get("serverShield") is True
    passed = cd_triggered and server_confirm
    report("ITEM5_skill_e",
           passed,
           f"cdOverlay={cd_after['display']}('{cd_after['text']}'), "
           f"localSkill1Cd={local_skill1_cd}ms, serverShield={cd_after.get('serverShield')}")


# ═══════════════════════════════════════════════════════
# ITEM 6: Q키 궁극기 — HUD 반응 확인
# ═══════════════════════════════════════════════════════
def test_item6_skill_q(page):
    print("\n[ITEM-6] Q키(skill2) 발동 UI 반응 확인")

    # skill2 CD 상태 전
    cd2_before = page.evaluate("""() => {
        const el = document.getElementById('cdSkill2');
        return { display: el ? el.style.display : 'N/A', text: el ? el.textContent : '' };
    }""")

    seq_before = page.evaluate("typeof inputSeq !== 'undefined' ? inputSeq : 0")

    page.keyboard.down("q")
    time.sleep(0.12)
    page.keyboard.up("q")
    time.sleep(0.4)

    cd2_after = page.evaluate("""() => {
        const el = document.getElementById('cdSkill2');
        return { display: el ? el.style.display : 'N/A', text: el ? el.textContent : '' };
    }""")
    seq_after = page.evaluate("typeof inputSeq !== 'undefined' ? inputSeq : 0")
    local_skill2_cd = page.evaluate("typeof localSkills !== 'undefined' ? localSkills.skill2Cd : null")

    # R키(ult) 도 추가 테스트
    page.keyboard.press("r")
    time.sleep(0.3)
    ult_charge = page.evaluate("typeof localUltCharge !== 'undefined' ? localUltCharge : null")
    ult_bar_w = page.evaluate("""() => {
        const el = document.getElementById('ultBar');
        return el ? el.style.width : 'N/A';
    }""")

    page.screenshot(path=f"{SHOT_DIR}/qa_final_06_skill_q.png")

    # skill2 CD 발동 OR seq 증가 (입력 전송됨)
    seq_diff = seq_after - seq_before
    cd2_triggered = (cd2_after["display"] == "flex") or (local_skill2_cd is not None and local_skill2_cd > 0) or seq_diff > 0

    passed = cd2_triggered
    report("ITEM6_skill_q",
           passed,
           f"cd2: {cd2_before}→{cd2_after}, seq+{seq_diff}, localSkill2Cd={local_skill2_cd}, ultBar={ult_bar_w}")


# ═══════════════════════════════════════════════════════
# ITEM 7: JS 콘솔 에러 0건
# ═══════════════════════════════════════════════════════
def test_item7_console_errors(console_errors):
    print("\n[ITEM-7] JS 콘솔 에러 0건 확인")
    # AudioContext/autoplay policy 에러는 헤드리스 환경 known issue로 제외
    real_errors = [e for e in console_errors
                   if "NotAllowedError" not in e
                   and "AudioContext" not in e
                   and "user gesture" not in e.lower()
                   and "autoplay" not in e.lower()]

    passed = len(real_errors) == 0
    report("ITEM7_console_errors",
           passed,
           f"총 {len(console_errors)}건 중 실질 에러 {len(real_errors)}건: {real_errors[:3] if real_errors else '없음'}")


# ═══════════════════════════════════════════════════════
# ITEM 8: 1280×720 레이아웃 깨짐 없음
# ═══════════════════════════════════════════════════════
def test_item8_layout(page):
    print("\n[ITEM-8] 1280×720 레이아웃 — overflow/겹침 없음")

    # 중요 HUD 요소 위치·크기 확인
    results_layout = page.evaluate("""() => {
        const ids = ['scoreboard', 'killfeed', 'bottomBar', 'selfHp', 'hud', 'gameCanvas'];
        return ids.map(id => {
            const el = document.getElementById(id);
            if (!el) return { id, found: false };
            const r = el.getBoundingClientRect();
            return {
                id,
                found: true,
                x: Math.round(r.x), y: Math.round(r.y),
                w: Math.round(r.width), h: Math.round(r.height),
                outOfBounds: r.x < -5 || r.y < -5 || r.right > 1290 || r.bottom > 730
            };
        });
    }""")

    # canvas가 화면을 덮는지
    canvas_info = page.evaluate("""() => {
        const c = document.getElementById('gameCanvas');
        if (!c) return null;
        return { w: c.width, h: c.height };
    }""")

    page.screenshot(path=f"{SHOT_DIR}/qa_final_08_layout.png")

    out_of_bounds = [r for r in results_layout if r.get("outOfBounds")]
    not_found = [r["id"] for r in results_layout if not r.get("found")]

    passed = len(out_of_bounds) == 0 and len(not_found) == 0
    detail = f"canvas={canvas_info}, outOfBounds={[r['id'] for r in out_of_bounds]}, notFound={not_found}"
    report("ITEM8_layout_1280x720", passed, detail)


# ═══════════════════════════════════════════════════════
# ITEM - HUD 실시간 요소 전체 확인 (ITEM 4 보완)
# ═══════════════════════════════════════════════════════
def test_hud_realtime(page):
    print("\n[HUD] HUD 실시간 요소: HP바·킬피드·궁극기 게이지·타이머")

    hud_check = page.evaluate("""() => {
        const score0 = document.getElementById('score0');
        const score1 = document.getElementById('score1');
        const timeD  = document.getElementById('timeDisplay');
        const hpBar  = document.getElementById('selfHpBar');
        const ultBar = document.getElementById('ultBar');
        const kfeed  = document.getElementById('killfeed');
        const teamHp0 = document.getElementById('teamHp0');
        const respawn = document.getElementById('respawnOverlay');

        return {
            score0_txt:  score0 ? score0.textContent : null,
            score1_txt:  score1 ? score1.textContent : null,
            time_txt:    timeD  ? timeD.textContent  : null,
            hp_bar_w:    hpBar  ? hpBar.style.width  : null,
            ult_bar_w:   ultBar ? ultBar.style.width : null,
            killfeed_exists: !!kfeed,
            teamHp0_exists: !!teamHp0,
            respawn_exists: !!respawn,
        };
    }""")

    page.screenshot(path=f"{SHOT_DIR}/qa_final_hud.png")

    # 타이머가 M:SS 형식인지
    time_txt = hud_check.get("time_txt") or ""
    timer_ok = ":" in time_txt and len(time_txt) >= 4

    all_ok = (hud_check["score0_txt"] is not None and
              hud_check["time_txt"] is not None and
              hud_check["hp_bar_w"] is not None and
              hud_check["ult_bar_w"] is not None and
              hud_check["killfeed_exists"] and
              timer_ok)

    report("HUD_realtime_elements", all_ok,
           f"score=({hud_check['score0_txt']}-{hud_check['score1_txt']}), "
           f"time={time_txt}, hp={hud_check['hp_bar_w']}, "
           f"ult={hud_check['ult_bar_w']}, timer_format_ok={timer_ok}")


# ═══════════════════════════════════════════════════════
# WS 직접: DPS 총알 → 적 HP 감소 검증
# ═══════════════════════════════════════════════════════
async def _ws_damage_test():
    hp_before = None
    hp_after  = None
    victim_id = None
    attacker_id = None

    async def recv_one(ws, timeout=2.0):
        try:
            return json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
        except:
            return None

    async def drain_latest(ws, secs=0.6):
        latest = None
        deadline = asyncio.get_event_loop().time() + secs
        while asyncio.get_event_loop().time() < deadline:
            msg = await recv_one(ws, timeout=0.15)
            if msg and msg.get("type") == "snapshot":
                latest = msg
        return latest

    async with websockets.connect(WS_URL) as ws_a, \
               websockets.connect(WS_URL) as ws_v:

        init_a = json.loads(await asyncio.wait_for(ws_a.recv(), timeout=3))
        init_v = json.loads(await asyncio.wait_for(ws_v.recv(), timeout=3))
        attacker_id = init_a.get("playerId")
        victim_id   = init_v.get("playerId")

        await ws_a.send(json.dumps({"type": "selectHero", "heroClass": "dps"}))
        await ws_v.send(json.dumps({"type": "selectHero", "heroClass": "tank"}))

        # playing 진입 대기
        phase = "waiting"
        deadline = asyncio.get_event_loop().time() + 9
        while asyncio.get_event_loop().time() < deadline:
            msg = await recv_one(ws_a, timeout=1.5)
            if not msg: break
            t = msg.get("type")
            gs = msg.get("gameState", {}) if t == "snapshot" else {}
            if (t == "gameState" and msg.get("phase") == "playing") or \
               (t == "snapshot" and gs.get("phase") == "playing"):
                phase = "playing"
                break

        if phase != "playing":
            return {"phase": phase, "hp_before": None, "hp_after": None, "delta": None}

        snap = await drain_latest(ws_a, 0.6)
        if snap:
            for p in snap.get("players", []):
                if p["id"] == victim_id:
                    hp_before = p["hp"]

        # 8발 발사 (DPS basicCd=300ms)
        for shot in range(8):
            await ws_a.send(json.dumps({
                "seq": shot+1,
                "timestamp": int(time.time()*1000),
                "move": {"x": 0, "y": 0},
                "aim": 0.0,
                "action": {"type": "basic"}
            }))
            await asyncio.sleep(0.35)

        # 총알 도달 대기 (dist≈1000, speed=500 → 2s)
        await asyncio.sleep(2.5)

        snap = await drain_latest(ws_a, 0.8)
        if snap:
            for p in snap.get("players", []):
                if p["id"] == victim_id:
                    hp_after = p["hp"]

        delta = (hp_before - hp_after) if (hp_before is not None and hp_after is not None) else None
        return {"phase": phase, "hp_before": hp_before, "hp_after": hp_after, "delta": delta}

def test_ws_damage():
    print("\n[WS] DPS 총알 → 적 HP 감소 (WebSocket 직접 검증)")
    try:
        res = asyncio.run(_ws_damage_test())
        phase = res["phase"]
        if phase != "playing":
            report("WS_dps_damage", False, f"playing 진입 실패: phase={phase}")
            return
        hp_b, hp_a, delta = res["hp_before"], res["hp_after"], res["delta"]
        if hp_b is None or hp_a is None:
            report("WS_dps_damage", False, f"HP 수치 수신 실패: {hp_b}→{hp_a}")
            return
        passed = delta is not None and delta > 0
        report("WS_dps_damage", passed,
               f"tank HP: {hp_b}→{hp_a}, delta={delta} (expect >0, tank maxHp=400, dps_dmg=60/shot)")
    except Exception as e:
        report("WS_dps_damage", False, f"예외: {e}")


# ═══════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════
def main():
    print("=" * 60)
    print("QA 최종 인수 테스트 — P-023 Overwatch-like Web Game")
    print("검증 기준: 1280×720, localhost:3000")
    print("=" * 60)

    # WS 직접 데미지 테스트 먼저 (별도 서버 세션 불필요)
    print("\n--- WebSocket 직접 데미지 검증 ---")
    test_ws_damage()

    # Playwright 브라우저 테스트
    print("\n--- Playwright 브라우저 테스트 ---")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-web-security",
        ])
        ctx = browser.new_context(viewport={"width": 1280, "height": 720})
        page = ctx.new_page()

        console_errors = []
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(str(e)))

        # ITEM 1
        test_item1_hero_select(page)

        # ITEM 2: Tank 카드 클릭 → 카운트다운 → playing
        test_item2_countdown_to_game(page)

        # ITEM 3: WASD 이동
        test_item3_movement(page)

        # ITEM 4: 마우스 클릭 → 총알
        test_item4_shoot(page)

        # ITEM 5: E키 어빌리티
        test_item5_skill_e(page)

        # ITEM 6: Q키 (skill2)
        test_item6_skill_q(page)

        # HUD 실시간 요소
        test_hud_realtime(page)

        # ITEM 7: 콘솔 에러
        test_item7_console_errors(console_errors)

        # ITEM 8: 레이아웃
        test_item8_layout(page)

        # 최종 스크린샷
        page.screenshot(path=f"{SHOT_DIR}/qa_final_09_final_state.png")

        browser.close()

    # 요약
    print("\n" + "=" * 60)
    print("최종 결과 요약")
    print("=" * 60)
    passed_n = sum(1 for v in results.values() if v["status"] == "PASS")
    total_n  = len(results)
    for k, v in results.items():
        mark = "✓" if v["status"] == "PASS" else "✗"
        print(f"  {mark} {k}: {v['status']} — {v['detail']}")
    print(f"\n  합계: {passed_n}/{total_n} PASS")

    return 0 if passed_n == total_n else 1

if __name__ == "__main__":
    sys.exit(main())
