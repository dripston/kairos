#!/usr/bin/env python3
"""Assemble QA.md from the automated checks (run after tools/build.sh or the equivalent steps)."""
import json, subprocess, sys
def table(f): return subprocess.run([sys.executable, "tools/qa_table.py", f], capture_output=True, text=True).stdout
facts = json.load(open("qa/facts.json"))
doc = open("tools/QA.template.md").read()
doc = doc.replace("{{PRODUCT_TABLE}}", table("qa/product_5s.jsonl")).replace("{{TECH_TABLE}}", table("qa/technical_5s.jsonl"))
for k, v in facts.items(): doc = doc.replace("{{%s}}" % k, str(v))
open("QA.md", "w").write(doc); print("QA.md written")
