# -*- coding: utf-8 -*-
"""server/agents — 說說學伴 TalkyBuddy 的 agent 模組集合。

所有 agent 遵循與 cloud_llm / diagnose 一致的設計原則：
- 離線降級路徑永遠可用（scaffold 題庫為主幹）
- 任何例外不往外拋，一律靜默降級
- 雲端路徑由 allow_cloud 閘門控制，allow_cloud=False 完全不出境
"""
