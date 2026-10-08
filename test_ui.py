import subprocess, time, json, urllib.request, asyncio, websockets

async def run_test():
    chrome_path = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
    proc = subprocess.Popen([
        chrome_path,
        '--headless=new',
        '--remote-debugging-port=9224',
        '--disable-gpu',
        '--no-sandbox',
        'about:blank'
    ])
    try:
        await asyncio.sleep(2)
        tabs = json.loads(urllib.request.urlopen('http://127.0.0.1:9224/json').read())
        ws_url = tabs[0]['webSocketDebuggerUrl']
        
        async with websockets.connect(ws_url) as ws:
            msg_id = 0
            async def send_cmd(method, params=None):
                nonlocal msg_id
                msg_id += 1
                await ws.send(json.dumps({'id': msg_id, 'method': method, 'params': params or {}}))
                while True:
                    res = json.loads(await ws.recv())
                    if res.get('id') == msg_id:
                        return res.get('result', {})

            await send_cmd('Page.enable')
            await send_cmd('Runtime.enable')

            async def evaluate(expr):
                r = await send_cmd('Runtime.evaluate', {'expression': expr, 'returnByValue': True})
                return r.get('result', {}).get('value')

            # Navigate to login
            await send_cmd('Page.navigate', {'url': 'http://127.0.0.1:8000/login'})
            await asyncio.sleep(1)

            # Fill login and submit
            await evaluate("""
                document.querySelector('input[name="mobile"]').value = '9000000001';
                document.querySelector('input[name="password"]').value = 'demo123';
                document.querySelector('form').submit();
            """)
            await asyncio.sleep(2)
            url = await evaluate('window.location.href')
            print('URL after login:', url)

            # Navigate to explore
            await send_cmd('Page.navigate', {'url': 'http://127.0.0.1:8000/explore'})
            await asyncio.sleep(1)

            # Check if openBuySellModal exists
            has_func = await evaluate('typeof openBuySellModal')
            print('openBuySellModal type:', has_func)

            # Click BUY on RELIANCE
            res = await evaluate("""(() => {
                const btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === 'BUY');
                if (!btn) return 'BUY button not found';
                btn.click();
                const modal = document.getElementById('buySellModal');
                const submitBtn = document.getElementById('btnSubmitOrder');
                const warnFunds = document.getElementById('modalFundsWarning');
                return {
                    modalActive: modal.classList.contains('active'),
                    modalDisplay: window.getComputedStyle(modal).display,
                    modalOpacity: window.getComputedStyle(modal).opacity,
                    submitBtnDisabled: submitBtn.disabled,
                    submitBtnText: submitBtn.innerText,
                    warnFundsDisplay: window.getComputedStyle(warnFunds).display,
                    warnFundsText: document.getElementById('modalFundsWarningText').innerText,
                    modalCurrentLtp: typeof modalCurrentLtp !== 'undefined' ? modalCurrentLtp : 'undef',
                    modalWalletBalance: typeof modalWalletBalance !== 'undefined' ? modalWalletBalance : 'undef'
                };
            })()""")
            print('BUY Modal state:', json.dumps(res, indent=2))

            # Submit BUY order
            buy_submit_res = await evaluate("""(() => {
                const btn = document.getElementById('btnSubmitOrder');
                btn.click();
                return 'clicked submit';
            })()""")
            print('Clicked submit:', buy_submit_res)
            await asyncio.sleep(1)

            feedback_res = await evaluate("""(() => {
                const fb = document.getElementById('orderFeedback');
                return {
                    display: window.getComputedStyle(fb).display,
                    text: fb.innerText
                };
            })()""")
            print('Feedback after BUY submit:', json.dumps(feedback_res, indent=2))

            # Now try clicking SELL tab for RELIANCE
            sell_res = await evaluate("""(() => {
                const tabSell = document.getElementById('tabSell');
                tabSell.click();
                const submitBtn = document.getElementById('btnSubmitOrder');
                const warnHoldings = document.getElementById('modalHoldingsWarning');
                return {
                    submitBtnDisabled: submitBtn.disabled,
                    submitBtnText: submitBtn.innerText,
                    warnHoldingsDisplay: window.getComputedStyle(warnHoldings).display,
                    modalOwnedQty: typeof modalOwnedQty !== 'undefined' ? modalOwnedQty : 'undef',
                    windowUserHoldings: typeof window.userHoldings !== 'undefined' ? Object.keys(window.userHoldings) : 'undef'
                };
            })()""")
            print('SELL Modal state for RELIANCE:', json.dumps(sell_res, indent=2))

            # Now try SELL on a stock not owned (e.g. MARUTI)
            maruti_res = await evaluate("""(() => {
                openBuySellModal('MARUTI', 'SELL', 12500);
                const submitBtn = document.getElementById('btnSubmitOrder');
                const warnHoldings = document.getElementById('modalHoldingsWarning');
                return {
                    submitBtnDisabled: submitBtn.disabled,
                    submitBtnText: submitBtn.innerText,
                    warnHoldingsDisplay: window.getComputedStyle(warnHoldings).display,
                    warnHoldingsText: warnHoldings.innerText,
                    modalOwnedQty: typeof modalOwnedQty !== 'undefined' ? modalOwnedQty : 'undef'
                };
            })()""")
            print('SELL Modal state for MARUTI (unowned):', json.dumps(maruti_res, indent=2))

    finally:
        proc.terminate()

asyncio.run(run_test())
