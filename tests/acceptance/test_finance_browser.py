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
from tests.fixtures.finance_planning import (
    projection,
    with_boundary_windows,
    with_unavailable_openings,
    with_conservative_minima,
    with_grouped_issues,
)


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
                with_grouped_issues(
                    with_conservative_minima(
                        with_unavailable_openings(
                            with_boundary_windows(projection(value, 24))
                        )
                    )
                ),
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
            # An accepted Books owner sees one source of planning truth. A
            # stale legacy view URL must not restore the old workbook plan.
            assert await evaluate(
                "document.querySelector('.finance-hero').textContent.includes('YOUR HOUSEHOLD BOOKS')"
            )
            assert (
                await evaluate(
                    "document.querySelectorAll('.finance-kpi, .finance-cashflow, .finance-budget, .finance-scenarios, .finance-controls').length"
                )
                == 0
            )
            await evaluate(
                "history.replaceState({}, '', '?view=scenarios'); renderFinance(state.surface)"
            )
            await wait_for("Boolean(document.querySelector('.finance-choices'))")
            assert (
                await evaluate(
                    "document.querySelectorAll('.finance-scenarios, .finance-controls').length"
                )
                == 0
            )
            identity = await evaluate(
                "fetch('/api/member/v1/bootstrap').then(r => r.json()).then(x => ({member:x.member,trust:x.trust}))"
            )
            assert identity["member"]["authenticated_id"] == PRINCIPAL
            assert not identity["member"]["viewing_as"]
            assert identity["trust"]["reads_as"] == identity["trust"]["actions_as"]
            assert await evaluate("""(() => {
              const formatted = (value, currency) => publishedMoneyMinor(value, currency).replaceAll('\u00a0', ' ');
              return [formatted(8999999999999999, 'USD'), formatted(-1, 'USD'), formatted(1, 'JPY'), formatted(1, 'BHD'), formatted(null, 'USD'), formatted(123, 'ZZZ')];
            })()""") == [
                "USD 89,999,999,999,999.99",
                "-USD 0.01",
                "JPY 1",
                "BHD 0.001",
                "Unknown",
                "123 ZZZ minor units",
            ]
            await wait_for(
                "document.querySelector('.finance-planning')?.textContent.replaceAll('\u00a0', ' ').includes('USD 5.00 to USD 5.01')"
            )
            dashboard_output = Path(
                os.environ.get("FINANCE_BROWSER_ARTIFACT_DIR", str(tmp_path))
            )
            dashboard_output.mkdir(parents=True, exist_ok=True)
            for width, height, name in (
                (1280, 1000, "desktop"),
                (390, 850, "narrow-viewport"),
            ):
                await call(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": width,
                        "height": height,
                        "deviceScaleFactor": 1,
                        "mobile": False,
                    },
                )
                await evaluate("scrollTo(0, 0)")
                await asyncio.sleep(0.1)
                assert await evaluate(
                    "document.documentElement.scrollWidth <= innerWidth"
                ), name
                shot = await call("Page.captureScreenshot", {"format": "png"})
                (dashboard_output / f"finance-books-{name}.png").write_bytes(
                    base64.b64decode(shot["data"])
                )
            await call(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": 1280,
                    "height": 1000,
                    "deviceScaleFactor": 1,
                    "mobile": False,
                },
            )
            await evaluate("""(() => {
              const select=document.querySelector('[aria-label="Projection context"]');
              if(select.options.length!==24) throw Error('context coverage lost');
              select.value='funding-chosen-plan';select.dispatchEvent(new Event('change'));
            })()""")
            await wait_for(
                "document.querySelector('.finance-planning').textContent.replaceAll('\u00a0', ' ').includes('USD 8.00 to USD 8.01') && document.querySelector('.finance-planning').textContent.includes('not bank cash')"
            )
            assert await evaluate(
                "document.querySelector('.finance-planning').textContent.includes('INCOMPLETE') && document.querySelector('.finance-planning').textContent.includes('Source refresh: OFF') && document.querySelector('.finance-planning').textContent.includes('Provider Clock Unknown')"
            )
            await evaluate("""(() => {
              const select=document.querySelector('[aria-label="Projection context"]');
              select.value='funding-chosen-plan-verified';select.dispatchEvent(new Event('change'));
            })()""")
            await wait_for(
                "document.querySelector('.finance-planning').textContent.includes('UNAVAILABLE') && document.querySelector('.finance-planning').textContent.includes('Verified Opening Unavailable')"
            )
            await evaluate("""(() => {
              const select=document.querySelector('[aria-label="Projection context"]');
              select.value='funding-chosen-plan';select.dispatchEvent(new Event('change'));
            })()""")
            await wait_for(
                "document.querySelector('.finance-planning').textContent.replaceAll('\u00a0', ' ').includes('USD 8.00 to USD 8.01') && document.querySelector('.finance-planning').textContent.includes('Minimum date: 2026-03-08')"
            )
            await evaluate(
                "document.querySelector('.finance-gap-details details').open=true"
            )
            await wait_for(
                "document.querySelector('.finance-gap-details').textContent.includes('Required input:')"
            )
            await evaluate(
                "Array.from(document.querySelectorAll('.finance-gap-details button')).find(b=>b.textContent==='Evidence details').click()"
            )
            await wait_for(
                "document.querySelector('.finance-gap-details').textContent.includes('Source content is not included') && document.querySelector('.finance-gap-details').textContent.includes('Required input:')"
            )
            await evaluate(
                "document.querySelector('.finance-gap-details details').open=false"
            )
            await evaluate(
                "Array.from(document.querySelectorAll('.finance-gap-details details')).find(d=>d.querySelector('summary').textContent.includes('Source Issues 0')).open=true"
            )
            await wait_for(
                "document.querySelector('.finance-gap-details').textContent.includes('16 required of 16 original issues')"
            )
            await evaluate(
                "Array.from(document.querySelectorAll('.finance-gap-details button')).find(b=>b.textContent==='Original source issues').click()"
            )
            await wait_for(
                "document.querySelector('.finance-gap-details').textContent.includes('Required source issue: source-issue-000')"
            )
            assert await evaluate(
                "document.querySelector('.finance-planning').textContent.includes('Conservative minimum')"
            )
            await evaluate(
                "Array.from(document.querySelectorAll('.finance-gap-details details')).forEach(d=>d.open=false)"
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
                "document.querySelector('.finance-planning').textContent.includes('2026-03-06 – 2026-03-08') && document.querySelector('.finance-planning').textContent.includes('do not prove occurrence')"
            )
            await evaluate("""(() => {
              const s=document.querySelector('[aria-label="Projection horizon"]');
              s.value='365';s.dispatchEvent(new Event('change'));
            })()""")
            await wait_for(
                "document.querySelector('.finance-planning').textContent.includes('2027-03-08 exclusive')"
            )
            await evaluate(
                "Array.from(document.querySelectorAll('.finance-planning button')).find(b=>b.textContent==='Show dated events').click()"
            )
            await wait_for(
                "document.querySelector('.finance-planning').textContent.includes('2027-03-07 – 2027-03-10')"
            )
            await evaluate(
                "Array.from(document.querySelectorAll('.finance-planning h3')).find(h=>h.textContent==='Dated events').scrollIntoView({block:'start'})"
            )
            overlap_shot = await call("Page.captureScreenshot", {"format": "png"})
            (planning_output / "finance-overlap-synthetic.png").write_bytes(
                base64.b64decode(overlap_shot["data"])
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
            await call(
                "Page.navigate",
                {"url": f"http://127.0.0.1:{port}/member/settings?section=cos-work"},
            )
            await wait_for(
                "document.querySelector('.finance-metadata-settings')?.textContent.includes('Your metadata access is disabled.')"
            )
            await evaluate("""(() => {
              const card = document.querySelector('.finance-metadata-settings');
              card.querySelector('[name=owner_metadata]').checked = true;
              card.querySelector('form').requestSubmit();
            })()""")
            await wait_for(
                "document.querySelector('.finance-metadata-settings').textContent.includes('Your metadata access is enabled.')"
            )
            await evaluate(
                "Array.from(document.querySelectorAll('.finance-metadata-settings button')).find(b=>b.textContent==='Inspect retained bindings').click()"
            )
            await wait_for(
                "document.querySelector('.finance-metadata-result')?.dataset.state === 'populated'"
            )
            assert await evaluate(
                "!document.querySelector('.finance-metadata-settings [name=cos_metadata]').checked"
            )
            assert await evaluate(
                "!document.querySelector('.finance-metadata-result').textContent.includes('value_minor')"
            )
            await evaluate(
                "document.querySelector('.finance-metadata-settings').scrollIntoView({block:'start'})"
            )
            metadata_shot = await call("Page.captureScreenshot", {"format": "png"})
            (output / "finance-metadata-synthetic.png").write_bytes(
                base64.b64decode(metadata_shot["data"])
            )
            await evaluate("""(() => {
              const card = document.querySelector('.finance-metadata-settings');
              card.querySelector('[name=owner_metadata]').checked = false;
              card.querySelector('form').requestSubmit();
            })()""")
            await wait_for(
                "document.querySelector('.finance-metadata-settings').textContent.includes('History is preserved.')"
            )
            await call("Page.reload")
            await wait_for(
                "document.querySelector('.finance-metadata-settings')?.textContent.includes('Your metadata access is disabled.')"
            )
            assert await evaluate(
                "!document.querySelector('.finance-metadata-settings [name=owner_metadata]').checked && document.querySelector('.finance-metadata-result').textContent === ''"
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
