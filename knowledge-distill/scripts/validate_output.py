#!/usr/bin/env python3
"""确定性校验 knowledge-distill 产物（SKILL.md）的合规性。

用法:
    python validate_output.py <skill_dir> [<skill_dir> ...] [--quiet] [--pack]

    --pack  按 pack 入口契约检查 ACTION REQUIRED + 分诊表，并检查分诊表引用的子能力六节

退出码（与参数顺序无关，取最坏结果）:
    0 = 通过
    1 = 存在 P0（阻断）或参数错误
    2 = 仅有 P1（建议）
纯标准库实现，无第三方依赖。
"""
import re
import sys
from pathlib import Path
from urllib.parse import unquote

REQUIRED_FRONTMATTER = ["name", "description", "version"]
VALID_CONFIDENCE = {"observed", "strong inference", "tentative inference"}

REQUIRED_SECTIONS = [
    ("触发条件", ["触发条件", "何时调用", "when to use"]),
    ("停止条件", ["停止条件", "stop condition"]),
    ("fallback", ["分支与反例", "fallback", "不要走这条路"]),
    ("证据账本", ["证据账本", "evidence"]),
    ("未知项", ["未知项", "unknown"]),
    ("自检清单", ["任务完成自检", "自检", "checklist"]),
]

PACK_ENTRY_SECTIONS = [
    ("ACTION REQUIRED", ["action required"]),
    ("分诊表", ["分诊表", "分诊", "路由", "router"]),
]

HEADING_RE = re.compile(r"^[ \t]{0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$", re.MULTILINE)
FORMAT_ANNOTATION_RE = re.compile(
    r"格式|模板|示例|样例|\b(?:formats?|templates?|examples?)\b", re.IGNORECASE
)
QUOTE_LINE_RE = re.compile(
    r"^ {0,3}(?:(?:[-+*]|\d+[.)])[ \t]+)?(?:>|&gt;)[ \t]?(.*)$"
)
FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")
MARKDOWN_LINK_RE = re.compile(
    r"!?\[[^\]]*\]\(\s*(?:<([^>\n]+)>|([^\s)]+))(?:\s+['\"][^)]*['\"])?\s*\)"
)
REFERENCE_DEFINITION_RE = re.compile(
    r"^ {0,3}\[([^\]]+)\]:[ \t]*(?:<([^>\n]+)>|(\S+))", re.MULTILINE
)
REFERENCE_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\](?:\[([^\]]*)\])?")
BACKTICK_PATH_RE = re.compile(
    r"`((?:\./)?(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.(?:md|py))`"
)
IGNORED_CODE_FILES = {"SKILL.md", "distill-state.md"}
MISSING_VALUES = {"", "-", "—", "…", "..."}


def strip_fences(text: str) -> str:
    out, fence = [], None
    for line in text.split("\n"):
        match = FENCE_RE.match(line)
        if fence is None:
            if match:
                fence = (match.group(1)[0], len(match.group(1)))
                out.append("")
                continue
            out.append(line)
            continue
        if (
            match
            and match.group(1)[0] == fence[0]
            and len(match.group(1)) >= fence[1]
            and not match.group(2).strip()
        ):
            fence = None
        out.append("")
    return "\n".join(out)


