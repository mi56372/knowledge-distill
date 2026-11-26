# knowledge-distill

把原始素材蒸馏成可安装、可触发、可执行的 AI 技能文件。

给它一本书、一个代码仓库、一节录屏课、一份行业规范，它输出的不是摘要或读书笔记，而是符合 SKILL.md 规范、能被 agent 自动路由执行的技能包。

> 这里的"蒸馏"指方法论提炼，与模型蒸馏（teacher-student 训练）无关。

## 它和"帮我总结一下"的区别

| 摘要 | knowledge-distill |
|---|---|
| 复述内容 | 提取"当 X 时执行 Y"的可执行规则 |
| 结论无法验证 | 每条结论挂证据类型 + 精确定位 + 置信标签 |
| 读起来像文章 | 产出的 SKILL.md 能被 agent 自动触发 |
| 边界模糊 | 必须给出 fallback 分支与可判定的停止条件 |
| 靠人眼判断质量 | `validate_output.py` 机器校验，退出码 0 才算交付 |

置信标签只有三档：**Observed**（直接可见）、**Strong inference**（强推断）、**Tentative inference**（弱推断）。拿不到精确定位的内容一律不写进产物，只能进 unknown 清单并附建议探针。

素材不可读、缺音轨、缺章节时不会硬写：要么终止并说明缺什么，要么按 `覆盖范围：partial` 标注后部分交付。

## 支持的素材

| 素材 | 处理方式 |
|---|---|
| 代码仓库、目录结构、CI 配置 | 工程拓扑 / 硬性不变量 / 契约边界 / 可执行模式 / 反模式，五路提取 |
| 书、长视频课程、播客、访谈 | 整篇理解 → 五路提取 → 三重验证 → 能力卡 → 压力测试 |
| 教程录屏、演示视频 | 画面事实 + OCR + 时间轴对齐，定位精确到 mm:ss |
| 会议录音、电话录音 | 无画面轨专用流程，偏决策与待办提取 |
| 截图、架构图、设计稿、扫描件 | 结构 + 可量化参数 + 语义映射 + 可复现规格 |
| 长文档、国标/行标/内部规范 | 框架 / 原则 / 案例 / 反例 / 术语；保留条款编号，强制条款与建议条款不混用 |
| Excel、CSV、数据集 | 数据实算，定位格式为 sheet + 范围 |
| 网页、在线课程 | 先做探针体检（含登录墙判定），可读后再分诊 |

## 三种工作模式

1. **蒸馏**：素材 → 技能包。主流程分阶段 0-4，阶段 0 做素材体检（可读性/完整性/版本时效/授权密级/隐私），中途在产物目录维护 `distill-state.md`，中断可续跑。
2. **评审**：对已有 skill 做七维百分制打分（证据纪律、可触发性、反例与停止条件、格式合规、可执行性、渐进披露、非摘要性），只诊断不重写。
3. **增量更新**：素材变化后做影响分析 + 事务性同步；变化超过 50% 才退回全量重蒸。

## 上手

### 1. 放置

把本目录放进 agent 的技能扫描目录，任选一种：

```
<项目>/.codeartsdoer/skills/knowledge-distill/
<项目>/.skills/knowledge-distill/
```

目录第一层必须直接包含 `SKILL.md`。

### 2. 触发

对 agent 说：

> 把这个仓库（或这本书 / 这段录屏）做成 skill

也可以用英文触发词：`distill into a skill`。skill 会先体检素材，再按信号路由到对应补丁，逐阶段产出。

### 3. 校验产物

```bash
python scripts/validate_output.py <产物目录>
python scripts/validate_output.py <产物目录> --pack   # pack 产物
```

检查项：frontmatter 完整性、必需章节、证据账本格式与置信标签、本地链接断链、references/ 孤儿文件、隐私脱敏、超长引用。

校验通过后可用安装器落盘（默认拒绝覆盖已有目录）：

```bash
python scripts/install_skill.py <产物目录> <目标目录> --dry-run
python scripts/install_skill.py <产物目录> <目标目录>
```

## 目录结构

```
knowledge-distill/
├── SKILL.md        # 执行契约：铁律、分诊表、阶段流程、自检
├── references/     # 13 个补丁与模式文档：8 类素材补丁 + pack/多轮/评审/增量/bootstrap/网页探针
├── scripts/        # validate_output.py 校验器 + install_skill.py 事务式安装器
└── tests/          # 63 项回归测试
```

## 环境要求

- Python 3.9+；校验器与安装器**只用标准库**，零第三方依赖
- 音视频、图片、表格的深度处理是可选增强，缺失时按 `references/bootstrap.md` 在隔离虚拟环境安装（faster-whisper / tesseract / pandas / pymupdf 等固定版本）；装不上则对应维度降级为 unknown，不猜内容

跑一遍回归测试：

```bash
python -m pytest tests -q
```

## 版本

当前 1.7.0。完整变更记录见 `SKILL.md` 末尾的"变更记录"章节。