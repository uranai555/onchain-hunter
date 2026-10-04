# Strategy Fingerprint Module v1 — 設計書

## 概要

勝ちウォレットの全取引履歴から**戦略パターンを構造化抽出**し、模倣可能な形でレポートするモジュール。
「誰が勝ってるか」→「**どうやって勝ってるか**」に軸を移す。

## インプット

`data/hyperliquid_fills.parquet`（1行=1fill）

| フィールド | 型 | 用途 |
|---|---|---|
| wallet_address | str | ウォレット識別子 |
| coin | str | 取引銘柄（BTC, ETH, SOL, xyz:BRENTOIL 等） |
| side | B/A | Bid or Ask |
| px | float | 約定価格 |
| sz | float | 取引サイズ |
| dir | str | Open Short/Close Short/Open Long/Close Long/Sell/Buy |
| closedPnl | float | 確定損益 |
| time | int (ms) | タイムスタンプ |
| datetime | datetime (UTC) | パース済み時刻 |
| startPosition | str (float) | 取引前のポジションサイズ |
| crossed | bool | 成行か指値か（True=成行=スプレッド越え） |
| fee | float | 支払い手数料 |
| liquidation | str/None | 強制ロウカットフラグ |
| oid | int | オーダーID（紐付け用） |

## アウトプット

`reports/strategy_fingerprint/` 以下にウォレット別のYAMLレポートを出力。

```yaml
wallet: "0x17c3c8fd..."
fingerprint_version: 1
generated_at: "2026-07-04T12:00:00Z"

time_patterns:
  # 時間帯集中度
  session_bias: asian          # asian / european / us / balanced
  hour_entropy: 0.72           # 0=1時間に集中, 1=全時間帯に均等分散
  peak_hours: [8, 9, 10]       # UTCでの集中時間帯
  weekday_concentration: 0.65  # 曜日偏り（高い=特定曜日のみ取引）
  night_trading_ratio: 0.15    # UTC深夜(0-6時)の取引比率

coin_profile:
  # 銘柄選好
  primary_coins: ["xyz:BRENTOIL", "xyz:CL", "xyz:GOLD"]
  coin_count: 12               # 取扱銘柄数
  top3_concentration: 0.78     # 上位3銘柄への取引集中度
  rotation_score: 0.45         # 銘柄ローテーション頻度（高い=コロコロ変える）
  stablecoin_pairs_ratio: 0.0  # USDC等安定ペア比率
  synthetic_assets_ratio: 0.65 # 合成資産（xyz:系）比率

position_sizing:
  # ポジションサイジングパターン
  sizing_style: variable        # fixed / variable / scaling / martingale
  avg_position_usd: 42500
  position_std_pct: 0.82       # サイズのばらつき（高い=不規則）
  max_position_usd: 520000
  scaling_in_ratio: 0.23       # 分割エントリー比率
  scaling_out_ratio: 0.15      # 分割イグジット比率
  risk_per_trade_pct: 1.2      # 1トレードあたりリスク割合（推定）

direction_bias:
  net_direction: short_biased  # long_biased / short_biased / neutral / scalping
  long_ratio: 0.12             # ロング取引比率
  short_ratio: 0.88            # ショート取引比率
  directional_consistency: 0.75  # 方向一貫性（高い=逆張りせず一方向）

entry_exit:
  # エントリー/イグジット特性
  market_order_ratio: 0.68     # 成行比率（高い=即時執行重視）
  entry_spread_cost_bps: 1.2   # エントリースプレッドコスト
  avg_hold_ms: 3600000         # 平均ホールド時間（ms）
  hold_std_ms: 1800000         # ホールド時間のばらつき
  quick_trade_ratio: 0.35      # 1分未満クローズ比率（スキャル指標）
  overnight_ratio: 0.05        # 日跨ぎ比率

performance:
  # パフォーマンス分解
  total_pnl: 27705.67
  total_trades: 2000
  win_rate: 0.62
  avg_win: 45.20
  avg_loss: -18.50
  profit_factor: 70.22
  sharpe_approx: 2.1
  max_drawdown: 127.39
  best_coin: "xyz:BRENTOIL"   # 最大利益銘柄
  worst_coin: "HYPE"           # 最大損失銘柄
  pnl_by_session:              # セッション別PnL
    asian: 15200
    european: 8900
    us: 3605

risk_management:
  # リスク管理特性
  stop_loss_style: tight       # tight / moderate / loose / none
  estimated_stop_pct: 1.5      # 推定ストップ幅（%）
  max_adverse_excursion_pct: 3.2  # 最大不利価格変動（推定）
  position_size_vs_pnl_corr: 0.45 # サイズ×PnL相関（高い=確信度でサイズ変動）
  liquidation_count: 0         # 強制ロウカット回数

event_response:
  # イベント反応性
  pre_event_positioning_score: 10  # イベント前ポジショニング（0-10）
  event_reaction_delay_ms: 1500   # イベント発生からの平均反応時間
  event_win_rate: 0.67           # イベント時の勝率

bot_indicators:
  # ボット/自動化推定指標
  automation_likelihood: 0.85    # 0-1（高い=自動化濃厚）
  sub_second_trades_ratio: 0.12  # サブ秒トレード比率
  same_second_multiple_ratio: 0.08 # 同一秒の複数注文比率
  round_lot_bias: 0.05           # キリのいいロット比率
  regular_interval_score: 0.72   # 規則的取引間隔スコア
  hour_synced_pattern: true      # 毎時同じ分に取引するパターン
```

