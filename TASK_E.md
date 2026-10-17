# E: Onchain #9 — Remaining small fixes

## 対象PR
uranai555/onchain-hunter #9 (HEAD 2167122)
ブランチ: swe2/short/leaderboard-conflict-fix-20261017173304

## 指示
以下のP2指摘を修正すること。

### 1. .env 挙動の一致
- HL_MAX_RETRIES=0 でも初回1リクエストを保持するよう実装と設定ドキュメントを一致

### 2. Unrealized-loss旗の反映
- unrealized-lossフラグを最終フィルターで反映
- 期間別実現損益が正しく変化する動的テスト追加

### 禁止
- 実資金取引（対象外、read-only維持）

### 完了条件
- pytest全PASS
- コミット: git add -A && git commit -m "fix: env consistency and unrealized PnL flag"
- 変更ファイル、テスト結果、HEAD SHAを報告