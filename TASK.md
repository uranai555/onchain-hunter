# Phase 1: Hyperliquid Perp 勝ちウォレット探索

## 概要

Hyperliquidの`info`エンドポイントから候補ウォレットの約定履歴を取得し、スコアリングしてMarkdownレポートとCSVを出力するPythonスクリプト群。

## ディレクトリ構造

```
onchain_hunter/
├── config.yaml              ← 全設定（既存）
├── .env.example             ← コピーして .env に（既存）
├── requirements.txt         ← 依存パッケージ（既存）
├── src/
│   ├── collectors/
│   │   └── hyperliquid.py   ← Hyperliquid APIからfills取得
│   ├── scoring/
│   │   ├── perp_score.py    ← perp_score_v2 + gaming_resistance_score
│   │   └── filters.py       ← 除外フィルタ
│   ├── reports/
│   │   └── markdown.py      ← レポート生成
│   └── utils/
│       ├── io.py            ← 入出力ユーティリティ
│       └── time.py          ← 時間処理
├── data/
│   ├── candidate_hyperliquid_wallets.csv  ← 候補ウォレットリスト（手動投入）
│   └── hyperliquid_fills.parquet          ← 生データ保存
├── reports/                              ← 出力先
└── scripts/
    └── run_daily.py         ← 日次実行エントリポイント
```

## 実装するファイル

### 1. `src/collectors/hyperliquid.py`

- Hyperliquidのinfoエンドポイント `https://api.hyperliquid.xyz/info` にPOST
- `type: "userFills"` + `user: address` でウォレットの約定履歴取得
- 過去90日間のデータを取得
- 500件ページネーション対応（`startTime` パラメータ）
- 引数: addressリスト → 戻り値: DataFrame
- 全アドレスの結果をマージして `data/hyperliquid_fills.parquet` に保存
- 関数: `fetch_user_fills(address: str, lookback_days: int = 90) -> list[dict]`
- 関数: `fetch_all_wallets(config: dict) -> pd.DataFrame`

### 2. `src/scoring/perp_score.py`

実装するスコアリング（全て0-100正規化）：

**perp_score_v2:**
```
0.20 * realized_pnl_score
+ 0.15 * risk_adjusted_return_score
+ 0.15 * drawdown_control_score
+ 0.15 * consistency_score
+ 0.10 * liquidity_replicability_score
+ 0.10 * style_clarity_score
+ 0.10 * gaming_resistance_score
+ 0.05 * crowding_penalty_inverse
```

**gaming_resistance_score:**
```
0.25 * lot_size_naturalness_score
+ 0.20 * return_distribution_quality_score
+ 0.20 * trade_interval_naturalness_score
+ 0.15 * out_of_sample_survival_score
+ 0.10 * pnl_concentration_inverse
+ 0.10 * leverage_tail_risk_inverse
```

**最低除外条件:**
- 取引回数 < 30
- 最大利益トレード1件が総利益の50%以上
- 最大利益銘柄1つが総利益の60%以上
- profit factor < 1.2
- 直近30Dだけプラスで90D/180Dがマイナス
- realized PnLプラスだがunrealized lossがrealized profitの50%以上

**トレードスタイル分類:**
- トレンドフォロー型 / 逆張り型 / ニュース反応型 / 高レバスキャル型 / 片張りホールド型 / マーケットメイクっぽい

### 3. `src/scoring/filters.py`

除外フィルタ:
- CEXウォレット除外（既知のアドレスパターン）
- 取引回数不足除外
- 単一銘柄依存除外
- 極端なリスク行動除外
- 関数: `apply_exclusion_filters(df_scores: pd.DataFrame, config: dict) -> pd.DataFrame`

### 4. `src/reports/markdown.py`

- 関数: `generate_daily_report(wallets_df: pd.DataFrame, config: dict) -> str`
  - Markdown出力（各ウォレットの全項目）
  - A/B/C/Dランク別グループ
  - 除外されたウォレットの理由付きリスト
- 関数: `generate_csv(wallets_df: pd.DataFrame, path: str)`

### 5. `scripts/run_daily.py`

日次実行パイプライン:
1. `config.yaml` 読み込み
2. `candidate_hyperliquid_wallets.csv` からアドレスリスト取得
3. `fetch_all_wallets(config)` でデータ収集
4. `perp_score_v2` でスコアリング + ゲーミング耐性
5. 除外フィルタ適用
6. レポート生成（Markdown + CSV + Parquet保存）
7. `reports/` に出力

### 6. `src/utils/io.py`

- `load_config(path: str) -> dict`
- `load_candidate_wallets(path: str) -> list[str]`
- その他汎用IO

### 7. `src/utils/time.py`

- `filter_by_lookback(df: pd.DataFrame, days: int) -> pd.DataFrame`
- その他時間関連

## 制約

- hyperliquid-api ライブラリを使わず、`requests` で直接infoエンドポイントにPOST
- 全スコアは0-100に正規化
- データ保存はParquet形式
- レポートは日本語（ウォレットアドレス以外）
- テストは不要（後日追加）
- このタスクは独立したGitHubリポジトリ `tomamu/onchain-hunter` で行う
- HermesがKanbanライフサイクルを所有する。Codexはコミットして完了報告するまで