## 出力ファイル構成

```
reports/strategy_fingerprint/
├── index.md                          ← 全ウォレットサマリー+ランキング
├── 0x17c3c8fd...fingerprint.yaml     ← 1ウォレット1ファイル
├── 0x45d26f28...fingerprint.yaml
└── comparison/
    ├── sizing_comparison.csv         ← 全ウォレットのサイジング比較表
    ├── time_patterns_comparison.csv  ← 時間帯比較表
    └── coin_preference_comparison.csv ← 銘柄選好比較表
```

## 実装ファイル

### `src/analysis/fingerprint.py` (新規)

戦略指紋抽出エンジン。以下の分析関数を持つ：

```python
def extract_fingerprint(fills: pd.DataFrame, wallet: str) -> dict
    """1ウォレットのfillsから完全な指紋抽出"""

def extract_time_patterns(fills: pd.DataFrame) -> dict
    """時間帯パターン分析"""

def extract_coin_profile(fills: pd.DataFrame) -> dict
    """銘柄選好・ローテーション分析"""

def extract_position_sizing(fills: pd.DataFrame) -> dict
    """ポジションサイジング分析"""

def extract_direction_bias(fills: pd.DataFrame) -> dict
    """方向性バイアス分析"""

def extract_entry_exit(fills: pd.DataFrame) -> dict
    """エントリー/イグジット特性分析"""

def extract_performance(fills: pd.DataFrame) -> dict
    """パフォーマンス分解分析"""

def extract_risk_management(fills: pd.DataFrame) -> dict
    """リスク管理特性分析"""

def extract_bot_indicators(fills: pd.DataFrame) -> dict
    """自動化/ボット指標分析"""
```

### `src/analysis/report.py` (新規)

指紋データからのレポート生成：

```python
def generate_fingerprint_report(fingerprints: list[dict], output_dir: str) -> None
    """全ウォレットの指紋レポートと比較表を生成"""

def _generate_index(fingerprints: list[dict], output_dir: str) -> None
    """index.md 生成"""

def _generate_comparison(fingerprints: list[dict], output_dir: str) -> None
    """比較CSV生成"""
```

### `config.yaml` 追記

```yaml
fingerprint:
  enabled: true
  output_dir: reports/strategy_fingerprint
  min_trades_for_analysis: 50       # 分析に必要な最低取引回数
  report_top_wallets: 10            # 詳細レポート上限
```

### `scripts/run_daily.py` 追記

Phase 3（Hyperliquidスコアリング後）に挿入：

```python
# ---- Phase 3: Strategy Fingerprint Analysis ----
fingerprint_cfg = config.get("fingerprint", {})
if fingerprint_cfg.get("enabled", False):
    logger.info("Running strategy fingerprint analysis ...")
    from src.analysis.fingerprint import (
        extract_fingerprint,
    )
    from src.analysis.report import generate_fingerprint_report

    wallets_to_analyze = filtered_df["wallet_address"].unique().tolist()
    fingerprints = []
    for addr in wallets_to_analyze:
        wallet_fills = fills_df[fills_df["wallet_address"] == addr]
        if len(wallet_fills) >= fingerprint_cfg.get("min_trades_for_analysis", 50):
            fp = extract_fingerprint(wallet_fills, addr)
            fingerprints.append(fp)

    generate_fingerprint_report(fingerprints, fingerprint_cfg.get("output_dir", "reports/strategy_fingerprint"))
    logger.info("Fingerprint analysis complete for %d wallets", len(fingerprints))
```