def parse_frontmatter(text: str):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    match = re.match(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", text, re.DOTALL)
    if not match:
        return {}, text
    frontmatter = {}
    for line in match.group(1).split("\n"):
        if ":" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition(":")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            frontmatter[key.strip()] = value
    return frontmatter, text[match.end():]


def heading_title(heading):
    title = heading.group(2) or ""
    return re.sub(r"(?:^|[ \t]+)#+[ \t]*$", "", title).strip()


def section_heading_match_score(title: str, keywords):
    normalized = re.sub(r"\s+", " ", title.strip().lower())
    for keyword in keywords:
        keyword = re.sub(r"\s+", " ", keyword.strip().lower())
        if normalized == keyword:
            return 2
    for keyword in keywords:
        keyword = re.sub(r"\s+", " ", keyword.strip().lower())
        if normalized.startswith(keyword):
            suffix = normalized[len(keyword) :].lstrip()
            if (
                suffix
                and suffix[0] in "（(:：—–-/|"
                and not FORMAT_ANNOTATION_RE.search(suffix)
            ):
                return 1
    return 0


def find_section(text: str, keywords, include_nested: bool = True):
    headings = list(HEADING_RE.finditer(text))
    best_index, best_score = None, 0
    for index, heading in enumerate(headings):
        score = section_heading_match_score(heading_title(heading), keywords)
        if score > best_score:
            best_index, best_score = index, score
    if best_index is None:
        return None
    heading = headings[best_index]
    level = len(heading.group(1))
    end = len(text)
    for next_heading in headings[best_index + 1 :]:
        if not include_nested or len(next_heading.group(1)) <= level:
            end = next_heading.start()
            break
    return text[heading.end():end]


def split_table_row(line: str):
    stripped = line.strip()
    if "|" not in stripped:
        return None
    cells, buffer = [], []
    in_code, code_ticks = False, 0
    index = 0
    while index < len(stripped):
        character = stripped[index]
        if character == "\\" and index + 1 < len(stripped) and stripped[index + 1] == "|":
            buffer.append("|")
            index += 2
            continue
        if character == "`":
            end = index
            while end < len(stripped) and stripped[end] == "`":
                end += 1
            ticks = end - index
            if not in_code:
                in_code, code_ticks = True, ticks
            elif ticks == code_ticks:
                in_code = False
            buffer.append(stripped[index:end])
            index = end
            continue
        if character == "|" and not in_code:
            cells.append("".join(buffer).strip())
            buffer = []
        else:
            buffer.append(character)
        index += 1
    cells.append("".join(buffer).strip())
    if stripped.startswith("|"):
        cells = cells[1:]
    if stripped.endswith("|") and not stripped.endswith("\\|"):
        cells = cells[:-1]
    return cells


def is_separator(cells):
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def check_ledger(body: str):
    problems, ledger_rows = [], 0
    ledger_section = find_section(body, REQUIRED_SECTIONS[3][1])
    if ledger_section is None:
        return problems, ledger_rows
    in_ledger, found_header = False, False
    for line_number, line in enumerate(ledger_section.split("\n"), 1):
        stripped = line.strip()
        cells = split_table_row(stripped)
        if (
            cells
            and len(cells) >= 4
            and re.search(r"结论|conclusion", cells[0], re.IGNORECASE)
            and re.search(r"定位|location", cells[2], re.IGNORECASE)
        ):
            found_header = True
            in_ledger = True
            if len(cells) != 4:
                problems.append(f"证据账本表头列数 {len(cells)} != 4")
            elif not (
                re.search(r"证据类型|evidence", cells[1], re.IGNORECASE)
                and re.search(r"置信|confidence", cells[3], re.IGNORECASE)
            ):
                problems.append("证据账本表头必须依次为：结论 | 证据类型 | 定位 | 置信标签")
            continue
        if not in_ledger:
            continue
        if not stripped:
            in_ledger = False
            continue
        if not stripped.startswith("|"):
            in_ledger = False
            continue
        if is_separator(cells or []):
            continue
        if len(cells or []) != 4:
            problems.append(f"证据账本第 {line_number} 行列数 {len(cells or [])} != 4")
            continue
        normalized = [cell.lower() for cell in cells]
        if any(value in MISSING_VALUES for value in normalized[:3]):
            problems.append(f"证据账本第 {line_number} 行缺少结论、证据类型或定位")
        elif normalized[3] not in VALID_CONFIDENCE:
            problems.append(
                f"证据账本第 {line_number} 行置信标签非法 '{cells[3]}'"
                "（合法: Observed / Strong inference / Tentative inference）"
            )
        else:
            ledger_rows += 1
    if not found_header:
        problems.append("证据账本章节缺少可解析的四列表头")
    elif ledger_rows == 0:
        problems.append("证据账本章节存在但没有任何有效证据行")
    return problems, ledger_rows


def normalize_reference_label(label: str):
    return re.sub(r"\s+", " ", label.strip()).casefold()


def reference_definitions(text: str):
    definitions = {}
    for match in REFERENCE_DEFINITION_RE.finditer(text):
        label = normalize_reference_label(match.group(1))
        definitions[label] = match.group(2) or match.group(3)
    return definitions


def extract_markdown_links(text: str, definitions=None):
    links = [match.group(1) or match.group(2) for match in MARKDOWN_LINK_RE.finditer(text)]
    definitions = reference_definitions(text) if definitions is None else definitions
    for match in REFERENCE_LINK_RE.finditer(text):
        tail = text[match.end() :].lstrip()
        if tail.startswith(("(", ":")):
            continue
        label = match.group(2) or match.group(1)
        target = definitions.get(normalize_reference_label(label))
        if target:
            links.append(target)
    return links


def extract_code_paths(text: str, allow_plain: bool = True):
    paths = []
    for match in BACKTICK_PATH_RE.finditer(text):
        path = match.group(1)
        if Path(path).name in IGNORED_CODE_FILES:
            continue
        if not allow_plain and "/" not in path:
            continue
        paths.append(path)
    return paths


def resolve_local_ref(skill_dir: Path, source_file: Path, reference: str):
    reference = unquote(reference.strip().strip("<>"))
    reference = reference.split("#", 1)[0].split("?", 1)[0]
    if not reference or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", reference):
        return None
    reference = reference.replace("\\", "/")
    if reference.startswith("/"):
        return None
    return (source_file.parent / reference).resolve()


def check_links(skill_dir: Path, main_text: str):
    problems, direct_targets = [], set()
    skill_dir = skill_dir.resolve()
    main_file = (skill_dir / "SKILL.md").resolve()
    main_body = strip_fences(main_text)
    direct_refs = extract_code_paths(main_body, allow_plain=False) + extract_markdown_links(main_body)

    for reference in direct_refs:
        target = resolve_local_ref(skill_dir, main_file, reference)
        if target is None:
            continue
        try:
            target.relative_to(skill_dir)
        except ValueError:
            problems.append(f"SKILL.md 引用了越界路径: {reference}")
            continue
        direct_targets.add(target)
        if not target.exists():
            problems.append(f"断链：SKILL.md 引用了不存在的 {reference}")

    markdown_files = []
    references_dir = skill_dir / "references"
    if references_dir.exists():
        markdown_files.extend(sorted(references_dir.rglob("*.md")))
    for markdown_file in markdown_files:
        try:
            text = strip_fences(markdown_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as error:
            problems.append(f"无法读取 {markdown_file.relative_to(skill_dir)}: {error}")
            continue
        references = extract_markdown_links(text) + extract_code_paths(text)
        for reference in references:
            target = resolve_local_ref(skill_dir, markdown_file, reference)
            if target is None:
                continue
            try:
                target.relative_to(skill_dir)
            except ValueError:
                problems.append(
                    f"越界链接：{markdown_file.relative_to(skill_dir)} -> {reference}"
                )
                continue
            if not target.exists():
                problems.append(
                    f"断链：{markdown_file.relative_to(skill_dir)} -> {reference}"
                )

    if references_dir.exists():
        for reference_file in sorted(references_dir.rglob("*")):
            if reference_file.is_file() and reference_file.resolve() not in direct_targets:
                problems.append(
                    f"孤儿文件：{reference_file.relative_to(skill_dir)} 未被 SKILL.md 显式引用"
                )
    return sorted(set(problems))


def extract_routing_table_references(routing: str, definitions):
    references = []
    lines = routing.split("\n")
    index = 0
    while index + 1 < len(lines):
        header = split_table_row(lines[index])
        separator = split_table_row(lines[index + 1])
        if (
            not header
            or len(header) < 2
            or not separator
            or len(separator) != len(header)
            or not is_separator(separator)
        ):
            index += 1
            continue
        index += 2
        while index < len(lines):
            cells = split_table_row(lines[index])
            if not cells or len(cells) < 2 or is_separator(cells):
                break
            for cell in cells:
                references.extend(extract_code_paths(cell, allow_plain=False))
                references.extend(extract_markdown_links(cell, definitions))
            index += 1
    return references


def pack_member_files(skill_dir: Path, main_text: str):
    problems = []
    main_body = strip_fences(main_text)
    routing = find_section(main_body, PACK_ENTRY_SECTIONS[1][1], include_nested=False)
    if routing is None:
        return [], ["--pack 模式缺少分诊表/路由章节"]
    references_dir = (skill_dir / "references").resolve()
    main_file = (skill_dir / "SKILL.md").resolve()
    references = extract_routing_table_references(
        routing, reference_definitions(main_body)
    )
    members = set()
    for reference in references:
        target = resolve_local_ref(skill_dir, main_file, reference)
        if target is None or not target.is_file() or target.suffix.lower() != ".md":
            continue
        try:
            target.relative_to(references_dir)
        except ValueError:
            continue
        members.add(target)
    if not members:
        problems.append("--pack 模式分诊表未引用 references/ 下的子能力文件")
    return sorted(members), problems


def count_fallback_branches(fallback: str):
    return len(
        re.findall(
            r"(?:当.{2,80}?时|when\b.{2,80}?\b(?:then|,)).{0,40}?"
            r"(?:不要|不得|退回|终止|改用|拒绝|do not|fall back|stop|reject)",
            fallback,
            re.IGNORECASE,
        )
    )


def check_pack_members(skill_dir: Path, main_text: str):
    """--pack 模式：只检查分诊表引用的子能力文件。"""
    members, problems = pack_member_files(skill_dir, main_text)
    for reference_file in members:
        try:
            text = strip_fences(reference_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as error:
            problems.append(f"无法读取 {reference_file.name}: {error}")
            continue
        sections = {}
        content_problems = []
        for label, keywords in REQUIRED_SECTIONS:
            sections[label] = find_section(text, keywords)
            if sections[label] is None:
                problems.append(
                    f"子能力 {reference_file.relative_to(skill_dir)} 缺少章节: {label}"
                )
        checklist = sections.get("自检清单")
        if checklist is not None and not re.search(
            r"^\s*-\s+\[[ xX]\]", checklist, re.MULTILINE
        ):
            content_problems.append("自检清单章节不是 checkbox 形式")
        ledger_problems, _ = check_ledger(text)
        content_problems.extend(ledger_problems)
        fallback = sections.get("fallback") or ""
        if count_fallback_branches(fallback) < 1:
            content_problems.append("fallback 章节未检测到'当 X 时不要/退回…'格式的分支")
        relative = reference_file.relative_to(skill_dir)
        problems.extend(f"子能力 {relative}: {problem}" for problem in content_problems)
    return problems


def check_long_quotes(prose: str, location: str):
    blocks, current = [], []
    start_line, current_is_list = 1, False

    def flush():
        nonlocal current, start_line, current_is_list
        if current:
            blocks.append((start_line, " ".join(current)))
        current, current_is_list = [], False

    for line_number, line in enumerate(strip_fences(prose).split("\n"), 1):
        match = QUOTE_LINE_RE.match(line)
        if not match:
            flush()
            continue
        is_list = bool(re.match(r"^ {0,3}(?:[-+*]|\d+[.)])[ \t]+", line))
        if is_list:
            flush()
            start_line = line_number
            current_is_list = True
        elif not current:
            start_line = line_number
        current.append(match.group(1).strip())
    flush()

    for line_number, quote in blocks:
        english_words = re.findall(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*", quote)
        if len(quote) > 150 or len(english_words) > 100:
            return (
                f"疑似超长原文引用：{location}第 {line_number} 行 "
                "> 150 字或 100 英文词"
            )
    return None


def check_package_safety(skill_dir: Path, main_text: str):
    problems = []
    markdown_files = [(skill_dir / "SKILL.md", main_text)]
    references_dir = skill_dir / "references"
    if references_dir.exists():
        markdown_files.extend(
            (path, None) for path in sorted(references_dir.rglob("*.md"))
        )

    for markdown_file, cached_text in markdown_files:
        relative = (
            "SKILL.md"
            if markdown_file == skill_dir / "SKILL.md"
            else markdown_file.relative_to(skill_dir).as_posix()
        )
        try:
            text = (
                cached_text
                if cached_text is not None
                else markdown_file.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError) as error:
            problems.append(f"无法读取 {relative}: {error}")
            continue

        _, prose = parse_frontmatter(text)
        for pattern, label in (
            (r"(?<!\d)1[3-9]\d{9}(?!\d)", "疑似未脱敏手机号"),
            (r"(?<![0-9Xx])\d{17}[\dXx](?![0-9Xx])", "疑似未脱敏身份证号"),
        ):
            if re.search(pattern, text):
                problems.append(
                    f"{label}：{relative} 命中铁律 7 脱敏要求，请替换为占位符"
                )

        location = "正文" if relative == "SKILL.md" else relative
        quote_problem = check_long_quotes(prose, location)
        if quote_problem:
            problems.append(quote_problem)
    return sorted(set(problems))


def check(skill_dir: Path, quiet: bool, pack: bool = False):
    problems_p0, problems_p1 = [], []
    md = skill_dir / "SKILL.md"
    if not md.exists():
        print(f"[P0] {md} 不存在")
        return 1
    try:
        text = md.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        print(f"[P0] 无法读取 {md}: {error}")
        return 1

    frontmatter, prose = parse_frontmatter(text)
    body = strip_fences(prose)
    if not frontmatter:
        problems_p0.append("frontmatter 缺失或格式错误（文件应以 --- 开头）")
    else:
        for key in REQUIRED_FRONTMATTER:
            if not frontmatter.get(key):
                problems_p0.append(f"frontmatter 缺少 {key}")
        name = frontmatter.get("name", "")
        if name and not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name):
            problems_p0.append(f"name 非 kebab-case: {name}")
        if name and skill_dir.name and name != skill_dir.name:
            problems_p1.append(f"name '{name}' 与目录名 '{skill_dir.name}' 不一致")
        description = frontmatter.get("description", "")
        if description:
            if not re.search(r"(Use when|使用|触发)", description, re.IGNORECASE):
                problems_p1.append("description 未说明触发场景")
            if not re.search(r"(不适用|NOT for|排除)", description, re.IGNORECASE):
                problems_p1.append("description 未声明不适用范围")

    sections = {}
    required_sections = PACK_ENTRY_SECTIONS if pack else REQUIRED_SECTIONS
    for label, keywords in required_sections:
        sections[label] = find_section(body, keywords)
        if sections[label] is None:
            problems_p0.append(f"缺少必需章节: {label}")

    checklist = sections.get("自检清单")
    if checklist is not None and not re.search(r"^\s*-\s+\[[ xX]\]", checklist, re.MULTILINE):
        problems_p1.append("自检清单章节不是 checkbox 形式")

    line_total = text.count("\n") + (0 if text.endswith("\n") else 1)
    if line_total > 400:
        problems_p1.append(f"SKILL.md {line_total} 行 > 400，细节应外置到 references/")

    description_length = len(frontmatter.get("description", ""))
    if description_length > 1024:
        problems_p1.append(
            f"description {description_length} 字符 > 1024，部分平台会拒绝加载"
        )

    problems_p1.extend(check_package_safety(skill_dir, text))
    problems_p1.extend(check_links(skill_dir, text))

    if pack:
        problems_p1.extend(check_pack_members(skill_dir, text))

    ledger_problems, ledger_rows = check_ledger(body)
    problems_p1.extend(ledger_problems)

    fallback = sections.get("fallback") or ""
    fallback_count = count_fallback_branches(fallback)
    if not pack and fallback_count < 1:
        problems_p1.append("fallback 章节未检测到'当 X 时不要/退回…'格式的分支")

    if problems_p0:
        print(f"FAIL {md} — {len(problems_p0)} 个 P0:")
        for problem in problems_p0:
            print(f"  [P0] {problem}")
        for problem in problems_p1:
            print(f"  [P1] {problem}")
        return 1
    if problems_p1:
        print(f"WARN {md} — P0 通过, {len(problems_p1)} 个 P1:")
        for problem in problems_p1:
            print(f"  [P1] {problem}")
        return 2
    if not quiet:
        print(f"PASS {md} — 证据账本 {ledger_rows} 行, fallback 分支 {fallback_count} 条")
    return 0


def main():
    quiet, pack, directories = False, False, []
    for argument in sys.argv[1:]:
        if argument == "--quiet":
            quiet = True
        elif argument == "--pack":
            pack = True
        elif argument.startswith("--"):
            print(f"未知参数: {argument}")
            print(__doc__)
            return 1
        else:
            directories.append(argument)
    if not directories:
        print(__doc__)
        return 1
    worst = 0
    for directory in directories:
        result = check(Path(directory), quiet, pack=pack)
        if result == 1 or worst == 1:
            worst = 1
        elif result == 2 or worst == 2:
            worst = 2
    return worst


if __name__ == "__main__":
    sys.exit(main())
