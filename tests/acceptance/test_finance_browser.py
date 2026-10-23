"""Opt-in hosted Chromium smoke of private choices in a disposable synthetic app.

This never opens a member browser or the household service. The browser uses a
new temporary profile and the app contains only deterministic synthetic data.
"""

import asyncio
import base64
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import socket
import subprocess
from contextlib import asynccontextmanager

import pytest
import uvicorn
from websockets.asyncio.client import connect

from adminme.finance.importer import import_publication
from adminme.finance.publication import content_hash
from adminme.lib.session import build_internal_session
from adminme.runtime.application import create_application
from tests.acceptance.test_finance_disclosure import enabled_settings
from tests.acceptance.test_finance_publications import publication
from tests.acceptance.test_openclaw_native_seam import PRINCIPAL
from adminme.finance.planning_store import import_projection
from tests.fixtures.finance_planning import projection


def _serve_choices(root, listener, ready):
    app = create_application(enabled_settings(root))
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with original(application):
            runtime = application.state.runtime
            value = publication(runtime.settings.instance.tenant_id)
            value["owner_id"] = PRINCIPAL
            for report in value["reports"]:
                for metric in report["metrics"]:
                    metric["owner_id"] = PRINCIPAL
            value["content_sha256"] = content_hash(value)
            await import_publication(
                runtime,
                build_internal_session(PRINCIPAL, "principal", value["tenant_id"]),
                value,
            )
            await import_projection(
                runtime,
                build_internal_session(PRINCIPAL, "principal", value["tenant_id"]),
                projection(value, 24),
            )
            ready.send(True)
            ready.close()
            yield

    app.router.lifespan_context = lifespan
    uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False)).run(
        sockets=[listener]
    )


