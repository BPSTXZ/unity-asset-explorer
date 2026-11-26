"""从 ECDICT csv 构建紧凑离线英汉词典 backend/data/endict.json。

来源：https://github.com/skywind3000/ecdict （ecdict.csv，约 77 万词条）。
仅保留常见小写单词（oxford/collins/词频/考试标签命中），提取简明中文首义。
用法：先下载 ecdict.csv 到 .cache/ecdict.csv，再运行本脚本。
"""
import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / ".cache" / "ecdict.csv"
OUT = ROOT / "backend" / "data" / "endict.json"

POS = r"(?:n|v|vt|vi|a|ad|adj|adv|prep|conj|pron|num|art|int|abbr|aux|modal|suf|pref|pl|sing)\.\s*"
COMMON_TAGS = ("zk", "gk", "cet4", "cet6", "ky", "ielts", "toefl", "gre")


def clean(tr: str) -> str:
    tr = tr.replace("\\n", "\n")
    line = tr.split("\n")[0].strip()
    line = re.sub(r"^" + POS, "", line)
    m = re.split(r"[；;，,（(]", line)[0].strip()
    m = re.sub(r"^" + POS, "", m).strip()
    if "<" in m or ">" in m or "[" in m or "]" in m:
        return ""
    return m


def main():
    csv.field_size_limit(10 ** 7)
    out = {}
    with open(SRC, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            w = r["word"]
            if not re.fullmatch(r"[a-z]{2,}", w):
                continue
            tr = r.get("translation") or ""
            if not tr:
                continue
            m = clean(tr)
            if not m or len(m) > 10 or not re.search(r"[\u4e00-\u9fff]", m):
                continue
            ox = r.get("oxford") or "0"
            co = r.get("collins") or "0"
            frq = r.get("frq") or "0"
            bnc = r.get("bnc") or "0"
            tag = r.get("tag") or ""
            if (ox not in ("", "0")) or (co not in ("", "0")) or (frq not in ("", "0")) \
                    or (bnc not in ("", "0")) or any(t in tag for t in COMMON_TAGS):
                out[w] = m
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"words={len(out)} size={OUT.stat().st_size/1e6:.2f}MB -> {OUT}")
    for w in ("active", "info", "hold", "material", "channel", "render", "forever", "shape", "impulse"):
        print(f"  {w:10} {out.get(w, '-')!r}")


if __name__ == "__main__":
    main()