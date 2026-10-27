"""Hosted Chromium acceptance of Finance views and private choices.

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

from adminme.runtime.application import create_application
from tests.acceptance.test_openclaw_native_seam import PRINCIPAL
from tests.fixtures.finance_planning import (
    with_boundary_windows,
    with_unavailable_openings,
    with_conservative_minima,
    with_grouped_issues,
)


def _serve_choices(root, listener, ready):
    from tests.acceptance.test_finance_activation import configured, fixture
    from tests.fixtures.finance_activation import activate

    app = create_application(configured(root))
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with original(application):
            runtime = application.state.runtime
            value, planned, package = fixture(
                runtime.settings.instance.tenant_id, total_events=24
            )
            planned = with_grouped_issues(
                with_conservative_minima(
                    with_unavailable_openings(with_boundary_windows(planned))
                )
            )
            manifest, packet = await activate(
                application,
                {"Tailscale-User-Login": "fixture-principal.ts.net"},
                value,
                planned,
                package,
                advisor=False,
            )
            (root / "reviewed-manifest.json").write_text(json.dumps(manifest))
            (root / "closed-package.json").write_text(json.dumps(packet))
            ready.send(True)
            ready.close()
            yield

    app.router.lifespan_context = lifespan
    uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False)).run(
        sockets=[listener]
    )


async def _press_enter(call):
    for event in ("keyDown", "keyUp"):
        await call(
            "Input.dispatchKeyEvent",
            {
                "type": event,
                "key": "Enter",
                "code": "Enter",
                "windowsVirtualKeyCode": 13,
            },
        )


async def _assert_finance_view_navigation(call, evaluate, wait_for):
    views = ("overview", "cashflow", "scenarios", "spending", "networth", "health")
    assert await evaluate(
        "Array.from(document.querySelectorAll('.finance-books-controls [data-finance-view]')).map(b => b.dataset.financeView)"
    ) == list(views)
    for view in views:
        await evaluate(
            f"document.querySelector('.finance-books-controls [data-finance-view={view}]').click()"
        )
        await wait_for(
            f"new URLSearchParams(location.search).get('view') === '{view}' && document.querySelector('.finance-books-controls [data-finance-view={view}]')?.getAttribute('aria-pressed') === 'true'"
        )
        assert await evaluate(
            "document.querySelectorAll('.finance-books-controls [aria-pressed=true]').length === 1 && document.querySelectorAll('.finance-kpi, .finance-cashflow, .finance-budget, .finance-scenarios').length === 0"
        )
    await evaluate("history.back()")
    await wait_for(
        "new URLSearchParams(location.search).get('view') === 'networth' && document.querySelector('[data-finance-view=networth]')?.getAttribute('aria-pressed') === 'true'"
    )
    await evaluate("document.querySelector('[data-finance-view=overview]').focus()")
    await _press_enter(call)
    await wait_for(
        "new URLSearchParams(location.search).get('view') === 'overview' && document.querySelector('[data-finance-view=overview]')?.getAttribute('aria-pressed') === 'true'"
    )
    await wait_for("Boolean(document.querySelector('.finance-choices'))")


async def _assert_finance_projection_controls(call, evaluate, wait_for):
    # Keep the production HTTP reads, altering only selected synthetic responses
    # to exercise browser recovery and delivery ordering without private data.
    await evaluate("""(() => {
      const probe = window.__financeBrowserProbe = {
        original: window.fetch, calls: [], mode: null, release: null
      };
      window.fetch = async (input, init) => {
        const url = new URL(typeof input === 'string' ? input : input.url, location.href);
        if (url.pathname !== '/api/member/v1/finance/planning') return probe.original.call(window, input, init);
        const report = url.searchParams.get('report_id');
        probe.calls.push({report, context: url.searchParams.get('context_id'), days: Number(url.searchParams.get('horizon_days'))});
        const mode = report === 'planning_projection' || (report === 'planning_contexts' && probe.mode === 'hold-index') ? probe.mode : null;
        if (mode) probe.mode = null;
        if (mode === 'fail') throw new TypeError('Synthetic planning transport failure');
        const response = await probe.original.call(window, input, init);
        if (!mode) return response;
        const body = await response.json();
        if (mode === 'stale-projection') body.projection.head_id = 'synthetic-newer-projection';
        if (mode === 'stale-accounting') body.publication.current_head_publication_id = 'synthetic-newer-publication';
        const replacement = new Response(JSON.stringify(body), {status: response.status, headers: response.headers});
        if (mode === 'hold' || mode === 'hold-failure' || mode === 'hold-index') {
          return new Promise((resolve, reject) => {
            probe.release = () => mode === 'hold-failure' ? reject(new TypeError('Synthetic delayed failure')) : resolve(replacement);
          });
        }
        return replacement;
      };
    })()""")
    try:
        await wait_for("Boolean(document.querySelector('.finance-planning-summary'))")
        assert await evaluate("""(() => {
          const spendable = document.querySelector('[data-finance-metric=spendable]');
          const summary = document.querySelector('.finance-planning-summary');
          return spendable.textContent.includes('Not established') &&
            !/USD|[$][0-9]/.test(spendable.textContent) &&
            summary.textContent.replaceAll('\u00a0', ' ').includes('USD 5.00 to USD 5.01') &&
            summary.textContent.includes('Cash safety: not established for today.') &&
            !summary.textContent.includes('Safe to spend');
        })()""")

        await evaluate("""(() => {
          window.__financeBrowserProbe.mode = 'hold-index';
          window.__financeBrowserProbe.release = null;
          document.querySelector('[data-finance-action=refresh]').click();
        })()""")
        await wait_for("Boolean(window.__financeBrowserProbe.release)")
        assert await evaluate("""(() => {
          const controls = document.querySelector('.finance-planning-controls');
          return controls.hidden && getComputedStyle(controls).display === 'none' &&
            !document.querySelector('.finance-planning-summary');
        })()"""), "Loading the context index must hide stale selections and amounts"
        await evaluate("window.__financeBrowserProbe.release()")
        await wait_for("Boolean(document.querySelector('.finance-planning-summary'))")

        # A comparison is an explicit bounded request, with no event acquisition.
        await evaluate("document.querySelector('[data-finance-view=cashflow]').click()")
        await wait_for("Boolean(document.querySelector('.finance-planning-summary'))")
        await evaluate("window.__financeBrowserProbe.calls = []")
        assert await evaluate(
            "document.querySelector('[data-finance-comparison]') === null"
        )
        await evaluate(
            "document.querySelector('[data-finance-compare=horizons]').click()"
        )
        await wait_for(
            "Boolean(document.querySelector('[data-finance-comparison]')) && window.__financeBrowserProbe.calls.filter(c => c.report === 'planning_projection').length === 5"
        )
        requests = await evaluate("window.__financeBrowserProbe.calls")
        assert len(requests) == 5, requests
        assert {row["report"] for row in requests} == {"planning_projection"}
        assert sorted(row["days"] for row in requests) == [7, 30, 90, 180, 365]
        assert len({row["context"] for row in requests}) == 1
        await wait_for(
            "document.querySelector('[data-finance-comparison]')?.textContent.includes('365')"
        )
        await evaluate("window.__financeBrowserProbe.calls = []")
        await evaluate(
            "document.querySelector('[data-finance-compare=scenarios]').click()"
        )
        await wait_for(
            "document.querySelectorAll('[data-finance-comparison] li').length === 3"
        )
        requests = await evaluate("window.__financeBrowserProbe.calls")
        assert len(requests) == 3, requests
        assert {row["context"] for row in requests} == {
            "bank-current-base",
            "bank-expected-base",
            "bank-chosen-base",
        }
        assert await evaluate(
            "document.querySelector('[data-finance-comparison]').textContent.includes('Spending case:')"
        )
        await call(
            "Emulation.setDeviceMetricsOverride",
            {"width": 390, "height": 850, "deviceScaleFactor": 1, "mobile": False},
        )
        assert await evaluate("document.documentElement.scrollWidth <= innerWidth"), (
            "The open horizon comparison must fit a 390px viewport"
        )
        await call(
            "Emulation.setDeviceMetricsOverride",
            {"width": 1280, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
        )
        await evaluate("document.querySelector('[data-finance-view=overview]').click()")
        await wait_for("Boolean(document.querySelector('.finance-planning-summary'))")

        # Each independently superseded head must be visible and refreshable.
        for mode, days in (("stale-projection", "90"), ("stale-accounting", "180")):
            await evaluate(
                f"(() => {{ window.__financeBrowserProbe.mode = '{mode}'; const horizon = document.querySelector('[aria-label=\"Projection horizon\"]'); horizon.value = '{days}'; horizon.dispatchEvent(new Event('change')); }})()"
            )
            await wait_for("Boolean(document.querySelector('.finance-stale-notice'))")
            assert await evaluate(
                "document.querySelector('[data-finance-metric=spendable]').textContent.includes('Not established')"
            )
            await evaluate(
                "document.querySelector('[data-finance-action=refresh]').click()"
            )
            await wait_for(
                "Boolean(document.querySelector('.finance-planning-summary')) && !document.querySelector('.finance-stale-notice')"
            )

        await evaluate("""(() => {
          window.__financeBrowserProbe.mode = 'fail';
          const horizon = document.querySelector('[aria-label="Projection horizon"]');
          horizon.value = '7'; horizon.dispatchEvent(new Event('change'));
        })()""")
        await wait_for(
            "Boolean(document.querySelector('.finance-planning-retry:not([hidden])'))"
        )
        assert await evaluate(
            "document.querySelector('.finance-planning-summary') === null"
        )
        await evaluate("document.querySelector('.finance-planning-retry').focus()")
        await _press_enter(call)
        await wait_for(
            "Boolean(document.querySelector('.finance-planning-summary')) && !document.querySelector('.finance-planning-retry:not([hidden])')"
        )

        # Neither a late success nor a late failure may replace the newer choice.
        for mode in ("hold", "hold-failure"):
            await evaluate(f"""(() => {{
              const probe = window.__financeBrowserProbe;
              probe.mode = '{mode}'; probe.release = null;
              const context = document.querySelector('[aria-label="Projection context"]');
              context.value = 'bank-expected-base'; context.dispatchEvent(new Event('change'));
            }})()""")
            await wait_for("Boolean(window.__financeBrowserProbe.release)")
            await evaluate("""(() => {
              const context = document.querySelector('[aria-label="Projection context"]');
              context.value = 'funding-chosen-plan'; context.dispatchEvent(new Event('change'));
            })()""")
            await wait_for(
                "document.querySelector('.finance-planning-summary')?.textContent.replaceAll('\u00a0', ' ').includes('USD 8.00 to USD 8.01')"
            )
            await evaluate("window.__financeBrowserProbe.release()")
            await evaluate(
                "new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
            )
            assert await evaluate(
                "document.querySelector('[aria-label=\"Projection context\"]').value === 'funding-chosen-plan' && document.querySelector('.finance-planning-summary').textContent.replaceAll('\u00a0', ' ').includes('USD 8.00 to USD 8.01') && !document.querySelector('.finance-planning-retry:not([hidden])')"
            )
        await evaluate("document.querySelector('[data-finance-action=reset]').focus()")
        await _press_enter(call)
        await wait_for(
            "document.querySelector('[aria-label=\"Projection context\"]')?.value === 'bank-current-base' && document.querySelector('[aria-label=\"Projection horizon\"]')?.value === '30' && document.querySelector('.finance-planning-summary')?.textContent.replaceAll('\u00a0', ' ').includes('USD 5.00 to USD 5.01')"
        )
        assert await evaluate("""(() => {
          const params = new URLSearchParams(location.search);
          return (!params.has('finance_context') || params.get('finance_context') === 'bank-current-base') &&
            (!params.has('finance_horizon') || params.get('finance_horizon') === '30');
        })()""")
    finally:
        await evaluate("""(() => {
          const probe = window.__financeBrowserProbe;
          if (probe.release) probe.release();
          window.fetch = probe.original;
          delete window.__financeBrowserProbe;
        })()""")


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
            # Every accepted-Books view keeps one source of planning truth.
            assert await evaluate(
                "document.querySelector('.finance-hero').textContent.includes('YOUR HOUSEHOLD BOOKS')"
            )
            assert (
                await evaluate(
                    "document.querySelectorAll('.finance-kpi, .finance-cashflow, .finance-budget, .finance-scenarios').length"
                )
                == 0
            )
            await _assert_finance_view_navigation(call, evaluate, wait_for)
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
                shot = await call("Page.captureScreenshot", {"format": "png"})
                (dashboard_output / f"finance-books-{name}.png").write_bytes(
                    base64.b64decode(shot["data"])
                )
                fits = await evaluate(
                    "document.documentElement.scrollWidth <= innerWidth"
                )
                if not fits:
                    overflow = await evaluate("""Array.from(document.querySelectorAll('body *')).map(e => {
                      const r = e.getBoundingClientRect();
                      return {tag:e.tagName, class:e.className, left:r.left, right:r.right, width:r.width, height:r.height, text:e.textContent.slice(0,120)};
                    }).filter(r => r.width > 0 && r.height > 0 && (r.right > innerWidth + 1 || r.left < -1)).slice(-80)""")
                    (
                        dashboard_output / f"finance-books-{name}-overflow.json"
                    ).write_text(json.dumps(overflow, indent=2))
                    raise AssertionError(f"{name} page overflow: {overflow}")
            await call(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": 1280,
                    "height": 1000,
                    "deviceScaleFactor": 1,
                    "mobile": False,
                },
            )
            await _assert_finance_projection_controls(call, evaluate, wait_for)
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
            await evaluate(
                "document.querySelector('.finance-event-list details > summary').focus()"
            )
            await _press_enter(call)
            await wait_for(
                "document.querySelector('.finance-event-list details')?.open"
            )
            assert await evaluate(
                "document.querySelector('.finance-event-list details').textContent.includes('Published route:') && document.querySelector('.finance-event-list details').textContent.includes('Bank effect:') && document.querySelector('.finance-event-list details').textContent.includes('Debt effect:')"
            )
            await evaluate(
                "document.querySelector('.finance-event-list details button').focus()"
            )
            await _press_enter(call)
            await wait_for(
                "document.querySelector('.finance-event-list').textContent.includes('Protected reference; source content is not included.')"
            )
            await call(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": 390,
                    "height": 850,
                    "deviceScaleFactor": 1,
                    "mobile": False,
                },
            )
            assert await evaluate(
                "document.documentElement.scrollWidth <= innerWidth"
            ), "Opened event evidence must fit a 390px viewport"
            await evaluate(
                "document.querySelector('.finance-event-list details').scrollIntoView({block:'start'})"
            )
            event_shot = await call("Page.captureScreenshot", {"format": "png"})
            (planning_output / "finance-event-evidence-narrow.png").write_bytes(
                base64.b64decode(event_shot["data"])
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
            await wait_for(
                "document.querySelector('[aria-label=\"Projection context\"]')?.value === 'funding-chosen-plan' && document.querySelector('[aria-label=\"Projection horizon\"]')?.value === '365' && document.querySelector('.finance-planning-summary')?.textContent.replaceAll('\u00a0', ' ').includes('USD 8.00 to USD 8.01')"
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
                "document.querySelector('.finance-metadata-settings [name=cos_metadata]') === null"
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
            await wait_for(
                "document.querySelector('.finance-access-settings')?.textContent.includes('Books dashboard enabled.')"
            )
            await evaluate("""(() => {
              const card = document.querySelector('.finance-access-settings');
              card.querySelector('[name=books_enabled]').checked = false;
              card.querySelector('[name=cos_analysis_enabled]').checked = false;
              card.querySelector('form').requestSubmit();
            })()""")
            await wait_for(
                "document.querySelector('.finance-access-settings')?.textContent.includes('Books access saved.')"
            )
            await call("Page.reload")
            await wait_for(
                "document.querySelector('.finance-access-settings')?.textContent.includes('Books dashboard disabled. CoS Finance analysis disabled.')"
            )
            await evaluate("""(() => {
              const card = document.querySelector('.finance-access-settings');
              card.querySelector('[name=books_enabled]').checked = true;
              card.querySelector('[name=books_enabled]').dispatchEvent(new Event('change'));
              if (card.querySelector('[name=cos_analysis_enabled]').disabled) throw new Error('Advisor control must be available');
              if (card.querySelector('[name=cos_analysis_enabled]').checked) throw new Error('Books access must not enable analysis');
              card.querySelector('[name=cos_analysis_enabled]').checked = false;
              card.querySelector('form').requestSubmit();
            })()""")
            await wait_for(
                "document.querySelector('.finance-access-settings')?.textContent.includes('Books access saved.')"
            )
            await evaluate(
                "Array.from(document.querySelectorAll('.finance-access-settings details')).at(-1).open=true"
            )

            async def file_input(index, path):
                document = await call("DOM.getDocument")
                selected = await call(
                    "DOM.querySelectorAll",
                    {
                        "nodeId": document["root"]["nodeId"],
                        "selector": ".finance-access-settings input[type=file]",
                    },
                )
                await call(
                    "DOM.setFileInputFiles",
                    {"nodeId": selected["nodeIds"][index], "files": [str(path)]},
                )

            await file_input(0, tmp_path / "instance" / "reviewed-manifest.json")
            await wait_for(
                "document.querySelector('.finance-access-settings pre').textContent.includes('independently_reviewed_evidence')"
            )
            await evaluate("""(() => {
              const card = document.querySelector('.finance-access-settings');
              const review = Array.from(card.querySelectorAll('input[type=checkbox]')).at(-1);
              review.checked = true; review.dispatchEvent(new Event('change'));
              Array.from(card.querySelectorAll('button')).find(b=>b.textContent==='Approve this manifest for 30 minutes').click();
            })()""")
            await wait_for(
                "document.querySelector('.finance-access-settings').textContent.includes('This exact manifest is approved until')"
            )
            await file_input(1, tmp_path / "instance" / "closed-package.json")
            await evaluate(
                "Array.from(document.querySelectorAll('.finance-access-settings button')).find(b=>b.textContent==='Import exact approved snapshot').click()"
            )
            await wait_for(
                "document.querySelector('.finance-access-settings').textContent.includes('Approved snapshot accepted.')"
            )
            assert await evaluate(
                "document.querySelector('.finance-access-settings').textContent.includes('\"idempotent\": true')"
            )
            await evaluate(
                "document.querySelector('.finance-access-settings').scrollIntoView({block:'start'})"
            )
            activation_shot = await call("Page.captureScreenshot", {"format": "png"})
            (output / "finance-activation-synthetic.png").write_bytes(
                base64.b64decode(activation_shot["data"])
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
