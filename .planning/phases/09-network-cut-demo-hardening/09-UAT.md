---
status: testing
phase: 09-network-cut-demo-hardening
source: [09-VERIFICATION.md]
started: 2026-07-25T21:30:00Z
updated: 2026-07-25T21:30:00Z
---

## Current Test

number: 1
name: NETCUT-03 真機斷網彩排（≥3 次，含 ≥1 次型態 B 講話中途切換）
expected: |
  依 edge/NETWORK_CUT_REHEARSAL.md §0–§4 在 Genio 520（root@192.168.31.78）上執行至少 3 次演練，
  每次以 dump_recent_turns.py 取得客觀證據並回填 §5 結果表。
  每列 M1（降級決策延遲）< 2.0s（型態 B 理論上界 3.0s），無多秒靜默；§6 判定為 GO。
awaiting: user response

## Tests

### 1. NETCUT-03 真機斷網彩排（≥3 次，含 ≥1 次型態 B）
expected: 每列 M1（降級決策延遲）< 2.0s（型態 B 理論上界 3.0s），無多秒靜默；§6 判定為 GO。
result: [pending]

### 2. modeBadge pulse 動效與 5 秒輪詢靜默的瀏覽器實測
expected: 開學生頁點一下 airplaneSwitch，徽章明顯播放一次縮放動效；靜置 ≥15 秒（≥3 拍輪詢）徽章保持靜止不閃動。
result: [pending]

## Summary

total: 2
passed: 0
issues: 0
pending: 2
skipped: 0
blocked: 0

## Gaps
