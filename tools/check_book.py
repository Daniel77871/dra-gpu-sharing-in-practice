#!/usr/bin/env python3
"""Validate zh-TW prose, Markdown links, headings, Mermaid and generated blocks."""

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

import yaml

if __package__:
    from .build_nav import ROOT, expected_updates, load_order
    from .markdown import heading_slugs, headings, links, markdown_lines, prose_lines
else:
    from build_nav import ROOT, expected_updates, load_order
    from markdown import heading_slugs, headings, links, markdown_lines, prose_lines

# Manually curated simplified-only forms, grouped by common vocabulary and
# simplified radicals (讠/纟/钅/饣). This is deliberately NOT a Unicode range or
# automatic conversion table: shared Traditional forms (干/后/于/里/云/台/面/
# 斗/余/制/范/系/松/征/朴/只/据/器/組/件/存) are excluded. Extend with reviewed
# individual forms, never with ambiguous one-to-many simplifications.
SIMPLIFIED_ONLY = frozenset(
    "们这为过时对说还没进动发开关门问间题经网络库运载调统显错计内线务现应设认资节数软视带宽缓优"
    "书东丝丢两严丧个丰临丽举义乌乐乔习乡买乱争亚产亲亿仅从仓仪众会伟传伤伦伪体侠侣侦侧俭债倾偿储兑党兴养兽冈册写军农冲决况冻净凉减凑凤凭凯击划刘则刚创删别剂剑剧劝办勋区医华协单卖卢卫厅历厉压厌厕厦厨厢县双变叠号叹吗听启员呜响哑唤喷团园围国图圆场坏块坚坛坟坠垄垒垦垫墙壮声壳备头夹夺奋奖妇妈婴孙学宁宝实宠审宪宫宾寝尘层屉届属岁岂岗岛岭峡峦币帅师帐帘帜帧帮广庄庆庐庙庞废弥弯归录忆忧怀态怜总恳恶惊惧惨惩惫惭惯愤懒戏战执扩扫扬扰抚抛抢护报担拟拢拣拥拦拨择挚挛挠挡挣挤挥捞损捡换掷掺揽搅摄摆摇摊敌敛斋斩断无旧旷昼暂杂权条来杨极枪枫柜柠标栈栋栏树样档桥桦桨桩梦检楼横樱欢欧歼残殒殓殡毁毕毡氢氩汇汉汤沟沥沦沧沪泞泪泸泻泼泽浅浆浇浊测济浏浑浓涛涝涟涡涣涤润涧涨涩渊渍渎渐渔渗湾湿溃溅滚滞满滤滥滨滩潜澜灭灯灵灿炉炜点炼烁烂烛烟烦烧烫热焕焖爱爷牵犊状犷犹狈狞独狭狮狰狱猎猪猫献獭环电畅畴疗疡疮疯痈痉痒痨痪瘪瘫瘾癞癣皑皱盘睁睐睑瞒矫矿码砖砚硕礼祷祸离秃积秽稳穷窃窍窑窜窝窥竞笔笃笋笺笼筛筝筹签简箩箫篮篱粪粮紧"
    "纠红约级纪纫纬纭纯纱纲纳纵纶纷纸纹纺纽纾练绅细织终绉绊绍绎绒结绕绘给绚绛绞绢绣绥绦继绩绪绫绯绰绳维绵绶绷绸绺综绽绿缀缄缅缆缇缈缉缎缔缕编缘缙缚缛缜缝缟缠缡缢缣缤缥缦缨缩缪缫缬缭缮缯缰缱缳缴"
    "讥讦讨让训议讯记讲讳讶许讹论讼讽访诀证评识诈诉诊词译试诗诙诚诛话诞诟诠诡询诣诤该详诧诨诫诬语误诱诲诳诵请诸诺读诽课谀谁谅谆谈谊谋谍谎谏谐谑谒谓谕谗谙谚谛谜谟谢谣谤谦谧谨谩谪谬谭谮谯谰谱谴谵谶"
    "针钉钊钓钗钙钛钝钞钟钠钡钢钣钥钦钧钨钩钮钰钱钳钴钵钹钻钼钽钾铀铁铂铃铄铅铆铐铒铗铙铛铜铝铟铠铢铣铤铧铨铬铭铮铰铱铲铵银铷铸铺铼链铿销锁锂锄锅锆锈锉锋锌锐锑锗锚锡锢锣锤锥锦锨锭键锯锰锵锶锷锹锻镀镁镂镇镉镊镌镍镏镐镑镓镖镜镝镞镣镧镪镬镭镯镶"
    "饥饨饪饭饮饯饰饱饲饵饶饷饺饼饿馁馄馅馆馈馊馋馍馏馐馑馒馓馔馕"
    "贝负财责贤败账货质贩贪贫贬购贮贯贰贱贴贵贷贸费贺贼贾贿赁赂赃赋赌赎赏赐赔赖赘赚赛赞赠赢赵赶趋跃践踪躯车轧轨轩转轮轰轴轻较辅辆辈辉辑输辕辖辙辞辩边辽达迁迈违连迟适选逊递逻遗遥邓邮邹邻郑酝酱酿释鉴长闪闭闯闰闲闷闸闹闺闻阀阁阅阎阐阑阔队阳阴阵阶际陆陇陈陕陨险随隐隶难雏雾静韦韩页顶项顺顾顿颁颂预颅领颇颈颊频颓颖颗颜额颠颤风飒飘飞马驭驮驯驰驱驳驴驶驻驼驾驿骁骂骄骆骇验骑骗骤鱼鲁鲍鲜鲤鲨鲸鳄鳍鳖鳗鳞鸟鸡鸣鸥鸦鸭鸳鸵鸽鸿鹅鹊鹏鹤鹦鹭鹰麦黄齐齿龄龙龚龟"
)

