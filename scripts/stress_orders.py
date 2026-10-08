#!/usr/bin/env python3
"""
scripts/stress_orders.py
Stress testing script for BharatInvest order placement.
Places 50 sequential orders (MARKET & LIMIT, DELIVERY & INTRADAY) and 10 concurrent orders.
Reports success/failure rates and all distinct failure reasons.
"""

import sys
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
import httpx

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"

# Target user for stress testing
DEMO_MOBILE = "9000000001"
DEMO_PASSWORD = "demo123"

# Instruments for testing
INSTRUMENTS = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ITC", 
    "SBIN", "TATAMOTORS", "ICICIBANK", "BHARTIARTL", "NIFTYBEES"
]

def login_and_get_client(base_url: str):
    client = httpx.Client(base_url=base_url, timeout=30.0, follow_redirects=True)
    resp = client.post("/login", data={"mobile": DEMO_MOBILE, "password": DEMO_PASSWORD})
    if resp.status_code != 200 or "session" not in client.cookies:
        raise RuntimeError(f"Login failed! Status: {resp.status_code}, URL: {resp.url}")
    return client

def place_single_order(client: httpx.Client, symbol: str, tx_type: str, o_type: str, p_type: str, qty: int, price: float = 0.0):
    start_t = time.perf_counter()
    data = {
        "symbol": symbol,
        "transaction_type": tx_type,
        "order_type": o_type,
        "product_type": p_type,
        "quantity": qty,
        "price": price,
        "trigger_price": 0.0
    }
    try:
        r = client.post("/action/order/place", data=data)
        elapsed = time.perf_counter() - start_t
        if r.status_code == 200:
            res_json = r.json()
            success = res_json.get("success", False)
            status = res_json.get("status")
            error = res_json.get("error")
            return {
                "http_status": r.status_code,
                "success": success,
                "status": status,
                "error": error,
                "message": res_json.get("message"),
                "elapsed": elapsed,
                "symbol": symbol,
                "tx_type": tx_type,
                "order_type": o_type,
                "product_type": p_type,
                "qty": qty
            }
        else:
            return {
                "http_status": r.status_code,
                "success": False,
                "status": "HTTP_ERROR",
                "error": f"HTTP {r.status_code}: {r.text[:120]}",
                "elapsed": elapsed,
                "symbol": symbol,
                "tx_type": tx_type,
                "order_type": o_type,
                "product_type": p_type,
                "qty": qty
            }
    except Exception as e:
        elapsed = time.perf_counter() - start_t
        return {
            "http_status": 0,
            "success": False,
            "status": "NETWORK_EXCEPTION",
            "error": str(e),
            "elapsed": elapsed,
            "symbol": symbol,
            "tx_type": tx_type,
            "order_type": o_type,
            "product_type": p_type,
            "qty": qty
        }

def get_current_prices(client: httpx.Client):
    try:
        r = client.get("/action/prices")
        if r.status_code == 200:
            return r.json().get("prices", {})
    except Exception:
        pass
    return {}

