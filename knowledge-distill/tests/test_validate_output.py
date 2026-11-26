import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import validate_output


VALID_SKILL = """---
name: sample-skill
description: 测试技能。Use when 需要验证样例。不适用于 其他场景。
version: 1.0.0
---
# 样例技能

## 触发条件
当输入为样例时使用。

## 停止条件
当没有新增候选时停止。

## 分支与反例
- 当输入不可读时：不要继续，退回并索取原始素材。

## 证据账本
| 结论 | 证据类型 | 定位 | 置信标签 |
|---|---|---|---|
| 样例规则来自原始素材 | 代码位置 | src/example.py:1 | Observed |

## 未知项
无。

## 任务完成自检
- [ ] 已核对证据定位
"""

PACK_ENTRY = """---
name: sample-skill
description: 测试 pack。Use when 需要路由多个能力。不适用于 单能力场景。
version: 1.0.0
---
# 样例 Pack

# ACTION REQUIRED
按分诊表选择子能力。

## 分诊表
| 信号 | 子能力 |
|---|---|
| 需要样例能力 | `references/cap.md` |
"""


class ValidateOutputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="knowledge distill ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def make_skill(self, text=VALID_SKILL, name="sample-skill"):
        skill_dir = self.root / name
        skill_dir.mkdir(parents=True)
        self.write(skill_dir / "SKILL.md", text)
        return skill_dir

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))

    def run_check(self, skill_dir, pack=False):
        output = io.StringIO()
        with redirect_stdout(output):
            result = validate_output.check(skill_dir, quiet=False, pack=pack)
        return result, output.getvalue()

    def test_valid_skill_passes(self):
        result, output = self.run_check(self.make_skill())
        self.assertEqual(result, 0, output)
        self.assertIn("PASS", output)

    def test_frontmatter_keywords_do_not_satisfy_sections(self):
        text = """---
name: sample-skill
description: 触发条件 停止条件 fallback 证据账本 未知项 自检清单。Use when 测试。不适用于 其他。
version: 1.0.0
---
# 只有关键词的正文
"""
        result, output = self.run_check(self.make_skill(text))
        self.assertEqual(result, 1, output)
        self.assertIn("缺少必需章节: 触发条件", output)
        self.assertIn("缺少必需章节: 证据账本", output)

    def test_checkbox_outside_self_check_does_not_count(self):
        text = VALID_SKILL.replace("- [ ] 已核对证据定位", "已核对证据定位")
        text = text.replace("当输入为样例时使用。", "当输入为样例时使用。\n- [ ] 伪装自检")
        result, output = self.run_check(self.make_skill(text))
        self.assertEqual(result, 2, output)
        self.assertIn("自检清单章节不是 checkbox 形式", output)

    def test_nested_fallback_heading_is_included_in_section(self):
        text = VALID_SKILL.replace(
            "- 当输入不可读时：不要继续，退回并索取原始素材。",
            "### 具体分支\n- 当输入不可读时：不要继续，退回并索取原始素材。",
        )
        result, output = self.run_check(self.make_skill(text))
        self.assertEqual(result, 0, output)

    def test_empty_ledger_cells_are_rejected(self):
        text = VALID_SKILL.replace(
            "| 样例规则来自原始素材 | 代码位置 | src/example.py:1 | Observed |",
            "|  |  | src/example.py:1 | Observed |",
        )
        result, output = self.run_check(self.make_skill(text))
        self.assertEqual(result, 2, output)
        self.assertIn("缺少结论、证据类型或定位", output)

    def test_fenced_ledger_example_is_not_counted(self):
        table = """| 结论 | 证据类型 | 定位 | 置信标签 |
|---|---|---|---|
| 样例规则来自原始素材 | 代码位置 | src/example.py:1 | Observed |"""
        text = VALID_SKILL.replace(table, f"```markdown\n{table}\n```")
        result, output = self.run_check(self.make_skill(text))
        self.assertEqual(result, 2, output)
        self.assertIn("缺少可解析的四列表头", output)

    def test_ledger_table_outside_section_does_not_count(self):
        table = """| 结论 | 证据类型 | 定位 | 置信标签 |
|---|---|---|---|
| 区外假证据 | 推理 | 其他章节 | Observed |"""
        text = VALID_SKILL.replace(
            """| 结论 | 证据类型 | 定位 | 置信标签 |
|---|---|---|---|
| 样例规则来自原始素材 | 代码位置 | src/example.py:1 | Observed |""",
            "",
        ).replace("## 未知项", f"## 其他\n{table}\n\n## 未知项")
        result, output = self.run_check(self.make_skill(text))
        self.assertEqual(result, 2, output)
        self.assertIn("证据账本章节缺少可解析的四列表头", output)

    def test_broken_links_inside_references_are_reported(self):
        skill_dir = self.make_skill(
            VALID_SKILL.replace("# 样例技能", "# 样例技能\n\n资源：`references/used.md`")
        )
        self.write(
            skill_dir / "references" / "used.md",
            "[缺失](missing.md)\n\n`../scripts/missing.py`\n",
        )
        result, output = self.run_check(skill_dir)
        self.assertEqual(result, 2, output)
        self.assertIn("references/used.md -> missing.md", output.replace("\\", "/"))
        self.assertIn("references/used.md -> ../scripts/missing.py", output.replace("\\", "/"))

    def test_orphan_reference_is_reported(self):
        skill_dir = self.make_skill()
        self.write(skill_dir / "references" / "orphan.md", "# 未被引用\n")
        result, output = self.run_check(skill_dir)
        self.assertEqual(result, 2, output)
        self.assertIn("孤儿文件：references/orphan.md", output.replace("\\", "/"))

    def test_markdown_link_counts_as_direct_reference(self):
        text = VALID_SKILL.replace(
            "# 样例技能", "# 样例技能\n\n[说明](references/used.md)"
        )
        skill_dir = self.make_skill(text)
        self.write(skill_dir / "references" / "used.md", "# 说明\n")
        result, output = self.run_check(skill_dir)
        self.assertEqual(result, 0, output)

    def test_crlf_input_passes(self):
        skill_dir = self.make_skill()
        (skill_dir / "SKILL.md").write_bytes(VALID_SKILL.replace("\n", "\r\n").encode("utf-8"))
        result, output = self.run_check(skill_dir)
        self.assertEqual(result, 0, output)

    def test_unclosed_fence_cannot_hide_required_sections(self):
        text = VALID_SKILL.replace("## 触发条件", "```markdown\n## 触发条件", 1)
        result, output = self.run_check(self.make_skill(text))
        self.assertEqual(result, 1, output)
        self.assertIn("缺少必需章节: 触发条件", output)

    def test_multiple_directories_return_worst_result(self):
        valid = self.make_skill()
        warning = self.make_skill(
            VALID_SKILL.replace(
                "| 样例规则来自原始素材 | 代码位置 | src/example.py:1 | Observed |",
                "|  |  | src/example.py:1 | Observed |",
            ),
            name="warning-skill",
        )
        failure = self.make_skill("# 缺少结构\n", name="failure-skill")
        with mock.patch.object(
            sys,
            "argv",
            ["validate_output.py", str(warning), str(valid), str(failure)],
        ), redirect_stdout(io.StringIO()):
            self.assertEqual(validate_output.main(), 1)

    def test_unknown_option_is_rejected(self):
        with mock.patch.object(sys, "argv", ["validate_output.py", "--unknown"]), redirect_stdout(
            io.StringIO()
        ):
            self.assertEqual(validate_output.main(), 1)


    def test_reference_only_cited_inside_fence_is_orphan(self):
        text = VALID_SKILL.replace(
            "# 样例技能", "# 样例技能\n\n```markdown\n参考 `references/hidden.md`\n```"
        )
        skill_dir = self.make_skill(text)
        self.write(skill_dir / "references" / "hidden.md", "# 隐藏章节\n")
        result, output = self.run_check(skill_dir)
        self.assertEqual(result, 2, output)
        self.assertIn("孤儿文件：references/hidden.md", output.replace("\\", "/"))

    def test_phone_number_triggers_redaction_hint(self):
        text = VALID_SKILL.replace("## 未知项\n无。", "## 未知项\n联系人 13800138000")
        result, output = self.run_check(self.make_skill(text))
        self.assertEqual(result, 2, output)
        self.assertIn("疑似未脱敏手机号", output)

    def test_pack_entry_allows_router_only_and_shared_reference(self):
        text = PACK_ENTRY.replace(
            "| 需要样例能力 | `references/cap.md` |",
            "| 需要样例能力 | `references/cap.md` |\n\n### 共享背景\n见 `references/shared.md`。",
        )
        skill_dir = self.make_skill(text)
        self.write(skill_dir / "references" / "cap.md", VALID_SKILL)
        self.write(skill_dir / "references" / "shared.md", "# 共享背景\n无需六节。\n")
        result, output = self.run_check(skill_dir, pack=True)
        self.assertEqual(result, 0, output)

    def test_pack_routing_ignores_shared_reference_in_prose(self):
        text = PACK_ENTRY.replace(
            "| 需要样例能力 | `references/cap.md` |",
            "| 需要样例能力 | `references/cap.md` |\n\n共享背景见 `references/shared.md`。",
        )
        skill_dir = self.make_skill(text)
        self.write(skill_dir / "references" / "cap.md", VALID_SKILL)
        self.write(skill_dir / "references" / "shared.md", "# 共享背景\n无需六节。\n")
        result, output = self.run_check(skill_dir, pack=True)
        self.assertEqual(result, 0, output)

    def test_pack_reference_style_link_routes_member(self):
        text = PACK_ENTRY.replace(
            "`references/cap.md`", "[cap][cap]"
        ) + "\n\n[cap]: references/cap.md\n"
        skill_dir = self.make_skill(text)
        self.write(skill_dir / "references" / "cap.md", "## 触发条件\n当 A 时。\n")
        result, output = self.run_check(skill_dir, pack=True)
        self.assertEqual(result, 2, output)
        self.assertIn(
            "子能力 references/cap.md 缺少章节: 证据账本",
            output.replace("\\", "/"),
        )

    def test_reference_style_broken_link_is_reported(self):
        text = VALID_SKILL.replace(
            "# 样例技能",
            "# 样例技能\n\n[缺失][missing]\n\n[missing]: references/missing.md",
        )
        result, output = self.run_check(self.make_skill(text))
        self.assertEqual(result, 2, output)
        self.assertIn("断链：SKILL.md 引用了不存在的 references/missing.md", output)

    def test_main_link_failure_is_reported_once(self):
        text = VALID_SKILL.replace(
            "# 样例技能", "# 样例技能\n\n[缺失](references/missing.md)"
        )
        result, output = self.run_check(self.make_skill(text))
        self.assertEqual(result, 2, output)
        self.assertEqual(output.count("断链"), 1, output)

    def test_pack_mode_checks_member_sections(self):
        skill_dir = self.make_skill(PACK_ENTRY)
        self.write(skill_dir / "references" / "cap.md", "## 触发条件\n当 A 时。\n")
        result, output = self.run_check(skill_dir, pack=True)
        self.assertEqual(result, 2, output)
        self.assertIn(
            "子能力 references/cap.md 缺少章节: 证据账本",
            output.replace("\\", "/"),
        )

    def test_pack_member_content_is_validated(self):
        skill_dir = self.make_skill(PACK_ENTRY)
        member = """## 触发条件
当 A 时。
## 停止条件
完成即停。
## 分支与反例
没有可执行分支。
## 证据账本
| 结论 | 证据类型 | 定位 | 置信标签 |
|---|---|---|---|
## 未知项
无。
## 任务完成自检
没有 checkbox。
"""
        self.write(skill_dir / "references" / "cap.md", member)
        result, output = self.run_check(skill_dir, pack=True)
        self.assertEqual(result, 2, output)
        self.assertIn("自检清单章节不是 checkbox 形式", output)
        self.assertIn("证据账本章节存在但没有任何有效证据行", output)
        self.assertIn("fallback 章节未检测到", output)

    def test_pack_requires_routing_section(self):
        text = PACK_ENTRY.replace("## 分诊表", "## 其他")
        skill_dir = self.make_skill(text)
        self.write(skill_dir / "references" / "cap.md", VALID_SKILL)
        result, output = self.run_check(skill_dir, pack=True)
        self.assertEqual(result, 1, output)
        self.assertIn("缺少必需章节: 分诊表", output)

    def test_pack_requires_member_reference(self):
        text = PACK_ENTRY.replace("| 需要样例能力 | `references/cap.md` |", "| 需要样例能力 | 无 |")
        result, output = self.run_check(self.make_skill(text), pack=True)
        self.assertEqual(result, 2, output)
        self.assertIn("分诊表未引用 references/ 下的子能力文件", output)

    def test_image_link_counts_as_direct_reference(self):
        text = VALID_SKILL.replace(
            "# 样例技能", "# 样例技能\n\n![图](references/diagram.png)"
        )
        skill_dir = self.make_skill(text)
        (skill_dir / "references").mkdir()
        (skill_dir / "references" / "diagram.png").write_bytes(b"png")
        result, output = self.run_check(skill_dir)
        self.assertEqual(result, 0, output)

    def test_markdown_link_with_spaces_is_checked(self):
        text = VALID_SKILL.replace(
            "# 样例技能", "# 样例技能\n\n[说明](<references/my file.md>)"
        )
        skill_dir = self.make_skill(text)
        self.write(skill_dir / "references" / "my file.md", "# 说明\n")
        result, output = self.run_check(skill_dir)
        self.assertEqual(result, 0, output)

    def test_table_pipe_inside_inline_code_is_not_a_separator(self):
        text = VALID_SKILL.replace("src/example.py:1", "`a | b`")
        result, output = self.run_check(self.make_skill(text))
        self.assertEqual(result, 0, output)

    def test_phone_number_in_frontmatter_triggers_redaction_hint(self):
        text = VALID_SKILL.replace(
            "description: 测试技能。", "description: 联系人 13800138000。测试技能。"
        )
        result, output = self.run_check(self.make_skill(text))
        self.assertEqual(result, 2, output)
        self.assertIn("疑似未脱敏手机号", output)

    def test_heading_closing_hashes_without_space_are_not_stripped(self):
        text = VALID_SKILL.replace("## 未知项\n无。", "## 未知项###\n无。")
        result, output = self.run_check(self.make_skill(text))
        self.assertEqual(result, 1, output)
        self.assertIn("缺少必需章节: 未知项", output)

    def test_format_annotation_does_not_satisfy_unknown_section(self):
        text = VALID_SKILL.replace("## 未知项\n无。", "## 未知项（格式）\n无。")
        result, output = self.run_check(self.make_skill(text))
        self.assertEqual(result, 1, output)
        self.assertIn("缺少必需章节: 未知项", output)

    def test_dash_format_heading_before_real_ledger_does_not_shadow_it(self):
        text = VALID_SKILL.replace(
            "## 证据账本",
            "## 证据账本 - 格式\n账本由四列组成。\n\n## 证据账本",
        )
        result, output = self.run_check(self.make_skill(text))
        self.assertEqual(result, 0, output)

    def test_exact_heading_wins_over_earlier_annotated_heading(self):
        text = VALID_SKILL.replace(
            "## 证据账本",
            "## 证据账本（待补）\n\n## 证据账本",
        )
        result, output = self.run_check(self.make_skill(text))
        self.assertEqual(result, 0, output)

    def test_format_heading_does_not_satisfy_unknown_section(self):
        text = VALID_SKILL.replace("## 未知项\n无。", "## 未知项格式\n无。")
        result, output = self.run_check(self.make_skill(text))
        self.assertEqual(result, 1, output)
        self.assertIn("缺少必需章节: 未知项", output)

    def test_annotated_heading_still_satisfies_required_section(self):
        text = VALID_SKILL.replace("## 未知项\n无。", "## 未知项（待核实）\n无。")
        result, output = self.run_check(self.make_skill(text))
        self.assertEqual(result, 0, output)

    def test_format_heading_before_real_ledger_does_not_shadow_it(self):
        text = VALID_SKILL.replace(
            "## 证据账本",
            "## 证据账本格式\n账本由四列组成。\n\n## 证据账本（设计依据）",
        )
        result, output = self.run_check(self.make_skill(text))
        self.assertEqual(result, 0, output)

    def test_reference_privacy_is_checked(self):
        text = VALID_SKILL.replace(
            "# 样例技能", "# 样例技能\n\n见 `references/used.md`。"
        )
        skill_dir = self.make_skill(text)
        self.write(skill_dir / "references" / "used.md", "联系人 13800138000")
        result, output = self.run_check(skill_dir)
        self.assertEqual(result, 2, output)
        self.assertIn("疑似未脱敏手机号：references/used.md", output)

    def test_reference_long_quote_is_checked(self):
        text = VALID_SKILL.replace(
            "# 样例技能", "# 样例技能\n\n见 `references/used.md`。"
        )
        skill_dir = self.make_skill(text)
        self.write(skill_dir / "references" / "used.md", "> " + "原" * 151)
        result, output = self.run_check(skill_dir)
        self.assertEqual(result, 2, output)
        self.assertIn("疑似超长原文引用：references/used.md", output)

    def test_multiline_reference_quote_is_checked_as_one_quote(self):
        text = VALID_SKILL.replace(
            "# 样例技能", "# 样例技能\n\n见 `references/used.md`。"
        )
        skill_dir = self.make_skill(text)
        self.write(
            skill_dir / "references" / "used.md",
            "> " + "原" * 80 + "\n> " + "原" * 80,
        )
        result, output = self.run_check(skill_dir)
        self.assertEqual(result, 2, output)
        self.assertIn("疑似超长原文引用：references/used.md", output)

    def test_list_reference_quote_is_checked(self):
        text = VALID_SKILL.replace(
            "# 样例技能", "# 样例技能\n\n见 `references/used.md`。"
        )
        skill_dir = self.make_skill(text)
        self.write(skill_dir / "references" / "used.md", "- > " + "原" * 151)
        result, output = self.run_check(skill_dir)
        self.assertEqual(result, 2, output)
        self.assertIn("疑似超长原文引用：references/used.md", output)

    def test_reference_long_english_quote_is_checked_by_word_count(self):
        text = VALID_SKILL.replace(
            "# 样例技能", "# 样例技能\n\n见 `references/used.md`。"
        )
        skill_dir = self.make_skill(text)
        self.write(skill_dir / "references" / "used.md", "> " + "word " * 101)
        result, output = self.run_check(skill_dir)
        self.assertEqual(result, 2, output)
        self.assertIn("100 英文词", output)

    def test_pack_child_privacy_is_checked(self):
        skill_dir = self.make_skill(PACK_ENTRY)
        self.write(
            skill_dir / "references" / "cap.md",
            VALID_SKILL.replace("## 未知项\n无。", "## 未知项\n联系人 13800138000"),
        )
        result, output = self.run_check(skill_dir, pack=True)
        self.assertEqual(result, 2, output)
        self.assertIn("疑似未脱敏手机号：references/cap.md", output)


if __name__ == "__main__":
    unittest.main()
