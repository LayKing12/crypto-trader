"""
check_ws.py — Diagnostic WebSocket Kraken
==========================================
Vérifie en autonomie :
  1. Connexion WebSocket Kraken
  2. Réception des prix en temps réel
  3. API REST OHLC Kraken (bougies pour indicateurs)
  4. Fear & Greed Index

Utilisation :
  python check_ws.py

Aucune clé API nécessaire — tout public.
"""
import asyncio
import json
import sys
import time
import httpx
import websockets

# Force UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

KRAKEN_WS = "wss://ws.kraken.com"
KRAKEN_REST = "https://api.kraken.com/0/public"
FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"

PAIRS_TO_TEST = ["XBT/USD", "ETH/USD", "SOL/USD"]
OHLC_PAIR = "XBTUSD"

GREEN = "\033[92m"
RED   = "\033[91m"
YELLOW = "\033[93m"
BLUE  = "\033[94m"
RESET = "\033[0m"
BOLD  = "\033[1m"

def ok(msg):   print(f"  {GREEN}[OK]{RESET} {msg}")
def fail(msg): print(f"  {RED}[FAIL]{RESET} {msg}")
def info(msg): print(f"  {BLUE}-->{RESET} {msg}")


# ── Test 1 : WebSocket Kraken ─────────────────────────────────────────────────

async def test_kraken_websocket(timeout: int = 15) -> bool:
    print(f"\n{BOLD}[1/4] WebSocket Kraken — wss://ws.kraken.com{RESET}")

    subscribe_msg = json.dumps({
        "event": "subscribe",
        "pair": PAIRS_TO_TEST,
        "subscription": {"name": "ticker"},
    })

    received: dict[str, float] = {}
    start = time.time()

    for attempt in range(1, 4):  # 3 tentatives max (rate-limit Kraken possible)
        try:
            if attempt > 1:
                info(f"Tentative {attempt}/3 dans 5s...")
                await asyncio.sleep(5)

            async with websockets.connect(KRAKEN_WS, ping_interval=20, open_timeout=10) as ws:
                ok("Connexion WebSocket etablie")
                await ws.send(subscribe_msg)
                info(f"Abonnement envoye pour : {', '.join(PAIRS_TO_TEST)}")

                async for raw in ws:
                    elapsed = time.time() - start
                    if elapsed > timeout:
                        break

                    msg = json.loads(raw)

                    # Events de contrôle
                    if isinstance(msg, dict):
                        event = msg.get("event", "")
                        if event == "subscriptionStatus" and msg.get("status") == "subscribed":
                            ok(f"  Abonne : {msg.get('pair', '?')}")
                        elif event == "subscriptionStatus" and msg.get("status") == "error":
                            fail(f"  Erreur abonnement : {msg.get('errorMessage')}")
                        continue

                    # Message ticker
                    if isinstance(msg, list) and len(msg) >= 4 and msg[2] == "ticker":
                        pair = msg[3]
                        price = float(msg[1]["c"][0])
                        ask   = float(msg[1]["a"][0])
                        bid   = float(msg[1]["b"][0])
                        vol   = float(msg[1]["v"][1])
                        received[pair] = price
                        ok(f"  {pair:10s}  prix={price:>12,.2f}  ask={ask:>12,.2f}  bid={bid:>12,.2f}  vol24h={vol:>12,.0f}")

                        if len(received) >= len(PAIRS_TO_TEST):
                            break

            if received:
                ok(f"{len(received)}/{len(PAIRS_TO_TEST)} paires reçues en {time.time()-start:.1f}s")
                return True
            fail("Aucun ticker reçu dans le delai imparti")
            return False

        except asyncio.TimeoutError:
            fail(f"Timeout ({timeout}s) — tentative {attempt}")
        except Exception as e:
            err = str(e)
            if "503" in err:
                info(f"Rate-limit Kraken (503) — tentative {attempt}/3")
            else:
                fail(f"Erreur WebSocket : {e}")
                return False

    fail("WebSocket Kraken inaccessible apres 3 tentatives")
    return False


# ── Test 2 : OHLC REST Kraken ────────────────────────────────────────────────

