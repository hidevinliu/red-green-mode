# red-green-mode

**你的 coding agent 说测试全绿了。这七个命令行工具负责查它是不是靠作弊绿的。**

[English](README.md) · [中文](README.zh.md)

coding agent 的成绩由它自己跑的测试判定，所以"通过"最省力的办法是去打裁判：删掉断言、给失败的
用例挂 `@pytest.mark.skip`、撒一把 `# type: ignore`，或者干脆一开始就写一个根本咬不住代码的测试。
你拿到一个绿色对勾，和一个坏掉的产品。

`red-green-mode` 是那个裁判。零依赖、纯 Python 标准库、不联网、不调模型——只有确定性的退出码，
任何 agent、任何语言栈、任何 CI 都能直接消费。

---

## 30 秒，在你自己机器上验

```bash
git clone https://github.com/hidevinliu/red-green-mode ~/rgm && export RGM=~/rgm
mkdir -p /tmp/demo && cd /tmp/demo && git init -q .
```

一个有真 bug 的函数，和一个能咬住它的测试：

```python
# billing.py
def apply_discount(price, pct):
    return price - price * pct / 100

# test_billing.py
def test_rejects_over_100():
    try:
        apply_discount(100, 150)
    except ValueError:
        return
    raise AssertionError("should reject pct > 100")
```

### 第一道门：它修的是代码，还是修的测试？

一个偷懒的 agent 根本没碰 `billing.py`：

```diff
+@pytest.mark.skip(reason="flaky")
 def test_rejects_over_100():
-    try:
-        apply_discount(100, 150)
-    except ValueError:
-        return
-    raise AssertionError("should reject pct > 100")
+    assert True
```

```console
$ python3 -m pytest -q
1 passed, 1 skipped          # ← agent 向你报喜

$ git diff > /tmp/d.diff
$ python3 $RGM/tools/rgm_anticheat.py scan --diff-file /tmp/d.diff --format sentinel
ANTICHEAT=FAIL
FINDINGS=3
WARNINGS=0
ALLOWS=0
$ echo $?
1
```

换成诚实的修法——给 `billing.py` 补上缺的判断，测试一个字不动：

```console
$ python3 -m pytest -q
2 passed

$ git diff > /tmp/honest.diff
$ python3 $RGM/tools/rgm_anticheat.py scan --diff-file /tmp/honest.diff
{"clean": true, "findings": [], "allows": []}
$ echo $?
0
```

同样是全绿，判定相反。

### 第二道门：这个测试到底咬没咬住代码？

反作弊只看这次改了什么。一个从出生就没牙的测试，能干干净净地过第一道门。
所以第二道门反过来去改你的生产代码，要求测试必须变红：

```python
# test_dead.py —— 永远绿，什么也没测
def test_it_runs():
    try:
        apply_discount(100, 10)
    except Exception:
        pass
```

```console
$ python3 $RGM/tools/rgm_mutation.py check-pair \
    --verifier "python3 -m pytest -q test_dead.py" \
    --target "billing.py::apply_discount" --format sentinel
RGM_MUTATION=FAIL
pair target=billing.py::apply_discount DEAD (survived 6/6)
$ echo $?
1
```

```console
$ python3 $RGM/tools/rgm_mutation.py check-pair \
    --verifier "python3 -m pytest -q test_billing.py" \
    --target "billing.py::apply_discount" --format sentinel
RGM_MUTATION=PASS
pair target=billing.py::apply_discount ALIVE (killed by: L2:'if not 0 <= pct <= 100:')
$ echo $?
0
```

注入 6 个变异，字节级还原 6 个（`finally` + 落盘 sidecar + 崩溃后可用的 `restore` 子命令）。
跑完你的工作区和跑之前一模一样。

---

## 里面有什么

七个返回退出码的工具。没有任何一个环节需要问模型的意见。

| 工具 | 它回答什么问题 | 退出码 |
|---|---|---|
| `rgm_anticheat.py` | 这次 diff 有没有**新引入**伪造绿灯的手法？ | `0` 干净 · `1` 抓到作弊 · `2` 跑不起来 |
| `rgm_mutation.py` | 验证器真的咬得住目标代码，还是个死靶子？ | `0` ALIVE · `1` DEAD |
| `acceptance_contract.py` | 验收标准写得合规吗？说好的 verifier 有没有被人事后换成 `echo PASS`？ | `0` 合规且签名一致 · `1` 漂移 |
| `rgm_ledger.py stall-check` | 这个循环是在推进，还是在空转烧 token？ | `0` 在推进 · `1` 空转 |
| `rgm_partition.py` | 这几个任务真的能并行，还是会互相踩？ | `0` 不相交 · `1` 有重叠 |
| `rgm_constraints.py` | 这次运行有没有写进仓库声明为只读的路径？ | `0` 守住了 · `1` 越界 |
| `rgm_gate.py` | 以上全部，一次裁决，输出一行给 hook 去 grep 的哨兵。 | `0` PASS · `1` FAIL · `2` 出错 |

