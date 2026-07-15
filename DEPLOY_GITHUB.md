# Far girare il bot su GitHub (Actions + Pages)

Il bot può girare **da solo su GitHub**, gratis: GitHub Actions lo esegue in paper ogni ~5 minuti su
dati reali ETH e pubblica la dashboard su GitHub Pages, raggiungibile da qualsiasi browser.

Tutto è già pronto nel repo:
- workflow: `.github/workflows/paper-crypto.yml` (cron ogni 5 min + avvio manuale con reset)
- output pubblicato: cartella `docs/` → GitHub Pages
- stato persistente: `reports/crypto_ci/cumulative.json` (committato ad ogni giro)

## Opzione A — un solo comando (consigliata, serve GitHub CLI `gh`)

Dalla cartella `pattern_scout_bot`:

```bash
./deploy_github.sh IL_TUO_UTENTE_GITHUB   # es: ./deploy_github.sh gianlucasimonetti
```

Lo script: crea il repo, fa il push, abilita GitHub Pages e avvia il primo run.
Se non hai `gh`: `brew install gh && gh auth login` (una volta sola).

## Opzione B — manuale (senza gh)

1. Crea un repo vuoto su GitHub (es. `pattern-scout-eth`).
2. Dalla cartella `pattern_scout_bot`:
   ```bash
   git init && git add -A && git commit -m "Pattern Scout paper bot"
   git branch -M main
   git remote add origin https://github.com/IL_TUO_UTENTE/pattern-scout-eth.git
   git push -u origin main
   ```
3. Su GitHub: **Settings → Pages → Source: GitHub Actions**.
4. Su GitHub: **Settings → Actions → General → Workflow permissions → Read and write**.
5. Vai su **Actions**, apri "Pattern Scout — Paper Crypto (ETH)" e premi **Run workflow**
   (da lì puoi anche spuntare `reset` per ripartire da 100 USDT).

## Dove trovo la dashboard da remoto

Dopo il primo run andato a buon fine:

```
https://IL_TUO_UTENTE.github.io/NOME_REPO/
```

Esempio: `https://gianlucasimonetti.github.io/pattern-scout-eth/`

La pagina mostra capitale (100 USDT), equity, PnL netto, le **operazioni aperte con profitto
corrente** e lo storico chiuse. Si aggiorna ad ogni giro del workflow (~5 min); premi **Reload**
nel browser per vedere l'ultimo stato. Il pulsante **↺ Ripristina 100 USDT** azzera la vista.

## Note e limiti di GitHub Actions

- Il cron minimo è 5 minuti e i run schedulati possono ritardare sotto carico: adeguato per una
  strategia su candele 5m, ma non è un feed in tempo reale al secondo.
- I workflow schedulati vengono **disattivati dopo 60 giorni** di inattività del repo: basta un
  commit o un avvio manuale per riattivarli.
- Ogni giro fa un piccolo commit di stato: è normale. Per un funzionamento 24/7 senza commit
  frequenti puoi in alternativa far girare `paper-crypto` su un piccolo server/PC sempre acceso.
