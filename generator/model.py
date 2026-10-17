"""指令 / 流程 / 文件 序列化 (v3: Relay 来自 scene)。"""
from xml.sax.saxutils import escape
import json
import geom

class Cmd:
    def __init__(self, name, comp, action, timeout, params="", relays=()):
        self.name, self.comp, self.action, self.timeout, self.params = name, comp, action, int(timeout), params
        self.relays, self.id, self.pre = list(relays), None, None
    def pre_str(self): return "-1,UnTask" if self.pre is None else f"{self.pre[0].id},{self.pre[1]}"
    def xml(self):
        head = (f'<Command ID="{self.id}" Name="{escape(self.name)}" PreTask="{self.pre_str()}" Component="{self.comp}" '
                f'Action="{self.action}" Params="{self.params}" Timeout="{self.timeout}" Path=""')
        if not self.relays: return head + " />"
        return head + ' ScriptContent="0">\n' + "\n".join("      " + r.xml(i + 1) for i, r in enumerate(self.relays)) + "\n    </Command>"
    def layout(self, k):
        e = ' SequenceTypeId="0"' if self.relays else ""; t = ' ScriptHead=""' if self.relays else ""
        return f'  <Command Position="0,{30*k}"{e} ID="{self.id}" Name="{escape(self.name)}" Type="2"{t}/>'

def chain(cmds):
    prev = None
    for c in cmds: c.pre = None if prev is None else (prev, "FS"); prev = c
    return cmds

class CommandBlock:
    """Native UnBrokenBlockList entry; Cycle is the total block iterations."""
    def __init__(self, name, cmds, cycles):
        self.name, self.cmds, self.cycles = name, list(cmds), int(cycles)
        assert self.cmds and self.cycles >= 1

    @property
    def timeout(self):
        ends, active, members = {}, set(), set(self.cmds)
        def end(c):
            if c in ends: return ends[c]
            assert c not in active, '块内依赖不得成环'
            active.add(c)
            start = 0
            if c.pre and c.pre[0] in members:
                prev, relation = c.pre
                start = end(prev) - (prev.timeout if relation == 'SS' else 0)
            active.remove(c)
            ends[c] = start + c.timeout
            return ends[c]
        return max(map(end, self.cmds))

    def native(self):
        assert all(c.id is not None for c in self.cmds)
        return dict(Cycle=self.cycles, Enable=True,
                    IDList=[c.id for c in self.cmds], Name=self.name)


class Flow:
    def __init__(self, name, cmds, repeats=1, sync=None, chain_commands=True, sync_groups=None, blocks=()):
        assert not (sync and sync_groups), '只使用一种共同结束组表示'
        self.sync_groups = [list(g) for g in sync_groups] if sync_groups else ([list(sync)] if sync else [])
        self.name, self.cmds, self.repeats, self.id = name, list(cmds), repeats, None
        self.blocks = list(blocks)
        self.sync = [c for group in self.sync_groups for c in group] if self.sync_groups else None
        if chain_commands: chain(self.cmds)
    @property
    def timeout(self):
        """依赖图的标称最长路径；并行计时不再与循环/温控时长相加。

        TRUE 循环的实际停止边界由执行器决定，此处不模拟硬件整轮结束。
        """
        ends, active, members = {}, set(), set(self.cmds)
        block_of = {}
        for block in self.blocks:
            assert set(block.cmds) <= members
            for c in block.cmds:
                assert c not in block_of, '本生成器只写非嵌套、不重叠的局部块'
                block_of[c] = block
        def end(c):
            if c in ends: return ends[c]
            assert c not in active, '指令依赖不得成环'
            active.add(c)
            start = 0
            if c.pre and c.pre[0] in members:
                previous, relation = c.pre
                assert relation in ('FS', 'SS'), relation
                start = end(previous) - (previous.timeout if relation == 'SS' else 0)
                previous_block = block_of.get(previous)
                if previous_block and previous_block is not block_of.get(c):
                    assert relation == 'FS', '块外后续指令须等待整个块完成'
                    start += (previous_block.cycles-1)*previous_block.timeout
            ends[c] = start + c.timeout
            active.remove(c)
            return ends[c]
        return max((end(c) + ((block_of[c].cycles-1)*block_of[c].timeout
                             if c in block_of else 0) for c in self.cmds), default=0)
    def xml(self):
        inner = "\n".join("    " + c.xml() for c in self.cmds)
        groups = ';'.join(','.join(str(c.id) for c in group) for group in self.sync_groups)
        se = f' SyncEndingCmdList="{groups}"' if groups else ""
        if self.blocks:
            encoded = escape(json.dumps([b.native() for b in self.blocks],
                                       ensure_ascii=False,separators=(',',':')), {'"':'&quot;'})
            se += f' UnBrokenBlockList="{encoded}"'
        return f'  <Flow ID="{self.id}" Name="{escape(self.name)}" Repeats="{self.repeats}" Timeout="{self.timeout}"{se}>\n{inner}\n  </Flow>'
    def layout(self, i):
        return "\n".join([f' <Flow Position="35,{14 + 104.406*i:g}" ID="{self.id}" Name="{escape(self.name)}" Type="0" Expandgroup="">']
                         + [c.layout(k) for k, c in enumerate(self.cmds)] + [" </Flow>"])

class Script:
    def __init__(self, name, flows, chipset, first_id):
        self.name, self.flows, self.chipset = name, flows, chipset
        n = first_id
        for f in flows:
            f.id, n = n, n + 1
            for c in f.cmds: c.id, n = n, n + 1
    @classmethod
    def combine(cls, name, scripts):
        """只合并文件层级，不重新分配流程/指令编号或修改依赖。"""
        assert scripts and len({s.chipset for s in scripts}) == 1
        result = cls.__new__(cls)
        result.name, result.chipset = name, scripts[0].chipset
        result.flows = [f for s in scripts for f in s.flows]
        ids = [item.id for f in result.flows for item in [f, *f.cmds]]
        assert len(ids) == len(set(ids)), '合并后的流程与指令编号必须全局唯一'
        return result
    def xml(self):
        return (f'<Script Name="{self.name}" Version="v0.0.0" Type="3" ChipsetType="{self.chipset}" ActiveTec="1">\n'
                + "\n".join(f.xml() for f in self.flows) + "\n</Script>\n")
    def layout_xml(self):
        p = [f'<Script Name="{self.name}" Version="v0.0.0.1">'] + [f.layout(i) for i, f in enumerate(self.flows)]
        p += [f' <Arrow EndID="{b.id}" StartID="{a.id}"/>' for a, b in zip(self.flows, self.flows[1:])]
        return "\n".join(p + ["</Script>", ""])