@pytest.mark.skipif(
    os.getenv("ADMINME_FINANCE_BROWSER") != "1",
    reason="Hosted disposable-browser acceptance only",
)
async def test_owner_saves_goal_and_preference_then_reloads(tmp_path):
    chrome = (
        shutil.which("google-chrome")
        or shutil.which("chromium")
        or shutil.which("chromium-browser")
    )
    assert chrome, (
        "Hosted browser check requires Chromium; absence is not a passing gate"
    )
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    listener = socket.socket()
    listener.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = context.Process(
        target=_serve_choices, args=(tmp_path / "instance", listener, send)
    )
    server.start()
    send.close()
    browser = None
    browser_log = None
    try:
        assert await asyncio.to_thread(receive.poll, 30), (
            "synthetic application startup timed out"
        )
        assert receive.recv() is True
        profile = tmp_path / "isolated-browser"
        browser_log = (tmp_path / "chromium-startup.log").open("w")
        browser = subprocess.Popen(
            [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--remote-debugging-address=127.0.0.1",
                "--remote-debugging-port=0",
                f"--user-data-dir={profile}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=browser_log,
        )
        for _ in range(300):
            if (profile / "DevToolsActivePort").exists():
                break
            assert browser.poll() is None, (
                tmp_path / "chromium-startup.log"
            ).read_text()[-4000:]
            await asyncio.sleep(0.1)
        browser_log.flush()
        assert (profile / "DevToolsActivePort").exists(), (
            tmp_path / "chromium-startup.log"
        ).read_text()[-4000:]
        coordinates = (profile / "DevToolsActivePort").read_text().splitlines()
        async with connect(
            f"ws://127.0.0.1:{coordinates[0]}{coordinates[1]}",
            max_size=10 * 1024 * 1024,
        ) as ws:
            sequence = 0
            session = None

            async def call(method, params=None, *, page=True):
                nonlocal sequence
                sequence += 1
                message = {"id": sequence, "method": method, "params": params or {}}
                if page and session:
                    message["sessionId"] = session
                await ws.send(json.dumps(message))
                while True:
                    response = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
                    if response.get("id") == sequence:
                        assert "error" not in response, response
                        return response.get("result", {})

            target = await call(
                "Target.createTarget", {"url": "about:blank"}, page=False
            )
            attached = await call(
                "Target.attachToTarget",
                {"targetId": target["targetId"], "flatten": True},
                page=False,
            )
            session = attached["sessionId"]
            version = await call("Browser.getVersion", page=False)
            print(json.dumps({"synthetic_browser": version["product"]}))
            await call("Network.enable")
            await call(
                "Network.setExtraHTTPHeaders",
                {"headers": {"Tailscale-User-Login": "fixture-principal.ts.net"}},
            )
            await call("Page.enable")
            await call("Runtime.enable")
            await call(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": 1280,
                    "height": 1000,
                    "deviceScaleFactor": 1,
                    "mobile": False,
                },
            )
            await call(
                "Page.navigate", {"url": f"http://127.0.0.1:{port}/member/finance"}
            )

            async def evaluate(expression):
                result = await call(
                    "Runtime.evaluate",
                    {
                        "expression": expression,
                        "awaitPromise": True,
                        "returnByValue": True,
                    },
                )
                assert "exceptionDetails" not in result, result.get("exceptionDetails")
                return result["result"].get("value")

            async def wait_for(expression):
                for _ in range(80):
                    if await evaluate(expression):
                        return
                    await asyncio.sleep(0.1)
                diagnostic = await evaluate(
                    "({url:location.href,text:document.body.innerText.slice(-4000),invalid:Array.from(document.querySelectorAll(':invalid')).map(e=>({tag:e.tagName,message:e.validationMessage}))})"
                )
                screenshot = await call("Page.captureScreenshot", {"format": "png"})
                output = Path(
                    os.environ.get("FINANCE_BROWSER_ARTIFACT_DIR", str(tmp_path))
                )
                output.mkdir(parents=True, exist_ok=True)
                (output / "finance-choices-failure.png").write_bytes(
                    base64.b64decode(screenshot["data"])
                )
                raise AssertionError(
                    f"synthetic finance UI did not reach {expression}: {diagnostic}"
                )

            await wait_for("Boolean(document.querySelector('.finance-choices'))")
            identity = await evaluate(
                "fetch('/api/member/v1/bootstrap').then(r => r.json()).then(x => ({member:x.member,trust:x.trust}))"
            )
            assert identity["member"]["authenticated_id"] == PRINCIPAL
            assert not identity["member"]["viewing_as"]
            assert identity["trust"]["reads_as"] == identity["trust"]["actions_as"]
            await wait_for(
                "document.querySelector('.finance-planning')?.textContent.includes('500 USD minor units to 501 USD minor units')"
            )
            await evaluate("""(() => {
              const select=document.querySelector('[aria-label="Projection context"]');
              if(select.options.length!==12) throw Error('context coverage lost');
              select.value='funding-chosen-plan';select.dispatchEvent(new Event('change'));
            })()""")
            await wait_for(
                "document.querySelector('.finance-planning').textContent.includes('800 USD minor units to 801 USD minor units') && document.querySelector('.finance-planning').textContent.includes('not bank cash')"
            )
            assert await evaluate(
                "document.querySelector('.finance-planning').textContent.includes('INCOMPLETE') && document.querySelector('.finance-planning').textContent.includes('Source refresh: OFF') && document.querySelector('.finance-planning').textContent.includes('Provider Clock Unknown')"
            )
            await evaluate(
                "document.querySelector('.finance-planning').scrollIntoView({block:'start'})"
            )
            planning_shot = await call("Page.captureScreenshot", {"format": "png"})
            planning_output = Path(
                os.environ.get("FINANCE_BROWSER_ARTIFACT_DIR", str(tmp_path))
            )
            planning_output.mkdir(parents=True, exist_ok=True)
            (planning_output / "finance-planning-synthetic.png").write_bytes(
                base64.b64decode(planning_shot["data"])
            )
            await evaluate(
                "Array.from(document.querySelectorAll('.finance-planning button')).find(b=>b.textContent==='Show dated events').click()"
            )
            await wait_for(
                "document.querySelector('.finance-planning').textContent.includes('Full context manifest: 2 events')"
            )
            await wait_for(
                "Array.from(document.querySelectorAll('.finance-choices button')).some(b => b.textContent === 'Add a goal' && !b.disabled)"
            )
            await evaluate(
                "Array.from(document.querySelectorAll('.finance-choices button')).find(b => b.textContent === 'Add a goal').click()"
            )
            await evaluate("""(() => {
              const f=document.querySelector('.dialog-form');
              f.querySelector('input').value='Synthetic travel goal';
              f.querySelectorAll('select')[0].value='purchase';
              f.querySelector('input[inputmode="decimal"]').value='12.34';
              f.querySelectorAll('select')[1].value='preferred';
              f.requestSubmit();
            })()""")
            await wait_for(
                "document.querySelector('.finance-choices').textContent.includes('Synthetic travel goal') && document.querySelector('.finance-choices').textContent.includes('$12.34')"
            )
            await evaluate(
                "Array.from(document.querySelectorAll('.finance-choices button')).find(b => b.textContent === 'Pause').click()"
            )
            await wait_for(
                "document.querySelector('.finance-choices').textContent.includes('Paused')"
            )
            await evaluate(
                "Array.from(document.querySelectorAll('.finance-choices button')).find(b => b.textContent === 'Choose a preference').click()"
            )
            await evaluate("""(() => {
              const f=document.querySelector('.dialog-form');const controls=f.querySelectorAll('select');
              controls[0].value='grocery_substitution';controls[0].dispatchEvent(new Event('change'));
              controls[1].value='keep_menu';f.requestSubmit();
            })()""")
            await wait_for(
                "document.querySelector('.finance-choices').textContent.includes('Keep Menu')"
            )
            await call("Page.reload")
            await wait_for(
                "Boolean(document.querySelector('.finance-choices')) && document.querySelector('.finance-choices').textContent.includes('Keep Menu') && document.querySelector('.finance-choices').textContent.includes('Paused')"
            )
            await evaluate(
                "document.querySelector('.finance-choices').scrollIntoView({block:'start'})"
            )
            screenshot = await call("Page.captureScreenshot", {"format": "png"})
            output = Path(os.environ.get("FINANCE_BROWSER_ARTIFACT_DIR", str(tmp_path)))
            output.mkdir(parents=True, exist_ok=True)
            (output / "finance-choices-synthetic.png").write_bytes(
                base64.b64decode(screenshot["data"])
            )
    finally:
        receive.close()
        listener.close()
        if browser is not None:
            browser.terminate()
            try:
                await asyncio.to_thread(browser.wait, 5)
            except subprocess.TimeoutExpired:
                browser.kill()
                await asyncio.to_thread(browser.wait, 5)
        if browser_log is not None:
            browser_log.close()
        server.terminate()
        await asyncio.to_thread(server.join, 5)
        if server.is_alive():
            server.kill()
            await asyncio.to_thread(server.join, 5)