**反作弊规则共 9 条**（7 条挂、2 条只提示）：Python / JS-TS / Go-Rust 三套测试跳过写法、
静态检查压制（`# noqa`、`@ts-ignore`、`eslint-disable`、`#[allow(...)]`）、恒真断言、
**被删掉**的断言和测试函数、linter 严格度下调，外加两条只警告的坏味道
（mock 掉被测对象本身、注释里写着"写死骗过测试"）。

它只读 diff 的新增行和删除行——你代码库里本来就有的 `# type: ignore` 不算这次运行的账。

**逃生阀。**有时候跳过一个测试是合理的。把理由写在行内：

```python
@pytest.mark.skip(reason="upstream API down")  # rgm-allow: 供应商断服，工单 OPS-412
```

这条发现会降级为 `allowed`，理由写进运行记录。
作弊和合理判断的唯一区别，就是有没有留下一条能被审计的理由。

---

## 安装

```bash
git clone https://github.com/hidevinliu/red-green-mode
python3 -m pytest red-green-mode/tests/ -q      # 262 passed，约 20 秒
```

**需要什么：**Python 3.9+ 和 `git`。就这些——工具本身不 import 任何标准库以外的东西。
`pytest` 只在跑**本仓库自己**的测试套件时需要，你拿工具去用在自己项目上并不需要它。
没有包要装，没有服务要起，没有配置文件要写。

### 当成 Claude Code / Codex 的 skill 用

```bash
git clone https://github.com/hidevinliu/red-green-mode ~/.claude/skills/red-green-mode
```

`SKILL.md` 会被「红绿灯模式」/「跑到全绿」/ *"keep fixing until the tests pass"* 这类说法触发，
然后驱动整个循环：选验证器 → 跑 → 给红灯分类 → 针对性修 → 再跑 → 收工前过一遍 gate。

**光有 skill 只是建议。**模型想停还是能停。要真正物理阻断，把 Stop hook
（`tools/rgm_stop_hook.sh`）接进 `settings.json`——gate 挂了它就 exit 2，
而且是 fail-closed：gate 本身跑不起来，你照样收不了工。
装法见 [`tools/STOP-HOOK-INSTALL.md`](tools/STOP-HOOK-INSTALL.md)。

### 配套 skill：`mutation-check`

只想单独用第二道门，不跑整个循环？`skills/mutation-check/` 是一个独立 skill，
被「这个测试是不是白测了」/「找出死测试」这类说法触发，直接调用 `rgm_mutation.py`：

```bash
ln -s ~/.claude/skills/red-green-mode/skills/mutation-check ~/.claude/skills/mutation-check
```

### 在 CI 里用，完全不涉及 agent

`rgm_anticheat.py` 本质就是个 diff 扫描器，对人写的 PR 一样管用：

```yaml
- name: Block test-tampering in this PR
  run: |
    git diff origin/${{ github.base_ref }}...HEAD > /tmp/pr.diff
    python3 tools/rgm_anticheat.py scan --diff-file /tmp/pr.diff --format sentinel
```

---

## 它**做不到**什么

信它之前先读 [`tools/ANTICHEAT-LIMITATIONS.md`](tools/ANTICHEAT-LIMITATIONS.md)。
最要紧的两条：

- **改生产代码去迎合测试，基本抓不到。**如果 agent 直接在 `billing.py` 里写死 `return 90`
  让测试过，正则扫 diff 是看不出来的。变异测试从"测试有没有牙"这个角度补了一部分，
  但"agent 写出错的、却被测试覆盖的代码"是个未解问题，不是已解问题。
- **运行记录（ledger）是受信输入。**`rgm_gate.py` 会通过 shell 执行它在里面找到的验证器命令。
  谁能写你的 ledger，谁就能让 gate 执行任意命令并返回 PASS。
  请把 ledger 完全当作 `Makefile` 对待：它是代码，按代码来审。

还有一条也说清楚：`evals/` 里有 53 条手写的场景评分条目，**没有自动 runner**。
它们是给人读的检查清单，不是跑绿了的基准。别把它当"53 个 eval 全过"来读。

---

## 相关项目，以及本项目的位置

- [`nizos/tdd-guard`](https://github.com/nizos/tdd-guard) 管的是**事前**：不许在没有失败测试的
  情况下写代码。本项目管**事后**：测试确实绿了——它绿得有没有道理？两者互补，不冲突。
- Anthropic 内置的 verification loop 让 agent 反复跑自己的检查，但不问 agent 有没有动过那些检查。
  那个缺口就是这个仓库存在的全部理由。
- 学术侧正从基准测试那一端逼近同一个问题——SpecBench、EvilGenie、TRACE。
  这个仓库是同一件事的小号、无聊、退出码形态的版本，今天就能塞进一个 hook 里。

## 许可

MIT，见 [LICENSE](LICENSE)。
