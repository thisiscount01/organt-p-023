#!/usr/bin/env python3
"""
QA 수용 테스트 — P-023 Overwatch-like Web Game
항목별 PASS/FAIL + 수치 증거 출력
"""

import subprocess, time, sys, json, threading, asyncio, math
import websockets
import requests
from playwright.sync_api import sync_playwright, expect

BASE = "http://localhost:3001"
WS   = "ws://localhost:3001"
PORT = 3001

results = {}

def report(key, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    results[key] = {"status": status, "detail": detail}
    print(f"  [{status}] {key}: {detail}")

def start_server():
    proc = subprocess.Popen(
        ["node", "server.js"],
        env={**__import__("os").environ, "PORT": str(PORT)},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd="/home/user/organt_workspace/p-023-overwatch-like-웹-게임"
    )
    # Wait until /health responds
    for _ in range(20):
        try:
            r = requests.get(f"{BASE}/health", timeout=1)
            if r.status_code == 200:
                return proc
        except:
            pass
        time.sleep(0.5)
    raise RuntimeError("Server did not start within 10s")

# ─── Scenario 1: /health 200 ─────────────────────────────────────────────────
def test_health():
    print("\n[S1] /health 200 응답")
    try:
        r = requests.get(f"{BASE}/health", timeout=3)
        data = r.json()
        passed = r.status_code == 200 and data.get("ok") is True
        report("S1_health", passed, f"status={r.status_code} body={data}")
    except Exception as e:
        report("S1_health", False, str(e))

# ─── Scenario 2: 브라우저 접속 → 스플래시 → 영웅선택 ─────────────────────────
def test_splash_and_hero_select(page):
    print("\n[S2] 스플래시 → 영웅 선택 화면")
    console_errors = []
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda err: console_errors.append(str(err)))

    page.goto(BASE, wait_until="domcontentloaded")

    # Splash overlay should be visible initially
    splash = page.locator("#splashOverlay")
    # It might have faded by the time we check since health is already up
    # Check that heroSelect eventually becomes visible (display:flex)
    page.wait_for_selector("#heroSelect", state="visible", timeout=5000)
    hero_visible = page.locator("#heroSelect").is_visible()
    report("S2_hero_select", hero_visible,
           f"#heroSelect visible={hero_visible}, console_errors={len(console_errors)}")
    return console_errors

# ─── Scenario 3: 영웅 선택 → 카운트다운 → playing ────────────────────────────
def test_hero_select_to_playing(page):
    print("\n[S3] 영웅선택(DPS) → 카운트다운 → playing")

    # Click DPS card
    page.locator(".dps-card").click()

    # Wait for countdown or playing phase
    # The countdown div shows up with display:flex
    playing_reached = False
    try:
        # Countdown should appear
        page.wait_for_selector("#countdown", state="visible", timeout=3000)
        countdown_visible = True
    except:
        countdown_visible = False

    # Then playing phase (countdown div disappears)
    try:
        page.wait_for_selector("#countdown", state="hidden", timeout=10000)
        playing_reached = True
    except:
        # Check if already in playing
        cd_display = page.evaluate("document.getElementById('countdown').style.display")
        playing_reached = (cd_display == "none" or cd_display == "")

    # Verify gameState phase via JS
    phase = page.evaluate("typeof gameState !== 'undefined' ? gameState.phase : 'unknown'")
    report("S3_playing", playing_reached and phase == "playing",
           f"countdown_visible={countdown_visible}, phase={phase}, playing_reached={playing_reached}")
    return phase

# ─── Scenario 4: 2인 멀티 접속 → 양쪽 playing, 다른 팀 ─────────────────────
def test_two_players(page1, page2, ctx2):
    print("\n[S4] 2인 멀티 → playing 진입 + 팀 배정 확인")

    # page1 already connected; page2 needs to connect
    # Navigate page2 to the game
    page2.goto(BASE, wait_until="domcontentloaded")
    page2.wait_for_selector("#heroSelect", state="visible", timeout=5000)
    page2.locator(".tank-card").click()

    # Both should reach playing
    try:
        page2.wait_for_selector("#countdown", state="hidden", timeout=12000)
        p2_playing = True
    except:
        p2_playing = False

    # Check teams via JS
    team1 = page1.evaluate("typeof myTeam !== 'undefined' ? myTeam : -1")
    team2 = page2.evaluate("typeof myTeam !== 'undefined' ? myTeam : -1")
    phase1 = page1.evaluate("typeof gameState !== 'undefined' ? gameState.phase : 'unknown'")
    phase2 = page2.evaluate("typeof gameState !== 'undefined' ? gameState.phase : 'unknown'")

    both_playing = (phase1 == "playing" and phase2 == "playing")
    different_teams = (team1 != team2 and team1 >= 0 and team2 >= 0)

    report("S4_two_players_playing", both_playing,
           f"p1_phase={phase1}, p2_phase={phase2}")
    report("S4_different_teams", different_teams,
           f"team1={team1}, team2={team2}")

