"""
End-to-end browser test with a FAKE camera + microphone and a STUBBED model (tests/stubs/openai.py).
Proves the wiring: camera -> Python -> vision -> memory, Auto Memory (duplicate suppressed, change stored,
HUD stop), photo re-examination for a color question, push-to-talk -> STT -> answer -> TTS.
It does NOT prove real model quality — run the app with your keys for that.

  pip install playwright && python -m playwright install chromium
  rm -f events.json shopping.json actions.json stub_calls.log; rm -rf images
  STUB_LOG=stub_calls.log PYTHONPATH=tests/stubs OPENAI_API_KEY=test AUTO_MEMORY_INTERVAL_MS=4000 \
      streamlit run app.py --server.port 8610 --server.headless true &
  python tests/e2e_browser.py .
"""
import asyncio, json, sys
from playwright.async_api import async_playwright
SP = sys.argv[1]; URL = "http://localhost:8610/"
def ok(msg): print("PASS", msg)
async def nav(pg, name):
    await pg.get_by_role("radio", name=name).first.click()
    await pg.wait_for_timeout(1500)
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required"])
        ctx = await b.new_context(viewport={"width": 1440, "height": 1000}, permissions=["camera", "microphone"])
        pg = await ctx.new_page()
        await pg.goto(URL, wait_until="networkidle"); await pg.wait_for_timeout(2500)
        await pg.screenshot(path=f"{SP}/d_home.png", full_page=True); ok("home rendered")
        await nav(pg, "Vision")
        cam = pg.frame_locator("iframe[title*='aftersight_camera']")
        await cam.locator("#go").click()
        await cam.locator("#cam").filter(has_text="LIVE").wait_for(timeout=15000); ok("camera live")
        await cam.locator("#shutter").click()
        await cam.locator("#status").filter(has_text="Remembered").wait_for(timeout=40000)
        ok("manual capture: " + await cam.locator("#status").inner_text())
        await pg.wait_for_timeout(1500); await pg.screenshot(path=f"{SP}/d_vision.png", full_page=True)
        # Auto Memory (opt-in)
        await pg.get_by_text("I understand and want Auto Memory on").click(); await pg.wait_for_timeout(800)
        await pg.get_by_role("button", name="Start Auto Memory").click(); await pg.wait_for_timeout(2500)
        await cam.locator("#auto").wait_for(state="visible", timeout=15000); ok("auto indicator visible in HUD")
        await cam.locator("#status").filter(has_text="No meaningful change").wait_for(timeout=60000)
        ok("auto #1 duplicate suppressed: " + await cam.locator("#status").inner_text())
        await cam.locator("#status").filter(has_text="Auto Memory · Remembered").wait_for(timeout=60000)
        ok("auto #2 change stored: " + await cam.locator("#status").inner_text())
        await pg.screenshot(path=f"{SP}/d_auto.png", full_page=True)
        await cam.locator("#astop").click(); await pg.wait_for_timeout(3000)
        vis = await cam.locator("#auto").is_visible()
        print("PASS" if not vis else "FAIL", "HUD stop turns auto off")
        # Ask: visual detail -> photo re-examination
        await nav(pg, "Ask")
        box = pg.get_by_placeholder("Ask anything about what you've seen…")
        await box.fill("What color was the object I was holding?"); await box.press("Enter")
        await pg.get_by_text("re-examined retained photo").first.wait_for(timeout=40000)
        ans = await pg.locator(".answer").last.inner_text(); ok("re-examination answer: " + ans)
        # Voice: push-to-talk (let the page settle after the previous answer first)
        await pg.wait_for_timeout(2500)
        mic = pg.frame_locator("iframe[title*='aftersight_voice']").locator("#mic")
        bb = await mic.bounding_box()
        await pg.mouse.move(bb["x"] + bb["width"]/2, bb["y"] + bb["height"]/2); await pg.mouse.down()
        await pg.wait_for_timeout(1800); await pg.mouse.up()
        await pg.locator(".qbubble", has_text="🎙").last.wait_for(timeout=40000)
        await pg.locator("audio").last.wait_for(state="attached", timeout=40000)
        ok("voice: transcribed question shown + spoken answer audio attached")
        await pg.wait_for_timeout(1000); await pg.screenshot(path=f"{SP}/d_ask.png", full_page=True)
        await nav(pg, "Memories"); await pg.screenshot(path=f"{SP}/d_memories.png", full_page=True)
        await nav(pg, "Activity"); await pg.screenshot(path=f"{SP}/d_activity.png", full_page=True)
        await pg.evaluate("window.scrollTo(0,0)"); await pg.locator("[data-testid=stButtonGroup] button").nth(5).click(); await pg.wait_for_timeout(2000)
        t = await pg.locator("[data-testid='stMetricValue']").all_inner_texts(); ok("settings metrics: " + str(t))
        await pg.screenshot(path=f"{SP}/d_settings.png", full_page=True)
        # mobile
        m = await b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, permissions=["camera","microphone"])
        mp = await m.new_page(); await mp.goto(URL, wait_until="networkidle"); await mp.wait_for_timeout(2500)
        await mp.screenshot(path=f"{SP}/m_home.png", full_page=True)
        await nav(mp, "Ask"); await mp.screenshot(path=f"{SP}/m_ask.png", full_page=True); ok("mobile rendered")
        await b.close()
asyncio.run(main())