CLEAR_TERMS = {
    "顯存": "VRAM／GPU 記憶體", "显存": "VRAM／GPU 記憶體", "內存": "記憶體", "内存": "記憶體",
    "進程": "行程", "进程": "行程", "線程": "執行緒", "线程": "執行緒", "默認": "預設", "默认": "預設",
    "信息": "資訊", "網絡": "網路", "网络": "網路", "服務器": "伺服器", "接口": "介面", "性能": "效能",
    "優化": "最佳化", "調度": "排程", "集群": "叢集", "鏡像": "映像檔", "運行時": "執行期", "運行": "執行",
    "源碼": "原始碼", "變量": "變數", "模板": "範本", "調用": "呼叫", "緩存": "快取",
    "硬件": "硬體", "軟件": "軟體", "帶寬": "頻寬", "並發": "並行", "場景": "情境", "算力": "運算能力",
    "加載": "載入", "激活": "啟用", "創建": "建立", "獲取": "取得", "返回": "回傳", "打印": "印出",
    "命令行": "命令列", "用戶": "使用者", "設置": "設定", "兼容": "相容", "協議": "協定",
    "交互": "互動", "異步": "非同步", "運維": "維運",
}
AMBIGUOUS = {"程序": "行程", "文件": "檔案", "數據": "資料", "支持": "支援"}
DIAGRAMS = {"flowchart", "graph", "sequenceDiagram", "stateDiagram-v2", "classDiagram", "timeline",
            "gantt", "block-beta", "quadrantChart", "xychart-beta"}


@dataclass
class Issue:
    path: str
    line: int
    rule: str
    message: str
    severity: str = "error"


def lint_text(text, path):
    issues = []
    raw_lines = text.splitlines()
    for number, line in prose_lines(text, include_mermaid=True):
        raw = raw_lines[number - 1]
        allowed = re.findall(r"<!--\s*zh-lint:\s*allow\s+(.+?)\s*-->", raw)
        for term in allowed:
            line = line.replace(term, "")
        # Link destinations and images can contain upstream spellings; lint labels.
        line = re.sub(r"\]\([^)]*\)", "]", line)
        for term, replacement in CLEAR_TERMS.items():
            for match in re.finditer(re.escape(term), line):
                quoted = any(m.start() <= match.start() < m.end() for m in re.finditer(
                    r'「[^」]*」|『[^』]*』|“[^”]*”|"[^"]*"|\x27[^\x27]*\x27', line))
                severity = "warning" if term == "性能" and quoted else "error"
                issues.append(Issue(path, number, "zh-term", f"{severity}: {term} → {replacement}", severity))
        for term, replacement in AMBIGUOUS.items():
            if term in line:
                issues.append(Issue(path, number, "zh-term", f"warning: 請確認「{term}」語意，可能應使用「{replacement}」", "warning"))
        for char in sorted(set(line) & SIMPLIFIED_ONLY):
            issues.append(Issue(path, number, "zh-char", f"Simplified-only character: {char}"))
        if re.search(r"(?<![\u3400-\u9fff])庫(?![\u3400-\u9fff])", line):
            issues.append(Issue(path, number, "zh-term", "庫 → 函式庫／library"))
    return issues


