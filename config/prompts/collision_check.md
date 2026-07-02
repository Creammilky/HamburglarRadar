你要判断一篇论文是否可能与用户当前研究方向“撞车”。

用户当前方向：
{{user_project_description}}

论文：
Title: {{title}}
Abstract: {{abstract}}

请输出严格 JSON：
{
  "collision_risk": "high | medium | low | unknown",
  "overlap_points": ["具体重叠点"],
  "difference_points": ["关键差异点"],
  "what_to_read_first": ["优先阅读的章节或信息"],
  "suggested_response": "用户应该如何处理：引用、区分、改实验、忽略等"
}

要求：
- 只根据给定信息判断。
- 不要把宽泛同领域误判为撞车。
- 如果摘要信息不足，输出 unknown，并说明需要阅读全文。