# ─── Scenario 5: DPS 총알 → 적 HP 감소 (WebSocket 직접) ─────────────────────
async def _ws_damage_test():
    """두 WS 클라이언트 연결 → playing 진입 → DPS 기본공격 → HP 감소 확인
    핵심 수정: 공격 완료 후 버퍼를 전부 드레인한 뒤 최신 스냅샷으로 hp_after 측정
    """
    hp_before = None
    hp_after  = None
    victim_id = None
    attacker_id = None

    async def recv_one(ws, timeout=2.0):
        try:
            return json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
        except:
            return None

    async def drain_to_latest_snapshot(ws, drain_sec=0.5):
        """주어진 시간 동안 메시지를 소진하고 마지막 snapshot 반환"""
        latest = None
        deadline = asyncio.get_event_loop().time() + drain_sec
        while asyncio.get_event_loop().time() < deadline:
            msg = await recv_one(ws, timeout=0.15)
            if msg and msg.get("type") == "snapshot":
                latest = msg
        return latest

    async with websockets.connect(f"{WS}") as ws_atk, \
               websockets.connect(f"{WS}") as ws_vic:

        # --- init 수신 ---
        init_a = json.loads(await asyncio.wait_for(ws_atk.recv(), timeout=3))
        init_v = json.loads(await asyncio.wait_for(ws_vic.recv(), timeout=3))
        attacker_id = init_a.get("playerId")
        victim_id   = init_v.get("playerId")

        # --- 영웅 선택 ---
        await ws_atk.send(json.dumps({"type": "selectHero", "heroClass": "dps"}))
        await ws_vic.send(json.dumps({"type": "selectHero", "heroClass": "tank"}))

        # --- playing 진입 대기 (최대 9초) ---
        phase = "waiting"
        deadline = asyncio.get_event_loop().time() + 9

        while asyncio.get_event_loop().time() < deadline:
            msg = await recv_one(ws_atk, timeout=1.5)
            if not msg:
                break
            t = msg.get("type")
            gs = msg.get("gameState", {}) if t == "snapshot" else {}
            if (t == "gameState" and msg.get("phase") == "playing") or \
               (t == "snapshot" and gs.get("phase") == "playing"):
                phase = "playing"
                break

        if phase != "playing":
            return {"phase": phase, "hp_before": None, "hp_after": None, "delta": None}

        # --- hp_before 측정: 최신 스냅샷 드레인 후 읽기 ---
        snap = await drain_to_latest_snapshot(ws_atk, drain_sec=0.6)
        if snap:
            for p in snap.get("players", []):
                if p["id"] == victim_id:
                    hp_before = p["hp"]

        if hp_before is None:
            # fallback: single recv
            for _ in range(5):
                msg = await recv_one(ws_atk, timeout=1.0)
                if msg and msg.get("type") == "snapshot":
                    for p in msg.get("players", []):
                        if p["id"] == victim_id:
                            hp_before = p["hp"]
                    break

        # --- 공격 전송 ---
        # DPS(team0) spawn ~(100,300) → Tank(team1) spawn ~(1100,300)
        # angle=0 (오른쪽): 두 플레이어 모두 y≈300에 있으므로 직격
        # 스폰 y 확인: SPAWNS[0][0]={100,300}, SPAWNS[1][0]={1100,300}
        aim_angle = 0.0

        for shot in range(8):
            await ws_atk.send(json.dumps({
                "seq": shot + 1,
                "timestamp": int(time.time() * 1000),
                "move": {"x": 0, "y": 0},
                "aim": aim_angle,
                "action": {"type": "basic"}
            }))
            await asyncio.sleep(0.35)   # DPS basicCd=300ms, margin 50ms

        # --- 총알 이동 대기 ---
        # dist=1000px, speed=500px/s → 2s 이동. 여유 2.5s 대기
        await asyncio.sleep(2.5)

        # --- hp_after 측정: 버퍼 전체 소진 후 최신 값 ---
        snap = await drain_to_latest_snapshot(ws_atk, drain_sec=0.8)
        if snap:
            for p in snap.get("players", []):
                if p["id"] == victim_id:
                    hp_after = p["hp"]

        if hp_after is None:
            for _ in range(5):
                msg = await recv_one(ws_atk, timeout=1.0)
                if msg and msg.get("type") == "snapshot":
                    for p in msg.get("players", []):
                        if p["id"] == victim_id:
                            hp_after = p["hp"]
                    break

        delta = None
        if hp_before is not None and hp_after is not None:
            delta = hp_before - hp_after

        return {"phase": phase, "hp_before": hp_before, "hp_after": hp_after, "delta": delta,
                "attacker": attacker_id, "victim": victim_id}