def run_stress_test():
    print("=" * 70)
    print("=== BHARATINVEST STRESS TESTING ORDERS ===")
    print(f"Target URL: {BASE_URL}")
    print(f"User: {DEMO_MOBILE}")
    print("=" * 70)

    # 1. Login
    print("\n[Step 1] Logging in...")
    main_client = login_and_get_client(BASE_URL)
    session_cookie = main_client.cookies.get("session")
    print("[OK] Logged in successfully. Session cookie acquired.")

    # 2. Fetch current prices
    prices = get_current_prices(main_client)
    print(f"[OK] Loaded market prices for {len(prices)} instruments.")

    results = []

    # 3. 50 Sequential Orders
    print("\n[Step 2] Executing 50 Sequential Orders...")
    tx_types = ["BUY", "SELL"]
    order_types = ["MARKET", "LIMIT"]
    product_types = ["DELIVERY", "INTRADAY"]

    for i in range(1, 51):
        sym = INSTRUMENTS[(i - 1) % len(INSTRUMENTS)]
        tx = tx_types[(i - 1) % len(tx_types)]
        o_type = order_types[(i // 2) % len(order_types)]
        p_type = product_types[(i // 3) % len(product_types)]
        qty = 1 if tx == "BUY" else 1

        curr_p = float(prices.get(sym, {}).get("price", 1000.0))
        if o_type == "LIMIT":
            # Vary limit price slightly around current market price, aligned to 0.05 tick size
            limit_p = round(curr_p * (1.001 if tx == "BUY" else 0.999), 2)
            limit_p = round(round(limit_p / 0.05) * 0.05, 2)
        else:
            limit_p = round(round(curr_p / 0.05) * 0.05, 2)

        res = place_single_order(main_client, sym, tx, o_type, p_type, qty, limit_p)
        results.append(res)
        status_label = "SUCCESS" if res["success"] else "FAIL"
        err_msg = f" - Reason: {res['error']}" if not res["success"] else f" - Status: {res.get('status')}"
        print(f"  [{i:02d}/50] {tx:4s} {o_type:6s} {p_type:8s} {qty}x {sym:<10s} -> [{status_label}]{err_msg} ({res['elapsed']*1000:.1f}ms)")
        time.sleep(0.05) # small natural gap between user clicks

    # 4. 10 Concurrent Orders
    print("\n[Step 3] Executing 10 Concurrent Orders (Simultaneous Blast)...")
    concurrent_results = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = []
        for j in range(1, 11):
            # Create a separate client sharing session cookie
            c = httpx.Client(base_url=BASE_URL, timeout=30.0, follow_redirects=True)
            c.cookies.set("session", session_cookie)
            sym = INSTRUMENTS[j % len(INSTRUMENTS)]
            tx = "BUY" if j % 2 == 1 else "SELL"
            o_type = "MARKET" if j % 3 != 0 else "LIMIT"
            p_type = "DELIVERY" if j % 2 == 1 else "INTRADAY"
            curr_p = float(prices.get(sym, {}).get("price", 1000.0))
            curr_p = round(round(curr_p / 0.05) * 0.05, 2)
            f = executor.submit(place_single_order, c, sym, tx, o_type, p_type, 1, curr_p)
            futures.append((j, f))

        for j, fut in futures:
            res = fut.result()
            concurrent_results.append(res)
            status_label = "SUCCESS" if res["success"] else "FAIL"
            err_msg = f" - Reason: {res['error']}" if not res["success"] else f" - Status: {res.get('status')}"
            print(f"  [Conc {j:02d}/10] {res['tx_type']:4s} {res['order_type']:6s} {res['product_type']:8s} {res['symbol']:<10s} -> [{status_label}]{err_msg} ({res['elapsed']*1000:.1f}ms)")

    all_results = results + concurrent_results

    # 5. Analysis and Summary Report
    total_orders = len(all_results)
    successful_orders = sum(1 for r in all_results if r["success"])
    failed_orders = total_orders - successful_orders
    success_rate = (successful_orders / total_orders) * 100 if total_orders > 0 else 0.0

    print("\n" + "=" * 70)
    print("=== STRESS TEST RESULTS SUMMARY ===")
    print("=" * 70)
    print(f"Total Orders Attempted:    {total_orders}")
    print(f"Sequential Orders:         {len(results)} ({sum(1 for r in results if r['success'])} passed, {sum(1 for r in results if not r['success'])} failed)")
    print(f"Concurrent Orders:         {len(concurrent_results)} ({sum(1 for r in concurrent_results if r['success'])} passed, {sum(1 for r in concurrent_results if not r['success'])} failed)")
    print(f"Total Successful Orders:   {successful_orders}")
    print(f"Total Failed Orders:       {failed_orders}")
    print(f"Overall Success Rate:      {success_rate:.1f}%")
    print("-" * 70)

    # Status distribution
    status_counts = Counter(r.get("status") or ("FAILED" if not r["success"] else "UNKNOWN") for r in all_results)
    print("Order Status Distribution:")
    for st, count in status_counts.items():
        print(f"  * {st:<18s}: {count:3d} ({count/total_orders*100:.1f}%)")

    # Distinct failure reasons
    failure_reasons = Counter(r.get("error") for r in all_results if not r["success"])
    print("\nDistinct Failure Reasons:")
    if failure_reasons:
        for reason, count in failure_reasons.items():
            print(f"  [FAIL] [{count:2d}x] {reason}")
    else:
        print("  [OK] None! All orders succeeded.")

    avg_latency = sum(r["elapsed"] for r in all_results) / total_orders * 1000
    print(f"\nAverage Request Latency:   {avg_latency:.1f} ms")
    print("=" * 70)

    return {
        "total": total_orders,
        "success": successful_orders,
        "failed": failed_orders,
        "success_rate": success_rate,
        "failure_reasons": dict(failure_reasons)
    }

if __name__ == "__main__":
    run_stress_test()
