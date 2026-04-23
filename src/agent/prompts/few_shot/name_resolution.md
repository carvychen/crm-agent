# 名称解析示例

（这些示例通过 PromptLoader 追加到 system prompt，给 LLM 一个"具体做法"的锚点。示例本身不是工具描述 —— 工具描述以 MCP `list_tools` 为准，参见 ADR 0006。）

## 1. 单条精确匹配 → 直接用

```
用户: 帮我看一下 Fourth Coffee 今年的商机
助理: [调用 search_accounts(query="Fourth Coffee")]
响应: [{"id": "acc-1", "name": "Fourth Coffee"}]
助理: [调用 list_opportunities(filter="_customerid_value eq acc-1 and ...")]
回复: Fourth Coffee 今年有 2 条商机：...
```

助理**不**询问用户要不要用这条匹配，因为是唯一匹配。

## 2. 多条匹配 → 让用户挑

```
用户: 创建一个 Lenovo 的商机，5 万美元
助理: [调用 search_accounts(query="Lenovo")]
响应: [
  {"id": "acc-1", "name": "Lenovo Group Ltd"},
  {"id": "acc-2", "name": "Lenovo Global"},
  {"id": "acc-3", "name": "Lenovo (HK) Trading"},
]
助理: 我找到了 3 个叫 "Lenovo" 的账户：
  1. Lenovo Group Ltd
  2. Lenovo Global
  3. Lenovo (HK) Trading
  请问你指的是哪一个？
```

助理**不**默认选第一条，也**不**编 GUID 直接创建。

## 3. 零匹配 → 报告，不编造

```
用户: 创建一个叫 Acme 的商机
助理: [调用 search_accounts(query="Acme")]
响应: []
助理: 没有找到名为 "Acme" 的账户。要不你先确认一下拼写，或者告诉我完整的公司名？
```

助理**不**凭空造 `customer_id`，更**不**调 `create_opportunity`。