def test_dps_damage():
    print("\n[S5] DPS 총알 → 적 HP 감소 (WS 직접)")
    try:
        result = asyncio.run(_ws_damage_test())
        phase = result["phase"]
        hp_b  = result["hp_before"]
        hp_a  = result["hp_after"]
        delta = result["delta"]

        if phase != "playing":
            report("S5_dps_damage", False, f"playing 진입 실패: phase={phase}")
            return

        if hp_b is None or hp_a is None:
            report("S5_dps_damage", False, f"HP 수치 수신 실패 (hp_before={hp_b}, hp_after={hp_a})")
            return

        passed = delta is not None and delta > 0
        report("S5_dps_damage", passed,
               f"victim_hp: {hp_b} → {hp_a}, delta={delta} (tank maxHp=400, dps_dmg=60)")
    except Exception as e:
        report("S5_dps_damage", False, f"예외: {e}")

# ─── Scenario 6: HUD 요소 존재 ────────────────────────────────────────────────
def test_hud_elements(page):
    print("\n[S6] HUD 요소 존재: #score0, #score1, #timeDisplay, #killfeed, #selfHpBar, #ultBar")
    hud_ids = ["score0", "score1", "timeDisplay", "killfeed", "selfHpBar", "ultBar"]
    missing = []
    for hid in hud_ids:
        el = page.locator(f"#{hid}")
        if el.count() == 0:
            missing.append(hid)

    # Check #selfHpBar width is set
    hp_width = page.evaluate("document.getElementById('selfHpBar') ? document.getElementById('selfHpBar').style.width : 'N/A'")
    ult_width = page.evaluate("document.getElementById('ultBar') ? document.getElementById('ultBar').style.width : 'N/A'")
    time_text = page.evaluate("document.getElementById('timeDisplay') ? document.getElementById('timeDisplay').textContent : ''")

    passed = len(missing) == 0
    report("S6_hud_elements", passed,
           f"missing={missing}, selfHpBar.width={hp_width}, ultBar.width={ult_width}, timeDisplay='{time_text}'")

# ─── Scenario 7: 콘솔 에러 0건 ───────────────────────────────────────────────
def test_console_errors(page, prev_errors):
    print("\n[S7] 콘솔 에러 0건")
    # Collect any new errors (prev_errors already collected from page setup)
    # Check via page.evaluate if there are any error markers
    all_errors = prev_errors[:]

    # Check for JS errors via evaluate
    js_err = page.evaluate("window.__qa_errors ? window.__qa_errors : []")
    all_errors.extend(js_err if isinstance(js_err, list) else [])

    # Filter out AudioContext / policy warnings that aren't real errors
    real_errors = [e for e in all_errors
                   if "NotAllowedError" not in e
                   and "AudioContext" not in e
                   and "user gesture" not in e.lower()]

    passed = len(real_errors) == 0
    report("S7_console_errors", passed,
           f"총 console error {len(real_errors)}건: {real_errors[:3] if real_errors else '없음'}")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════
def main():
    print("=" * 60)
    print("QA 수용 테스트 — P-023 Overwatch-like Web Game")
    print("=" * 60)

    # Start server
    print("\n[서버 시작] node server.js (port 3001)")
    proc = start_server()
    print("  서버 구동 완료")

    console_errors = []

    try:
        # S1: Health check
        test_health()

        # S5: DPS damage (independent WS test — run before Playwright occupies state)
        test_dps_damage()

        # S2-S4, S6-S7: Playwright browser tests
        with sync_playwright() as p:
            browser1 = p.chromium.launch(headless=True)
            browser2 = p.chromium.launch(headless=True)

            ctx1 = browser1.new_context()
            ctx2 = browser2.new_context()

            page1 = ctx1.new_page()
            page2 = ctx2.new_page()

            # Collect console errors
            page1.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            page1.on("pageerror", lambda err: console_errors.append(str(err)))

            # S2
            test_splash_and_hero_select(page1)

            # S3
            test_hero_select_to_playing(page1)

            # S4
            test_two_players(page1, page2, ctx2)

            # S6
            test_hud_elements(page1)

            # S7
            test_console_errors(page1, console_errors)

            browser1.close()
            browser2.close()

    finally:
        proc.terminate()
        proc.wait()

    # Summary
    print("\n" + "=" * 60)
    print("최종 결과 요약")
    print("=" * 60)
    passed = sum(1 for v in results.values() if v["status"] == "PASS")
    total  = len(results)
    for k, v in results.items():
        mark = "✓" if v["status"] == "PASS" else "✗"
        print(f"  {mark} {k}: {v['status']} — {v['detail']}")
    print(f"\n  합계: {passed}/{total} PASS")

    exit_code = 0 if passed == total else 1
    sys.exit(exit_code)

if __name__ == "__main__":
    main()
