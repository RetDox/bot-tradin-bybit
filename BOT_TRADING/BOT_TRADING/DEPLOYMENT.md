# Deploy BOT PRO

## Railway con Bybit

Bybit usa API key/secret e puo' girare su Railway. Il progetto ora supporta due motori:

- `EXCHANGE=mt5`: usa MetaTrader 5 locale
- `EXCHANGE=bybit`: usa Bybit API V5 via `pybit`

Su Railway usa Bybit, non MT5.

Railway installa `requirements.txt`, che contiene solo le dipendenze compatibili con Bybit/cloud. Per usare MT5 su Windows installa invece:

```powershell
pip install -r requirements-mt5.txt
```

### Variabili Railway consigliate

Imposta queste variabili nel progetto Railway:

```text
EXCHANGE=bybit
BOT_HOST=0.0.0.0
BOT_DEBUG=false
BYBIT_TESTNET=true
BYBIT_DEMO=false
BYBIT_DRY_RUN=true
BYBIT_FORCE_DEMO_ORDER=false
BYBIT_API_KEY=...
BYBIT_API_SECRET=...
BYBIT_CATEGORY=linear
BYBIT_SYMBOLS=XAUUSDT,BTCUSDT
BYBIT_ACCOUNT_SIZE_USDT=200
BYBIT_FIXED_QTY=0.30
BYBIT_INTERVAL=5
BYBIT_QUOTE_COIN=USDT
BYBIT_STRATEGY_MODE=ghost
BYBIT_GHOST_THRESHOLD=85
BYBIT_GHOST_OBSERVE_THRESHOLD=70
BYBIT_CONFIRM_INTERVAL=15
BYBIT_MIN_ADX=18
BYBIT_SL_ATR=1.2
BYBIT_TP_ATR=2.2
BYBIT_MAX_QTY=10
BYBIT_ORDER_COOLDOWN_SECONDS=300
BYBIT_BE_TRIGGER_ATR=0.6
BYBIT_BE_LOCK_ATR=0.1
```

Parti cosi': `BYBIT_TESTNET=true` e `BYBIT_DRY_RUN=true`.

`BYBIT_ACCOUNT_SIZE_USDT=200` fa calcolare le size come se il conto fosse da 200 USDT anche quando il wallet demo mostra un saldo molto piu' alto.

`BYBIT_FIXED_QTY=0.30` forza ogni nuova posizione a usare quantita' 0.30. Se vuoi tornare al calcolo automatico basato su rischio e stop loss, imposta `BYBIT_FIXED_QTY=0`.

Per lavorare principalmente sull'oro, usa `BYBIT_SYMBOLS=XAUUSDT` se il contratto e' disponibile sul tuo account Bybit. Se Bybit non accetta quel simbolo nella tua area/account, le alternative oro piu comuni sono `XAUTUSDT` o `PAXGUSDT`.

Per la modalita' GhostMode adattiva usa:

```text
BYBIT_STRATEGY_MODE=ghost
BYBIT_GHOST_THRESHOLD=85
BYBIT_GHOST_OBSERVE_THRESHOLD=70
```

GhostMode legge M1/M5/M15/H1, identifica stato mercato, calcola confidence score e salva ogni entrata/uscita in `trade_journal.jsonl`.

Per una logica piu' selettiva classica usa:

```text
BYBIT_STRATEGY_MODE=quality
BYBIT_CONFIRM_INTERVAL=15
BYBIT_MIN_ADX=18
BYBIT_SL_ATR=1.2
BYBIT_TP_ATR=2.2
```

La modalita' `strict` usa tutti i filtri originali. La modalita' `relaxed` usa trend EMA, momentum e RSI. La modalita' `quality` usa conferma multi-timeframe, ADX, volatilita' e pullback breakout: fa meno trade, ma cerca setup migliori.

### Notifiche Telegram

Per ricevere un messaggio sul telefono quando il bot apre una posizione, crea un bot con `@BotFather`, scrivigli almeno un messaggio da Telegram, poi imposta su Railway:

```text
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
DAILY_SUMMARY_TZ=Europe/Rome
DAILY_SUMMARY_HOUR=23
DAILY_SUMMARY_MINUTE=59
```

Le notifiche partono quando un ordine viene confermato come aperto, quando una posizione viene rilevata come chiusa e all'orario del riepilogo giornaliero.

Puoi richiedere il report quando vuoi scrivendo al bot Telegram:

```text
/report
```

Il blocco automatico dei trade dopo perdite o drawdown e' disattivato. Il report continua a mostrare PnL, trade chiusi e perdite consecutive.

Quando hai verificato log, segnali, sizing e dashboard:

```text
BYBIT_TESTNET=false
BYBIT_DRY_RUN=false
```

### Demo Trading Bybit

Bybit Demo Trading non e' la stessa cosa di Testnet. Per il conto demo devi creare le API key mentre sei nella sezione `Demo Trading` di Bybit e usare:

```text
BYBIT_TESTNET=false
BYBIT_DEMO=true
BYBIT_DRY_RUN=false
```

Il dominio usato dalla libreria diventa `api-demo.bybit.com`. Se lasci `BYBIT_DRY_RUN=true`, il bot non aprira' posizioni nemmeno sul demo.

Per verificare la pipeline ordini sul demo puoi attivare temporaneamente:

```text
BYBIT_FORCE_DEMO_ORDER=true
BYBIT_FORCE_SIDE=BUY
```

Il bot provera' ad aprire un solo ordine demo sul primo simbolo configurato. Dopo il test rimetti subito:

```text
BYBIT_FORCE_DEMO_ORDER=false
```

Prima di usare mainnet crea una API key Bybit con soli permessi necessari al trading. Non abilitare withdrawal. Se Bybit ti permette IP whitelist e Railway ti da un outbound IP stabile tramite networking adatto al tuo piano/setup, usala.

## Nota importante su MT5 e Railway

Il motore MT5 usa il package `MetaTrader5`, che comunica con il terminale MetaTrader 5 installato e loggato sulla stessa macchina. Railway esegue servizi cloud/container Linux: va bene per Flask/API, ma non e' adatto a far girare direttamente il motore MT5 di questo bot.

Per trading 24/7 la soluzione consigliata e' una VPS Windows con:

- MetaTrader 5 installato
- account broker loggato
- Algo Trading abilitato
- Python e dipendenze installate
- questo progetto avviato come processo persistente

Railway puo' essere usato solo come dashboard/API esterna se separi il motore trading su VPS Windows.

## Opzione consigliata: VPS Windows

1. Installa MetaTrader 5 sulla VPS.
2. Accedi al conto broker e abilita Algo Trading.
3. Installa Python.
4. Crea un virtual environment nella cartella del progetto:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

5. Avvia la dashboard localmente:

```powershell
python app.py
```

6. Per esporre la dashboard sulla rete della VPS:

```powershell
$env:BOT_HOST="0.0.0.0"
$env:BOT_PORT="5000"
python app.py
```

Prima di esporla pubblicamente, aggiungi autenticazione o proteggila dietro VPN/firewall. La dashboard contiene comandi di start/stop del bot.

## Opzione ibrida: Railway + VPS Windows

Usa Railway per una dashboard pubblica leggera e la VPS Windows per il bot reale. In questo caso bisogna aggiungere una piccola API sicura sul worker VPS, con token, e far comunicare Railway con quella API.

Questa architettura evita di mettere MetaTrader dentro Railway e mantiene MT5 dove puo' funzionare davvero.

## Comandi utili

Backtest:

```powershell
python backtest.py --symbol EURUSD --bars 5000
python backtest.py --symbol EURUSD --bars 10000 --optimize
```

Dashboard:

```powershell
python app.py
```

Variabili supportate:

- `BOT_HOST`: host Flask, default `127.0.0.1`
- `BOT_PORT`: porta Flask se `PORT` non e' impostata
- `PORT`: porta usata da molte piattaforme cloud
- `BOT_DEBUG`: `true` per debug locale, default `false`
