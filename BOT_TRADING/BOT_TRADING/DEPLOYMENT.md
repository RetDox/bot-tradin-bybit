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
BYBIT_API_KEY=...
BYBIT_API_SECRET=...
BYBIT_CATEGORY=linear
BYBIT_SYMBOLS=XAUUSDT
BYBIT_INTERVAL=5
BYBIT_QUOTE_COIN=USDT
```

Parti cosi': `BYBIT_TESTNET=true` e `BYBIT_DRY_RUN=true`.

Per lavorare principalmente sull'oro, usa `BYBIT_SYMBOLS=XAUUSDT` se il contratto e' disponibile sul tuo account Bybit. Se Bybit non accetta quel simbolo nella tua area/account, le alternative oro piu comuni sono `XAUTUSDT` o `PAXGUSDT`.

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