async def test_kraken_ohlc() -> bool:
    print(f"\n{BOLD}[2/4] REST OHLC Kraken — {KRAKEN_REST}/OHLC{RESET}")
    url = f"{KRAKEN_REST}/OHLC"
    params = {"pair": OHLC_PAIR, "interval": 60}  # bougies 1h

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

            if data.get("error"):
                fail(f"Erreur API : {data['error']}")
                return False

            result = data.get("result", {})
            candles = next((v for k, v in result.items() if k != "last"), [])

            if not candles:
                fail("Aucune bougie reçue")
                return False

            ok(f"{len(candles)} bougies 1h reçues pour {OHLC_PAIR}")
            last = candles[-1]
            # [time, open, high, low, close, vwap, volume, count]
            info(f"Dernière bougie — O:{float(last[1]):,.2f}  H:{float(last[2]):,.2f}  "
                 f"L:{float(last[3]):,.2f}  C:{float(last[4]):,.2f}  Vol:{float(last[6]):,.2f}")

            closes = [float(c[4]) for c in candles]
            ema20 = _quick_ema(closes, 20)
            ema200 = _quick_ema(closes, 200)
            info(f"EMA20={ema20:,.2f}  EMA200={ema200:,.2f}  "
                 f"Régime={'bull_trend' if ema20 > ema200 else 'bear_trend'}")
            return True

    except Exception as e:
        fail(f"Erreur OHLC : {e}")
        return False


# ── Test 3 : Fear & Greed ────────────────────────────────────────────────────

async def test_fear_greed() -> bool:
    print(f"\n{BOLD}[3/4] Fear & Greed Index — alternative.me{RESET}")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(FEAR_GREED_URL)
            resp.raise_for_status()
            data = resp.json()
            value = int(data["data"][0]["value"])
            label = data["data"][0]["value_classification"]
            ok(f"Index = {value}/100  ({label})")
            if value < 25:
                info("→ Peur extrême — signal d'achat potentiel (score 85)")
            elif value > 75:
                info("→ Cupidité extrême — prudence (score 25)")
            else:
                info("→ Zone neutre (score 50)")
            return True
    except Exception as e:
        fail(f"Erreur Fear & Greed : {e}")
        return False


# ── Test 4 : Kraken REST public (server time) ────────────────────────────────

async def test_kraken_rest_connectivity() -> bool:
    print(f"\n{BOLD}[4/4] Connectivité REST Kraken (Time endpoint){RESET}")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{KRAKEN_REST}/Time")
            resp.raise_for_status()
            data = resp.json()
            server_time = data["result"]["unixtime"]
            drift = abs(server_time - time.time())
            ok(f"Kraken server time reçu — drift horloge = {drift:.1f}s")
            if drift > 30:
                info(f"ATTENTION drift {drift:.0f}s — inoffensif pour paper trading.")
                info(f"Pour le trading LIVE, synchronise l'horloge Windows : w32tm /resync")
            return True
    except Exception as e:
        fail(f"REST Kraken inaccessible : {e}")
        return False


# ── Utilitaire EMA rapide (sans dépendances) ─────────────────────────────────

def _quick_ema(closes: list[float], period: int) -> float:
    if len(closes) < period:
        return closes[-1] if closes else 0.0
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = price * k + ema * (1 - k)
    return round(ema, 2)


# ── Runner ────────────────────────────────────────────────────────────────────

async def main():
    print(f"\n{BOLD}{'='*55}")
    print(f"  CryptoMind — Diagnostic WebSocket & API Kraken")
    print(f"{'='*55}{RESET}")

    results = await asyncio.gather(
        test_kraken_rest_connectivity(),
        test_kraken_ohlc(),
        test_fear_greed(),
        return_exceptions=True,
    )

    # WS en dernier — petit délai pour éviter rate-limit Kraken
    await asyncio.sleep(3)
    ws_ok = await test_kraken_websocket()

    rest_ok, ohlc_ok, fg_ok = [
        r if isinstance(r, bool) else False for r in results
    ]

    print(f"\n{BOLD}{'='*55}")
    print("  Résumé")
    print(f"{'='*55}{RESET}")
    checks = [
        ("REST Kraken (connectivity)", rest_ok),
        ("OHLC Kraken (bougies 1h)",  ohlc_ok),
        ("Fear & Greed Index",         fg_ok),
        ("WebSocket Kraken (ticker)",  ws_ok),
    ]
    all_ok = True
    for label, status in checks:
        sym = f"{GREEN}[OK]{RESET}  " if status else f"{RED}[FAIL]{RESET}"
        print(f"  {sym}  {label}")
        if not status:
            all_ok = False

    print()
    if all_ok:
        print(f"  {GREEN}{BOLD}Tout est operationnel -- pret pour le paper trading !{RESET}")
        print(f"  Lance le serveur : {YELLOW}uvicorn main:app --reload --port 8000{RESET}")
    else:
        print(f"  {RED}{BOLD}Des erreurs ont ete detectees -- corrige-les avant de demarrer.{RESET}")
        print(f"  Vérifie : connexion internet, pare-feu, .env")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
