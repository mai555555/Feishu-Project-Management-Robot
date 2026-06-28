# 可复用工具包说明

这个项目后续新增业务时，优先复用 `app/services/toolkits` 下的工具包，避免把所有逻辑继续堆到 `commands.py`。

## 1. intent_tools.py

负责稳定判断用户意图中的通用部分。

当前包含：

- `is_task_table_intent(text)`：判断是否要生成/拆解任务表。
- `is_work_log_table_intent(text)`：判断是否要创建工作日志表。
- `is_confirm_text(text)`：判断用户是否确认。
- `is_cancel_text(text)`：判断用户是否取消。

新增业务建议：

- 先在这里补充稳定关键词或模式。
- 再在 `intent_router.py` 增加更高层的业务意图分类。
- 不要在普通聊天里直接执行真实操作。

## 2. document_tools.py

负责统一读取资料来源。

当前包含：

- 飞书新版文档链接识别：`extract_docx_id`
- 多维表格链接识别：`extract_base_link`
- 飞书域名识别：`extract_feishu_origin`
- 消息附件解析：`file_info_from_payload`
- Word/PDF 下载和解析：`read_attached_document`
- 综合文档读取：`resolve_document_content`

新增业务如果需要读取资料，比如总结文档、生成会议纪要、生成测试用例，都应该调用这里。

## 3. task_table_tools.py

负责多维任务表的通用辅助逻辑。

当前包含：

- `build_base_link`：生成可点击的多维表格链接。
- `extract_project_name` / `normalize_project_id`：从自然语言里识别“官网项目、小程序项目”等项目范围。
- `remember_existing_task_table`：把用户发来的已有任务表记为当前任务表。
- `remember_generated_task_table`：记住新生成的任务表和任务明细。
- `format_task_table_result`：统一生成任务表结果回复。
- `query_unassigned_tasks`：查询最近任务表里的未分配任务。
- `query_tasks_by_assignee`：查询某个人负责的任务。
- `query_tasks_by_alias`：查询某个模块/负责人标签相关任务。
- `format_task_query_result`：统一生成任务查询回复。

新增业务如果要更新任务表、查询任务表、返回任务表链接，优先从这里扩展。

项目维度说明：任务表记忆现在支持 `project_name/project_id`。当用户说“官网项目后端有哪些任务”“官网项目后端负责人是 @张三”时，应优先把项目名传给 task_table_tools 和负责人同步逻辑，避免多个项目之间串表。

## 推荐新增业务流程

例如以后要做“查询未分配任务”：

1. 在 `intent_router.py` 中识别“哪些还没分配/未分配任务”。
2. 在任务表工具包中增加 `query_unassigned_tasks(...)`。
3. 在 `commands.py` 里只做编排：识别意图 -> 调工具 -> 返回回复。

`commands.py` 应该越来越像调度层，而不是业务实现层。
