# Onchain Smart Money Hunter

オンチェーンで「勝っているように見える」ウォレットを疑い、再現可能な行動だけを残す分析パイプライン。

**設計思想:** [onchain-smart-money-hunter SKILL.md](../.hermes/skills/research/onchain-smart-money-hunter/SKILL.md)

## 3本柱

- **Phase 1** Hyperliquid Perp 勝ちウォレット探索 ← **実装中**
- **Phase 2** DeFi利回り探索 (DefiLlama + Dune/Flipside)
- **Phase 3** 草コイン/DEX上手いウォレット探索 (Solana/Base)

## 成果物

- `reports/hyperliquid_top_wallets_daily.md` — 日次トップウォレットレポート
- `reports/hyperliquid_wallet_profiles.csv` — 全ウォレット一覧
- `reports/defi_yield_watchlist.md` — 利回り候補
- `reports/dex_smart_wallets.md` — DEXウォレット候補
- `reports/copyability_backtest.md` — 後追い可能性検証

## セットアップ

```bash
cp .env.example .env
# .env にAPIキーを記入
pip install -r requirements.txt
```

## config

`config.yaml` で全設定を管理。