## 分析アルゴリズム詳細

### 時間帯パターン (`_extract_time_patterns`)

1. `datetime`から `hour` と `weekday` を抽出
2. エントリー取引のみで時間帯分布計算（dirが'Open Long' or 'Open Short' or 'Buy'）
3. 各時間帯（0-23 UTC）の取引比率→エントロピー計算
4. セッション分類: Asian(0-8), European(8-16), US(16-24)
5. `night_trading_ratio` = 0-6時の取引数 / 全日取引数

### 銘柄選好 (`_extract_coin_profile`)

1. 全銘柄の取引回数・PnL・取引量を集計
2. `top3_concentration` = 上位3銘柄の取引量 / 全取引量
3. `rotation_score` = 銘柄切り替え回数 / 全取引回数（時系列で隣接取引の銘柄変更率）
4. `synthetic_assets_ratio` = 'xyz:' で始まる銘柄の取引量比率

### ポジションサイジング (`_extract_position_sizing`)

1. `sz * px = position_usd` でポジションサイズ計算
2. サイズの統計分布から `sizing_style` 判定:
   - CV < 0.2 → fixed（一定ロット）
   - CV 0.2-0.8 → variable（変動）
   - CV > 0.8 かつ 最大/中央値 > 10 → martingale（マーチンゲール臭）
3. `scaling_in_ratio` = 同一ウォレット・同一銘柄で5分以内に複数エントリーした割合
4. `scaling_out_ratio` = 同一ウォレット・同一銘柄で5分以内に複数イグジットした割合

### 方向性バイアス (`_extract_direction_bias`)

1. dirカラムで方向分類:
   - 'Open Long'/'Buy' → long
   - 'Open Short'/'Sell' → short
   - 'Close Long'/'Close Short' → 決済（方向判定から除外）
2. `directional_consistency` = 連続同一方向トレードの比率（高い=一方向に張り続ける）

### エントリー/イグジット特性 (`_extract_entry_exit`)

1. `market_order_ratio` = `crossed==True` の比率
2. 平均ホールド時間 = エントリーから決済までの経過時間（oidでペアリング）
   - 同一oid + 同一coinでエントリー→決済をペアリング
   - ペアリングできない場合は最終エントリーから最終決済の時間差で近似
3. `quick_trade_ratio` = ホールド時間 < 60秒のトレード比率
4. `overnight_ratio` = UTC 0時を跨いだトレード比率

### リスク管理 (`_extract_risk_management`)

1. トレード中の最大不利価格変動（MAE）を推定:
   - エントリー後、クローズ前までの価格変動範囲
2. `stop_loss_style` = 最大損失 / 平均ポジションサイズの比率で判定:
   - < 1% → tight
   - 1-3% → moderate
   - 3-5% → loose
   - > 5% → none
3. `estimated_stop_pct` = 上位5%の損失の中央値
4. `position_size_vs_pnl_corr` = ポジションサイズとPnLの相関係数（高い=確信度連動型）

### ボット指標 (`_extract_bot_indicators`)

1. `sub_second_trades_ratio` = 同一秒内に複数取引がある比率
2. `same_second_multiple_ratio` = 同一秒の同一coin複数取引比率
3. `regular_interval_score` = 取引間隔の規則性評価
   - 隣接取引の時間間隔を計算
   - 間隔が特定値（1s/5s/10s/60s/300s等）に集中してるか分布検定
4. `round_lot_bias` = szが「キリの良い数字」（1, 10, 100, 1000等）で取引した比率
5. `hour_synced_pattern` = 毎時同じ分（例: 毎時00分）に取引する習慣があるか
6. `automation_likelihood` = 上記全指標の加重合成

## テスト

`tests/test_fingerprint.py` に以下を追加:
- テスト用fillsデータフレームの生成（パターン別）
- 各抽出関数の値域テスト（0-1等）
- ボットパターン検出の検証
- エッジケース（空データ、1トレードのみ等）

## 制約

- 既存のデータモデルからパターン抽出のみ（新しいAPIコール不要）
- 全分析はベクトル化演算（pandas groupby/apply）で実装
- ウォレットごとに独立して計算可能（並列化容易）
- 出力は構造化YAML（機械可読）+ Markdown（人間可読）