SEQ_QUOTED_ALIAS = re.compile(r'^\s*(participant|actor)\s+\S+\s+as\s+"')
SEQ_QUOTED_TEXT = re.compile(r'^[^:]*:\s*".*"\s*$')


def check_mermaid(text, path):
    issues = []
    first = None
    in_block = False
    sequence = False
    for number, line, language, delimiter in markdown_lines(text):
        if delimiter and language == "mermaid":
            sequence = False
            if not in_block:
                first = number
            else:
                if first is not None:
                    issues.append(Issue(path, first, "mermaid", "empty Mermaid block"))
                first = None
            in_block = not in_block
        elif in_block and first is not None and line.strip() and not line.lstrip().startswith("%%"):
            keyword = line.strip().split()[0].rstrip(";")
            if keyword not in DIAGRAMS:
                issues.append(Issue(path, number, "mermaid", f"unknown diagram keyword: {keyword}"))
            sequence = keyword == "sequenceDiagram"
            first = None
        elif in_block and sequence and line.strip():
            # In sequence diagrams Mermaid prints quotes literally (checked with mermaid-cli 11.4.2).
            if SEQ_QUOTED_ALIAS.match(line):
                issues.append(Issue(path, number, "mermaid", "sequenceDiagram alias is quoted; Mermaid renders the quotes literally"))
            elif SEQ_QUOTED_TEXT.match(line):
                issues.append(Issue(path, number, "mermaid", "sequenceDiagram message or note text is quoted; Mermaid renders the quotes literally"))
    if in_block:
        issues.append(Issue(path, first or 1, "mermaid", "unclosed Mermaid block"))
    return issues


def check_links(text, path, root):
    issues = []
    for number, destination in links(text):
        parsed = urlsplit(destination)
        if parsed.scheme or parsed.netloc:
            continue
        target = (root / path).parent / unquote(parsed.path) if parsed.path else root / path
        if target.is_dir():
            target /= "README.md"
        if not target.is_file():
            issues.append(Issue(path, number, "link", f"missing file: {destination}"))
        elif parsed.fragment:
            if target.suffix.lower() != ".md":
                continue  # SVG/PDF fragments are not Markdown headings.
            if unquote(parsed.fragment) not in heading_slugs(target.read_text(encoding="utf-8")):
                issues.append(Issue(path, number, "anchor", f"missing anchor: {destination}"))
    return issues


def check_book(root):
    issues = []
    try:
        pages = load_order(root)
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as error:
        return [Issue("tools/book_order.yml", 1, "order", str(error))]
    expected = {page["path"] for page in pages}
    actual = {str(p.relative_to(root)) for p in (root / "book").rglob("*.md")}
    for path in sorted(expected - actual):
        issues.append(Issue(path, 1, "order", "ordered page does not exist"))
    for path in sorted(actual - expected):
        issues.append(Issue(path, 1, "order", "page missing from book_order.yml"))
    for path in sorted(actual):
        text = (root / path).read_text(encoding="utf-8")
        h1 = [h for h in headings(text) if h[0] == 1]
        if len(h1) != 1:
            issues.append(Issue(path, h1[1][2] if len(h1) > 1 else 1, "h1", f"expected one H1, found {len(h1)}"))
        issues.extend(lint_text(text, path))
        issues.extend(check_links(text, path, root))
        issues.extend(check_mermaid(text, path))
    try:
        for path in expected_updates(root):
            issues.append(Issue(str(path.relative_to(root)), 1, "nav", "stale nav or TOC; run tools/build_nav.py"))
    except (OSError, ValueError) as error:
        issues.append(Issue("tools/book_order.yml", 1, "nav", str(error)))
    return issues


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    issues = check_book(args.root)
    for issue in issues:
        print(f"{issue.path}:{issue.line}: [{issue.rule}] {issue.message}")
    errors = sum(i.severity == "error" for i in issues)
    warnings = len(issues) - errors
    print(f"Book check: {errors} errors, {warnings} warnings")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
